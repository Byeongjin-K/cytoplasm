"""P2 — no-agent 잡을 Hermes cron 에서 사용자 타이머로 옮기는 수렴기·러너·판정.

가짜 `hermes`·`systemctl`(호출을 기록하는 작은 파이썬 스크립트)로 계정 안 수렴기를 실제로 돌린다.
막는 것: 타이머가 Hermes 잡 paused 재확인 없이 켜지는 것, 러너가 active Hermes 잡과 함께 도는 것,
되돌리기가 남이 멈춘 잡을 깨우는 것, 판독 불가를 "없음"으로 읽는 것.
"""
from __future__ import annotations

import json
import os
import stat
import textwrap
from pathlib import Path

import pytest

from automation import cron_timer, cron_timer_run
from automation.cron_schedule import ScheduleError, on_calendar
from automation.deploy_declarations import parse_declaration_file
from automation.watcher_manifest import ManifestError

_HERMES = r'''#!/usr/bin/env python3
import json, os, sys
state = os.environ["FAKE_STATE"]
log = open(os.environ["FAKE_LOG"], "a")
log.write("hermes " + " ".join(sys.argv[1:]) + "\n")
jobs = json.load(open(state))
if os.environ.get("FAKE_LIST_FAIL"):
    sys.exit(1)
if sys.argv[1:3] == ["cron", "list"]:
    print("Scheduled Jobs")
    print()
    for j in jobs:
        print(f"  {j['id']} [{j['state']}]")
        print(f"    Name: {j['name']}")
        print("    Schedule: 17 * * * *")
        print("    Deliver: local")
        print("    Script: x.py")
        print("    Mode: no-agent")
        print()
elif sys.argv[1:3] in (["cron", "pause"], ["cron", "resume"]):
    if not os.environ.get("FAKE_PAUSE_IGNORED"):
        for j in jobs:
            if j["id"] == sys.argv[3]:
                j["state"] = "paused" if sys.argv[2] == "pause" else "active"
        json.dump(jobs, open(state, "w"))
'''

_SYSTEMCTL = r'''#!/usr/bin/env python3
import json, os, sys
log = open(os.environ["FAKE_LOG"], "a")
args = [a for a in sys.argv[1:] if a != "--user"]
log.write("systemctl " + " ".join(args) + "\n")
path = os.environ["FAKE_UNITS"]
units = json.load(open(path)) if os.path.exists(path) else {}
if args[0] == "enable":
    units[args[-1]] = "on"
elif args[0] == "disable":
    units[args[-1]] = "off"
elif args[0] == "is-enabled":
    print("enabled" if units.get(args[1]) == "on" else "disabled")
elif args[0] == "is-active":
    print("active" if units.get(args[1]) == "on" else "inactive")
json.dump(units, open(path, "w"))
'''


def _exe(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


@pytest.fixture
def node(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> cron_timer.Node:
    home = tmp_path / "home"
    (home / ".hermes" / "scripts").mkdir(parents=True)
    (home / ".hermes" / "scripts" / "doctor_watch.py").write_text("print('ran')\n", encoding="utf-8")
    (home / ".hermes" / "config.yaml").write_text("model: x\ntimezone: Asia/Seoul\n", encoding="utf-8")
    (tmp_path / "jobs.json").write_text(json.dumps([{"id": "ab12", "name": "doctor-watch", "state": "active"}]))
    monkeypatch.setenv("FAKE_STATE", str(tmp_path / "jobs.json"))
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "calls.log"))
    monkeypatch.setenv("FAKE_UNITS", str(tmp_path / "units.json"))
    monkeypatch.setenv("CRON_TIMER_HERMES", str(_exe(tmp_path / "hermes", _HERMES)))
    monkeypatch.setenv("CRON_TIMER_SYSTEMCTL", str(_exe(tmp_path / "systemctl", _SYSTEMCTL)))
    return cron_timer.Node(home)


JOB = cron_timer.Job("doctor-watch", "17 * * * *", "doctor_watch.py")
TIMER = "autophagy-cron-doctor-watch.timer"


def _calls(node: cron_timer.Node) -> list[str]:
    return (node.home.parent / "calls.log").read_text(encoding="utf-8").splitlines()


def _jobs(node: cron_timer.Node) -> list[dict[str, str]]:
    return json.loads((node.home.parent / "jobs.json").read_text())


@pytest.mark.parametrize(("schedule", "want"), [
    ("17 * * * *", "*-*-* *:17:00 Asia/Seoul"),
    ("0 9 * * *", "*-*-* 09:00:00 Asia/Seoul"),
    ("20 9-19 * * 1-5", "Mon..Fri *-*-* 09..19:20:00 Asia/Seoul"),
    ("*/10 * * * *", "*-*-* *:00/10:00 Asia/Seoul"),
    ("every 30m", "*-*-* *:00/30:00 Asia/Seoul"),
    ("every 2h", "*-*-* 00/2:00:00 Asia/Seoul"),
])
def test_hermes_schedules_become_the_same_calendar(schedule: str, want: str) -> None:
    assert on_calendar(schedule, "Asia/Seoul") == want


@pytest.mark.parametrize("schedule", ["0 9 1 * 1", "every 7m", "1-10/2 * * * *", "0 9 * * 5-1", "61 * * * *"])
def test_schedules_whose_meaning_would_change_are_refused(schedule: str) -> None:
    with pytest.raises(ScheduleError):
        on_calendar(schedule)


def test_a_systemd_runner_must_deliver_locally() -> None:
    row = "agent|a/w.py|w-watch|v2:cron;schedule=0 8 * * *;script=w.py;deliver=discord;mode=no-agent;runner=systemd\n"
    with pytest.raises(ManifestError, match="deliver=local"):
        parse_declaration_file("automation/pkg/deploy-manifest.txt", row)
    ok = parse_declaration_file("automation/pkg/deploy-manifest.txt", row.replace("discord", "local"))
    assert ok[0].attr("runner") == "systemd"
    assert parse_declaration_file("automation/pkg/deploy-manifest.txt", row.replace(";runner=systemd", "")
                                  .replace("discord", "local"))[0].attr("runner") == "hermes"


def test_converge_pauses_and_rereads_hermes_before_it_enables_the_timer(node: cron_timer.Node) -> None:
    line = cron_timer.converge(node, JOB, root="/srv/rel")

    calls = _calls(node)
    assert "hermes=paused" in line
    assert calls.index("hermes cron pause ab12") < calls.index(f"systemctl enable --now {TIMER}")
    pause = calls.index("hermes cron pause ab12")
    assert "hermes cron list --all" in calls[pause + 1:calls.index(f"systemctl enable --now {TIMER}")]
    assert _jobs(node)[0]["state"] == "paused"
    timer = (node.home / ".config/systemd/user" / TIMER).read_text()
    assert "OnCalendar=*-*-* *:17:00 Asia/Seoul" in timer
    assert cron_timer.check(node, JOB, root="/srv/rel") == ("ok", "-")


def test_an_unreadable_listing_changes_nothing(node: cron_timer.Node, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_LIST_FAIL", "1")
    with pytest.raises(cron_timer.TimerError) as caught:
        cron_timer.converge(node, JOB, root="/srv/rel")
    assert caught.value.rc == 4
    assert not any(call.startswith(("systemctl enable", "hermes cron pause")) for call in _calls(node))


def test_a_pause_that_does_not_stick_never_starts_the_timer(node: cron_timer.Node, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_PAUSE_IGNORED", "1")
    with pytest.raises(cron_timer.TimerError) as caught:
        cron_timer.converge(node, JOB, root="/srv/rel")
    assert caught.value.rc == 5
    assert not any(call.startswith("systemctl enable") for call in _calls(node))


def test_duplicate_hermes_names_are_refused(node: cron_timer.Node) -> None:
    (node.home.parent / "jobs.json").write_text(json.dumps([
        {"id": "ab12", "name": "doctor-watch", "state": "active"},
        {"id": "cd34", "name": "doctor-watch", "state": "paused"}]))
    with pytest.raises(cron_timer.TimerError) as caught:
        cron_timer.converge(node, JOB, root="/srv/rel")
    assert caught.value.rc == 7


def test_revert_turns_the_timer_off_before_it_resumes_only_the_job_it_paused(node: cron_timer.Node) -> None:
    cron_timer.converge(node, JOB, root="/srv/rel")

    assert cron_timer.revert(node, "doctor-watch").endswith("hermes=resumed")

    calls = _calls(node)
    assert calls.index(f"systemctl disable --now {TIMER}") < calls.index("hermes cron resume ab12")
    assert _jobs(node)[0]["state"] == "active"
    assert cron_timer.check(node, JOB, root="/srv/rel")[0] == "stale"


def test_revert_leaves_a_job_someone_else_paused(node: cron_timer.Node) -> None:
    (node.home.parent / "jobs.json").write_text(json.dumps([{"id": "ab12", "name": "doctor-watch", "state": "paused"}]))
    cron_timer.converge(node, JOB, root="/srv/rel")

    assert "left paused" in cron_timer.revert(node, "doctor-watch")
    assert "hermes cron resume ab12" not in _calls(node)


def _hermes_jobs_json(home: Path, paused: bool) -> None:
    (home / ".hermes" / "cron").mkdir(parents=True, exist_ok=True)
    (home / ".hermes" / "cron" / "jobs.json").write_text(json.dumps(
        {"jobs": [{"id": "ab12", "name": "doctor-watch", "paused_at": "x" if paused else None}]}))


def test_the_runner_never_runs_while_the_hermes_job_is_active(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path
    (home / ".hermes" / "scripts").mkdir(parents=True)
    (home / ".hermes" / "scripts" / "doctor_watch.py").write_text(
        "import pathlib; pathlib.Path('ran').write_text('1')\n", encoding="utf-8")
    _hermes_jobs_json(home, paused=False)

    assert cron_timer_run.run(home, JOB) == 0
    assert "CRON-TIMER-SKIP name=doctor-watch reason=hermes-job-active" in capsys.readouterr().out
    assert not (home / ".hermes" / "scripts" / "ran").exists()

    _hermes_jobs_json(home, paused=True)
    assert cron_timer_run.run(home, JOB) == 0
    assert (home / ".hermes" / "scripts" / "ran").exists()
    state = json.loads((home / ".hermes" / "cron-timers" / "doctor-watch.json").read_text())
    assert (state["last_status"], state["failure_streak"]) == ("ok", 0)


def test_the_runner_counts_failures_and_passes_only_declared_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path
    (home / ".hermes" / "scripts").mkdir(parents=True)
    (home / ".hermes" / ".env").write_text("WANTED=yes\nPROVIDER_SECRET=no\n", encoding="utf-8")
    (home / ".hermes" / "scripts" / "w.py").write_text(textwrap.dedent("""\
        import os, sys
        open('env.txt', 'w').write(os.environ.get('WANTED', '-') + os.environ.get('PROVIDER_SECRET', '-'))
        sys.exit(3)
        """), encoding="utf-8")
    monkeypatch.delenv("WANTED", raising=False)
    job = cron_timer.Job("w-watch", "* * * * *", "w.py", ("WANTED",))

    assert cron_timer_run.run(home, job) == 3
    assert cron_timer_run.run(home, job) == 3
    assert (home / ".hermes" / "scripts" / "env.txt").read_text() == "yes-"
    state = json.loads((home / ".hermes" / "cron-timers" / "w-watch.json").read_text())
    assert (state["last_status"], state["failure_streak"], state["last_error"]) == ("error", 2, "exit 3")
    assert oct(os.stat(home / ".hermes" / "cron-timers" / "w-watch.json").st_mode & 0o777) == "0o600"
