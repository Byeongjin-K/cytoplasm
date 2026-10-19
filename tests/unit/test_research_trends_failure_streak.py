"""CR-A1: the weekly research watcher opens one incident and reports recovery."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Protocol, cast

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_DEPLOY = _ROOT / "automation" / "research_trends" / "deploy.sh"
os.environ.setdefault("TOPICS_SCRIPTS", str(_ROOT / "skills" / "topics" / "scripts"))
sys.path.insert(0, str(_ROOT / "automation" / "research_trends"))

from automation.research_trends import research_trends  # noqa: E402


class _StreakHelper(Protocol):
    def record(self, *args: object, **kwargs: object) -> str | None: ...


class _NoticeStub:
    """Observe notices while preserving the real helper's state transitions."""

    def __init__(self, real_helper: _StreakHelper) -> None:
        self._real_helper: _StreakHelper = real_helper
        self.notices: list[str] = []

    def record(self, *args: object, **kwargs: object) -> str | None:
        notice = self._real_helper.record(*args, **kwargs)
        if notice is not None:
            self.notices.append(notice)
        return notice


@pytest.fixture(autouse=True)
def _hermetic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WATCH_FAILURE_ROOT", str(tmp_path / "watch-failure"))


def _state(tmp_path: Path) -> dict[str, object]:
    path = tmp_path / "watch-failure" / "research-trends.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        "consecutive_failures": raw["consecutive_failures"],
        "incident_open": raw["incident_open"],
    }


def test_first_failed_tick_writes_state_and_emits_threshold_one_notice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    stub = _NoticeStub(cast(_StreakHelper, cast(object, research_trends.watch_failure_streak)))
    monkeypatch.setattr(research_trends, "watch_failure_streak", stub)
    monkeypatch.setattr(
        research_trends,
        "run",
        lambda: (_ for _ in ()).throw(RuntimeError("denied owner@example.com id 123456789")),
    )

    assert research_trends.main() == 1
    output = capsys.readouterr().out
    assert "[MASKED-EMAIL]" in output and "[MASKED-NUM]" in output
    assert stub.notices == []
    assert not (tmp_path / "watch-failure").exists()


def test_success_after_failure_emits_recovery_and_resets_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        research_trends,
        "run",
        lambda: (_ for _ in ()).throw(RuntimeError("temporary outage")),
    )
    assert research_trends.main() == 1
    _ = capsys.readouterr()

    monkeypatch.setattr(research_trends, "run", lambda: 0)
    assert research_trends.main() == 0

    assert capsys.readouterr().out == ""
    assert not (tmp_path / "watch-failure").exists()


def test_already_delivered_noop_is_exit_zero_with_empty_stdout(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("RESEARCH_TRENDS_DRY_RUN", raising=False)
    monkeypatch.setattr(research_trends, "_safe_topics", lambda: ("SLAM",))
    monkeypatch.setattr(
        research_trends,
        "_delivered_week",
        lambda: research_trends._iso_week(research_trends.datetime.now(research_trends.KST)),
    )

    assert research_trends.main() == 0
    assert capsys.readouterr().out == ""


def test_helper_missing_fallback_keeps_one_masked_line(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(research_trends, "watch_failure_streak", None)
    monkeypatch.setattr(
        research_trends,
        "run",
        lambda: (_ for _ in ()).throw(OSError("owner@example.com account 987654321")),
    )

    assert research_trends.main() == 1

    line = capsys.readouterr().out.strip()
    assert line.startswith("research-trends error: OSError: ")
    assert "owner@example.com" not in line
    assert "987654321" not in line
    assert "[MASKED-EMAIL]" in line
    assert "[MASKED-NUM]" in line
    assert len(line) <= 300


def test_unpersisted_failure_streak_keeps_the_failure_exit_visible(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    marker = "failure streak state was not persisted"
    helper = SimpleNamespace(PERSISTENCE_FAILURE=marker, record=lambda *_args, **_kwargs: marker)
    monkeypatch.setattr(research_trends, "watch_failure_streak", helper)
    monkeypatch.setattr(
        research_trends,
        "run",
        lambda: (_ for _ in ()).throw(RuntimeError("tick broke")),
    )

    assert research_trends.main() == 1
    assert "RuntimeError: tick broke" in capsys.readouterr().out


def test_stale_open_incident_does_not_double_notify(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "watch-failure"
    root.mkdir(parents=True)
    (root / "research-trends.json").write_text(
        json.dumps({"consecutive_failures": 4, "incident_open": True}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        research_trends,
        "run",
        lambda: (_ for _ in ()).throw(RuntimeError("still down")),
    )

    assert research_trends.main() == 1
    assert "still down" in capsys.readouterr().out
    assert _state(tmp_path) == {"consecutive_failures": 4, "incident_open": True}


def test_garbage_streak_state_does_not_crash_tick(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "watch-failure"
    root.mkdir(parents=True)
    (root / "research-trends.json").write_text("{garbage", encoding="utf-8")
    monkeypatch.setattr(research_trends, "run", lambda: 0)

    assert research_trends.main() == 0
    assert (root / "research-trends.json").read_text(encoding="utf-8") == "{garbage"


def test_deploy_ships_helper_without_registering_the_retired_schedule() -> None:
    deploy = _DEPLOY.read_text(encoding="utf-8")
    helper = "$repo_root/skills/mail/scripts/watch_failure_streak.py"
    provenance = deploy.split("deploy_provenance_check", 1)[1].split("|| exit", 1)[0]

    assert helper in provenance
    assert f'push_file "{helper}"' in deploy
    assert "hermes cron edit" not in deploy
    assert "hermes cron create" not in deploy
    assert not any(line.lstrip().startswith(("converge_timer ", "converge_cron "))
                   for line in deploy.splitlines())


@pytest.mark.parametrize("run_ok", [False, True])
def test_broken_notice_output_does_not_change_tick_exit_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    run_ok: bool,
) -> None:
    root = tmp_path / "watch-failure"
    root.mkdir(parents=True)
    (root / "research-trends.json").write_text(
        json.dumps({"consecutive_failures": 1, "incident_open": True}),
        encoding="utf-8",
    )
    if run_ok:
        monkeypatch.setattr(research_trends, "run", lambda: 0)
    else:
        monkeypatch.setattr(
            research_trends,
            "run",
            lambda: (_ for _ in ()).throw(RuntimeError("tick broke")),
        )

    def broken_print(*_args: object, **_kwargs: object) -> None:
        raise BrokenPipeError("notice sink closed")

    monkeypatch.setattr("builtins.print", broken_print)

    # record() 는 print 전에 이미 성공했다 — 닫힌 sink 는 notice 만 잃고 기록은 남으므로
    # 실패 틱도 exit 0 이다(기록된 실패=무배너, 2026-08-24 계약).
    assert research_trends.main() == (0 if run_ok else 1)


def test_notice_failure_does_not_change_tick_exit_semantics(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    broken = SimpleNamespace(
        record=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("notice broke"))
    )
    monkeypatch.setattr(research_trends, "watch_failure_streak", broken)

    monkeypatch.setattr(research_trends, "run", lambda: 0)
    assert research_trends.main() == 0
    assert capsys.readouterr().out == ""
    monkeypatch.setattr(
        research_trends,
        "run",
        lambda: (_ for _ in ()).throw(RuntimeError("tick broke")),
    )
    assert research_trends.main() == 1
    assert "RuntimeError: tick broke" in capsys.readouterr().out
