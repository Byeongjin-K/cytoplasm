from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path
from typing import assert_never

from . import patent_export_binding
from . import patent_export_gate
from . import patent_export_manifest
from .patent_storage import PatentPaths, workspace
from .patent_export_render import render_approval as render_approval


class PatentExportError(RuntimeError):
    """Export execution error."""


def draft_path(paths: PatentPaths, slug: str) -> Path:
    return workspace(paths, slug) / "draft.md"


def compute_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return f"sha256:{hasher.hexdigest()}"


def gws_bin() -> str:
    return os.environ.get("PATENT_GWS_BIN", "gws")


def age_bin() -> str:
    return os.environ.get("PATENT_AGE_BIN", "age")


def ssh_pubkey() -> Path:
    path = Path(os.environ.get("PATENT_SSH_PUBKEY", "~/.ssh/id_ed25519.pub")).expanduser()
    if not path.exists():
        raise PatentExportError(f"SSH pubkey not found: {path}")
    return path


def encrypt_age(src: Path, dst: Path, pubkey: Path) -> None:
    result = subprocess.run(
        [age_bin(), "-R", str(pubkey), "-o", str(dst), str(src)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise PatentExportError(f"age encryption failed: {result.stderr}")


def preflight_acl(folder_id: str) -> None:
    result = subprocess.run(
        [
            gws_bin(),
            "drive",
            "permissions",
            "list",
            "--params",
            json.dumps({"fileId": folder_id, "fields": "permissions(id,type,role,emailAddress,domain)"}),
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise PatentExportError(f"gws permissions list failed: {result.stderr}")
    
    try:
        data = json.loads(result.stdout.strip() or "{}")
    except json.JSONDecodeError:
        raise PatentExportError("Invalid JSON from gws permissions list")
    
    permissions = data.get("permissions", [])
    if not permissions:
        raise PatentExportError("No permissions found for folder")
    
    if len(permissions) > 1:
        raise PatentExportError("Folder has extra users beyond the single owner")
    
    perm = permissions[0]
    if perm.get("role") != "owner" or perm.get("type") != "user":
        raise PatentExportError("folder permission is not a single owner user")


def drive_upload_into(file: Path, folder_id: str) -> tuple[str, str]:
    result = subprocess.run(
        [
            gws_bin(),
            "drive",
            "files",
            "create",
            "--json",
            json.dumps({"name": file.name, "parents": [folder_id]}),
            "--upload",
            file.name,
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(file.parent),
    )
    if result.returncode != 0:
        raise PatentExportError(f"gws files create failed: {result.stderr}")
    
    try:
        data = json.loads(result.stdout.strip() or "{}")
    except json.JSONDecodeError:
        raise PatentExportError("Invalid JSON from gws files create")
    
    file_id = str(data.get("id", ""))
    if not file_id:
        raise PatentExportError("No file id returned from gws files create")
    
    link = str(data.get("webViewLink") or f"https://drive.google.com/file/d/{file_id}/view")
    return file_id, link


def prepare_export(paths: PatentPaths, slug: str, *, mode: str) -> str:
    if mode not in ("enc", "plaintext"):
        raise PatentExportError(f"unsupported export mode: {mode}")
    draft = draft_path(paths, slug)
    if not draft.exists() or draft.stat().st_size == 0:
        raise PatentExportError("Draft does not exist or is empty")
    
    from . import patent_export_approval

    now = patent_export_manifest.now_ts()
    payload = patent_export_approval.PatentApprovalPayload(
        slug=slug,
        plaintext_sha256=compute_sha256(draft),
        dest_folder_id=patent_export_manifest.archive_folder_id(),
        mode=mode,
        expiry_ts=now + 3600,
        created_ts=now,
        render_version=3,
    )
    content = None
    if not patent_export_binding.live_requests(slug):
        try:
            content = render_approval(payload)
        except patent_export_gate.ExportGateError:
            payload = replace(payload, render_version=1)
            content = render_approval(payload)
    facade = patent_export_approval.lifecycle()
    binding = patent_export_binding.new_binding(slug)
    channel_id = binding.channel_id
    adapter = patent_export_approval.PatentApprovalGate(payload, binding, content=content)
    intent = facade.ApprovalIntent(
        key=patent_export_approval.approval_key(slug),
        action_hash=patent_export_approval.semantic_action_hash(payload),
        channel_id=channel_id,
    )
    try:
        verdict = facade.request_owner_approval(
            intent,
            adapter,
            patent_export_approval.confirm_lease(),
            patent_export_approval.posting_journal(),
        )
    except (facade.ApprovalRecordsError, facade.ApprovalSurfaceError) as error:
        raise patent_export_gate.ExportGateError("patent approval lifecycle failed", 3) from error
    match verdict.outcome:
        case facade.Outcome.POSTED:
            approved = adapter.result()
        case facade.Outcome.PENDING:
            approved = adapter.result(verdict.live)
        case facade.Outcome.DEFERRED | facade.Outcome.REFUSED:
            reason = verdict.reason.value if verdict.reason is not None else "unknown"
            raise patent_export_gate.ExportGateError(
                f"patent approval not posted ({verdict.outcome.value}:{reason})",
                3 if reason in {"store-unreadable", "posting-journal-stale"} else 1,
            )
        case unreachable:
            assert_never(unreachable)
    if approved is None:
        raise patent_export_gate.ExportGateError("patent approval result unavailable", 3)
    return (
        f"PATENT-EXPORT-PREPARED slug={slug} sha256={approved.plaintext_sha256} "
        f"expiry={approved.expiry_ts} mode={approved.mode}"
    )


def execute_export(paths: PatentPaths, slug: str) -> str:
    with patent_export_manifest.lock(slug):
        m = patent_export_manifest.load_manifest(slug)
        if m.state is not patent_export_manifest.State.APPROVED:
            raise PatentExportError(f"manifest state is {m.state}, expected APPROVED")
        if patent_export_manifest.now_ts() >= m.expiry_ts:
            raise PatentExportError("approval has expired")
        if m.dest_folder_id != patent_export_manifest.archive_folder_id():
            raise PatentExportError("destination is not the current allowlist folder")
        if m.mode not in ("enc", "plaintext"):
            raise PatentExportError(f"unsupported export mode: {m.mode}")
        reaction = patent_export_gate.reaction_state(m)
        if reaction == patent_export_gate.CANCEL_EMOJI:
            patent_export_manifest.transition(
                slug,
                allowed_from={patent_export_manifest.State.APPROVED},
                to=patent_export_manifest.State.CANCELLED,
            )
            raise PatentExportError("owner cancelled the export")
        if reaction != patent_export_gate.APPROVE_EMOJI:
            raise PatentExportError("owner approval is not currently present")
        work = Path(tempfile.mkdtemp(prefix="patent-export-"))
        try:
            snapshot = work / "draft.md"
            shutil.copyfile(draft_path(paths, slug), snapshot)
            snapshot.chmod(0o600)
            if compute_sha256(snapshot) != m.plaintext_sha256:
                raise PatentExportError("draft changed since prepare")
            preflight_acl(m.dest_folder_id)
            if m.mode == "enc":
                upload_target = work / "draft.md.age"
                encrypt_age(snapshot, upload_target, ssh_pubkey())
                upload_target.chmod(0o600)
                ciphertext_sha: str | None = compute_sha256(upload_target)
            else:
                upload_target = snapshot
                ciphertext_sha = None
            file_id, link = drive_upload_into(upload_target, m.dest_folder_id)
            patent_export_manifest.transition(
                slug,
                allowed_from={patent_export_manifest.State.APPROVED},
                to=patent_export_manifest.State.CONSUMED,
            )
            _append_audit(m, slug, ciphertext_sha, file_id, link)
            patent_export_gate.dm_owner(
                patent_export_binding.owner_dm(), f"Patent export completed: {link}"
            )
            return f"PATENT-EXPORTED slug={slug} file={file_id}"
        finally:
            shutil.rmtree(work, ignore_errors=True)


def export_owner_command(paths: PatentPaths, slug: str, *, mode: str, channel_id: str, message_id: str) -> str:
    """Owner 2026-10-18 18:29 KST "5번도 카드 없이 해": export to the owner's own single-owner
    archive folder without a card. The YooDori gate verified the owner's message first; the
    snapshot, ACL preflight, encryption and audit are the same as the card path."""
    if mode not in ("enc", "plaintext"):
        raise PatentExportError(f"unsupported export mode: {mode}")
    draft = draft_path(paths, slug)
    if not draft.exists() or draft.stat().st_size == 0:
        raise PatentExportError("Draft does not exist or is empty")
    folder = patent_export_manifest.archive_folder_id()
    work = Path(tempfile.mkdtemp(prefix="patent-export-"))
    try:
        snapshot = work / "draft.md"
        shutil.copyfile(draft, snapshot)
        snapshot.chmod(0o600)
        plaintext_sha = compute_sha256(snapshot)
        preflight_acl(folder)
        if mode == "enc":
            upload_target = work / "draft.md.age"
            encrypt_age(snapshot, upload_target, ssh_pubkey())
            upload_target.chmod(0o600)
            ciphertext_sha: str | None = compute_sha256(upload_target)
        else:
            upload_target, ciphertext_sha = snapshot, None
        file_id, link = drive_upload_into(upload_target, folder)
        _write_audit({
            "approval": {"channel": channel_id, "message_id": message_id, "method": "owner_command",
                         "owner_id": patent_export_gate.owner_id()},
            "mode": mode, "plaintext_sha256": plaintext_sha, "ciphertext_sha256": ciphertext_sha,
            "dest_folder_id": folder, "file_id": file_id,
            "result": {"status": "owner_command", "webViewLink": link},
            "target_id": f"patent:{slug}", "nonce": None,
        })
        return f"PATENT-EXPORTED slug={slug} file={file_id} link={link}"
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _append_audit(
    m: patent_export_manifest.Manifest, slug: str, ciphertext_sha: str | None, file_id: str, link: str
) -> None:
    _write_audit({
        "approval": {
            "channel": m.channel_id,
            "message_id": m.message_id,
            "method": "manual_reaction",
            "owner_id": patent_export_gate.owner_id(),
        },
        "mode": m.mode,
        "plaintext_sha256": m.plaintext_sha256,
        "ciphertext_sha256": ciphertext_sha,
        "dest_folder_id": m.dest_folder_id,
        "file_id": file_id,
        "result": {"status": "approved", "webViewLink": link},
        "target_id": f"patent:{slug}",
        "nonce": m.nonce,
    })


def _write_audit(fields: dict) -> None:
    record = {
        "action": "patent_export.drive_backup",
        **fields,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    audit_path = patent_export_manifest._export_root() / "audit.jsonl"
    with audit_path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n")
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    audit_path.chmod(0o600)
