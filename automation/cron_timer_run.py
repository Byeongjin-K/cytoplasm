"""systemd 사용자 타이머가 부르는 러너 — Hermes no-agent 실행과 같은 모양으로 스크립트 하나를 돌린다.

Hermes 가 하던 것(노드 `cron/scheduler.py::_run_job_script`)을 그대로 따른다: `~/.hermes/scripts/<스크립트>`
를 cwd=그 디렉터리에서 파이썬으로, 3600초 제한. 환경은 Hermes 가 `~/.hermes/.env` 를 읽은 뒤 비밀을
걷어 넘기던 것 대신, 선언 `env=` 가 이름을 댄 값만 그 파일에서 가져온다(나머지는 스크립트가 스스로
`~/.env.secrets` 를 읽는다 — 규약 (b)). PYTHONPATH 는 넘기지 않는다(Hermes 래퍼도 지운다).

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
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from automation.cron_timer import SCRIPT_TIMEOUT, STATE_DIR, TimerError, Job


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
    try:
        rc = subprocess.run([sys.executable, str(scripts / job.script)], cwd=scripts, env=env,
                            timeout=SCRIPT_TIMEOUT, check=False).returncode
    except subprocess.TimeoutExpired:
        rc = 124
    except OSError:
        rc = 127
    _record(home, job.name, "ok" if rc == 0 else "error", "" if rc == 0 else f"exit {rc}")
    return rc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m automation.cron_timer_run")
    parser.add_argument("--name", required=True)
    parser.add_argument("--script", required=True)
    parser.add_argument("--env", default="")
    args = parser.parse_args(argv)
    try:
        return run(Path.home(), Job(args.name, "* * * * *", args.script,
                                    tuple(e for e in args.env.split(",") if e)))
    except TimerError as error:
        print(f"CRON-TIMER-BLOCK: {error}", file=sys.stderr)
        return error.rc


if __name__ == "__main__":
    raise SystemExit(main())
