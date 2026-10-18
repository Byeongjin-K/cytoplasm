"""Draft store + send-backend routing facade for W4-2 (제약 1/6).

NO reply is ever sent before an owner confirmation (triage_confirm has the two
transports). This module owns the draft lifecycle — create, bind the approval
message, load, expire, discard — plus the paths every surface writes to, and it
routes an approved draft to the backend its account requires:

  provider="gmail"  → ``triage_gate_gmail.execute_gmail_draft``
  otherwise         → ``triage_gate_mailon.execute_mailon_draft``

The backends live in siblings (G8 LOC split) but reach their side effects back
through THIS module's attributes (``_run_send``, ``write_json``, ``os``,
``_draft_path``), so the gate keeps exactly one seam per effect.

Two CONSECUTIVE approved-send failures downgrade the runtime mail-mode to
no-go (triage_mode, source W4-2-runtime) and every execution re-checks the
effective mode fail-closed first — see ``triage_gate_mailon``.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import mail_quote
import mail_wrapper
import triage_core
from triage_mode import gate_dir, write_json

if TYPE_CHECKING:
    import gmail_approval_gate

SEND_TIMEOUT_S = 900


class GateError(RuntimeError):
    """Gate refusal with a CLI exit code (1 unconfirmed, 3 config, 6 exec)."""

    def __init__(self, message: str, exit_code: int = 3) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True, slots=True)
class Approval:
    """A verified owner confirmation bound to one draft execution."""

    ref: str
    method: str
    owner: str


def db_path() -> Path:
    return Path(os.environ.get("TRIAGE_DB", "~/state/mail-triage.db")).expanduser()


def mail_home() -> Path:
    path = Path(os.environ.get("TRIAGE_MAIL_HOME", "~/mail")).expanduser()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def _public_drafts_dir() -> Path:
    path = gate_dir() / "drafts"
    path.mkdir(mode=0o700, exist_ok=True)
    return path


def _legacy_drafts_dir() -> Path:
    """Read existing drafts without moving or reprocessing them."""
    return Path(os.environ.get("TRIAGE_MAIL_HOME", "~/mail")).expanduser() / "triage-drafts"


def _approval_log() -> Path:
    return Path(
        os.environ.get("TRIAGE_APPROVAL_LOG", "/srv/autophagy-agents/logs/approvals.jsonl")
    ).expanduser()


def _send_log() -> Path:
    return gate_dir() / "send-log.jsonl"


# ---------------------------------------------------------------- drafts

def _draft_path(draft_id: str) -> Path | None:
    if not draft_id.isalnum():
        raise GateError(f"잘못된 드래프트 id: {draft_id!r}", 3)
    for directory in (_public_drafts_dir(), _legacy_drafts_dir()):
        candidate = directory / f"{draft_id}.json"
        if candidate.exists():
            return candidate
    return None


def create_draft(
    *, uid: str, sender: str, mail_subject: str, to: str, subject: str, body: str,
    category: str, flags: tuple[str, ...],
    kind: str = "reply", channel_id: str = "",
    attachment_paths: tuple[str | Path, ...] = (),
    cc: str = "",
    origin_channel_id: str = "", origin_message_id: str = "",
    quote: str = "",
    forward: dict | None = None,
    drive_share: dict | None = None,
    bcc: tuple[str, ...] = (),
) -> dict:
    """Persist a pending draft; ``quote`` (the answered or forwarded mail) is sent below ``body``."""
    directory = _public_drafts_dir()
    draft_id = secrets.token_hex(3)
    while (directory / f"{draft_id}.json").exists():
        draft_id = secrets.token_hex(3)
    attachments = triage_core.build_attachment_manifest(attachment_paths)
    private_paths = tuple(item["source_path_private"] for item in attachments)
    argv = triage_core.build_send_argv(
        mailon_python(), to, subject, mail_quote.with_quote(body, quote), private_paths, cc,
        **({"bcc": bcc} if bcc else {}),
    )
    record = {
        "argv": list(argv),
        "body": body,
        "category": category,
        "cc": cc,
        "channel_id": channel_id,
        "created": triage_core.utc_now(),
        "flags": list(flags),
        "id": draft_id,
        "kind": kind,
        "mail_subject": mail_subject,
        "message_id": "",
        "origin_channel_id": origin_channel_id,
        "origin_message_id": origin_message_id,
        "sender": sender,
        "sender_masked": triage_core.mask_value(sender),
        "status": "pending",
        "subject": subject,
        "surface": None,
        "to": to,
        "uid": uid,
        "uid_opaque": triage_core.mask_value(uid),
        "policy_version": None,
    }
    if bcc:
        record["bcc"] = list(bcc)
    if attachments:
        record["attachments"] = attachments
        record["attachment_manifest_sha256"] = (
            triage_core.attachment_manifest_sha256(attachments)
        )
    if quote:  # absent on legacy/no-quote drafts so their hashes stay byte-identical
        record["quote"] = quote
    if forward:  # mail_forward.ForwardSource.record(); absent on every non-forward draft
        record["forward"] = forward
    if drive_share:  # mail_drive_share.prepare(); absent on every draft without a Drive link
        record["drive_share"] = drive_share
    record["sha256"] = triage_core.draft_sha256(record)
    write_json(directory / f"{draft_id}.json", record)
    return record


def create_gmail_draft(snapshot: gmail_approval_gate.GmailApprovalSnapshot) -> dict:
    """Persist a canonical Gmail action for the existing mail approval lifecycle."""
    import gmail_approval_gate

    directory = _public_drafts_dir()
    draft_id = secrets.token_hex(3)
    while (directory / f"{draft_id}.json").exists():
        draft_id = secrets.token_hex(3)
    record = gmail_approval_gate.approval_draft(
        snapshot, draft_id=draft_id, created_at=triage_core.utc_now()
    )
    write_json(directory / f"{draft_id}.json", record)
    return record


def load_draft(draft_id: str) -> dict:
    path = _draft_path(draft_id)
    if path is None:
        raise GateError(f"드래프트 없음: {draft_id}", 3)
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("status") != "pending":
        raise GateError(f"드래프트 {draft_id} 상태={record.get('status')} — pending 아님", 1)
    return record


def set_approval_binding(
    draft: dict, *, kind: str, surface: str, channel_id: str, policy_version: int,
    approval_thread_id: str = "", approval_guild_id: str | None = None,
) -> dict:
    """Persist the whole binding; ``approval_thread_id`` is the request's own thread.

    그 스레드 id 는 승인 액션 해시 밖에 산다(``origin_channel_id`` 와 같은 취급) —
    결과 통지가 돌아갈 곳을 기억할 뿐 발송 내용에 개입하지 않는다. 값이 없으면
    필드를 쓰지 않아 기존 레코드는 바이트 그대로 남는다.
    """
    path = _draft_path(draft["id"])
    if path is None:
        raise GateError(f"드래프트 없음: {draft['id']}", 3)
    current = json.loads(path.read_text(encoding="utf-8"))
    current_channel = current.get("channel_id")
    if isinstance(current_channel, str) and current_channel and current_channel != channel_id:
        raise GateError("기존 승인 메시지의 채널 바인딩 변경 거부", 3)
    updated = {
        **current,
        "kind": kind,
        "surface": surface,
        "channel_id": channel_id,
        "policy_version": policy_version,
    }
    if approval_thread_id:
        updated["approval_thread_id"] = approval_thread_id
    if approval_guild_id is not None:
        updated["approval_guild_id"] = approval_guild_id
    write_json(path, updated)
    return updated


def set_message_id(
    draft: dict,
    message_id: str,
    channel_id: str = "",
    approval_created_at: str = "",
) -> dict:
    path = _draft_path(draft["id"])
    if path is None:
        raise GateError(f"드래프트 없음: {draft['id']}", 3)
    current = json.loads(path.read_text(encoding="utf-8"))
    bound_message_id = current.get("message_id")
    if isinstance(bound_message_id, str) and bound_message_id and bound_message_id != message_id:
        raise GateError("기존 승인 메시지 id 교체 거부", 3)
    current_channel = current.get("channel_id")
    if isinstance(current_channel, str) and current_channel and current_channel != channel_id:
        raise GateError("기존 승인 메시지의 채널 바인딩 변경 거부", 3)
    updated = {**current, "message_id": message_id, "channel_id": channel_id}
    if approval_created_at:
        updated["approval_created_at"] = approval_created_at
    updated.update({key: draft[key] for key in ("render_version", "approval_format", "approval_attachment",
                                              "card_sha256")
                    if key in draft})
    write_json(path, updated)
    return updated


def discard_draft(draft_id: str) -> None:
    path = _draft_path(draft_id)
    if path is None:
        raise GateError(f"드래프트 없음: {draft_id}", 3)
    path.unlink()


def expire_draft(draft_id: str, message_id: str, reason: str) -> None:
    """Make one still-bound pending draft terminal without recording owner approval."""
    path = _draft_path(draft_id)
    if path is None:
        raise GateError(f"드래프트 없음: {draft_id}", 3)
    current = json.loads(path.read_text(encoding="utf-8"))
    if current.get("status") != "pending" or current.get("message_id", "") != message_id:
        raise GateError("드래프트 만료 중 승인 바인딩 변경 감지 — 거부", 3)
    write_json(path, {**current, "status": "expired", "expired_reason": reason})


def list_drafts() -> list[dict]:
    records = []
    for directory in (_public_drafts_dir(), _legacy_drafts_dir()):
        records += [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(directory.glob("*.json"))
        ]
    return records


def has_draft_for(uid: str) -> bool:
    return any(record.get("uid") == uid for record in list_drafts())


# ------------------------------------------------------------------ send

def mailon_python() -> str:
    override = os.environ.get("TRIAGE_MAILON_PYTHON", "")
    if override:
        return override
    return str(mail_wrapper._cfg()["python"])


def _run_send(argv: tuple[str, ...]) -> tuple[int, str, str]:
    cfg = mail_wrapper._cfg()
    try:
        proc = subprocess.run(
            list(argv), cwd=cfg["repo"] if cfg["repo"].is_dir() else None,
            env=mail_wrapper.build_subprocess_env(cfg),
            capture_output=True, text=True, timeout=SEND_TIMEOUT_S, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        return 127, "", f"{type(error).__name__}"
    return proc.returncode, proc.stdout, proc.stderr


def execute_draft(draft: dict, approval: Approval) -> None:
    """Re-verify the frozen draft hash, then route to the account's send backend."""
    if triage_core.draft_sha256(draft) != draft["sha256"]:
        raise GateError("드래프트 내용 해시 불일치 — 실행 중단", 1)
    # ``send_argv`` sits outside the hash, so only the share step below may supply it.
    draft = {key: value for key, value in draft.items() if key != "send_argv"}
    if "drive_share" in draft:
        if draft.get("provider") == "gmail":
            raise GateError("Drive 공유 메일은 기관메일 발송만 지원합니다", 2)
        import mail_drive_share

        draft = mail_drive_share.run(draft, approval)
    if draft.get("provider") == "gmail":
        import triage_gate_gmail

        triage_gate_gmail.execute_gmail_draft(draft, approval)
        return
    import triage_gate_mailon

    triage_gate_mailon.execute_mailon_draft(draft, approval)
