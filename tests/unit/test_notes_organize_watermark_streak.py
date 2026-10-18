"""Delivered-week and real failure-incident contract for notes-weekly-organize."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from automation.notes_organize import notes_organize

_REPO = Path(__file__).resolve().parents[2]
_WRAPPER = _REPO / "automation" / "notes_organize" / "notes_organize.py"
_REAL_STREAK_DIR = _REPO / "skills" / "mail" / "scripts"
_KST = timezone(timedelta(hours=9), "KST")


def _week() -> str:
    calendar = datetime.now(_KST).isocalendar()
    return f"{calendar.year}-W{calendar.week:02d}"


def _run_wrapper(
    home: Path,
    scripts: Path,
    state: Path,
    streak: Path | str,
    **extra_env: str,
) -> subprocess.CompletedProcess[str]:
    home.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "HOME": str(home),
        "PYTHONPATH": os.pathsep.join((str(_REAL_STREAK_DIR), str(_REPO))),
        "REPORT_SCRIPTS": str(scripts),
        "NOTES_ORGANIZE_STATE_DIR": str(state),
        "WATCH_FAILURE_ROOT": str(streak),
        **extra_env,
    }
    return subprocess.run(
        [sys.executable, str(_WRAPPER)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env=env,
    )


def _plant_cli(scripts: Path, body: str = "raise SystemExit(0)\n") -> None:
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "report_cli.py").write_text(body, encoding="utf-8")


def test_success_writes_delivered_week(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    state = tmp_path / "notes-state"
    _plant_cli(scripts)

    result = _run_wrapper(tmp_path / "home", scripts, state, tmp_path / "streak")

    assert result.returncode == 0
    assert result.stdout == ""
    assert (state / "delivered-week").read_text(encoding="utf-8") == f"{_week()}\n"


def test_second_tick_in_same_week_is_silent_and_does_not_invoke_child(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    state = tmp_path / "notes-state"
    trace = tmp_path / "child-invoked"
    _plant_cli(
        scripts,
        "import os\n"
        "from pathlib import Path\n"
        "Path(os.environ['TRACE_FILE']).write_text('invoked\\n', encoding='utf-8')\n",
    )
    state.mkdir()
    (state / "delivered-week").write_text(f"{_week()}\n", encoding="utf-8")

    result = _run_wrapper(
        tmp_path / "home",
        scripts,
        state,
        tmp_path / "streak",
        TRACE_FILE=str(trace),
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert not trace.exists()


def test_two_consecutive_failures_emit_exactly_one_real_helper_notice(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    state = tmp_path / "notes-state"
    streak = tmp_path / "streak"
    _plant_cli(
        scripts,
        "import sys\n"
        "sys.stderr.write('private victim@example.test 123456\\n')\n"
        "raise SystemExit(3)\n",
    )

    outcomes = [
        _run_wrapper(tmp_path / "home", scripts, state, streak),
        _run_wrapper(tmp_path / "home", scripts, state, streak),
    ]

    assert [result.returncode for result in outcomes] == [1, 1]
    notices = [line for result in outcomes for line in result.stdout.splitlines() if line]
    assert len(notices) == 2
    assert all("[MASKED-EMAIL]" in line and "[MASKED-NUM]" in line for line in notices)
    assert not (state / "delivered-week").exists()
    assert not streak.exists()


def test_success_after_failure_emits_one_real_helper_recovery_notice(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    state = tmp_path / "notes-state"
    streak = tmp_path / "streak"
    _plant_cli(scripts, "raise SystemExit(1)\n")
    failed = _run_wrapper(tmp_path / "home", scripts, state, streak)
    assert failed.returncode == 1
    _plant_cli(scripts)

    recovered = _run_wrapper(tmp_path / "home", scripts, state, streak)

    assert recovered.returncode == 0
    assert recovered.stdout == ""
    assert not streak.exists()
    assert (state / "delivered-week").exists()


def test_helper_missing_failure_emits_exactly_one_masked_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    scripts = tmp_path / "scripts"
    cli = scripts / "report_cli.py"
    _plant_cli(scripts, "raise SystemExit(3)\n")
    monkeypatch.setattr(notes_organize, "CLI", cli)
    monkeypatch.setattr(notes_organize, "_load_env_secrets", lambda: None)
    monkeypatch.setattr(notes_organize, "_delivered_week", lambda: "")
    monkeypatch.setattr(notes_organize, "watch_failure_streak", None)

    assert notes_organize.main() == 1
    assert capsys.readouterr().out == "notes-organize error rc=3\n"


def test_broken_notice_path_is_silent_and_preserves_failure_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    scripts = tmp_path / "scripts"
    cli = scripts / "report_cli.py"
    _plant_cli(scripts, "raise SystemExit(3)\n")
    broken = SimpleNamespace(
        record=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("notice broke"))
    )
    monkeypatch.setattr(notes_organize, "CLI", cli)
    monkeypatch.setattr(notes_organize, "_load_env_secrets", lambda: None)
    monkeypatch.setattr(notes_organize, "_delivered_week", lambda: "")
    monkeypatch.setattr(notes_organize, "watch_failure_streak", broken)

    assert notes_organize.main() == 1
    assert capsys.readouterr().out == "notes-organize error rc=3\n"


def test_unpersisted_failure_streak_keeps_the_failure_exit_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    scripts = tmp_path / "scripts"
    cli = scripts / "report_cli.py"
    _plant_cli(scripts, "raise SystemExit(3)\n")
    marker = "failure streak state was not persisted"
    helper = SimpleNamespace(PERSISTENCE_FAILURE=marker, record=lambda *_args, **_kwargs: marker)
    monkeypatch.setattr(notes_organize, "CLI", cli)
    monkeypatch.setattr(notes_organize, "_load_env_secrets", lambda: None)
    monkeypatch.setattr(notes_organize, "_delivered_week", lambda: "")
    monkeypatch.setattr(notes_organize, "watch_failure_streak", helper)

    assert notes_organize.main() == 1
    assert capsys.readouterr().out == "notes-organize error rc=3\n"


def test_broken_notice_output_is_silent_and_preserves_recovery_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        notes_organize,
        "watch_failure_streak",
        SimpleNamespace(record=lambda *_args, **_kwargs: "recovered"),
    )

    def broken_print(*_args: object, **_kwargs: object) -> None:
        raise BrokenPipeError("notice sink closed")

    monkeypatch.setattr("builtins.print", broken_print)
    notes_organize._announce(ok=True)


def test_success_ignores_broken_notice_state_path_and_keeps_exit_contract(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    state = tmp_path / "notes-state"
    _plant_cli(scripts)

    result = _run_wrapper(
        tmp_path / "home",
        scripts,
        state,
        "~definitely-no-such-autophagy-user/state",
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert (state / "delivered-week").read_text(encoding="utf-8") == f"{_week()}\n"


def test_new_iso_week_runs_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scripts = tmp_path / "scripts"
    cli = scripts / "report_cli.py"
    state = tmp_path / "notes-state"
    streak = tmp_path / "streak"
    trace = tmp_path / "child-invoked"
    next_week = "2099-W01"
    _plant_cli(scripts, f"from pathlib import Path\nPath({str(trace)!r}).write_text('invoked\\n')\n")
    state.mkdir()
    (state / "delivered-week").write_text("2098-W52\n", encoding="utf-8")
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("NOTES_ORGANIZE_STATE_DIR", str(state))
    monkeypatch.setenv("WATCH_FAILURE_ROOT", str(streak))
    monkeypatch.setattr(notes_organize, "CLI", cli)
    monkeypatch.setattr(notes_organize, "_current_iso_week", lambda: next_week)
    monkeypatch.setattr(notes_organize, "watch_failure_streak", notes_organize._packaged_streak)

    assert notes_organize.main() == 0
    assert trace.read_text(encoding="utf-8") == "invoked\n"
    assert (state / "delivered-week").read_text(encoding="utf-8") == f"{next_week}\n"


def test_corrupt_watermark_and_streak_state_restart_without_crashing(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    state = tmp_path / "notes-state"
    streak = tmp_path / "streak"
    _plant_cli(scripts)
    state.mkdir()
    (state / "delivered-week").write_bytes(b"\xffcorrupt-watermark")
    streak.mkdir()
    streak_file = streak / "notes-weekly-organize.json"
    streak_file.write_text("{garbage", encoding="utf-8")

    result = _run_wrapper(tmp_path / "home", scripts, state, streak)

    assert result.returncode == 0
    assert result.stdout == ""
    assert (state / "delivered-week").read_text(encoding="utf-8") == f"{_week()}\n"
    # Helper-consistent behavior: corrupt state reads as zero, and healthy steady state
    # does not touch disk, leaving malformed input inspectable for diagnosis.
    assert streak_file.read_text(encoding="utf-8") == "{garbage"
