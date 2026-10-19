"""Digest rollover at both opening boundaries, separate from FS3-pinned tests."""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from tests.unit.test_digest_one_thread import (
    _KST, calendar_digest, digest_thread, triage_confirm,
    triage_digest_thread, triage_transport,
)
from tests.unit.test_digest_one_thread import _state as _state


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch, tmp_path):
    channels: dict[str, dict] = {}
    surface = calendar_digest.calendar_binding._surface()

    def resolve(kind, _directory, _owner, *, request):
        channel = str(1529000000000000100 + len(channels))
        channels[channel] = {"name": surface.request_thread_name(kind, request), "archived": False}
        return SimpleNamespace(channel_id=channel, surface="agent-chat-thread",
                               policy_version=8, guild_id="1529000000000000001", kind=kind)

    def api(method, path, payload=None):
        channel = path.rsplit("/", 1)[-1]
        if method == "GET":
            return channels[channel]
        assert method == "PATCH"
        channels[channel].update(payload)
        return {}

    monkeypatch.setattr(surface, "resolve_new_binding", resolve)
    monkeypatch.setattr(triage_digest_thread.triage_binding, "approval_directory", object)
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: "1529000000000000002")
    monkeypatch.setattr(triage_confirm, "_api", api)
    monkeypatch.setattr(triage_digest_thread.triage_gate, "list_drafts", lambda: [])
    monkeypatch.setattr(triage_transport, "_calendar_digest_pending", lambda ids: dict.fromkeys(ids, 0))
    monkeypatch.setenv("CALENDAR_GATE_DIR", str(tmp_path / "calendar"))
    monkeypatch.setattr(calendar_digest.calendar_binding, "approval_directory", object)
    monkeypatch.setattr(calendar_digest.calendar_confirm, "owner_id", lambda: "1529000000000000002")
    monkeypatch.setattr(calendar_digest.calendar_confirm, "_api", api)
    monkeypatch.setattr(calendar_digest.calendar_binding, "stored_binding", lambda value: SimpleNamespace(
        channel_id=value["channel_id"]))
    monkeypatch.setattr(calendar_digest, "pending_by_thread", lambda ids: dict.fromkeys(ids, 0))
    return channels


def test_mail_opening_next_day_archives_previous_as_done(opened):
    # Given: an older mail digest; a calendar-only digest must not age it out.
    first = triage_digest_thread.open_for(_KST, 1)
    for index in range(digest_thread.KEEP_DAYS):
        (digest_thread.state_dir() / f"reminder-{index}.json").write_text("{}")
    name = opened[first.channel_id]["name"]
    calendar_digest.daily_binding({"digest_day": "2026-10-19"})
    assert opened[first.channel_id]["archived"] is False
    # When: the next mail digest opens.
    triage_digest_thread.open_for(_KST + timedelta(days=1), 2)
    # Then: the previous mail thread is closed and its mark persisted.
    assert opened[first.channel_id] == {"name": f"✅ 완료 · {name}", "archived": True}
    assert digest_thread.load(first.day).mark == digest_thread.MARK_DONE


def test_standalone_calendar_opening_next_day_uses_shared_settle(opened, monkeypatch):
    # Given: an older calendar-only digest and a newer, unrelated mail digest.
    calls = []
    original = digest_thread.settle

    def settle(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(digest_thread, "settle", settle)
    first = calendar_digest.daily_binding({"digest_day": "2026-10-17"})
    name = opened[first.channel_id]["name"]
    mail = triage_digest_thread.open_for(_KST + timedelta(days=2), 1)
    assert opened[first.channel_id]["archived"] is False
    # When: the next calendar-only digest opens.
    calendar_digest.daily_binding({"digest_day": "2026-10-18"})
    # Then: only the previous calendar digest is closed, using the common policy.
    assert calls
    assert opened[first.channel_id] == {"name": f"✅ 완료 · {name}", "archived": True}
    assert opened[mail.channel_id]["archived"] is False


@pytest.mark.parametrize("pending,reason", [(1, "live-cards"), (None, "unknown-cards")])
def test_mail_rollover_keeps_live_or_unknown_cards_visible(opened, monkeypatch, capsys, pending, reason):
    # Given: the older thread has undecided cards or an unreadable card store.
    first = triage_digest_thread.open_for(_KST, 1)
    monkeypatch.setattr(triage_transport, "_calendar_digest_pending",
                        lambda ids: None if pending is None else dict.fromkeys(ids, pending))
    # When: the next mail digest opens.
    triage_digest_thread.open_for(_KST + timedelta(days=1), 1)
    # Then: the older thread stays visible and its skip reason is logged.
    assert opened[first.channel_id]["archived"] is False
    assert f"thread={first.channel_id} reason={reason}" in capsys.readouterr().err
