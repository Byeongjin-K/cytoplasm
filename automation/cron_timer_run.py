"""systemd 사용자 타이머가 부르는 러너 — Hermes no-agent 실행과 같은 모양으로 스크립트 하나를 돌린다.

Hermes 가 하던 것(노드 `cron/scheduler.py::_run_job_script`)을 그대로 따른다: `~/.hermes/scripts/<스크립트>`
를 cwd=그 디렉터리에서 파이썬으로, 3600초 제한. 환경은 Hermes 가 `~/.hermes/.env` 를 읽은 뒤 비밀을
걷어 넘기던 것 대신, 선언 `env=` 가 이름을 댄 값만 그 파일에서 가져온다(나머지는 스크립트가 스스로
`~/.env.secrets` 를 읽는다 — 규약 (b)). PYTHONPATH 는 넘기지 않는다(Hermes 래퍼도 지운다).

`--deliver discord` 면 Hermes 의 `deliver=discord` 와 같이 비어 있지 않은 stdout 을 Hermes 홈 채널
(`~/.hermes/.env` 의 `DISCORD_HOME_CHANNEL_THREAD_ID` → `DISCORD_HOME_CHANNEL`)로 보내고, 실패하면 실패 한 줄을
보낸다. 실패는 `notice_bot.fault_channel`이 있으면 페어 채널로 보낸다.
성공한 실행의 stdout 은 형 채널로 가기 전에 `interop.owner_text` 규칙으로 가른다(형 지시 2026-10-20
00:10 KST): JSON 결과 줄은 아래 출력 폴더(내부 로그)에만 남고, 진단·경고 줄(표식 코드·종료코드·경로)은
페어 채널로 가며, 형에게는 사람 말 줄만 간다. 페어 채널이 없는 설치는 JSON 줄만 빼고 지금처럼 홈 채널로
보낸다(목적지 없는 진단을 없애지 않는다).
실패·경고 알림은 같은 고장을 되풀이하지 않는다(형 2026-10-21 14:06 KST "이런 코드 경고 개선한거 아니었어?"):
같은 작업의 같은 종류 실패(종료코드+안정된 원인 값)는 처음 한 번, 계속되면 `REALERT` 마다 한 번, 회복하면
"다시 정상" 한 줄. 글은 사람 말 두 줄이고(종료코드·`key=value`·표식 코드는 싣지 않는다), 원문은 출력
폴더와 journal 에 남는다. 알림 상태는 `~/.hermes/cron-timers/alerts/<이름>.json`(0600), 글이 실제로 나간
뒤에만 "보냄"으로 적는다 — 보내기에 실패하면 다음 실행이 다시 보낸다.
침묵 표식(`[SILENT]` 등)은 보내지 않는다. 비어 있지 않은 stdout 은 `~/.hermes/cron-timers/output/<이름>/` 에
최근 20개까지 남는다(Hermes 의 잡 출력 폴더 자리 — N1 이 다이제스트 실패 표식을 거기서 읽는다).

두 번 실행 방지: 같은 이름의 Hermes 잡이 active 면 돌지 않고 `CRON-TIMER-SKIP` 만 남긴다(0). 잡별
잠금이 잡혀 있어도 건너뛴다. 결과는 `~/.hermes/cron-timers/<이름>.json`(0600)에 남아 doctor 가
Hermes 잡처럼 연속 실패를 판정한다 — 오류 문구는 싣지 않고 종료코드만 적는다.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from automation.cron_timer import SCRIPT_TIMEOUT, STATE_DIR, Job, TimerError

SILENT: Final = frozenset({"[SILENT]", "SILENT", "NO_REPLY", "NO REPLY"})
KEEP_OUTPUTS: Final = 20
#: 같은 고장이 이어질 때 다시 알리는 간격.
REALERT: Final = timedelta(hours=6)
Send = Callable[[str, str, str], None]
#: 같은 종류인지 가르는 안정된 원인 값(바뀌는 숫자·경로·시각은 넣지 않는다).
_KIND_KEYS: Final = ("error_code", "failure_signature", "code", "stage")
_KEY_VALUE: Final = re.compile(r"(?<![\w/.-])([a-z][a-z0-9_]+)=([^\s,)]+)")
_REASON_BY_CODE: Final = {"timeout": "시간 안에 끝나지 않았어요"}
_REASON_BY_RC: Final = {124: "제한 시간을 넘겨 멈췄어요", 127: "작업 스크립트를 실행하지 못했어요"}
_HANGUL: Final = re.compile(r"[가-힣]")


def hermes_job_active(home: Path, name: str) -> bool | None:
    """True=active 인 같은 이름 잡이 있다, False=없거나 모두 멈춤, None=jobs.json 을 읽지 못했다."""
    path = home / ".hermes" / "cron" / "jobs.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return None
    jobs = raw.get("jobs") if isinstance(raw, dict) else raw
    if not isinstance(jobs, list):
        return None
    return any(isinstance(j, dict) and j.get("name") == name and j.get("enabled") is not False
               and not j.get("paused_at") for j in jobs)


def _env_file(path: Path, names: Sequence[str]) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    found: dict[str, str] = {}
    for line in lines:
        key, sep, value = line.strip().removeprefix("export ").partition("=")
        if sep and key.strip() in names:
            found[key.strip()] = value.strip().strip("'\"")
    return found


def _record(home: Path, name: str, status: str, error: str) -> None:
    folder = home / STATE_DIR
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = folder / f"{name}.json"
    try:
        streak = int(json.loads(path.read_text(encoding="utf-8")).get("failure_streak", 0))
    except (OSError, ValueError, AttributeError):
        streak = 0
    if status == "error":
        streak += 1
    elif status == "ok":
        streak = 0
    body = {"name": name, "last_run_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "last_status": status, "failure_streak": streak, "last_error": error}
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(body, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def _discord_send(token: str, target: str, body: str) -> None:
    from automation.interop.discord_transport import DiscordTransport

    _ = DiscordTransport(token, target).send(body)


def _keep_output(home: Path, name: str, text: str) -> None:
    folder = home / STATE_DIR / "output" / name
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = folder / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.txt"
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)
    for old in sorted(folder.glob("*.txt"))[:-KEEP_OUTPUTS]:
        old.unlink()


@dataclass(frozen=True, slots=True)
class Trouble:
    """이번 실행의 고장 한 건 — `kind` 가 같으면 같은 고장으로 본다."""

    kind: str
    reason: str


def _human_reason(lines: Sequence[str], job: Job) -> str:
    """원문 줄에서 기계 표식을 걷어낸 사람 말 한 토막. 없으면 ""."""
    from automation.interop.owner_text import RULES, findings

    for line in lines:
        text = line.strip().removeprefix(f"{job.name} error:").removeprefix(f"{job.name}:")
        for _, pattern in RULES:
            text = pattern.sub(" ", text)
        text = re.sub(r"\s+", " ", text).strip(" :;,.—-()[]")
        if _HANGUL.search(text) and not findings(text):
            return text[:120]
    return ""


def trouble(job: Job, rc: int, lines: Sequence[str]) -> Trouble:
    """실패(rc≠0) 또는 경고 줄을 같은 종류끼리 묶을 열쇠와 사람 말 원인으로 바꾼다."""
    from automation.interop.owner_text import RULES

    status = dict(RULES)["status-code"]
    values = {key: value for line in lines for key, value in _KEY_VALUE.findall(line) if key in _KIND_KEYS}
    markers = sorted({m.group(0) for line in lines for m in status.finditer(line)})
    reason = _human_reason(lines, job)
    stable = [f"{key}={values[key]}" for key in _KIND_KEYS if key in values] + markers
    if not stable:
        stable = [re.sub(r"\d+", "#", reason)] if reason else []
    kind = (f"fail:{rc}" if rc else "warn") + ("|" + "|".join(stable) if stable else "")
    if not reason:
        reason = _REASON_BY_CODE.get(values.get("error_code", ""), "") or _REASON_BY_RC.get(rc, "")
    if not reason:
        reason = "오류로 끝났어요" if rc else "자세한 내용은 작업 출력 기록에 남겼어요"
    return Trouble(kind, reason)


def _alert_path(home: Path, name: str) -> Path:
    return home / STATE_DIR / "alerts" / f"{name}.json"


def _alert_state(home: Path, name: str) -> dict[str, object]:
    try:
        raw = json.loads(_alert_path(home, name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_alert(home: Path, name: str, state: dict[str, object] | None) -> None:
    path = _alert_path(home, name)
    if state is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(state, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def _when(raw: object, fallback: datetime) -> datetime:
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError:
        return fallback


def _span(delta: timedelta) -> str:
    minutes = max(1, int(delta.total_seconds() // 60))
    hours, minutes = divmod(minutes, 60)
    return f"{hours}시간 {minutes}분" if hours and minutes else f"{hours}시간" if hours else f"{minutes}분"


def alert_text(job: Job, found: Trouble | None, state: dict[str, object], now: datetime) -> str:
    """이번 실행에서 올릴 알림 글. 올릴 것이 없으면 "" (같은 고장이 간격 안에 되풀이됐거나 계속 정상)."""
    name = f"정기 작업 '{job.name}'"
    count = int(str(state.get("count", 0) or 0)) + 1
    if found is None:
        if not state.get("sent_at"):
            return ""
        lasted = _span(now - _when(state.get("since"), now))
        what = "실패" if str(state.get("kind", "")).startswith("fail") else "경고"
        return f"✅ {name}가 다시 정상이에요({what} {count - 1}번, {lasted} 만에)."
    fail = found.kind.startswith("fail")
    if state.get("kind") != found.kind or not state.get("sent_at"):
        head = f"⚠️ {name}가 {'실패했어요' if fail else '경고를 남겼어요'}: {found.reason}."
        tail = ("다음 실행에서 다시 시도하고, 유돌이 살펴봐요(안 되면 짱돌에게 넘겨요). "
                f"같은 {'실패' if fail else '경고'}는 정상이 될 때까지 6시간마다 한 번만 알려요.")
        return f"{head}\n{tail}"
    if now - _when(state.get("sent_at"), now) < REALERT:
        return ""
    lasted = _span(now - _when(state.get("since"), now))
    return (f"⚠️ {name}가 아직 {'실패' if fail else '경고'} 중이에요({lasted}째, {count}번 연속): {found.reason}.\n"
            "유돌이 살펴보는 중이고, 정상이 되면 바로 알려요.")


def _next_state(found: Trouble | None, state: dict[str, object], now: datetime, posted: bool) -> dict[str, object] | None:
    if found is None:
        return None
    stamp = now.isoformat(timespec="seconds")
    same = state.get("kind") == found.kind
    episode = bool(state.get("kind"))
    return {"kind": found.kind, "reason": found.reason,
            "since": state.get("since") if episode else stamp,
            "count": int(str(state.get("count", 0) or 0)) + 1,
            "sent_at": stamp if posted else (state.get("sent_at") if same else None)}


def deliver(home: Path, job: Job, rc: int, text: str, send: Send | None = None,
            now: datetime | None = None) -> str:
    """Hermes `deliver=discord` 와 같은 자리로 보낸다. 보낸 것이 없으면 "", 보냈으면 "sent".

    실패·경고는 `alert_text` 가 정한 때만 올리고, 쉬는 실행에는 `CRON-TIMER-ALERT-HELD` 를 journal 에 남긴다.
    """
    from automation.interop.owner_text import split

    moment = now or datetime.now(UTC)
    body = text.strip()
    state = _alert_state(home, job.name)
    if rc == 0 and (not body or body in SILENT) and not state:
        return ""
    names = ("DISCORD_BOT_TOKEN", "DISCORD_HOME_CHANNEL", "DISCORD_HOME_CHANNEL_THREAD_ID")
    found = {**_env_file(home / ".env.secrets", names), **_env_file(home / ".hermes" / ".env", names)}
    from automation.notice_bot import fault_channel, notice_channel, resolve_token

    token = resolve_token(home=home, environ={**found, **{key: value for key, value in os.environ.items() if value}})
    target = found.get("DISCORD_HOME_CHANNEL_THREAD_ID") or found.get("DISCORD_HOME_CHANNEL", "")
    target = notice_channel(home) or target
    fault = fault_channel(home)
    if rc != 0:
        target = fault or target
    if not token or not target.isdigit():
        raise TimerError(4, "no Discord token or home channel for deliver=discord")
    post = send or _discord_send
    view = split("" if body in SILENT else body)
    if rc != 0:
        found: Trouble | None = trouble(job, rc, [*view.diagnostics, *view.owner.splitlines()])
        body = ""
    elif fault and view.diagnostics:
        found, body = trouble(job, 0, view.diagnostics), view.owner
    else:
        found, body = None, view.owner if fault else view.without_data
    alert = alert_text(job, found, state, moment)
    if alert:
        post(token, fault or target, alert)
    elif found is not None:
        print(f"CRON-TIMER-ALERT-HELD name={job.name} kind={found.kind}", flush=True)
    if found is not None or state:
        _save_alert(home, job.name, _next_state(found, state, moment, bool(alert)))
    if body:
        post(token, target, body)
    return "sent" if body or alert else ""


def run(home: Path, job: Job) -> int:
    job.check()
    active = hermes_job_active(home, job.name)
    if active is None:
        print(f"CRON-TIMER-BLOCK name={job.name} reason=hermes-jobs-unreadable", flush=True)
        _record(home, job.name, "error", "hermes-jobs-unreadable")
        return 1
    if active:
        print(f"CRON-TIMER-SKIP name={job.name} reason=hermes-job-active", flush=True)
        _record(home, job.name, "skipped", "")
        return 0
    folder = home / STATE_DIR
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = os.open(folder / f"{job.name}.lock", os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(lock)
        print(f"CRON-TIMER-SKIP name={job.name} reason=previous-run-still-going", flush=True)
        return 0
    try:
        return _execute(home, job)
    finally:
        os.close(lock)


def _execute(home: Path, job: Job) -> int:
    scripts = home / ".hermes" / "scripts"
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    for key, value in _env_file(home / ".hermes" / ".env", job.env).items():
        env.setdefault(key, value)
    capture = job.deliver == "discord"
    text = ""
    try:
        done = subprocess.run([sys.executable, str(scripts / job.script)], cwd=scripts, env=env,
                              timeout=SCRIPT_TIMEOUT, check=False, stdout=subprocess.PIPE if capture else None,
                              text=True, errors="replace")
        rc, text = done.returncode, done.stdout or ""
    except subprocess.TimeoutExpired:
        rc = 124
    except OSError:
        rc = 127
    if text:
        sys.stdout.write(text)
        sys.stdout.flush()
        _keep_output(home, job.name, text)
    error = "" if rc == 0 else f"exit {rc}"
    if capture:
        try:
            if deliver(home, job, rc, text) == "sent":
                print(f"CRON-TIMER-DELIVERED name={job.name}", flush=True)
        except (TimerError, OSError, RuntimeError, ValueError) as failure:
            print(f"CRON-TIMER-DELIVERY-FAIL name={job.name} reason={type(failure).__name__}", flush=True)
            rc, error = rc or 1, error or "delivery-failed"
    _record(home, job.name, "ok" if rc == 0 else "error", error)
    return rc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m automation.cron_timer_run")
    parser.add_argument("--name", required=True)
    parser.add_argument("--script", required=True)
    parser.add_argument("--env", default="")
    parser.add_argument("--deliver", default="local", choices=("local", "discord"))
    args = parser.parse_args(argv)
    try:
        return run(Path.home(), Job(args.name, "* * * * *", args.script,
                                    tuple(e for e in args.env.split(",") if e), args.deliver))
    except TimerError as error:
        print(f"CRON-TIMER-BLOCK: {error}", file=sys.stderr)
        return error.rc


if __name__ == "__main__":
    raise SystemExit(main())
