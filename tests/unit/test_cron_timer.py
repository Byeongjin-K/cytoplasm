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
from automation.deploy_declarations import all_declarations, parse_declaration_file
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


def test_every_declared_timer_schedule_has_the_same_calendar() -> None:
    rows = [d for d in all_declarations(Path(__file__).resolve().parents[2])
            if d.kind == "cron" and d.attr("runner") == "systemd"]
    agent_cron = {d.destination for d in all_declarations(Path(__file__).resolve().parents[2])
                  if d.kind == "cron" and d.account == "agent"}
    assert {d.destination for d in rows} == agent_cron and all(d.account == "agent" for d in rows)
    for row in rows:
        assert row.attr("deliver") in ("local", "discord")
        assert on_calendar(row.attr("schedule"), "Asia/Seoul").endswith(" Asia/Seoul")


def test_a_systemd_runner_delivers_locally_or_to_discord_only() -> None:
    row = "agent|a/w.py|w-watch|v2:cron;schedule=0 8 * * *;script=w.py;deliver=discord;mode=no-agent;runner=systemd\n"
    with pytest.raises(ManifestError, match="local or discord"):
        parse_declaration_file("automation/pkg/deploy-manifest.txt", row.replace("discord", "telegram"))
    ok = parse_declaration_file("automation/pkg/deploy-manifest.txt", row)
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


def _discord_home(tmp_path: Path, script: str) -> tuple[Path, cron_timer.Job]:
    home = tmp_path
    (home / ".hermes" / "scripts").mkdir(parents=True)
    (home / ".hermes" / ".env").write_text("DISCORD_HOME_CHANNEL=111\nDISCORD_HOME_CHANNEL_THREAD_ID=222\n", encoding="utf-8")
    (home / ".env.secrets").write_text("DISCORD_BOT_TOKEN=fixture-token\n", encoding="utf-8")
    (home / ".hermes" / "scripts" / "d.py").write_text(script, encoding="utf-8")
    return home, cron_timer.Job("d-watch", "* * * * *", "d.py", (), "discord")


def test_discord_delivery_posts_stdout_to_the_hermes_home_thread(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, job = _discord_home(
        tmp_path, "print('새 메일 1통: 회의 안내')\nprint('DIGEST-FAIL stage=deliver retry_safe=false code=x')\n")
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(cron_timer_run, "_discord_send", lambda *a: sent.append(a))
    monkeypatch.setattr("automation.notice_bot.fault_channel", lambda home=None: "333")
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)

    assert cron_timer_run.run(home, job) == 0

    assert ("fixture-token", "222", "새 메일 1통: 회의 안내") in sent, "사람 말 줄은 홈 스레드로"
    assert [target for _, target, body in sent if "DIGEST-FAIL" in body] == ["333"], "표식 코드는 페어 채널로만"
    kept = list((home / ".hermes" / "cron-timers" / "output" / "d-watch").glob("*.txt"))
    assert len(kept) == 1 and "DIGEST-FAIL" in kept[0].read_text()


@pytest.mark.parametrize("script", ["", "print('[SILENT]')\n"])
def test_silent_runs_deliver_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str) -> None:
    home, job = _discord_home(tmp_path, script)
    sent: list[object] = []
    monkeypatch.setattr(cron_timer_run, "_discord_send", lambda *a: sent.append(a))

    assert cron_timer_run.run(home, job) == 0
    assert sent == []


def test_a_failed_run_delivers_a_korean_failure_line_and_counts_the_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, job = _discord_home(tmp_path, "import sys\nsys.exit(2)\n")
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(cron_timer_run, "_discord_send", lambda *a: sent.append(a))

    monkeypatch.setattr("automation.notice_bot.fault_channel", lambda home=None: "333")

    assert cron_timer_run.run(home, job) == 2
    assert sent and sent[0][1] == "333" and sent[0][2].startswith("⚠️ 정기 작업 'd-watch' 실패(종료코드 2)")
    state = json.loads((home / ".hermes" / "cron-timers" / "d-watch.json").read_text())
    assert (state["last_status"], state["failure_streak"]) == ("error", 1)


def test_an_undeliverable_report_is_a_failure_not_silence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, job = _discord_home(tmp_path, "print('report')\n")
    (home / ".hermes" / ".env").write_text("", encoding="utf-8")

    assert cron_timer_run.run(home, job) == 1
    state = json.loads((home / ".hermes" / "cron-timers" / "d-watch.json").read_text())
    assert (state["last_status"], state["last_error"]) == ("error", "delivery-failed")


def test_discord_units_carry_the_hermes_like_environment() -> None:
    service, _ = cron_timer.render(cron_timer.Job("d-watch", "*/2 * * * *", "d.py", (), "discord"),
                                   root="/srv/rel", timezone="Asia/Seoul")
    assert "--deliver discord" in service
    assert "EnvironmentFile=-%h/.env.secrets" in service and "Environment=PATH=%h/.local/bin:" in service
