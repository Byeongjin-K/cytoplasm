"""Owner Q281 (2026-10-19): release operator items leave Discord; the healthcheck posts to the pair
channel only when a check keeps failing for 10 minutes. Plus the quote's unitless line-height.

A new file because these are new contracts: the healthcheck's pure incident tests stay as they are and
this file pins the sustain layer in front of them, the release sweep's inbox record and the
line-height floor.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from automation import healthcheck_notify, owner_notice, release_applied_state

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "mail" / "scripts"))
import mail_quote  # noqa: E402

_HEAD = "d" * 40


@pytest.mark.parametrize(("style", "kept"), [
    ("line-height:1.5", "line-height:1.5"),
    ("line-height:0.2", ""),
    ("line-height:3px", ""),
    ("line-height:18px", "line-height:18px"),
])
def test_a_unitless_line_height_is_a_multiple_not_pixels(style: str, kept: str) -> None:
    assert mail_quote._safe_style(style) == kept


def test_a_first_failure_is_not_posted_until_it_lasts_ten_minutes() -> None:
    # Given: no open incident and a check that just started failing
    state = healthcheck_notify.NotifyState()

    # When: the first sweep sees it, then a sweep 10 minutes later still sees it
    confirmed, seen = healthcheck_notify.sustained(state, failing=("db",), now=1000.0)
    later, _ = healthcheck_notify.sustained(
        healthcheck_notify.NotifyState(first_seen=seen), failing=("db",), now=1600.0,
    )

    # Then: only the later sweep counts it
    assert confirmed == ()
    assert later == ("db",)


def test_a_blink_restarts_the_wait() -> None:
    first = healthcheck_notify.NotifyState(first_seen=(("db", 1000.0),))
    _, after_recovery = healthcheck_notify.sustained(first, failing=(), now=1300.0)
    confirmed, _ = healthcheck_notify.sustained(
        healthcheck_notify.NotifyState(first_seen=after_recovery), failing=("db",), now=1700.0,
    )

    assert after_recovery == ()
    assert confirmed == ()


def test_an_open_incident_stays_counted() -> None:
    state = healthcheck_notify.NotifyState(open_incidents=("db",), first_seen=(("db", 1000.0),))

    confirmed, _ = healthcheck_notify.sustained(state, failing=("db",), now=1001.0)

    assert confirmed == ("db",)


def _capture_notices(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    faults: list[bool] = []

    def capture(notice: str, *, message: object = None, fault: bool = False) -> bool:
        faults.append(fault)
        return True

    monkeypatch.setattr(owner_notice, "notify_owner", capture)
    return faults


def test_the_sweep_posts_once_only_after_ten_minutes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "state.json"
    monkeypatch.setenv("HEALTHCHECK_NOTIFY_STATE", str(path))
    faults = _capture_notices(monkeypatch)
    clock = iter((1000.0, 1300.0, 1600.0, 1900.0))
    monkeypatch.setattr(healthcheck_notify.time, "time", lambda: next(clock))

    for _ in range(4):
        assert healthcheck_notify.main(["db|local|node|ops|probe"]) == 0

    assert faults == [True]
    assert healthcheck_notify.load_state(path).open_incidents == ("db",)


def _sweep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, rc: int) -> list[tuple[str, ...]]:
    commands: list[tuple[str, ...]] = []

    def run(command: tuple[str, ...], *, env: dict[str, str], check: bool) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, rc)

    monkeypatch.setattr(release_applied_state.subprocess, "run", run)
    monkeypatch.setenv("RELEASE_APPROVAL_CMD", "fake-sender")
    monkeypatch.setenv("RELEASE_NOTES_DIR", str(tmp_path / "notes"))
    pending = ("남은 소유자 조치:", "- 헬스체크 허용 목록 갱신 실패 — node: 지문 불일치")
    release_applied_state.Sweep(tmp_path / "state", tmp_path, _HEAD, _HEAD, pending)._notify(_HEAD, "v2.6.6")
    return commands


def test_release_items_go_to_the_inbox_and_never_to_the_node_sender(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    inbox = tmp_path / "inbox.jsonl"
    monkeypatch.setenv("RELEASE_OPS_INBOX", str(inbox))

    commands = _sweep(tmp_path, monkeypatch, rc=0)

    assert commands == [("fake-sender", "send", "--version", "v2.6.6", "--head", _HEAD)]
    row = json.loads(inbox.read_text(encoding="utf-8"))
    assert row["kind"] == "release-ops"
    assert row["id"].startswith("release-ops-")
    assert "지문 불일치" in row["content"] and "v2.6.6" in row["content"]
    assert "RELEASE-OPS v2.6.6" in capsys.readouterr().out


def test_a_failed_send_records_nothing_so_the_retry_does_not_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    inbox = tmp_path / "inbox.jsonl"
    monkeypatch.setenv("RELEASE_OPS_INBOX", str(inbox))

    _ = _sweep(tmp_path, monkeypatch, rc=3)

    assert not inbox.exists()


def test_without_an_inbox_the_items_stay_in_the_release_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("RELEASE_OPS_INBOX", raising=False)

    _ = _sweep(tmp_path, monkeypatch, rc=0)

    assert "RELEASE-OPS v2.6.6" in capsys.readouterr().out
