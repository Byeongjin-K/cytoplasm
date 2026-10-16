"""Mail-wrapper and calendar subprocess transport for the triage CLI."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import triage_core
import triage_gate

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent

CALENDAR_TIMEOUT_SECONDS: Final = 120
_STDERR_CLIP = 200  # 자식 stderr 는 운영 로그용 단서일 뿐 — 메일 본문을 옮겨 담지 않는다
# calendar_cli.main() 의 종료코드 계약 (AmbiguousTime=5, ParseRejected=2,
# ROUTING_REJECT_EXIT_CODE=4, GateError.exit_code: 1=미확인 3=설정 6=실행).
# 원인이 다르면 다른 문자열을 돌려줘야 소유자가 승인 거부와 크래시를 구분할 수 있다.
_CALENDAR_CAUSE: Final[dict[int, str]] = {
    1: "calendar-refused",
    2: "calendar-unparsed",
    3: "calendar-misconfigured",
    4: "calendar-routing",
    6: "calendar-exec-failed",
}


def _env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default)).expanduser()


def _wrapper_json(argv: list[str], *, timeout: int = 1200) -> tuple[int, dict]:
    proc = subprocess.run(  # noqa: S603 — fixed wrapper path, read-only surface
        [sys.executable, str(SCRIPT_DIR / "mail_wrapper.py"), *argv],
        capture_output=True, text=True, timeout=timeout, check=False,
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = {}
    return proc.returncode, payload


def _list_mails(limit: int, sync: bool) -> list[dict]:
    argv = ["list", "--limit", str(limit)] + (["--sync"] if sync else [])
    rc, payload = _wrapper_json(argv)
    if rc == 5:
        return []
    if rc != 0:
        guidance = str(payload.get("guidance") or "")[:200]
        raise triage_gate.GateError(f"wrapper list 실패 rc={rc}: {guidance}", 4)
    return list(payload.get("mails") or [])


def _sent_mails(limit: int) -> list[dict]:
    """Recent owner sent mails from state.db (no sync; the inbox sync already ran)."""
    rc, payload = _wrapper_json(["list", "--all-folders", "--limit", str(limit)])
    if rc != 0:
        raise triage_gate.GateError(f"wrapper list(all-folders) 실패 rc={rc}", 4)
    return [mail for mail in payload.get("mails") or [] if mail.get("folder") == "sent"]


def _get_mail(uid: str) -> dict:
    rc, payload = _wrapper_json(["get", uid, "--body"])
    if rc != 0:
        raise triage_gate.GateError(f"wrapper get 실패 rc={rc} uid(불투명)={triage_core.mask_value(uid)}", 4)
    return dict(payload.get("mail") or {})


def _clip(detail: str) -> str:
    """One-line, length-capped projection of a child's stderr for the operator log."""
    collapsed = " ".join(detail.split())
    return collapsed[:_STDERR_CLIP] if collapsed else "(없음)"


@dataclass(frozen=True, slots=True)
class ScheduleNote:
    """The calendar delegation result: the short ``note`` plus, when known, its approval card.

    ``note`` keeps its old meaning (``calendar:<id>`` or a failure cause) because it is
    stored and joined elsewhere; the card fields only feed the digest line.
    """

    note: str
    card_url: str = ""
    start: str = ""
    end: str = ""
    title: str = ""


_CARD_LINE = re.compile(
    r"^DIGEST-CARD draft=\S+ url=(?P<url>\S+) start=(?P<start>\S+) end=(?P<end>\S+) summary=(?P<title>.*)$",
    re.M,
)


def _with_card(note: str, stdout: str) -> ScheduleNote:
    """Attach the calendar's ``DIGEST-CARD`` facts; an older calendar prints none."""
    match = _CARD_LINE.search(stdout)
    if not match:
        return ScheduleNote(note)
    url = match["url"] if match["url"].startswith("https://") else ""
    start, end = ("" if value == "-" else value for value in (match["start"], match["end"]))
    return ScheduleNote(note, url, start, end, match["title"].strip())


def _delegate_schedule(schedule_text: str, uid_opaque: str, digest_day: str = "") -> ScheduleNote:
    proc_or_note = _run_calendar(schedule_text, uid_opaque, digest_day)
    if isinstance(proc_or_note, str):
        return ScheduleNote(proc_or_note)
    proc = proc_or_note
    if proc.returncode == 0:
        match = re.search(r"^DRAFT-CREATED id=(\w+)", proc.stdout, re.M)
        return _with_card(f"calendar:{match.group(1)}" if match else "calendar:unknown", proc.stdout)
    if proc.returncode == 5:
        print(f"CAL-AMBIGUOUS uid={uid_opaque} (되묻기 필요 — 초안 없음)")
        return ScheduleNote("calendar-ambiguous")
    cause = _CALENDAR_CAUSE.get(proc.returncode, f"calendar-failed-rc{proc.returncode}")
    print(
        f"CAL-FAIL uid={uid_opaque} rc={proc.returncode} cause={cause} stderr={_clip(proc.stderr)}",
        file=sys.stderr,
    )
    return ScheduleNote(cause)


def _run_calendar(
    schedule_text: str, uid_opaque: str, digest_day: str,
) -> subprocess.CompletedProcess[str] | str:
    calendar_cli = _env_path(
        "TRIAGE_CALENDAR_CLI", "/srv/autophagy-skills/live/calendar/scripts/calendar_cli.py"
    )
    if not calendar_cli.exists():
        return "calendar-unavailable"
    try:
        proc = subprocess.run(  # noqa: S603 — calendar owns draft + digest approval publication
            [sys.executable, str(calendar_cli), "draft-create", "--text", schedule_text,
             *(["--digest-day", digest_day] if digest_day else [])],
            capture_output=True, text=True, timeout=CALENDAR_TIMEOUT_SECONDS, check=False,
        )
    except subprocess.TimeoutExpired:  # 느린 캘린더 CLI 가 다이제스트 전체를 죽이지 못한다
        print(f"CAL-TIMEOUT uid={uid_opaque} timeout={CALENDAR_TIMEOUT_SECONDS}s", file=sys.stderr)
        return "calendar-timeout"
    except OSError as error:  # 실행 불가한 인터프리터·경로 — 역시 항목 하나만 잃는다
        print(f"CAL-SPAWN-FAIL uid={uid_opaque} error={_clip(str(error))}", file=sys.stderr)
        return "calendar-spawn-failed"
    return proc
