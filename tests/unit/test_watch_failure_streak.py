"""고빈도 워처가 장애를 소유자에게 **한 번만** 알리는가 — 침묵도 홍수도 아니어야 한다.

2026-08-18 실측: `mail-triage-watch` 111회, calendar·coordination 각 222회 연속 실패가
소유자에게 한 번도 닿지 않았다(`--deliver local` 은 전달 대상이 0이다). 그렇다고 `*/2`
워처를 `--deliver discord` 로 바꾸면 지속 실패 시 하루 720건이 된다. 그래서 전달 대상이
아니라 **발화 빈도**를 고친다 — 리컨실러의 `FAILURE_NOTICE_THRESHOLD` 패턴 재사용.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SOURCE = _REPO / "skills" / "mail" / "scripts" / "watch_failure_streak.py"
_SHARED_HELPER_ADOPTERS = (
    ("research-trends", _REPO / "automation" / "research_trends" / "research_trends.py", 1),
    ("notes-weekly-organize", _REPO / "automation" / "notes_organize" / "notes_organize.py", 1),
    ("budget-watch", _REPO / "skills" / "budget" / "scripts" / "budget_watch.py", 3),
)


def _module():
    spec = importlib.util.spec_from_file_location("watch_failure_streak", _SOURCE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def streak(tmp_path: Path):
    module = _module()
    return module, tmp_path


def _failure_notice_threshold(source: Path) -> int:
    for node in ast.parse(source.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        else:
            continue
        if isinstance(target, ast.Name) and target.id == "FAILURE_NOTICE_THRESHOLD":
            assert isinstance(value, ast.Constant)
            assert isinstance(value.value, int)
            return value.value
    raise AssertionError(f"{source} does not expose FAILURE_NOTICE_THRESHOLD")


@pytest.mark.parametrize(
    ("watch_name", "source", "expected_threshold"), _SHARED_HELPER_ADOPTERS
)
def test_shared_helper_adopter_inventory_pins_each_notice_threshold(
    watch_name: str, source: Path, expected_threshold: int
) -> None:
    """Each adopter must retain its explicit threshold when using the shared helper."""
    text = source.read_text(encoding="utf-8")

    assert "watch_failure_streak.record(" not in text, (
        f"{watch_name} must call the shared watch_failure_streak helper"
    )
    assert _failure_notice_threshold(source) == expected_threshold, (
        f"{watch_name} must keep FAILURE_NOTICE_THRESHOLD={expected_threshold}"
    )


def test_a_healthy_watcher_stays_completely_silent(streak) -> None:
    module, root = streak

    # Then: a passing tick says nothing — the silent-tick contract is preserved.
    assert module.record("mail-triage-watch", ok=True, root=root) is None


def test_failures_below_the_threshold_do_not_wake_the_owner(streak) -> None:
    module, root = streak

    # When: the watcher fails fewer times than the threshold.
    notices = [
        module.record("mail-triage-watch", ok=False, threshold=3, root=root) for _ in range(2)
    ]

    # Then: a transient hiccup produces no DM at all.
    assert notices == [None, None]


def test_the_threshold_tick_speaks_exactly_once_and_then_stops(streak) -> None:
    module, root = streak

    # When: the streak reaches and then passes the threshold.
    notices = [
        module.record("mail-triage-watch", ok=False, threshold=3, detail="rc=1", root=root)
        for _ in range(6)
    ]

    # Then: exactly one notice is emitted — 111 dead ticks become one DM, not 111.
    spoken = [notice for notice in notices if notice is not None]
    assert len(spoken) == 1
    assert "failed 3 ticks in a row" in spoken[0]
    assert "rc=1" in spoken[0]


def test_recovery_closes_the_incident_with_one_line(streak) -> None:
    module, root = streak
    for _ in range(3):
        module.record("mail-triage-watch", ok=False, threshold=3, root=root)

    # When: the watcher succeeds again.
    recovery = module.record("mail-triage-watch", ok=True, root=root)

    # Then: the owner is told the incident closed, once, and silence resumes.
    assert recovery is not None
    assert "recovered" in recovery
    assert module.record("mail-triage-watch", ok=True, root=root) is None


def test_a_second_incident_is_reported_again(streak) -> None:
    module, root = streak
    for _ in range(3):
        module.record("mail-triage-watch", ok=False, threshold=3, root=root)
    module.record("mail-triage-watch", ok=True, root=root)

    # When: the watcher breaks again after recovering.
    notices = [
        module.record("mail-triage-watch", ok=False, threshold=3, root=root) for _ in range(3)
    ]

    # Then: the new incident is not suppressed by the old one.
    assert len([notice for notice in notices if notice is not None]) == 1


def test_two_watchers_do_not_share_one_incident(streak) -> None:
    module, root = streak
    for _ in range(3):
        module.record("mail-triage-watch", ok=False, threshold=3, root=root)

    # Then: another watcher starts its own count rather than inheriting a stranger's.
    assert module.record("mail-daily-digest", ok=False, threshold=3, root=root) is None


def test_corrupt_state_restarts_the_count_instead_of_crashing_the_tick(streak) -> None:
    module, root = streak
    path = module.state_path("mail-triage-watch", root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    # Then: a damaged state file cannot take the watcher down with it.
    assert module.record("mail-triage-watch", ok=False, threshold=2, root=root) is None
    assert module.record("mail-triage-watch", ok=False, threshold=2, root=root) is not None



def test_the_state_root_can_be_redirected_away_from_the_owner_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    monkeypatch.setenv("WATCH_FAILURE_ROOT", str(tmp_path / "redirected"))

    # When: a failing tick is recorded with no explicit root.
    _ = module.record("mail-triage-watch", ok=False, threshold=1)

    # Then: it lands under the override, never in ~/.hermes — tests must not overwrite
    # live owner state, or the next real tick judges from a fabricated streak.
    assert (tmp_path / "redirected" / "mail-triage-watch.json").is_file()
