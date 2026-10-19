"""no-agent 잡을 Hermes cron 에서 계정의 systemd 사용자 타이머로 옮기는 수렴기(P2).

선언 `v2:cron;…;runner=systemd` 행을 그 계정 안에서 맞춘다. 배포기(`automation/deploy_timer.sh`)가
노드의 릴리스 트리로 `python3 -m automation.cron_timer converge …` 를 부른다.

불변식 — 같은 잡이 두 곳에서 동시에 돌지 않는다:
1. 타이머는 Hermes 잡이 **paused 인 것을 다시 읽어 확인한 뒤에만** 켠다. CLI 목록이 판독
   불가이고 릴리스 정지 표식이 정확할 때만 jobs.json 을 새로 읽는다. 둘 다 판독 불가면
   아무것도 바꾸지 않는다(rc 4). Hermes 잡은 지우지 않고 일시정지만 한다(되돌리기 = resume).
2. 러너(`cron_timer_run`)는 매 틱 jobs.json 을 보고 그 잡이 active 면 실행하지 않는다 — 누가
   Hermes 잡을 손으로 resume 해도 두 번 돌지 않는다.
3. 첫 실행은 다음 달력 시각이다(`enable --now` 는 타이머만 켠다). 일시정지 직전에 시작된 Hermes
   실행과 겹치지 않게.

되돌리기(`revert`) = 타이머를 끄고(재확인) → 이 수렴기가 멈춘 Hermes 잡만 resume(재확인).
종료코드: 2 인자 거부 · 4 목록·상태 판독 불가 · 5 변경 실패/사후 불일치 · 6 잠금 · 7 판정상 거부.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from automation.cron_schedule import ScheduleError, on_calendar
from automation.deploy_all_cron_listing import parse_cron_listing, read_cron_job_states
from automation.hermes_gateways import stopped_by_release

NAME: Final = re.compile(r"[a-z0-9][a-z0-9-]*")
SCRIPT: Final = re.compile(r"[A-Za-z0-9_]+\.py")
ENV: Final = re.compile(r"[A-Z_][A-Z0-9_]*")
UNIT_PREFIX: Final = "autophagy-cron-"
#: local = stdout 은 journal 에만, discord = Hermes 처럼 홈 채널로 보낸다(`cron_timer_run`).
DELIVERIES: Final = ("local", "discord")
STATE_DIR: Final = Path(".hermes") / "cron-timers"
_TZ_LINE: Final = re.compile(r"^timezone:\s*['\"]?([A-Za-z][A-Za-z0-9_+/-]*)['\"]?\s*$", re.M)
_TIMEOUT: Final = 120
#: Hermes 의 스크립트 기본 제한과 같다(벤더 cron/scheduler.py `_DEFAULT_SCRIPT_TIMEOUT`).
SCRIPT_TIMEOUT: Final = 3600


class TimerError(Exception):
    def __init__(self, rc: int, message: str) -> None:
        super().__init__(message)
        self.rc = rc


@dataclass(frozen=True, slots=True)
class Job:
    name: str
    schedule: str
    script: str
    env: tuple[str, ...] = ()
    deliver: str = "local"

    def check(self) -> None:
        if not (NAME.fullmatch(self.name) and SCRIPT.fullmatch(self.script)
                and all(ENV.fullmatch(e) for e in self.env) and self.deliver in DELIVERIES):
            raise TimerError(2, f"unsafe argument for {self.name[:40]}")


def unit_names(name: str) -> tuple[str, str]:
    return f"{UNIT_PREFIX}{name}.service", f"{UNIT_PREFIX}{name}.timer"


def hermes_timezone(home: Path) -> str:
    try:
        text = (home / ".hermes" / "config.yaml").read_text(encoding="utf-8")
    except OSError:
        return ""
    match = _TZ_LINE.search(text)
    return match[1] if match else ""


def render(job: Job, *, root: str, timezone: str) -> tuple[str, str]:
    """(service, timer) 유닛 본문. `root` 는 러너 모듈을 import 할 릴리스 트리(심링크 그대로)."""
    try:
        calendar = on_calendar(job.schedule, timezone)
    except ScheduleError as error:
        raise TimerError(2, f"schedule {job.schedule!r}: {error}") from None
    env = f" --env {','.join(job.env)}" if job.env else ""
    deliver = " --deliver discord" if job.deliver == "discord" else ""
    service = (
        "[Unit]\n"
        f"Description=Autophagy scheduled job {job.name} (no-agent script, moved from Hermes cron)\n"
        "After=network-online.target\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        "WorkingDirectory=%h/.hermes/scripts\n"
        f"Environment=PYTHONPATH={root}\n"
        "Environment=HERMES_HOME=%h/.hermes\n"
        "Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin\n"
        "EnvironmentFile=-%h/.env.secrets\n"
        f"ExecStart=/usr/bin/python3 -B -m automation.cron_timer_run --name {job.name} --script {job.script}{env}{deliver}\n"
        f"TimeoutStartSec={SCRIPT_TIMEOUT + 100}\n"
    )
    timer = (
        "[Unit]\n"
        f"Description=Run autophagy job {job.name} on its declared schedule ({job.schedule})\n\n"
        "[Timer]\n"
        f"OnCalendar={calendar}\n"
        "AccuracySec=1s\n"
        "Persistent=true\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )
    return service, timer


class Node:
    """계정 안의 실제 명령. 시험은 `CRON_TIMER_HERMES`·`CRON_TIMER_SYSTEMCTL` 로 바꾼다."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self.hermes_bin = os.environ.get("CRON_TIMER_HERMES") or str(home / ".local" / "bin" / "hermes")
        self.systemctl = (os.environ.get("CRON_TIMER_SYSTEMCTL") or "systemctl").split()

    def _call(self, argv: Sequence[str]) -> tuple[int, str]:
        env = {**os.environ, "XDG_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"}
        try:
            done = subprocess.run(list(argv), capture_output=True, text=True, env=env,
                                  timeout=_TIMEOUT, check=False)
        except subprocess.TimeoutExpired:
            return 124, ""
        except OSError:
            return 127, ""
        return done.returncode, done.stdout

    def hermes_jobs(self, name: str) -> list[dict[str, str]]:
        rc, out = self._call((self.hermes_bin, "cron", "list", "--all"))
        listing = parse_cron_listing(out) if rc == 0 else None
        if listing is None and stopped_by_release(self.home):
            listing = read_cron_job_states(self.home / ".hermes" / "cron" / "jobs.json")
        if listing is None:
            raise TimerError(4, f"hermes cron list --all failed or was unreadable; nothing changed for {name}")
        return listing.get(name, [])

    def hermes(self, verb: str, job_id: str) -> int:
        return self._call((self.hermes_bin, "cron", verb, job_id))[0]

    def unit(self, *args: str) -> tuple[int, str]:
        return self._call((*self.systemctl, "--user", *args))


def _lock(home: Path) -> int:
    path = home / ".hermes" / "cron" / ".autophagy-converge.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
    deadline = time.monotonic() + 60
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.monotonic() > deadline:
                os.close(fd)
                raise TimerError(6, "another cron convergence holds the lock") from None
            time.sleep(0.5)


def _write(path: Path, text: str) -> bool:
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return True


def _marker(home: Path, name: str) -> Path:
    return home / STATE_DIR / f"{name}.paused-by-converge.json"


def _one(jobs: list[dict[str, str]], name: str) -> dict[str, str] | None:
    if len(jobs) > 1:
        raise TimerError(7, f"duplicate Hermes job name {name}; nothing changed")
    return jobs[0] if jobs else None


def _pause_hermes(node: Node, job: Job) -> str:
    found = _one(node.hermes_jobs(job.name), job.name)
    if found is None:
        return "absent"
    if found["state"] != "paused":
        node.hermes("pause", found["id"])
        again = _one(node.hermes_jobs(job.name), job.name)
        if again is None or again["state"] != "paused":
            raise TimerError(5, f"Hermes job {job.name} ({found['id']}) is not paused after pause; timer not started")
        marker = _marker(node.home, job.name)
        marker.parent.mkdir(parents=True, exist_ok=True)
        _ = _write(marker, json.dumps({"id": found["id"]}) + "\n")
    return "paused"


def converge(node: Node, job: Job, *, root: str) -> str:
    if owner_off(node.home, job.name):
        state, detail = check(node, job, root=root)
        if state != "ok":
            raise TimerError(5, f"released-off timer drift: {detail}")
        return f"CRON-TIMER-UNCHANGED name={job.name} released-off"
    job.check()
    if not (node.home / ".hermes" / "scripts" / job.script).is_file():
        raise TimerError(7, f"script {job.script} is not deployed; timer not started")
    service, timer = render(job, root=root, timezone=hermes_timezone(node.home))
    fd = _lock(node.home)
    try:
        hermes = _pause_hermes(node, job)
        units = node.home / ".config" / "systemd" / "user"
        names = unit_names(job.name)
        changed = _write(units / names[0], service) | _write(units / names[1], timer)
        if node.unit("daemon-reload")[0] != 0 or node.unit("enable", "--now", names[1])[0] != 0:
            raise TimerError(5, f"systemctl could not enable {names[1]}")
        if (node.unit("is-enabled", names[1])[1].strip(), node.unit("is-active", names[1])[1].strip()) != ("enabled", "active"):
            raise TimerError(5, f"{names[1]} is not enabled and active after enable")
    finally:
        os.close(fd)
    return f"CRON-TIMER-OK name={job.name} action={'written' if changed else 'unchanged'} hermes={hermes}"


def revert(node: Node, name: str) -> str:
    Job(name, "* * * * *", "x.py").check()
    timer = unit_names(name)[1]
    fd = _lock(node.home)
    try:
        _ = node.unit("disable", "--now", timer)
        if node.unit("is-active", timer)[1].strip() == "active":
            raise TimerError(5, f"{timer} is still active; Hermes job left paused")
        found = _one(node.hermes_jobs(name), name)
        marker = _marker(node.home, name)
        ours = json.loads(marker.read_text(encoding="utf-8")).get("id") if marker.is_file() else None
        if found is None or found["state"] != "paused":
            return f"CRON-TIMER-REVERTED name={name} hermes={'absent' if found is None else found['state']}"
        if found["id"] != ours:
            return f"CRON-TIMER-REVERTED name={name} hermes=paused-by-someone-else (left paused)"
        node.hermes("resume", found["id"])
        again = _one(node.hermes_jobs(name), name)
        if again is None or again["state"] != "active":
            raise TimerError(5, f"Hermes job {name} did not resume")
        marker.unlink()
    finally:
        os.close(fd)
    return f"CRON-TIMER-REVERTED name={name} hermes=resumed"


def owner_off(home: Path, name: str) -> bool:
    from automation.cron_timer_owner_off import released_off
    from automation.legacy_services import memory_timer_off

    return memory_timer_off(home, name) or released_off(home, name)


def check(node: Node, job: Job, *, root: str) -> tuple[str, str]:
    """(state, detail) — ok · absent · stale · unknown. 릴리스 관측과 doctor 가 같은 판정을 쓴다."""
    job.check()
    if owner_off(node.home, job.name):
        timer_name = unit_names(job.name)[1]
        disabled = node.unit("is-enabled", timer_name)[1].strip()
        active = node.unit("is-active", timer_name)[1].strip()
        return ("ok", "released-off") if (disabled, active) == ("disabled", "inactive") else (
            "stale", "released-off-drift",
        )
    service, timer = render(job, root=root, timezone=hermes_timezone(node.home))
    units = node.home / ".config" / "systemd" / "user"
    names = unit_names(job.name)
    have = [(units / n).read_text(encoding="utf-8") if (units / n).is_file() else None for n in names]
    if have == [None, None]:
        return "absent", "no-unit"
    if have != [service, timer]:
        return "stale", "different-unit"
    if (node.unit("is-enabled", names[1])[1].strip(), node.unit("is-active", names[1])[1].strip()) != ("enabled", "active"):
        return "stale", "timer-off"
    try:
        found = _one(node.hermes_jobs(job.name), job.name)
    except TimerError as error:
        return "unknown", "duplicate-hermes-job" if error.rc == 7 else "unreadable-listing"
    if found is not None and found["state"] != "paused":
        return "stale", "hermes-job-active"
    return "ok", "-"


def identity(job: Job) -> str:
    fields = (job.name, " ".join(job.schedule.split()), job.script, ",".join(job.env), job.deliver)
    return hashlib.sha256("\n".join(fields).encode()).hexdigest()[:16]


def declared(root: Path, role: str, name: str) -> Job:
    """이 릴리스 트리의 `runner=systemd` cron 선언 하나 — 관측기가 주기 문자열을 원격 인자로 싣지 않게."""
    from automation.deploy_declarations import all_declarations

    for row in all_declarations(root):
        if (row.kind, row.account, row.destination, row.attr("runner")) == ("cron", role, name, "systemd"):
            return Job(name, row.attr("schedule"), row.attr("script"),
                       tuple(e for e in row.attr("env").split(",") if e), row.attr("deliver"))
    raise TimerError(7, f"no runner=systemd declaration for {role}:{name}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m automation.cron_timer")
    sub = parser.add_subparsers(dest="verb", required=True)
    for verb in ("converge", "check", "render"):
        p = sub.add_parser(verb)
        p.add_argument("--name", required=True)
        p.add_argument("--schedule", required=True)
        p.add_argument("--script", required=True)
        p.add_argument("--env", default="")
        p.add_argument("--deliver", default="local")
        p.add_argument("--timezone", default=None)
    sub.add_parser("revert").add_argument("--name", required=True)
    declared_check = sub.add_parser("check-declared")
    declared_check.add_argument("--role", required=True)
    declared_check.add_argument("--name", required=True)
    args = parser.parse_args(argv)
    node = Node(Path.home())
    root = str(Path(os.path.abspath(__file__)).parent.parent)
    try:
        if args.verb == "revert":
            print(revert(node, args.name))
            return 0
        if args.verb == "check-declared":
            job = declared(Path(root), args.role, args.name)
            state, detail = check(node, job, root=root)
            print(f"TIMER-CHECK state={state} detail={detail} want={identity(job)}")
            return 0
        job = Job(args.name, args.schedule, args.script, tuple(e for e in args.env.split(",") if e), args.deliver)
        if args.verb == "render":
            job.check()
            tz = hermes_timezone(node.home) if args.timezone is None else args.timezone
            print("\n".join(render(job, root=root, timezone=tz)))
        elif args.verb == "check":
            state, detail = check(node, job, root=root)
            print(f"TIMER-CHECK state={state} detail={detail} want={identity(job)}")
        else:
            print(converge(node, job, root=root))
    except TimerError as error:
        print(f"CRON-TIMER-BLOCK: {error}", file=sys.stderr)
        return error.rc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
