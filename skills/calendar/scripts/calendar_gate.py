"""Draft store + approval records + gws execution for W3-1 (제약 1).

NO calendar mutation ever happens before an owner confirmation (see
calendar_confirm for the two confirmation transports). On confirm the gate
appends the external-effect approval record (the schema the deployed
pre_tool_call gate reads) plus a W0-6 audit record to approvals.jsonl, and only
then executes the exact gws argv frozen into the draft at draft time.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import calendar_core

if TYPE_CHECKING:
    from automation.entity_preflight.contracts import JsonValue
    from calendar_confirm_input import DraftRecord

GWS_TIMEOUT_S = 120


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


def _gate_dir() -> Path:
    path = Path(os.environ.get("CALENDAR_GATE_DIR", "~/.hermes/calendar-gate")).expanduser()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def _approval_log() -> Path:
    return Path(
        os.environ.get("CALENDAR_APPROVAL_LOG", "/srv/autophagy-agents/logs/approvals.jsonl")
    ).expanduser()


def gws_bin() -> str:
    override = os.environ.get("CALENDAR_GWS_BIN", "")
    if override:
        return override
    found = shutil.which("gws") or os.path.expanduser("~/.local/bin/gws")
    if not Path(found).exists():
        raise GateError("gws CLI를 찾을 수 없습니다 (CALENDAR_GWS_BIN 설정 필요)", 3)
    return found


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _drafts_dir() -> Path:
    path = _gate_dir() / "drafts"
    path.mkdir(mode=0o700, exist_ok=True)
    return path


def _draft_path(draft_id: str) -> Path:
    if not draft_id.isalnum():
        raise GateError(f"잘못된 드래프트 id: {draft_id!r}", 3)
    return _drafts_dir() / f"{draft_id}.json"


def build_draft(
    *, action: str, argv: tuple[str, ...], calendar_id: str, event_id: str,
    summary: str, start: str, end: str, channel_id: str,
    origin_channel_id: str = "", origin_message_id: str = "",
) -> dict:
    """Freeze one mutation as a draft; the origin refs only route its later result.

    ``origin_*`` is where the owner's instruction arrived (empty when it came
    straight from cha's own thread of control). It is deliberately NOT part of
    ``draft_sha256`` — the hash binds the mutation, so a legacy draft and an
    origin-bound draft of the same change keep the exact same content hash.
    """
    draft_id = secrets.token_hex(3)
    while _draft_path(draft_id).exists():
        draft_id = secrets.token_hex(3)
    # 이 레코드는 그대로 JSON 으로 얼어붙는다 — 값 타입도 그 사실대로 적어야
    # ``draft_sha256`` 의 JSON 계약과 맞는다(list 는 불변이라 list[str] 은 그 자체로
    # JsonValue 가 아니다).
    record: dict[str, JsonValue] = {
        "action": action,
        "argv": list(argv),
        "calendar_id": calendar_id,
        "channel_id": channel_id,
        "created": utc_now(),
        "end": end,
        "event_id": event_id,
        "id": draft_id,
        "origin_channel_id": origin_channel_id,
        "origin_message_id": origin_message_id,
        "start": start,
        "status": "pending",
        "summary": summary,
    }
    record["sha256"] = calendar_core.draft_sha256(record)
    return record


def persist_draft(record: dict[str, JsonValue] | DraftRecord) -> None:
    write_json(_draft_path(str(record["id"])), record)


def create_draft(
    *, action: str, argv: tuple[str, ...], calendar_id: str, event_id: str,
    summary: str, start: str, end: str, channel_id: str,
    origin_channel_id: str = "", origin_message_id: str = "",
) -> dict[str, JsonValue]:
    record = build_draft(
        action=action, argv=argv, calendar_id=calendar_id, event_id=event_id,
        summary=summary, start=start, end=end, channel_id=channel_id,
        origin_channel_id=origin_channel_id, origin_message_id=origin_message_id,
    )
    persist_draft(record)
    return record


def bind_approval_thread(draft: DraftRecord, thread_id: str, guild_id: str | None = None) -> DraftRecord:
    """Stamp this request's approval thread on the draft and return the new record.

    ``origin_*`` 와 같은 이유로 ``draft_sha256`` 밖이다 — 해시는 실행할 변경만 묶는다.
    워처가 결과를 승인 스레드로 되돌리려면 초안에서 이 값을 읽어야 한다.
    """
    record: DraftRecord = {**draft, "approval_thread_id": thread_id}
    if guild_id is not None:
        record["approval_guild_id"] = guild_id
    write_json(_draft_path(str(draft["id"])), record)
    return record


def load_draft(draft_id: str) -> DraftRecord:
    path = _draft_path(draft_id)
    if not path.exists():
        raise GateError(f"드래프트 없음: {draft_id}", 3)
    record: DraftRecord = json.loads(path.read_text(encoding="utf-8"))
    if record.get("status") != "pending":
        raise GateError(f"드래프트 {draft_id} 상태={record.get('status')} — pending 아님", 1)
    return record


def discard_draft(draft_id: str) -> None:
    path = _draft_path(draft_id)
    if not path.exists():
        raise GateError(f"드래프트 없음: {draft_id}", 3)
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("digest_day"):
        write_json(path, {**record, "status": "cancelled"})
    else:
        path.unlink()


def list_drafts() -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(_drafts_dir().glob("*.json"))
    ]


def execute_draft(draft: dict, approval: Approval) -> str:
    """Append approval+audit records, then run the exact frozen gws argv."""
    if calendar_core.draft_sha256(draft) != draft["sha256"]:
        raise GateError("드래프트 내용 해시 불일치 — 실행 중단", 1)
    argv = tuple(draft["argv"])
    _append_record(_approval_record(argv, approval))
    binary = gws_bin()
    result = subprocess.run(  # noqa: S603
        [binary, *argv[1:]], capture_output=True, text=True, timeout=GWS_TIMEOUT_S,
        check=False, cwd=str(_gate_dir()),  # gws는 빈 응답을 cwd에 파일로 쓰려 함 — 쓰기 가능한 곳 고정
    )
    if result.returncode != 0:
        _append_record(_audit_record(draft, approval, event_id="", status="failed"))
        tail = (result.stderr or result.stdout).strip().splitlines()[-3:]
        raise GateError(f"gws 실행 실패 (rc={result.returncode}): {' / '.join(tail)}", 6)
    event_id = draft["event_id"]
    if draft["action"] in {"create", "update"} and result.stdout.strip():
        payload = json.loads(result.stdout)
        event_id = str(payload.get("id", event_id))
    _append_record(_audit_record(draft, approval, event_id=event_id, status="executed"))
    write_json(
        _draft_path(draft["id"]),
        {**draft, "status": "executed", "approval_ref": approval.ref, "method": approval.method},
    )
    return event_id


def _approval_record(argv: tuple[str, ...], approval: Approval) -> dict:
    return {
        "action": "external_effect.approval",
        "approval": {
            "channel": "approvals",
            "message_id": approval.ref,
            "method": approval.method,
            "owner_id": approval.owner,
        },
        "hash": calendar_core.external_effect_action_hash(argv),
        "result": {"status": "approved"},
        "target_id": calendar_core.EXTERNAL_EFFECT_TARGET_ID,
        "timestamp": utc_now(),
    }


def _audit_record(draft: dict, approval: Approval, *, event_id: str, status: str) -> dict:
    action = f"calendar.{draft['action']}"
    target_id = f"event:{draft['calendar_id']}:{event_id or 'new'}"
    argv_sha = hashlib.sha256(
        json.dumps(draft["argv"], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    approval_field = {"channel": "dm", "method": approval.method, "ref": approval.ref}
    if approval.method == "owner_command":
        approval_field.update(
            channel=str(draft["origin_channel_id"]),
            origin_channel_id=str(draft["origin_channel_id"]),
            origin_message_id=str(draft["origin_message_id"]),
        )
    canonical = json.dumps(
        {
            "action": action,
            "approval": approval_field,
            "payload": {"argv_sha256": argv_sha},
            "target_id": target_id,
        },
        ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    )
    return {
        "action": action,
        "approval": approval_field,
        "hash": f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}",
        "result": {"status": status},
        "target_id": target_id,
        "timestamp": utc_now(),
    }


def _append_record(record: dict) -> None:
    path = _approval_log()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n")
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    path.chmod(0o600)


def write_json(path: Path, record: dict[str, JsonValue] | DraftRecord) -> None:
    """임시 파일에 쓴 뒤 이름을 갈아끼운다 — 독자가 잘린 레코드를 보지 않게.

    제자리 truncate(`write_text`)는 쓰는 동안 읽는 쪽에게 **빈 파일**을 보여준다.
    승인 producer 와 confirm 워처는 같은 레코드를 동시에 만진다(2026-08-01 실측:
    mail 경로에서 producer 가 `JSONDecodeError: ... (char 0)` 으로 사망). 같은 구현이
    여기에도 복제돼 있어 함께 고친다.

    임시 이름은 `.`로 시작하고 `.json` 으로 끝나지 않는다 — 대기 레코드를 훑는
    `*.json` glob 이 쓰다 말은 파일을 레코드로 읽으면 안 되기 때문이다.
    """
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    serialized = json.dumps(record, ensure_ascii=False, sort_keys=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}.", mode="w", encoding="utf-8", delete=False
        ) as handle:
            temporary = Path(handle.name)
            _ = handle.write(serialized)
            # flush 까지만 한다 — 찢어진 읽기를 막는 것은 `os.replace` 이고, fsync 는
            # 호출당 0.12ms 를 3.5ms 로 만든다(실측). 내구성은 PostingJournal 이 맡는다.
            handle.flush()
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
