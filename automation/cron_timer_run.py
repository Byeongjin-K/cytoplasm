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
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from automation.cron_timer import SCRIPT_TIMEOUT, STATE_DIR, Job, TimerError

SILENT: Final = frozenset({"[SILENT]", "SILENT", "NO_REPLY", "NO REPLY"})
KEEP_OUTPUTS: Final = 20
Send = Callable[[str, str, str], None]


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


def deliver(home: Path, job: Job, rc: int, text: str, send: Send | None = None) -> str:
    """Hermes `deliver=discord` 와 같은 자리로 보낸다. 보낸 것이 없으면 "", 보냈으면 "sent"."""
    from automation.interop.owner_text import split

    body = text.strip()
    if rc == 0 and (not body or body in SILENT):
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
    if rc != 0:
        post(token, target, f"⚠️ 정기 작업 '{job.name}' 실패(종료코드 {rc})" + (f"\n{body}" if body else ""))
        return "sent"
    view = split(body)
    if not fault:
        body = view.without_data
    else:
        body = view.owner
        if view.diagnostics:
            post(token, fault, f"⚠️ 정기 작업 '{job.name}' 경고\n" + "\n".join(view.diagnostics))
    if body:
        post(token, target, body)
    return "sent" if body or (fault and view.diagnostics) else ""


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
