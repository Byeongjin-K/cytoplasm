"""Memory-curator notice compatibility through the real facade and draft reader."""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from automation import owner_notice
from automation.interop.owner_message import Action, OwnerMessage, Ref, Result
from automation.memory_curator import effects, reminder
from tests.unit.test_memory_curator_effects import _posted_state

LEGACY = (
    "🔔 승인 대기 1건 — 아래 링크에서 처리하세요(이 알림 자체는 승인이 아닙니다).\n"
    "- `ab12cd` [USER.md] '어떤 판단 근거' → https://discord.com/channels/@me/111/222"
)


def test_legacy_bytes_when_reminder_is_due(tmp_path: Path) -> None:
    # Given a posted draft with fixed coordinates and summary.
    state = _posted_state(tmp_path, summary="어떤 판단 근거")
    sent: list[str] = []
    # When the existing reminder entry point runs.
    ok = effects.send_pending_reminder(
        state, gate_dir=tmp_path, marker_path=tmp_path / "marker",
        now=datetime(2026, 8, 3, 1, tzinfo=UTC),
        alert=lambda content: sent.append(content) is None,
    )
    # Then the shipped bytes are preserved.
    assert ok and sent == [LEGACY]


def test_legacy_bytes_when_facade_has_no_capability(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given an older facade accepting only the original string argument.
    monkeypatch.delenv("MEMORY_CURATOR_DRY_RUN", raising=False)
    monkeypatch.delattr(owner_notice, "ACCEPTS_OWNER_MESSAGE", raising=False)
    sent: list[str] = []
    monkeypatch.setattr(owner_notice, "notify_owner", lambda content, **_: sent.append(content) is None)
    # When the curator reports through its production adapter.
    ok = effects.alert_owner(LEGACY)
    # Then old runtimes receive exactly the same bytes.
    assert ok and sent == [LEGACY]


def test_approval_draft_is_untouched_when_reminder_runs(tmp_path: Path) -> None:
    # Given a real persisted approval draft.
    state = _posted_state(tmp_path, summary="어떤 판단 근거")
    draft = tmp_path / "drafts" / "ab12cd.json"
    before = draft.read_bytes()
    # When the reminder is delivered independently of approval posting.
    effects.send_pending_reminder(
        state, gate_dir=tmp_path, marker_path=tmp_path / "marker",
        now=datetime(2026, 8, 3, 1, tzinfo=UTC), alert=lambda content: True,
    )
    # Then the persisted approval binding has not been edited or replaced.
    assert draft.read_bytes() == before


def test_marker_is_held_when_transport_refuses(tmp_path: Path) -> None:
    # Given a due reminder and a failed transport.
    state = _posted_state(tmp_path, summary="어떤 판단 근거")
    marker = tmp_path / "marker"
    # When delivery fails.
    ok = effects.send_pending_reminder(
        state, gate_dir=tmp_path, marker_path=marker,
        now=datetime(2026, 8, 3, 1, tzinfo=UTC), alert=lambda content: False,
    )
    # Then the next tick can retry.
    assert not ok and not marker.exists()


def test_envelope_fields_when_facade_accepts_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given a current facade and fixed, producer-owned content.
    monkeypatch.delenv("MEMORY_CURATOR_DRY_RUN", raising=False)
    sent: list[tuple[str, OwnerMessage | None]] = []

    def capture(content: str, *, message: OwnerMessage | None = None, fault: bool = False) -> bool:
        sent.append((content, message))
        return True

    monkeypatch.setattr(owner_notice, "notify_owner", capture)
    # When the curator sends a completed state check.
    ok = effects.alert_owner("near-cap body")
    # Then every field is independently fixed, including absent location/action fields.
    assert ok and sent == [("near-cap body", OwnerMessage(
        subject_key="memory-curator", subject="메모리 큐레이터 점검", fact="near-cap body",
        location=Ref(scope="none"), owner=Action(verb="none"),
        agent_next="다음 주기에 메모리 상태를 다시 확인합니다.",
        recovery="not_applicable", detail=Result(outcome="executed"),
    ))]


def test_legacy_bytes_when_envelope_import_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given a runtime where the optional envelope import raises ImportError.
    monkeypatch.delenv("MEMORY_CURATOR_DRY_RUN", raising=False)
    monkeypatch.setitem(sys.modules, "automation.interop.owner_message", None)
    sent: list[str] = []
    monkeypatch.setattr(owner_notice, "notify_owner", lambda content, **_: sent.append(content) is None)
    # When the production adapter attempts its lazy envelope import.
    ok = effects.alert_owner(LEGACY)
    # Then its legacy content still reaches the old sender unchanged.
    assert ok and sent == [LEGACY]


@pytest.mark.parametrize("case", [
    ("owner-dm", None, Ref(scope="message", space="dm", channel_id="111", message_id="222",
                          search=("Discord 검색", "ab12cd")), "https://discord.com/channels/@me/111/222"),
    ("agent-chat-thread", "333", Ref(scope="message", space="guild", guild_id="333",
                                     channel_id="111", message_id="222", search=("Discord 검색", "ab12cd")),
     "https://discord.com/channels/333/111/222"),
    ("agent-chat-thread", None, Ref(scope="message", space="guild", channel_id="111", message_id="222",
                                   search=("Discord 검색", "ab12cd")), "Discord 검색 / ab12cd"),
    (None, None, Ref(scope="message", space="unknown", channel_id="111", message_id="222",
                     search=("Discord 검색", "ab12cd")), "Discord 검색 / ab12cd"),
])
def test_draft_reference_when_stored_space_varies(
    tmp_path: Path, case: tuple[str | None, str | None, Ref, str],
) -> None:
    # Given actual wiki binding fields on disk (not a live directory lookup).
    surface, guild, expected, link = case
    state = _posted_state(tmp_path, summary="어떤 판단 근거")
    (tmp_path / "drafts" / "ab12cd.json").write_text(json.dumps({
        "surface": surface, "approval_guild_id": guild, "channel_id": "111",
        "confirm_message_id": "222", "summary": "어떤 판단 근거",
    }), encoding="utf-8")
    # When pending approvals cross the persisted boundary.
    pending = effects.pending_approvals(state, tmp_path)
    # Then coordinates retain their explicit space, never an inferred DM.
    assert len(pending) == 1 and pending[0].ref == expected
    assert link in reminder.render(pending)
    if surface != "owner-dm":
        assert "@me" not in reminder.render(pending)


REMINDER_ENVELOPE = (
    "대상: 메모리 큐레이터 점검 (memory-curator)\n"
    "사실: 🔔 승인 대기 1건 — 아래 링크에서 처리하세요(이 알림 자체는 승인이 아닙니다). "
    "- `ab12cd` [USER.md] '어떤 판단 근거' → https://discord.com/channels/@me/111/222 (실행 완료)\n"
    "위치: 해당 없음\n"
    "인계: 소유자: 조치 없음; 다음: 다음 주기에 메모리 상태를 다시 확인합니다.\n"
    "되돌리기: 해당 없음"
)


def test_rendered_bytes_when_reminder_reaches_facade(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given a persisted approval and the real facade, with only wire transport replaced.
    state = _posted_state(tmp_path, summary="어떤 판단 근거")
    monkeypatch.delenv("MEMORY_CURATOR_DRY_RUN", raising=False)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setattr(owner_notice, "owner_notice_channel", lambda: "444")
    sent: list[tuple[str, str]] = []

    def capture(token: str, channel: str, body: str) -> None:
        sent.append((channel, body))

    monkeypatch.setattr(owner_notice, "send_notice", capture)
    # When the real reminder entry point invokes the real notice adapter.
    ok = effects.send_pending_reminder(
        state, gate_dir=tmp_path, marker_path=tmp_path / "marker",
        now=datetime(2026, 8, 3, 1, tzinfo=UTC), alert=effects.alert_owner,
    )
    # Then the facade ships the independently frozen five-field payload.
    assert ok and sent == [("444", REMINDER_ENVELOPE)]


def test_notice_returns_false_when_facade_transport_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given a real facade with an unavailable wire transport.
    monkeypatch.delenv("MEMORY_CURATOR_DRY_RUN", raising=False)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setattr(owner_notice, "owner_notice_channel", lambda: "444")

    def fail(token: str, channel: str, body: str) -> None:
        raise OSError("transport unavailable")

    monkeypatch.setattr(owner_notice, "send_notice", fail)
    # When sending through the current envelope path.
    ok = effects.alert_owner(LEGACY)
    # Then the caller can retry without a raised exception.
    assert ok is False


@pytest.mark.parametrize("coordinates", [
    (None, "222"), ("111", None), (None, None), ("", "222"), ("111", ""),
])
def test_draft_search_reference_when_coordinates_are_missing(
    tmp_path: Path, coordinates: tuple[str | None, str | None],
) -> None:
    # Given an existing posted draft whose coordinates are null or empty.
    state = _posted_state(tmp_path, summary="어떤 판단 근거")
    channel, message = coordinates
    _ = (tmp_path / "drafts" / "ab12cd.json").write_text(json.dumps({
        "surface": "agent-chat-thread", "approval_guild_id": "333",
        "channel_id": channel, "confirm_message_id": message,
    }), encoding="utf-8")
    # When the persisted draft crosses the reminder boundary.
    pending = effects.pending_approvals(state, tmp_path)
    # Then the location has only a neutral label and title-free draft key.
    assert len(pending) == 1 and pending[0].ref == Ref(
        scope="resource", space="unknown", guild_id=None, channel_id=None,
        message_id=None, url=None, search=("Discord 검색", "ab12cd"),
    )


@pytest.mark.parametrize("missing_field", ["channel_id", "confirm_message_id"])
def test_reminder_is_delivered_when_coordinate_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing_field: str,
) -> None:
    # Given a posted draft with one absent coordinate and private wiki fields.
    state = _posted_state(tmp_path, summary="어떤 판단 근거")
    payload = {
        "surface": "agent-chat-thread", "approval_guild_id": "333",
        "channel_id": "111", "confirm_message_id": "222",
        "summary": "근거 abcdefghijklmnop1234", "title": "비공개 제목",
        "body": "비공개 위키 본문", "note_text": "비공개 노트 원문",
    }
    del payload[missing_field]
    _ = (tmp_path / "drafts" / "ab12cd.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.delenv("MEMORY_CURATOR_DRY_RUN", raising=False)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setattr(owner_notice, "owner_notice_channel", lambda: "444")
    sent: list[tuple[str, str]] = []

    def capture(token: str, channel: str, body: str) -> None:
        sent.append((channel, body))

    monkeypatch.setattr(owner_notice, "send_notice", capture)
    # When the real reminder entry point delivers through the real facade.
    ok = effects.send_pending_reminder(
        state, gate_dir=tmp_path, marker_path=tmp_path / "marker",
        now=datetime(2026, 8, 3, 1, tzinfo=UTC), alert=effects.alert_owner,
    )
    # Then delivery carries a search-only location, not a guessed link or private data.
    assert ok and len(sent) == 1
    assert sent[0][1].splitlines()[2] == "위치: 링크 없음 (주소 없음); 검색: Discord 검색 / ab12cd"
    assert sent == [("444", (
        "대상: 메모리 큐레이터 점검 (memory-curator)\n"
        "사실: 🔔 승인 대기 1건 — 아래 링크에서 처리하세요(이 알림 자체는 승인이 아닙니다). "
        "- `ab12cd` [USER.md] '근거 [REDACTED]' (실행 완료)\n"
        "위치: 링크 없음 (주소 없음); 검색: Discord 검색 / ab12cd\n"
        "인계: 소유자: 조치 없음; 다음: 다음 주기에 메모리 상태를 다시 확인합니다.\n"
        "되돌리기: 해당 없음"
    ))]
    assert "discord.com" not in sent[0][1]


@pytest.mark.parametrize("old_runtime", ["capability", "import"])
def test_search_context_preserves_multiple_refs_and_legacy_delivery(
    monkeypatch: pytest.MonkeyPatch, old_runtime: str,
) -> None:
    from automation.memory_curator.notice import build_notice

    # Given two search-only references crossing the unchanged string callback.
    content = reminder.render((
        reminder.PendingApproval("ab12cd", "USER.md", "", Ref(
            scope="resource", search=("Discord 검색", "ab12cd"))),
        reminder.PendingApproval("ef34ab", "USER.md", "", Ref(
            scope="resource", search=("Discord 검색", "ef34ab"))),
    ))
    legacy = str(content)
    message = build_notice(content)
    # Then neither key is lost in the aggregate envelope location.
    assert message is not None and message.location == Ref(
        scope="resource", url=None, search=("Discord 검색", "ab12cd, ef34ab"))
    # When the same value reaches either old-runtime boundary.
    monkeypatch.delenv("MEMORY_CURATOR_DRY_RUN", raising=False)
    if old_runtime == "capability":
        monkeypatch.delattr(owner_notice, "ACCEPTS_OWNER_MESSAGE")
    else:
        monkeypatch.setitem(sys.modules, "automation.interop.owner_message", None)
    sent: list[str] = []
    monkeypatch.setattr(owner_notice, "notify_owner", lambda content, **_: sent.append(content) is None)
    # Then transport still receives the identical legacy string bytes.
    assert effects.alert_owner(content) and sent == [legacy]


@pytest.mark.parametrize("coordinates", [
    ("bad", "222"), ("111", "bad"), ("bad", None), (None, "bad"),
    (123, "222"), ("111", 123), ("0", "222"), ("111", "０"),
    ("111", 0), ("111", False), (None, False),
])
def test_reminder_is_suppressed_when_present_coordinates_are_malformed(
    tmp_path: Path, coordinates: tuple[str | int | None, str | int | None],
) -> None:
    # Given a posted draft with a present but malformed coordinate.
    state = _posted_state(tmp_path, summary="어떤 판단 근거")
    channel, message = coordinates
    _ = (tmp_path / "drafts" / "ab12cd.json").write_text(json.dumps({
        "channel_id": channel, "confirm_message_id": message,
    }), encoding="utf-8")
    # When the persisted draft crosses the reminder boundary.
    pending = effects.pending_approvals(state, tmp_path)
    # Then malformed coordinates remain excluded, even beside a missing coordinate.
    assert pending == ()
