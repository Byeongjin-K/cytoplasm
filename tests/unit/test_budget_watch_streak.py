"""budget-watch 가 지속 장애를 **한 번만** 말하는가 — 일시 503 은 무음으로 지나가야 한다.

2026-08-23 23:30 실측: `budget-watch error rc=4: error[api]: The service is currently
unavailable.` — Google Sheets 일시 503 이었고 다음 틱(30분)이 스스로 나았다. 그런 틱마다
소유자를 깨우면 하루 48번 늑대소년이 되고, 반대로 지금처럼 아무 말도 하지 않으면
권한 회수·시트 삭제 같은 **지속** 장애도 30분마다 조용히 쌓이기만 한다(계획 R3).

그래서 발화 손잡이는 전달 대상이 아니라 연속 실패 임계치다 — mail 두 워처가 이미 쓰는
`watch_failure_streak` 의 incident 모델을 그대로 채택한다. `*/30` 에 threshold=3 이면
약 1.5시간 연속 실패에서 정확히 1건, 회복에서 정확히 1건이다.

2026-08-24 사후 보강: `--deliver discord` 에서 스케줄러는 rc≠0 이면 stdout 과 무관하게
자체 실패 배너를 게시한다(18:30·20:30 KST 일시 실패 2건이 각각 소유자를 깨운 실측).
그래서 스트릭에 **기록된** 실패 틱은 exit 0 이고, 기록하지 못한 틱(헬퍼 부재·record
예외)만 exit 1 로 남아 배너가 최후 방어선이 된다 — 침묵은 기록으로만 산다.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

_REPO = Path(__file__).resolve().parents[2]
_WRAPPER = _REPO / "skills" / "budget" / "scripts" / "budget_watch.py"
_DEPLOY = _REPO / "skills" / "budget" / "deploy.sh"
_STATE_NAME = "budget-watch.json"

#: 2026-08-23 실사고의 stderr 마지막 줄 그대로.
_TRANSIENT_503 = "error[api]: The service is currently unavailable."


def _load_wrapper() -> ModuleType:
    spec = importlib.util.spec_from_file_location("budget_watch_streak_under_test", _WRAPPER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


watch = _load_wrapper()


@pytest.fixture(autouse=True)
def _hermetic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """스트릭 상태는 tmp 로, 시크릿 자체로드는 차단 — 테스트가 운영 상태를 건드리지 않는다."""
    monkeypatch.setenv("WATCH_FAILURE_ROOT", str(tmp_path / "watch-failure"))
    monkeypatch.setattr(watch, "_load_env_secrets", lambda *_args, **_kwargs: None)
    cli = tmp_path / "budget_cli.py"
    _ = cli.write_text("", encoding="utf-8")
    monkeypatch.setattr(watch, "CLI", cli)


def _stub_child(
    monkeypatch: pytest.MonkeyPatch, outcomes: list[tuple[int, str]]
) -> list[list[str]]:
    """자식 CLI 를 스크립트된 (rc, stderr) 로 대체한다 — 네트워크도 시트도 없다."""
    calls: list[list[str]] = []
    queue = list(outcomes)

    def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(argv))
        returncode, stderr = queue.pop(0) if queue else (0, "")
        return subprocess.CompletedProcess(argv, returncode, "", stderr)

    monkeypatch.setattr(watch.subprocess, "run", fake_run)
    return calls


def _state(tmp_path: Path) -> dict[str, object]:
    raw = json.loads((tmp_path / "watch-failure" / _STATE_NAME).read_text(encoding="utf-8"))
    return {
        "consecutive_failures": raw["consecutive_failures"],
        "incident_open": raw["incident_open"],
    }


def _lines(captured: str) -> list[str]:
    return [line for line in captured.splitlines() if line.strip()]


def test_the_threshold_matches_the_half_hour_cadence() -> None:
    # Then: `*/30` × 3 ≈ 1.5h — 일시 503 한 틱은 통과, 지속 장애는 같은 오전에 들린다.
    assert watch.FAILURE_NOTICE_THRESHOLD == 3
    assert watch.WATCH_NAME == "budget-watch"


def test_a_healthy_tick_stays_silent_and_writes_no_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: the governed CLI succeeds.
    _stub_child(monkeypatch, [(0, "")])

    # When: the cron tick runs.
    code = watch.main()

    # Then: no-agent 무음 계약 그대로 — exit 0, 빈 stdout, 디스크 접촉 없음.
    assert code == 0
    assert capsys.readouterr().out == ""
    assert not (tmp_path / "watch-failure" / _STATE_NAME).exists()


def test_two_consecutive_failures_do_not_wake_the_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: two ticks fail the way the 2026-08-23 transient 503 did.
    _stub_child(monkeypatch, [(4, _TRANSIENT_503), (4, _TRANSIENT_503)])

    # When: both ticks run.
    codes = [watch.main(), watch.main()]

    # Then: 임계치 전 틱은 기록되고 exit 0 — rc≠0 이면 스케줄러 배너가 소유자를 깨운다.
    assert codes == [1, 1]
    assert len(_lines(capsys.readouterr().out)) == 2
    assert not (tmp_path / "watch-failure" / _STATE_NAME).exists()


def test_the_third_failure_speaks_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: the outage persists across five ticks (~2.5h).
    _stub_child(monkeypatch, [(4, _TRANSIENT_503)] * 5)

    # When: every tick runs.
    codes = [watch.main() for _ in range(5)]

    # Then: 정확히 1건 — 임계 틱에서만 말하고, 그 뒤 사고가 열린 동안은 침묵한다.
    captured = capsys.readouterr().out
    assert codes == [1] * 5
    spoken = _lines(captured)
    assert len(spoken) == 5
    assert spoken[0].startswith("budget-watch error:")
    assert f"rc=4: {_TRANSIENT_503}" in spoken[0]
    assert not (tmp_path / "watch-failure" / _STATE_NAME).exists()


def test_a_stale_open_incident_never_double_notifies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: leftover state from an incident that already spoke.
    root = tmp_path / "watch-failure"
    root.mkdir(parents=True)
    _ = (root / _STATE_NAME).write_text(
        json.dumps({"consecutive_failures": 7, "incident_open": True}), encoding="utf-8"
    )
    _stub_child(monkeypatch, [(4, _TRANSIENT_503)])

    # When: the outage continues.
    code = watch.main()

    # Then: 같은 사고를 두 번 알리지 않고 stdout도 비며, 기록된 실패라 exit 0 이다.
    assert code == 1
    assert len(_lines(capsys.readouterr().out)) == 1
    assert _state(tmp_path) == {"consecutive_failures": 7, "incident_open": True}


def test_recovery_closes_the_incident_with_one_line_and_resets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: three failures opened an incident.
    _stub_child(monkeypatch, [*([(4, _TRANSIENT_503)] * 3), (0, ""), (0, "")])
    for _ in range(3):
        _ = watch.main()
    _ = capsys.readouterr()

    # When: the next tick succeeds, then one more.
    recovered = watch.main()
    captured = capsys.readouterr().out
    quiet = watch.main()

    # Then: 회복도 정확히 1건이고 상태는 0 으로 돌아가며, 그 다음 틱은 다시 무음이다.
    assert (recovered, quiet) == (0, 0)
    assert captured == ""
    assert capsys.readouterr().out == ""
    assert not (tmp_path / "watch-failure" / _STATE_NAME).exists()


def test_garbage_state_is_tolerated_rather_than_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: a truncated/garbled state file (crash mid-write, disk trouble).
    root = tmp_path / "watch-failure"
    root.mkdir(parents=True)
    _ = (root / _STATE_NAME).write_text("{not json", encoding="utf-8")
    _stub_child(monkeypatch, [(4, _TRANSIENT_503)])

    # When: the tick fails.
    code = watch.main()

    # Then: 0 에서 다시 세되 틱은 죽지 않고(기록됨=exit 0) 임계치 전 stdout은 비어 있다.
    assert code == 1
    assert len(_lines(capsys.readouterr().out)) == 1
    assert (root / _STATE_NAME).read_text() == "{not json"


@pytest.mark.parametrize(("child_rc", "expected"), [(0, 0), (4, 1)])
def test_a_broken_notice_path_never_changes_the_tick_verdict(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    child_rc: int,
    expected: int,
) -> None:
    # Given: the streak helper itself is broken (unwritable state root, bad deploy).
    def explode(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("state root exploded")

    monkeypatch.setattr(watch, "watch_failure_streak", SimpleNamespace(record=explode))
    _stub_child(monkeypatch, [(child_rc, _TRANSIENT_503 if child_rc else "")])

    # When: the tick runs.
    code = watch.main()

    # Then: 성공 틱은 그대로 0, 기록하지 못한 실패 틱은 exit 1 로 남아
    # 스케줄러 배너가 최후 방어선이 된다 — 침묵은 기록으로만 산다.
    assert code == expected
    assert bool(capsys.readouterr().out) is bool(child_rc)


def test_a_half_deployed_node_without_the_helper_still_reports_failures(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: the helper never landed (partial deploy) — the ImportError fallback is live.
    monkeypatch.setattr(watch, "watch_failure_streak", None)
    _stub_child(monkeypatch, [(4, _TRANSIENT_503)])

    # When: the tick fails.
    code = watch.main()

    # Then: 옛 동작(매 틱 한 줄)으로 내려앉을 뿐 워처는 죽지 않는다.
    captured = capsys.readouterr().out
    assert code == 1
    assert _lines(captured) == [f"budget-watch error: rc=4: {_TRANSIENT_503}"]


def test_threshold_notice_carries_only_the_masked_child_detail(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: three failures whose tail carries an address and a long identifier.
    detail = "denied for owner@example.com sheet 152648282079"
    _stub_child(monkeypatch, [(4, detail)] * 3)

    # When: the streak reaches its threshold.
    codes = [watch.main() for _ in range(3)]

    # Then: exactly the incident notice is delivered, with masked detail inside it.
    lines = _lines(capsys.readouterr().out)
    assert codes == [1, 1, 1]
    assert len(lines) == 3
    assert lines[0].startswith("budget-watch error: rc=4: ")
    assert "owner@example.com" not in lines[0]
    assert "152648282079" not in lines[0]
    assert "[MASKED-EMAIL]" in lines[0] and "[MASKED-NUM]" in lines[0]


def test_the_budget_deploy_script_ships_the_streak_helper() -> None:
    """헬퍼가 실리지 않으면 노드는 ImportError fallback 으로 옛 동작을 계속한다."""
    deploy_text = _DEPLOY.read_text(encoding="utf-8")

    assert "push_file \"$repo_root/skills/mail/scripts/watch_failure_streak.py\"" in deploy_text, (
        "skills/budget/deploy.sh 가 watch_failure_streak.py 를 올리지 않는다 — "
        "「커밋됨 ≠ 배포됨」"
    )
    guard_block = deploy_text.split("deploy_provenance_check", 1)
    assert len(guard_block) == 2, "deploy_provenance_check 호출을 찾지 못했다"
    assert "watch_failure_streak.py" in guard_block[1].split("|| exit", 1)[0], (
        "watch_failure_streak.py 가 provenance 검사 밖에 있다 — origin/main 대조를 건너뛴다"
    )
