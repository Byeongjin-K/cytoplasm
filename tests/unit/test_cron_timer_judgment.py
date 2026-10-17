"""P2 — doctor 와 릴리스 관측이 사용자 타이머로 옮긴 잡(`runner=systemd`)을 어떻게 읽는가.

`test_cron_timer.py` 와 갈라 둔 이유: 여기서는 가짜 저장소 선언으로 doctor `gather`·`evaluate` 와
`deploy_all_observe` 를 돌린다(`test_doctor_cron_env.py` 와 같은 모양). 막는 것: 일부러 멈춘 Hermes 잡을
"중지" 경고로 세는 것, 타이머가 꺼져 아무도 안 돌리는 잡을 놓치는 것, 타이머 쪽 반복 실패를 놓치는 것,
관측이 판독 불가를 ok 로 읽는 것.
"""
from __future__ import annotations

import json
from pathlib import Path

from automation import deploy_all_observe
from automation.deploy_declarations import parse_declaration_file
from automation.doctor.capabilities import Finding, evaluate
from automation.doctor.facts import gather
from automation.install.checks import Status

ROW = ("agent|automation/xw/x.py|x-watch|v2:cron;schedule=17 * * * *;script=x.py;deliver=local;"
       "mode=no-agent;runner=systemd\n")


def _repo(root: Path) -> Path:
    manifest = root / "repo" / "automation" / "xw" / "deploy-manifest.txt"
    manifest.parent.mkdir(parents=True)
    _ = manifest.write_text(ROW, encoding="utf-8")
    return root / "repo"


def _home(root: Path, *, paused: bool, unit: bool, state: dict[str, object] | None = None) -> Path:
    home = root / "home"
    (home / ".hermes" / "cron").mkdir(parents=True)
    job = {"name": "x-watch", "enabled": True, "last_status": "ok", "paused_at": "t" if paused else None}
    _ = (home / ".hermes" / "cron" / "jobs.json").write_text(json.dumps({"jobs": [job]}), encoding="utf-8")
    if unit:
        units = home / ".config" / "systemd" / "user"
        units.mkdir(parents=True)
        _ = (units / "autophagy-cron-x-watch.timer").write_text("[Timer]\n", encoding="utf-8")
    if state is not None:
        (home / ".hermes" / "cron-timers").mkdir(parents=True)
        _ = (home / ".hermes" / "cron-timers" / "x-watch.json").write_text(json.dumps(state), encoding="utf-8")
    return home


def _cron(home: Path, repo: Path, timer_state: str) -> Finding:
    def run(argv: tuple[str, ...], _extra: object) -> tuple[int, str]:
        return (0, f"{timer_state}\n") if argv[-1] == "autophagy-cron-x-watch.timer" else (0, "active\n")

    facts = gather(account="agent", role="agent", home=home, gateway_unit="hermes-gateway.service",
                   online=False, run=run, uid=1000, repo=repo)
    return next(f for f in evaluate(facts) if f.key == "scheduled-jobs")


def test_a_moved_job_paused_in_hermes_is_not_a_stopped_job(tmp_path: Path) -> None:
    home = _home(tmp_path, paused=True, unit=True, state={"last_status": "ok", "failure_streak": 0})

    finding = _cron(home, _repo(tmp_path), "active")

    assert finding.status is Status.PASS
    assert "사용자 타이머 1개" in finding.detail


def test_a_paused_job_whose_timer_is_off_runs_nowhere_and_fails(tmp_path: Path) -> None:
    home = _home(tmp_path, paused=True, unit=True)

    finding = _cron(home, _repo(tmp_path), "inactive")

    assert finding.status is Status.FAIL
    assert finding.subjects == ("x-watch",)


def test_repeated_timer_failures_fail_like_hermes_failures(tmp_path: Path) -> None:
    home = _home(tmp_path, paused=True, unit=True,
                 state={"last_status": "error", "failure_streak": 3, "last_error": "exit 1"})

    finding = _cron(home, _repo(tmp_path), "active")

    assert finding.status is Status.FAIL
    assert "x-watch(3회 연속: exit 1)" in finding.detail


def test_a_node_that_has_not_moved_yet_stays_as_before(tmp_path: Path) -> None:
    home = _home(tmp_path, paused=False, unit=False)

    assert _cron(home, _repo(tmp_path), "inactive").status is Status.PASS


def _observe(out: str, rc: int = 0) -> tuple[str, str]:
    [decl] = parse_declaration_file("automation/xw/deploy-manifest.txt", ROW)
    seen = deploy_all_observe._cron(decl, Path("/srv/rel"), lambda _a, args: (rc, out), {})
    return seen.state, seen.have


def test_release_observation_takes_the_account_side_verdict() -> None:
    assert _observe("TIMER-CHECK state=ok detail=- want=abc\n")[0] == "ok"
    assert _observe("TIMER-CHECK state=absent detail=no-unit want=abc\n") == ("absent", "-")
    assert _observe("TIMER-CHECK state=stale detail=hermes-job-active want=abc\n")[0] == "stale"
    assert _observe("", rc=127) == ("unknown", "?")
    assert _observe("garbage\n") == ("unknown", "?")
