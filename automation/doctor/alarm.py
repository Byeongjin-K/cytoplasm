"""doctor 알람 — 새 문제가 있을 때만 소유자에게 알린다(해결만 있으면 상태만 갱신).

매 점검마다 같은 문제를 다시 보내면 소유자는 곧 읽지 않게 된다(cry-wolf). 그래서 비통과 항목의
서명(상태·키·대상)을 상태 파일에 두고 차이만 보낸다. 전송이 실패하면 상태를 전진시키지 않아
다음 점검이 같은 변화를 다시 보낸다(at-least-once). 상태는 체크아웃 밖 `~/.hermes/doctor/` 에만 쓴다.
"""
from __future__ import annotations

import fcntl
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final

from automation.doctor.capabilities import Finding
from automation.doctor.report import WORD
from automation.install.checks import Status

if TYPE_CHECKING:
    from automation.interop.owner_message import OwnerMessage

Send = Callable[[str, "OwnerMessage | None"], bool]
STATE_FILE: Final = "state.json"
DOCTOR_COMMAND: Final = "cd /srv/autophagy-agent-current && python3 -m automation.doctor"


@dataclass(frozen=True, slots=True)
class Change:
    new: tuple[Finding, ...]
    resolved: tuple[str, ...]


def state_path(home: Path) -> Path:
    return home / ".hermes" / "doctor" / STATE_FILE


def _signature(finding: Finding) -> str:
    return f"{finding.status}:{finding.key}:{','.join(sorted(finding.subjects))}"


def signatures(findings: Sequence[Finding]) -> dict[str, str]:
    return {_signature(f): f"{WORD[f.status]} · {f.label}" for f in findings if f.status is not Status.PASS}


def diff(findings: Sequence[Finding], previous: Mapping[str, str] | None) -> Change | None:
    current = signatures(findings)
    before = previous or {}
    new = tuple(f for f in findings if f.status is not Status.PASS and _signature(f) not in before)
    resolved = tuple(label for sig, label in sorted(before.items()) if sig not in current)
    if not new and not resolved:
        return None
    return Change(new, resolved)


def content(account: str, change: Change) -> str:
    lines = [f"[doctor] {account} — 연결·동작 점검 결과가 바뀌었다"]
    if change.new:
        lines.append(f"새로 확인된 문제 {len(change.new)}건:")
        lines += [f"- {WORD[f.status]} · {f.label}: {f.detail}" for f in change.new]
    if change.resolved:
        lines.append(f"해결됨 {len(change.resolved)}건:")
        lines += [f"- {label}" for label in change.resolved]
    if change.new:
        lines.append(f"조치 절차 보기: {DOCTOR_COMMAND}")
    return "\n".join(lines)


def envelope(account: str, body: str, start: datetime, end: datetime) -> OwnerMessage | None:
    try:
        from automation.interop.owner_message import Action, OwnerMessage, Periodic, Ref
    except ImportError:
        return None
    findings = body.partition("\n")[2]
    return OwnerMessage(
        subject_key=account, subject="[doctor 연결·동작 점검]",
        fact=f"doctor 점검에서 {account} 연결·동작 결과가 바뀌었다\n{findings}",
        location=Ref(scope="none"),
        owner=Action(verb="open", target=Ref(scope="self"), argument="— 본문의 조치 절차 명령으로 확인한다"),
        agent_next="매시 다시 점검해 바뀔 때만 알린다", recovery="not_applicable",
        detail=Periodic(start=start, end=end), render_version="owner-ko-v2",
    )


def _send(body: str, message: OwnerMessage | None) -> bool:
    from automation.owner_notice import notify_owner

    if message is not None:
        return notify_owner(body, message=message, fault=True)
    return notify_owner(body, fault=True)


def _load(path: Path) -> tuple[dict[str, str] | None, datetime | None]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        problems = {str(k): str(v) for k, v in raw["problems"].items()}
        return problems, datetime.fromisoformat(str(raw["checked_at"]))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None, None


def _save(path: Path, problems: Mapping[str, str], now: datetime) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    _ = tmp.write_text(json.dumps({"checked_at": now.isoformat(), "problems": dict(problems)},
                                  ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.chmod(0o600)
    _ = tmp.replace(path)


def run_alarm(
    account: str, findings: Sequence[Finding], *, home: Path, now: datetime, send: Send = _send,
) -> str:
    path = state_path(home)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (path.parent / "alarm.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "DOCTOR-ALARM-BUSY: 다른 점검이 알람 상태를 쥐고 있다 — 그쪽이 보낸다"
        return _decide(account, findings, path=path, now=now, send=send)


def _decide(account: str, findings: Sequence[Finding], *, path: Path, now: datetime, send: Send) -> str:
    previous, checked_at = _load(path)
    change = diff(findings, previous)
    if change is None:
        _save(path, signatures(findings), now)
        return "DOCTOR-ALARM-UNCHANGED"
    if not change.new:
        _save(path, signatures(findings), now)
        return f"DOCTOR-ALARM-RECOVERED resolved={len(change.resolved)}"
    body = content(account, change)
    if not send(body, envelope(account, body, checked_at or now, now)):
        return "DOCTOR-ALARM-NOTIFY-FAILED: 상태를 전진시키지 않았다 — 다음 점검이 다시 보낸다"
    _save(path, signatures(findings), now)
    return f"DOCTOR-ALARM-SENT new={len(change.new)} resolved={len(change.resolved)}"
