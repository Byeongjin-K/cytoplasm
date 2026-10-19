"""Drive upload + per-person share inside one mail approval (owner request 2026-10-14).

A file too large for a mail attachment goes out as a Drive link. The owner asked for
one approval that covers all of it: "오토파지에 \"드라이브 업로드·공유\" 승인 경로를
새로 만들고 그걸로 보냄 (몇 시간, 다음에도 씀)". So the share is not a second card or a
second watcher — it is a field of the ordinary compose draft:

* ``prepare`` (compose time) freezes a private copy of the file and returns the share
  spec — file sha256 and size, the Drive folder and file name, the people, role
  ``reader`` — which ``triage_core.draft_sha256`` binds into the card the owner ✅s.
  The mail body carries ``<Drive 링크>`` exactly once.
* ``run`` (after ✅, inside ``triage_gate.execute_draft``) uploads through the Drive
  facade, checks Drive's sha256 and size, grants each person view-only access, checks
  the file holds nothing else (no anyone-with-link, no domain, no writer), and only then
  hands the send a body whose placeholder is the link. Any failure records the step on
  the draft and raises, so the mail is never sent with a missing or wrong link.

The share result (file id, link) lives outside the hash, like the retry fields, so a
send that fails after a good share reuses that upload on the next attempt.
"""
from __future__ import annotations

import hashlib
import html
import os
import re
import secrets
import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Final

import mail_send_retry
import triage_core
import triage_gate
import triage_mode
from triage_mode import append_record

PLACEHOLDER: Final = "<Drive 링크>"
ROLE: Final = "reader"
_EMAIL: Final = re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}")
_KST: Final = timedelta(hours=9)


def _share_dir() -> Path:
    path = triage_gate.gate_dir() / "drive-share"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def _digest(path: Path) -> tuple[str, int]:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest(), path.stat().st_size


def _people(grantees: tuple[str, ...]) -> list[str]:
    people = sorted({person.strip().lower() for person in grantees if person.strip()})
    if not people or any(not _EMAIL.fullmatch(person) for person in people):
        raise triage_gate.GateError("--share-with 에 공유받을 사람의 이메일 주소를 하나 이상 주세요", 2)
    return people


def prepare(
    path: str, grantees: tuple[str, ...], *, body: str, project: str = "", today: date | None = None,
) -> dict:
    """Freeze the file and return the share spec the draft hash binds."""
    if body.count(PLACEHOLDER) != 1:
        raise triage_gate.GateError(
            f"--drive-share 를 쓰면 본문에 `{PLACEHOLDER}` 자리가 정확히 한 번 있어야 합니다", 2)
    people = _people(grantees)
    try:
        source = Path(path).expanduser().resolve(strict=True)
    except OSError as error:
        raise triage_gate.GateError("공유할 파일을 읽을 수 없습니다", 2) from error
    if not source.is_file() or source.stat().st_size == 0:
        raise triage_gate.GateError("공유할 대상이 비어 있지 않은 일반 파일이 아닙니다", 2)
    from automation import drive_outputs, drive_taxonomy

    on = today or (datetime.now(UTC) + _KST).date()
    try:
        parts, drive_name = drive_outputs.share_target(source.name, on=on, project=project or None)
    except drive_taxonomy.TaxonomyError as error:
        raise triage_gate.GateError(f"Drive 공유 위치를 정할 수 없습니다: {error}", 2) from error
    holder = _share_dir() / secrets.token_hex(8)
    holder.mkdir(mode=0o700)
    frozen = holder / source.name
    try:
        shutil.copyfile(source, frozen)
        os.chmod(frozen, 0o600)
        sha256, size = _digest(frozen)
        if _digest(source) != (sha256, size):
            raise triage_gate.GateError("공유할 파일이 복사 중에 바뀌었습니다 — 다시 요청해 주세요", 2)
    except BaseException:
        shutil.rmtree(holder, ignore_errors=True)
        raise
    return {
        "drive_name": drive_name,
        "folder": list(parts),
        "frozen_path": str(frozen),
        "grantees": people,
        "placeholder": PLACEHOLDER,
        "role": ROLE,
        "sha256": sha256,
        "size_bytes": size,
        "source_name": source.name,
    }


def discard(spec: dict) -> None:
    """Remove a frozen copy whose draft was never written."""
    holder = Path(str(spec.get("frozen_path", ""))).parent
    if holder.parent == _share_dir():
        shutil.rmtree(holder, ignore_errors=True)


def with_link(draft: dict, link: str) -> dict:
    """The draft as sent: ``send_argv`` is the frozen argv with the link in the placeholder.

    The stored record keeps the approved ``argv``: a failed send writes the draft back,
    and a link baked into ``argv`` would break the hash its retry re-verifies.
    """
    argv = list(draft["argv"])
    index = argv.index("--body") + 1
    if argv[index].count(PLACEHOLDER) != 1:
        raise triage_gate.GateError("발송 본문에 Drive 링크 자리가 정확히 한 번 있지 않습니다", 2)
    argv[index] = argv[index].replace(PLACEHOLDER, link)
    if "--body-html" in argv:  # the same placeholder, HTML-escaped by mail_quote.html_body
        html_index = argv.index("--body-html") + 1
        escaped = html.escape(PLACEHOLDER, quote=False)
        if argv[html_index].count(escaped) != 1:
            raise triage_gate.GateError("HTML 본문에 Drive 링크 자리가 정확히 한 번 있지 않습니다", 2)
        anchor = f'<a href="{html.escape(link, quote=True)}">{html.escape(link, quote=False)}</a>'
        argv[html_index] = argv[html_index].replace(escaped, anchor)
    return {**draft, "send_argv": argv}


def _fail(draft: dict, cause: str, *, retryable: bool, detail: str = "") -> triage_gate.GateError:
    error = {"error_code": "drive_share_failed", "stage": "drive_share",
             "retryable": retryable, "cause": cause}
    if detail:  # node-private record only; the owner's notice prints stage and cause
        error["detail"] = detail
    path = triage_gate._draft_path(draft["id"])
    if path is not None:
        triage_gate.write_json(path, mail_send_retry.after_failure(draft, error, datetime.now(UTC)))
    return triage_gate.GateError(
        "Drive 업로드·공유 단계에서 멈춰 메일을 보내지 않았습니다 "
        f"(cause={cause} retryable={str(retryable).lower()})", 6)


def _detail(error: Exception) -> str:
    """Which call failed and how, for the private record: the class and a short message."""
    return f"{type(error).__name__}: {' '.join(str(error).split())[:200]}"


def run(draft: dict, approval: triage_gate.Approval) -> dict:
    """Upload, verify and share the approved file; return the draft to send with its link."""
    mode = triage_mode.effective_mode()
    if mode != "full-go":
        raise triage_gate.GateError(f"mail-mode={mode} — 실발송 비활성(W4-1N 분기)", 3)
    spec = draft["drive_share"]
    if spec.get("role") != ROLE:
        raise _fail(draft, "drive_role_refused", retryable=False)
    _ = with_link(draft, "")  # the placeholder check, before anything leaves the node
    prior = draft.get("drive_share_result")
    prior_id = str(prior.get("file_id") or "") if isinstance(prior, dict) else ""
    try:
        from automation import drive_outputs
    except ImportError as error:
        raise _fail(draft, "drive_facade_unavailable", retryable=True) from error
    try:
        result = drive_outputs.share(
            Path(spec["frozen_path"]), tuple(spec["folder"]), spec["drive_name"],
            sha256=spec["sha256"], size=spec["size_bytes"], grantees=tuple(spec["grantees"]),
            prior_id=prior_id or None,
        )
    except drive_outputs.ShareRefused as error:
        raise _fail(draft, error.code, retryable=False, detail=_detail(error)) from error
    except Exception as error:  # noqa: BLE001 — a transport failure is retried on the capped backoff
        raise _fail(draft, "drive_upload_failed", retryable=True, detail=_detail(error)) from error
    receipt = {"action": result.action, "file_id": result.file_id, "link": result.link,
               "invited_by_google": list(result.invited_by_google), "shared_at": triage_core.utc_now()}
    path = triage_gate._draft_path(draft["id"])
    if path is not None:
        triage_gate.write_json(path, {**draft, "drive_share_result": receipt})
    append_record(triage_gate._approval_log(), {
        "action": "drive.share",
        "approval": {"method": approval.method, "ref": approval.ref},
        "payload": {"draft_sha256": draft["sha256"], "file_sha256": spec["sha256"],
                    "grantee_count": len(spec["grantees"]), "role": ROLE},
        "result": {"status": "shared", "action": result.action, "file_id": result.file_id},
        "target_id": f"drive:share:{draft['id']}",
        "timestamp": receipt["shared_at"],
    })
    return with_link({**draft, "drive_share_result": receipt}, result.link)
