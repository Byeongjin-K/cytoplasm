"""Default color regression tests in a new file to preserve FS3 captures."""
from __future__ import annotations

import argparse
import json
import sys
from importlib import import_module
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills/calendar/scripts"))
calendar_cli = import_module("calendar_cli")
calendar_core = import_module("calendar_core")
calendar_card = import_module("calendar_card")
calendar_gate = import_module("calendar_gate")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setenv("CALENDAR_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("CALENDAR_PEERS_CONFIG", str(tmp_path / "absent-peers.yaml"))
    return tmp_path / "hermes"


def _draft():
    return calendar_cli._build_create_draft(argparse.Namespace(
        text="2031-10-01 오전 9시 예시 회의", summary="예시 회의",
        calendar="primary", channel_id="dm",
    ))


def _marker(home, value):
    home.mkdir(parents=True, exist_ok=True)
    (home / "calendar-default-color.mode").write_bytes(value)


def test_unset_keeps_legacy_argv_hash_and_card(home):
    # Given: no released color setting.
    request = calendar_core.parse_request(
        "2031-10-01 오전 9시 예시 회의",
        calendar_core.datetime(2030, 1, 1, tzinfo=calendar_core.KST),
    )
    old = calendar_gate.build_draft(
        action="create", argv=calendar_core.build_create_argv("primary", request),
        calendar_id="primary", event_id="", summary=request.summary,
        start=request.start.isoformat(), end=request.end.isoformat(), channel_id="dm",
    )
    # When
    actual = _draft()
    # Then: compare bytes of the frozen command, content hash and shipped card.
    assert actual["argv"] == old["argv"]
    assert actual["sha256"] == old["sha256"]
    # Captured from the untouched upstream builder for this invented request.
    assert actual["sha256"] == "440205451b3ac893489429f2fd41cb566b94201f15b3c1426acbfb2f5fd869dc"
    actual["id"] = old["id"]
    assert calendar_card.prepare_draft(actual) == calendar_card.prepare_draft(old) | {
        "created": actual["created"],
    }


@pytest.mark.parametrize("color", [str(value) for value in range(1, 12)])
def test_released_color_is_frozen_in_insert_and_displayed(home, color):
    # Given
    _marker(home, color.encode())
    # When
    draft = _draft()
    prepared = calendar_card.prepare_draft(draft)
    # Then: the card value is derived from the same hash-bound command.
    body = json.loads(draft["argv"][-1])
    assert body["colorId"] == color
    assert prepared["render_version"] == "5"
    from automation.calendar_default_color import color_label

    assert f"색: {color_label(color)}" in prepared["approval_content"]
    assert f"sha256:{draft['sha256']}" in prepared["approval_content"]


@pytest.mark.parametrize("value", [b"", b"0", b"12", b"02", b"2\n", b" 2", b"off\n"])
def test_invalid_node_marker_refuses_draft(home, value):
    # Given
    _marker(home, value)
    # When / Then
    with pytest.raises(calendar_gate.GateError):
        _draft()


def test_off_marker_keeps_old_command(home):
    # Given
    before = _draft()
    _marker(home, b"off")
    # When
    after = _draft()
    # Then
    assert after["argv"] == before["argv"]
    assert after["sha256"] == before["sha256"]


def test_switch_change_does_not_recolor_frozen_draft(home):
    # Given
    _marker(home, b"2")
    draft = _draft()
    _marker(home, b"11")
    # When
    prepared = calendar_card.prepare_draft(draft)
    # Then: no live setting lookup at approval time.
    assert json.loads(prepared["argv"][-1])["colorId"] == "2"
    assert "색: 세이지" in prepared["approval_content"]
    changed = {**draft, "argv": [*draft["argv"][:-1], draft["argv"][-1].replace('"2"', '"11"')]}
    assert calendar_core.draft_sha256(changed) != draft["sha256"]
    with pytest.raises(calendar_gate.GateError):
        calendar_card.prepare_draft(changed)


def test_patch_does_not_add_default_color(home):
    # Given
    _marker(home, b"2")
    # When
    argv = calendar_core.build_patch_argv("primary", "synthetic-event", {"summary": "예시 변경"})
    # Then
    assert json.loads(argv[-1]) == {"summary": "예시 변경"}


def test_v5_pending_round_trip(home):
    # Given
    from calendar_pending import PendingConfirm, PendingConfirmStore
    from datetime import UTC, datetime

    entry = PendingConfirm(
        draft_id="abc123", sha256="digest", dm_channel_id="222", dm_message_id="333",
        created=datetime(2030, 1, 1, tzinfo=UTC), render_version="5",
    )
    store = PendingConfirmStore(home / "pending.jsonl")
    # When
    store.append(entry)
    # Then
    assert store.load() == (entry,)


def test_governed_loader_uses_explicit_runtime_before_installed_release(home, monkeypatch):
    # Given: a staged runtime must not load helpers from a different installed release.
    governed = import_module("calendar_governed")
    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(home / "runtime"))
    # When
    root = governed._automation_root()
    # Then
    assert root == home / "runtime"
