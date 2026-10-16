from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest

from automation import owner_notice
from automation.interop.approval_surface import POLICY_VERSION, ApprovalKind, RequestThread, request_thread_notice

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "skills" / "calendar" / "scripts"
os.environ["CALENDAR_SCRIPTS"] = str(_SCRIPTS)
sys.path.insert(0, str(_SCRIPTS))

calendar_cli = import_module("calendar_cli")
calendar_confirm = import_module("calendar_confirm")
calendar_gate = import_module("calendar_gate")
_pending = import_module("calendar_pending")
PendingConfirm = _pending.PendingConfirm
PendingConfirmStore = _pending.PendingConfirmStore
OWNER_DM_CHANNEL_ID = "1526487936065952385"
AGENT_CHAT_CHANNEL_ID = "1526487936065952390"
AGENT_CHAT_THREAD_ID = "1526487936065952391"
AGENT_CHAT_GUILD_ID = "1526487936065952392"
REQUEST_THREAD_ID = "1526487936065952400"
REQUEST_NOTICE_MESSAGE_ID = "1526487936065952401"


def _load_watch_module():
    spec = importlib.util.spec_from_file_location(
        "calendar_confirm_reaction_watch", _SCRIPTS / "confirm_reaction_watch.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


watch = _load_watch_module()


@dataclass
class FakeDiscord:
    reactions: dict[str, tuple[dict[str, str | bool], ...]]
    content: str = "calendar confirmation sha256:sha-123"
    sent_messages: list[str] = field(default_factory=list)
    message_reads: int = 0
    reaction_reads: list[str] = field(default_factory=list)

    def message_content(self, _entry: PendingConfirm) -> str:
        self.message_reads += 1
        return self.content

    def reaction_users(
        self, _entry: PendingConfirm, emoji: str
    ) -> tuple[dict[str, str | bool], ...]:
        self.reaction_reads.append(emoji)
        return self.reactions.get(emoji, ())

    def send_owner_dm(self, content: str) -> None:
        """리마인더 배달 전용 — 결과·정리 통지는 `owner_notice` 파사드가 가져갔다(2026-09-10)."""
        self.sent_messages.append(content)


@dataclass
class FakeCommands:
    confirmed: list[PendingConfirm] = field(default_factory=list)
    discarded: list[str] = field(default_factory=list)

    def confirm(self, entry: PendingConfirm, _owner_id: str) -> None:
        self.confirmed.append(entry)

    def discard(self, draft_id: str) -> None:
        self.discarded.append(draft_id)


def _entry(*, created: datetime | None = None) -> PendingConfirm:
    return PendingConfirm(
        draft_id="abc123",
        sha256="sha-123",
        dm_channel_id="dm-1",
        dm_message_id="msg-1",
        created=created or datetime(2026, 7, 17, tzinfo=UTC),
    )


def _run(
    tmp_path: Path,
    reactions: dict[str, tuple[dict[str, str | bool], ...]],
    *,
    created: datetime | None = None,
    content: str = "calendar confirmation sha256:sha-123",
    draft_hash: str = "sha-123",
    record: dict | None = None,
) -> tuple[PendingConfirmStore, FakeDiscord, FakeCommands]:
    store = PendingConfirmStore(tmp_path / "pending-confirms.jsonl")
    store.append(_entry(created=created))
    discord = FakeDiscord(reactions, content)
    commands = FakeCommands()
    watch.run_once(
        store=store,
        owner_id="cha-owner",
        discord=discord,
        commands=commands,
        draft_sha256=lambda _draft_id: draft_hash,
        draft_record=lambda _draft_id: {"action": "delete", "id": "abc123"} if record is None else record,
        now=datetime(2026, 7, 17, 12, 0, tzinfo=UTC),
    )
    return store, discord, commands


@pytest.fixture(autouse=True)
def _isolated_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """이 스위트는 실행하는 사람의 실제 캘린더 게이트를 절대 읽지 않는다.

    `run_once` 는 고아 초안 스윕을 함께 돌리고, 그 스윕은 2026-09-10 부터 카드가 없는
    초안에 **실제로 카드를 올린다**. 이 파일의 `_run` 은 `CALENDAR_GATE_DIR` 을 설정하지
    않으므로 격리가 없으면 스윕이 `~/.hermes/calendar-gate` 를 읽는다 — 실측으로 이
    워크스테이션에 pending 초안이 398건 있고, `now=datetime.now(UTC)` 로 도는 케이스는
    그중 3분~24시간 나이의 초안에 대해 실제 `request_confirmation` 을 호출했다(토큰이
    없어 실패했을 뿐이다). 스윕 입력이 필요한 케이스는 `list_drafts=` 로 명시 주입한다 —
    그 자리는 `test_calendar_orphan_draft_sweep.py` 다.
    """
    monkeypatch.setattr(calendar_gate, "list_drafts", lambda: [])


@pytest.fixture(autouse=True)
def notices(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """스레드가 없는 결과 통지는 `owner_notice` 파사드로 나간다(2026-09-10 소유자 지시).

    목적지(#notifications ↔ 소유자 DM)는 파사드가 설정에서 정하므로 여기서는 **무엇이
    나갔는지**만 본다 — 목적지 해석은 `test_calendar_owner_notice_routing.py` 가 고정한다.
    """
    delivered: list[str] = []
    monkeypatch.setattr(
        owner_notice, "notify_owner", lambda notice, *, message=None: delivered.append(notice) is None
    )
    return delivered


def test_confirm_when_owner_has_only_approve_reaction(tmp_path: Path) -> None:
    # Given
    store, _discord, commands = _run(tmp_path, {"✅": ({"id": "cha-owner", "bot": False},)})

    # When / Then
    assert [entry.draft_id for entry in commands.confirmed] == ["abc123"]
    assert commands.discarded == []
    assert store.load() == ()


def test_discard_without_confirm_when_owner_has_cancel_reaction(
    tmp_path: Path, notices: list[str]
) -> None:
    # Given
    store, discord, commands = _run(tmp_path, {"⛔": ({"id": "cha-owner", "bot": False},)})

    # When / Then
    assert commands.confirmed == []
    assert commands.discarded == ["abc123"]
    assert discord.sent_messages == []  # 워처가 직접 DM 을 열지 않는다
    [notice] = notices
    assert "캘린더 삭제 취소" in notice
    assert "abc123" in notice
    assert "소유자 ⛔ 리액션으로 취소되었습니다" in notice
    assert store.load() == ()


def test_cancel_wins_when_owner_has_both_reactions(tmp_path: Path) -> None:
    # Given
    reactions = {
        "✅": ({"id": "cha-owner", "bot": False},),
        "⛔": ({"id": "cha-owner", "bot": False},),
    }

    # When
    _store, _discord, commands = _run(tmp_path, reactions)

    # Then
    assert commands.confirmed == []
    assert commands.discarded == ["abc123"]


def test_ignore_bot_and_non_owner_reactions(tmp_path: Path) -> None:
    # Given
    reactions = {
        "✅": (
            {"id": "cha-owner", "bot": True},
            {"id": "other-user", "bot": False},
        )
    }

    # When
    store, _discord, commands = _run(tmp_path, reactions)

    # Then
    assert commands.confirmed == []
    assert commands.discarded == []
    assert len(store.load()) == 1


def test_discard_when_confirmation_expires(tmp_path: Path, notices: list[str]) -> None:
    # Given
    expired = datetime(2026, 7, 15, 11, 59, tzinfo=UTC)

    # When
    store, discord, commands = _run(tmp_path, {}, created=expired)

    # Then
    assert commands.confirmed == []
    assert commands.discarded == ["abc123"]
    assert discord.sent_messages == []  # 워처가 직접 DM 을 열지 않는다
    [notice] = notices
    assert "캘린더 삭제 만료 취소" in notice
    assert "abc123" in notice
    assert "확정 시간이 지나 취소되었습니다" in notice
    assert store.load() == ()


def test_retain_when_draft_or_dm_hash_binding_mismatches(tmp_path: Path) -> None:
    # Given / When
    draft_store, _draft_discord, draft_commands = _run(
        tmp_path / "draft", {"✅": ({"id": "cha-owner", "bot": False},)}, draft_hash="other"
    )
    dm_store, _dm_discord, dm_commands = _run(
        tmp_path / "dm", {"✅": ({"id": "cha-owner", "bot": False},)}, content="sha256:other"
    )

    # Then
    assert draft_commands.confirmed == [] and draft_commands.discarded == []
    assert dm_commands.confirmed == [] and dm_commands.discarded == []
    assert len(draft_store.load()) == 1
    assert len(dm_store.load()) == 1


def test_pending_store_is_owner_readable_only(tmp_path: Path) -> None:
    # Given
    store = PendingConfirmStore(tmp_path / "pending-confirms.jsonl")

    # When
    store.append(_entry())

    # Then
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


def test_post_confirm_posts_reactions_and_records_bound_pending_entry(tmp_path: Path, monkeypatch) -> None:
    # Given
    monkeypatch.setenv("CALENDAR_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("CALENDAR_PENDING_CONFIRMS", str(tmp_path / "pending-confirms.jsonl"))
    config = tmp_path / "interop.json"
    config.write_text(json.dumps({"agent_chat_channel_id": AGENT_CHAT_CHANNEL_ID}), encoding="utf-8")
    monkeypatch.setenv("INTEROP_CONFIG", str(config))
    draft = calendar_gate.create_draft(
        action="delete", argv=calendar_cli.calendar_core.build_delete_argv("primary", "evt1"), calendar_id="primary",
        event_id="evt1", summary="private", start="", end="", channel_id="dm",
    )
    calls: list[tuple[str, str, dict[str, str] | None]] = []

    def fake_api(method: str, path: str, payload: dict[str, str] | None = None):
        calls.append((method, path, payload))
        if path == "/users/@me/channels":
            return {"id": OWNER_DM_CHANNEL_ID}
        if path == f"/channels/{OWNER_DM_CHANNEL_ID}":
            return {"id": OWNER_DM_CHANNEL_ID, "name": "", "recipients": [{"id": "cha-owner"}], "type": 1}
        if path == f"/channels/{AGENT_CHAT_CHANNEL_ID}":
            return {"id": AGENT_CHAT_CHANNEL_ID, "guild_id": AGENT_CHAT_GUILD_ID, "name": "agent-chat", "type": 0}
        if method == "POST" and path == f"/channels/{AGENT_CHAT_CHANNEL_ID}/messages":
            return {"id": REQUEST_NOTICE_MESSAGE_ID}
        if method == "POST" and path == (
            f"/channels/{AGENT_CHAT_CHANNEL_ID}/messages/{REQUEST_NOTICE_MESSAGE_ID}/threads"
        ):
            return {"id": REQUEST_THREAD_ID}
        if path == f"/channels/{REQUEST_THREAD_ID}":
            return {"id": REQUEST_THREAD_ID, "name": f"🗓️ 캘린더 · {draft['id']}",
                    "parent_id": AGENT_CHAT_CHANNEL_ID, "type": 11}
        if path == f"/channels/{REQUEST_THREAD_ID}/messages":
            return {"id": "msg-1"}
        return None

    monkeypatch.setattr(calendar_confirm, "owner_id", lambda: "cha-owner")
    monkeypatch.setattr(calendar_confirm, "_api", fake_api)

    # When
    monkeypatch.setattr(sys, "argv", ["calendar_cli", "post-confirm", "--draft", draft["id"]])
    assert calendar_cli.main() == 0

    # Then
    entry = PendingConfirmStore().load()
    assert entry == (
        PendingConfirm(
            draft_id=draft["id"], sha256=draft["sha256"], dm_channel_id=REQUEST_THREAD_ID,
            dm_message_id="msg-1", created=entry[0].created,
        ),
    )
    assert entry[0].channel_id == REQUEST_THREAD_ID
    assert entry[0].surface == "agent-chat-thread"
    assert entry[0].policy_version == POLICY_VERSION
    assert [
        call for call in calls
        if call[1].startswith(f"/channels/{AGENT_CHAT_CHANNEL_ID}/messages")
    ] == [
        ("POST", f"/channels/{AGENT_CHAT_CHANNEL_ID}/messages", {
            "content": request_thread_notice(ApprovalKind.CALENDAR, RequestThread(draft['id'])),
        }),
        ("POST", f"/channels/{AGENT_CHAT_CHANNEL_ID}/messages/{REQUEST_NOTICE_MESSAGE_ID}/threads", {
            "name": f"🗓️ 캘린더 · {draft['id']}", "auto_archive_duration": 10080,
        }),
    ]
    assert [call for call in calls if call[0] == "PUT"] == [
            ("PUT", f"/channels/{REQUEST_THREAD_ID}/messages/msg-1/reactions/%E2%9C%85/@me", None),
            ("PUT", f"/channels/{REQUEST_THREAD_ID}/messages/msg-1/reactions/%E2%9B%94/@me", None),
    ]



def test_cancel_discard_purges_entry_when_owner_notify_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given the owner's ⛔ where the notice cannot be delivered (파사드가 False 로 답한다)
    monkeypatch.setattr(owner_notice, "notify_owner", lambda notice: False)
    store, _discord, commands = _run(tmp_path, {"⛔": ({"id": "cha-owner", "bot": False},)})

    # When / Then — the successful discard is not rolled back by the notify failure
    assert commands.discarded == ["abc123"]
    assert store.load() == ()


def test_expiry_discard_with_notify_failure_leaves_no_stale_entry_for_next_tick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given an expired entry whose cancellation notice cannot be delivered (파사드가 False)
    expired = datetime(2026, 7, 15, 11, 59, tzinfo=UTC)
    monkeypatch.setattr(owner_notice, "notify_owner", lambda notice: False)
    store, _discord, commands = _run(tmp_path, {}, created=expired)
    assert commands.discarded == ["abc123"]

    # When the next tick runs against a resolver that fails for missing drafts
    second_commands = FakeCommands()

    def missing_draft(_draft_id: str) -> str:
        raise watch.ConfirmWatchError("pending draft is unavailable")

    watch.run_once(
        store=store,
        owner_id="cha-owner",
        discord=FakeDiscord({}),
        commands=second_commands,
        draft_sha256=missing_draft,
        now=datetime(2026, 7, 17, 12, 1, tzinfo=UTC),
    )

    # Then the store stays empty and nothing is re-processed
    assert store.load() == ()
    assert second_commands.discarded == []


def test_cli_discard_purges_pending_confirm_entry(tmp_path: Path, monkeypatch) -> None:
    # Given a draft bound to a pending confirmation entry
    monkeypatch.setenv("CALENDAR_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("CALENDAR_PENDING_CONFIRMS", str(tmp_path / "pending-confirms.jsonl"))
    draft = calendar_gate.create_draft(
        action="delete", argv=("gws", "calendar", "events", "delete"), calendar_id="primary",
        event_id="evt1", summary="private", start="", end="", channel_id="dm",
    )
    PendingConfirmStore().append(
        PendingConfirm(
            draft_id=draft["id"], sha256=draft["sha256"], dm_channel_id="dm-1",
            dm_message_id="msg-1", created=datetime(2026, 7, 17, tzinfo=UTC),
        )
    )

    # When
    monkeypatch.setattr(sys, "argv", ["calendar_cli", "discard", "--draft", draft["id"]])
    assert calendar_cli.main() == 0

    # Then the pending entry is purged together with the draft
    assert PendingConfirmStore().load() == ()


def _draft_with_pending(tmp_path: Path, monkeypatch, *, message_id: str = "msg-1"):
    monkeypatch.setenv("CALENDAR_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("CALENDAR_PENDING_CONFIRMS", str(tmp_path / "pending-confirms.jsonl"))
    draft = calendar_gate.create_draft(
        action="delete", argv=("gws", "calendar", "events", "delete"), calendar_id="primary",
        event_id="evt1", summary="private", start="", end="", channel_id="dm",
    )
    entry = PendingConfirm(
        draft_id=draft["id"], sha256=draft["sha256"], dm_channel_id="dm-1",
        dm_message_id=message_id, created=datetime.now(UTC),
    )
    PendingConfirmStore().append(entry)
    return draft, entry


def test_watcher_approval_queries_discord_once_then_child_uses_only_bound_artifact(
    tmp_path: Path, monkeypatch
) -> None:
    # Given a real pending draft and an owner approval visible to the watcher
    draft, _entry = _draft_with_pending(tmp_path, monkeypatch)
    discord = FakeDiscord(
        {"✅": ({"id": "cha-owner", "bot": False},)},
        content=f"calendar confirmation sha256:{draft['sha256']}",
    )
    executions: list[str] = []
    monkeypatch.setattr(calendar_confirm, "owner_id", lambda: "cha-owner")
    monkeypatch.setattr(
        calendar_confirm, "_api",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("duplicate Discord query")),
    )
    monkeypatch.setattr(
        calendar_gate, "execute_draft",
        lambda current, _approval: executions.append(current["id"]) or current["event_id"],
    )
    monkeypatch.setattr(
        calendar_cli.calendar_preflight,
        "read_event",
        lambda _calendar_id, event_id: {"id": event_id, "summary": draft["summary"]},
    )

    class InProcessCommands:
        def confirm(self, current: PendingConfirm, owner: str) -> None:
            authorization = calendar_confirm.create_watcher_authorization(current, owner)
            args = SimpleNamespace(
                draft=current.draft_id, injection_file="", watch_authorization=str(authorization)
            )
            assert calendar_cli.cmd_confirm(args) == 0

        def discard(self, _draft_id: str) -> None:
            raise AssertionError("approval must not discard")

    # When
    watch.run_once(
        store=PendingConfirmStore(), owner_id="cha-owner", discord=discord,
        commands=InProcessCommands(), draft_sha256=lambda _draft_id: draft["sha256"],
        now=datetime.now(UTC),
    )

    # Then: one exact-message read + one read for each reaction; child makes none.
    assert discord.message_reads == 1
    assert discord.reaction_reads == ["⛔", "✅"]
    assert executions == [draft["id"]]
    assert PendingConfirmStore().load() == ()


def test_watcher_authorization_is_one_use_and_cannot_be_replayed(tmp_path: Path, monkeypatch) -> None:
    draft, entry = _draft_with_pending(tmp_path, monkeypatch)
    monkeypatch.setattr(calendar_confirm, "owner_id", lambda: "cha-owner")
    authorization = calendar_confirm.create_watcher_authorization(entry, "cha-owner")
    copied_authorization = authorization.with_name("copied.json")
    copied_authorization.write_bytes(authorization.read_bytes())
    copied_authorization.chmod(0o600)
    executions: list[str] = []
    monkeypatch.setattr(
        calendar_gate, "execute_draft",
        lambda current, _approval: executions.append(current["id"]) or current["event_id"],
    )
    monkeypatch.setattr(
        calendar_cli.calendar_preflight,
        "read_event",
        lambda _calendar_id, event_id: {"id": event_id, "summary": draft["summary"]},
    )
    args = SimpleNamespace(
        draft=draft["id"], injection_file="", watch_authorization=str(authorization)
    )

    assert calendar_cli.cmd_confirm(args) == 0
    assert executions == [draft["id"]]
    assert not authorization.exists()

    # Re-creating pending cannot make a pre-copied token with the same nonce usable.
    PendingConfirmStore().append(entry)
    monkeypatch.setattr(sys, "argv", [
        "calendar_cli", "confirm", "--draft", draft["id"],
        "--watch-authorization", str(copied_authorization),
    ])
    assert calendar_cli.main() != 0
    assert executions == [draft["id"]]


def test_direct_confirm_without_artifact_keeps_independent_discord_validation(
    tmp_path: Path, monkeypatch
) -> None:
    draft, _entry = _draft_with_pending(tmp_path, monkeypatch)
    calls: list[str] = []
    monkeypatch.setattr(calendar_confirm, "owner_id", lambda: "cha-owner")
    monkeypatch.setattr(calendar_confirm, "reject_cancel_reaction", lambda _draft: calls.append("cancel"))

    def missing_text(_draft):
        calls.append("text")
        raise calendar_gate.GateError("no text", 1)

    monkeypatch.setattr(calendar_confirm, "confirm_via_owner_scan", missing_text)
    monkeypatch.setattr(
        calendar_confirm, "confirm_via_reaction",
        lambda _draft: calls.append("reaction") or "reaction:msg-1",
    )
    monkeypatch.setattr(
        calendar_gate, "execute_draft",
        lambda current, _approval: calls.append("execute") or current["event_id"],
    )
    monkeypatch.setattr(
        calendar_cli.calendar_preflight,
        "read_event",
        lambda _calendar_id, event_id: {"id": event_id, "summary": draft["summary"]},
    )
    args = SimpleNamespace(draft=draft["id"], injection_file="", watch_authorization="")

    assert calendar_cli.cmd_confirm(args) == 0
    assert calls == ["cancel", "text", "reaction", "execute"]


def test_forged_or_cross_message_watcher_authorization_never_executes(
    tmp_path: Path, monkeypatch
) -> None:
    draft, entry = _draft_with_pending(tmp_path, monkeypatch)
    monkeypatch.setattr(calendar_confirm, "owner_id", lambda: "cha-owner")
    executions: list[str] = []
    monkeypatch.setattr(
        calendar_gate, "execute_draft",
        lambda current, _approval: executions.append(current["id"]) or current["event_id"],
    )

    forged = calendar_confirm.create_watcher_authorization(entry, "cha-owner")
    payload = json.loads(forged.read_text(encoding="utf-8"))
    payload["dm_message_id"] = "other-message"
    forged.write_text(json.dumps(payload), encoding="utf-8")
    forged.chmod(0o600)
    forged_args = SimpleNamespace(
        draft=draft["id"], injection_file="", watch_authorization=str(forged)
    )
    try:
        calendar_cli.cmd_confirm(forged_args)
    except calendar_gate.GateError as error:
        assert error.exit_code == 1
    else:
        raise AssertionError("forged authorization was accepted")

    other_draft, other_entry = _draft_with_pending(tmp_path, monkeypatch, message_id="msg-2")
    cross = calendar_confirm.create_watcher_authorization(other_entry, "cha-owner")
    cross_args = SimpleNamespace(
        draft=draft["id"], injection_file="", watch_authorization=str(cross)
    )
    try:
        calendar_cli.cmd_confirm(cross_args)
    except calendar_gate.GateError as error:
        assert error.exit_code == 1
    else:
        raise AssertionError("cross-draft authorization was accepted")

    assert other_draft["id"] != draft["id"]
    assert executions == []


# ------------------------------------------- origin-channel thread result routing

ORIGIN_CHANNEL = "200000000000000009"
ORIGIN_MESSAGE = "410000000000000009"
THREAD_ID = "300000000000000009"
SECRET_SUMMARY = "비공개 진료 예약"
SECRET_EVENT_ID = "evt-secret-9"
SECRET_START = "2026-07-18T15:00:00+09:00"
SECRET_END = "2026-07-18T16:00:00+09:00"
SECRET_CALENDAR = "secret-calendar@group.calendar.google.com"


def _origin_record(**overrides: str) -> dict:
    """A pending draft carrying both the origin binding and calendar content."""
    return {
        "action": "create",
        "argv": ["gws", "calendar", "events", "insert"],
        "calendar_id": SECRET_CALENDAR,
        "channel_id": "dm",
        "created": "2026-07-17T00:00:00Z",
        "end": SECRET_END,
        "event_id": SECRET_EVENT_ID,
        "id": "abc123",
        "origin_channel_id": ORIGIN_CHANNEL,
        "origin_message_id": ORIGIN_MESSAGE,
        "sha256": "sha-123",
        "start": SECRET_START,
        "status": "pending",
        "summary": SECRET_SUMMARY,
        **overrides,
    }


def _assert_calendar_content_masked(text: str) -> None:
    """캘린더 내용은 cha DM 밖으로 나가지 않는다 — 스레드 문구/이름 공통 규칙."""
    for secret in (SECRET_SUMMARY, SECRET_START, SECRET_END, SECRET_EVENT_ID, SECRET_CALENDAR):
        assert secret not in text


class _SentChunk:
    def __init__(self, message_id: str) -> None:
        self.message_id = message_id


def _run_origin(
    tmp_path: Path,
    monkeypatch,
    reactions: dict[str, tuple[dict[str, str | bool], ...]],
    *,
    record: dict,
    thread_fail: bool = False,
    created: datetime | None = None,
) -> SimpleNamespace:
    thread_names: list[str] = []
    thread_posts: list[tuple[str, str]] = []
    dm_notices: list[tuple[str, str]] = []
    threads_path = f"/channels/{ORIGIN_CHANNEL}/messages/{ORIGIN_MESSAGE}/threads"

    def api(method: str, path: str, payload: dict[str, str] | None = None):
        if method == "POST" and path == threads_path:
            if thread_fail:
                raise RuntimeError("thread API down")
            thread_names.append(str((payload or {})["name"]))
            return {"id": THREAD_ID}
        raise AssertionError(f"unexpected Discord call: {method} {path}")

    class _ThreadTransport:
        def __init__(self, channel_id: str) -> None:
            self.channel_id = channel_id

        def send(self, content: str) -> tuple[_SentChunk, ...]:
            thread_posts.append((self.channel_id, content))
            return (_SentChunk("thread-post-1"),)

    monkeypatch.setattr(calendar_confirm, "owner_id", lambda: "cha-owner")
    monkeypatch.setattr(calendar_confirm, "_api", api)
    monkeypatch.setattr(calendar_confirm, "_thread_transport", _ThreadTransport)
    monkeypatch.setattr(
        calendar_confirm, "send_owner_dm",
        lambda owner, content: dm_notices.append((owner, content)),
    )
    store = PendingConfirmStore(tmp_path / "pending-confirms.jsonl")
    store.append(_entry(created=created))
    discord = FakeDiscord(reactions, f"calendar confirmation sha256:{record['sha256']}")
    commands = FakeCommands()
    watch.run_once(
        store=store,
        owner_id="cha-owner",
        discord=discord,
        commands=commands,
        draft_sha256=lambda _draft_id: str(record["sha256"]),
        draft_record=lambda _draft_id: record,
        now=datetime(2026, 7, 17, 12, 0, tzinfo=UTC),
    )
    return SimpleNamespace(
        store=store, discord=discord, commands=commands,
        thread_names=thread_names, thread_posts=thread_posts, dm_notices=dm_notices,
    )


def test_origin_bound_approval_posts_masked_result_to_the_origin_thread(
    tmp_path: Path, monkeypatch
) -> None:
    # Given an origin-bound draft the owner approves with ✅
    record = _origin_record()

    # When the watcher tick resolves the approval
    result = _run_origin(
        tmp_path, monkeypatch, {"✅": ({"id": "cha-owner", "bot": False},)}, record=record
    )

    # Then the masked result lands in the origin thread, not in a DM
    assert [entry.draft_id for entry in result.commands.confirmed] == ["abc123"]
    assert result.discord.sent_messages == []
    assert result.dm_notices == []
    assert result.thread_names == ["🗓️ 캘린더 확정 (draft abc123)"]
    assert [channel for channel, _content in result.thread_posts] == [THREAD_ID]
    content = result.thread_posts[0][1]
    assert "캘린더 등록 실행 완료" in content
    assert "abc123" in content
    _assert_calendar_content_masked(content)
    _assert_calendar_content_masked(result.thread_names[0])


def test_origin_bound_cancellation_posts_masked_result_to_the_origin_thread(
    tmp_path: Path, monkeypatch
) -> None:
    # Given an origin-bound deletion draft the owner cancels with ⛔
    record = _origin_record(action="delete")

    # When the watcher tick resolves the cancellation
    result = _run_origin(
        tmp_path, monkeypatch, {"⛔": ({"id": "cha-owner", "bot": False},)}, record=record
    )

    # Then the masked cancel result lands in the origin thread, not in a DM
    assert result.commands.confirmed == []
    assert result.commands.discarded == ["abc123"]
    assert result.discord.sent_messages == []
    assert result.dm_notices == []
    assert [channel for channel, _content in result.thread_posts] == [THREAD_ID]
    content = result.thread_posts[0][1]
    assert "캘린더 삭제 취소" in content
    assert "abc123" in content
    _assert_calendar_content_masked(content)


# ------------------------------------------- per-request approval thread closing

APPROVAL_THREAD = "300000000000000091"
APPROVAL_THREAD_NAME = "🗓️ 캘린더 · abc123"


def _run_approval_thread(
    tmp_path: Path,
    monkeypatch,
    reactions: dict[str, tuple[dict[str, str | bool], ...]],
    *,
    record: dict,
    created: datetime | None = None,
    now: datetime = datetime(2026, 7, 17, 12, 0, tzinfo=UTC),
) -> SimpleNamespace:
    """Run one tick against a record already bound to its own approval thread."""
    thread_posts: list[tuple[str, str]] = []
    patches: list[dict] = []
    dm_notices: list[tuple[str, str]] = []

    def api(method: str, path: str, payload: dict | None = None):
        if method == "GET" and path == f"/channels/{APPROVAL_THREAD}":
            return {"id": APPROVAL_THREAD, "name": APPROVAL_THREAD_NAME}
        if method == "PATCH" and path == f"/channels/{APPROVAL_THREAD}":
            patches.append(dict(payload or {}))
            return {"id": APPROVAL_THREAD}
        raise AssertionError(f"unexpected Discord call: {method} {path}")

    class _ThreadTransport:
        def __init__(self, channel_id: str) -> None:
            self.channel_id = channel_id

        def send(self, content: str) -> tuple[_SentChunk, ...]:
            thread_posts.append((self.channel_id, content))
            return (_SentChunk("thread-post-1"),)

    monkeypatch.setattr(calendar_confirm, "owner_id", lambda: "cha-owner")
    monkeypatch.setattr(calendar_confirm, "_api", api)
    monkeypatch.setattr(calendar_confirm, "_thread_transport", _ThreadTransport)
    monkeypatch.setattr(
        calendar_confirm, "send_owner_dm",
        lambda owner, content: dm_notices.append((owner, content)),
    )
    store = PendingConfirmStore(tmp_path / "pending-confirms.jsonl")
    store.append(_entry(created=created))
    discord = FakeDiscord(reactions, f"calendar confirmation sha256:{record['sha256']}")
    commands = FakeCommands()
    watch.run_once(
        store=store, owner_id="cha-owner", discord=discord, commands=commands,
        draft_sha256=lambda _draft_id: str(record["sha256"]),
        draft_record=lambda _draft_id: record, now=now,
    )
    return SimpleNamespace(
        store=store, discord=discord, commands=commands,
        thread_posts=thread_posts, patches=patches, dm_notices=dm_notices,
    )


def test_execution_result_closes_the_request_thread_it_was_approved_in(
    tmp_path: Path, monkeypatch
) -> None:
    # Given a draft approved in its own thread, with no instructing channel at all
    record = _origin_record(
        origin_channel_id="", origin_message_id="", approval_thread_id=APPROVAL_THREAD
    )

    # When the watcher tick applies the owner's ✅
    result = _run_approval_thread(
        tmp_path, monkeypatch, {"✅": ({"id": "cha-owner", "bot": False},)}, record=record
    )

    # Then the masked result lands in the approval thread, which is then closed
    assert [entry.draft_id for entry in result.commands.confirmed] == ["abc123"]
    assert [channel for channel, _content in result.thread_posts] == [APPROVAL_THREAD]
    assert "캘린더 등록 실행 완료" in result.thread_posts[0][1]
    _assert_calendar_content_masked(result.thread_posts[0][1])
    assert result.patches == [
        {"archived": True, "name": f"✅ 완료 · {APPROVAL_THREAD_NAME}"}
    ]
    assert result.dm_notices == []


def test_cancellation_result_closes_the_request_thread_as_cancelled(
    tmp_path: Path, monkeypatch
) -> None:
    # Given an approval-thread-bound draft the owner cancels with ⛔
    record = _origin_record(action="delete", approval_thread_id=APPROVAL_THREAD)

    # When the watcher tick applies the cancellation
    result = _run_approval_thread(
        tmp_path, monkeypatch, {"⛔": ({"id": "cha-owner", "bot": False},)}, record=record
    )

    # Then the notice goes to the approval thread, which closes as cancelled
    assert result.commands.discarded == ["abc123"]
    assert [channel for channel, _content in result.thread_posts] == [APPROVAL_THREAD]
    assert "캘린더 삭제 취소" in result.thread_posts[0][1]
    _assert_calendar_content_masked(result.thread_posts[0][1])
    assert result.patches == [
        {"archived": True, "name": f"⛔ 취소 · {APPROVAL_THREAD_NAME}"}
    ]


def test_expiry_result_closes_the_request_thread_as_expired(
    tmp_path: Path, monkeypatch
) -> None:
    # Given an approval-thread-bound draft that outlived its 24h window
    record = _origin_record(approval_thread_id=APPROVAL_THREAD)

    # When the watcher tick expires it
    result = _run_approval_thread(
        tmp_path, monkeypatch, {}, record=record,
        created=datetime(2026, 7, 15, 11, 59, tzinfo=UTC),
    )

    # Then the expiry notice closes the same thread
    assert result.commands.discarded == ["abc123"]
    assert [channel for channel, _content in result.thread_posts] == [APPROVAL_THREAD]
    assert "캘린더 등록 만료 취소" in result.thread_posts[0][1]
    assert result.patches == [
        {"archived": True, "name": f"⌛ 만료 · {APPROVAL_THREAD_NAME}"}
    ]


def test_origin_thread_failure_falls_back_to_the_owner_dm_path(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # Given an origin-bound approved draft whose thread creation fails
    record = _origin_record()

    # When the watcher tick reports the committed execution
    result = _run_origin(
        tmp_path, monkeypatch, {"✅": ({"id": "cha-owner", "bot": False},)},
        record=record, thread_fail=True,
    )

    # Then the confirmed execution survives and the notice falls back to the DM path
    assert [entry.draft_id for entry in result.commands.confirmed] == ["abc123"]
    assert result.thread_posts == []
    [(owner, content)] = result.dm_notices
    assert owner == "cha-owner"
    assert "캘린더 등록 실행 완료" in content
    _assert_calendar_content_masked(content)
    assert "NOTIFY-THREAD-FAIL" in capsys.readouterr().err
    assert result.store.load() == ()


def test_create_draft_persists_origin_binding_without_changing_the_draft_hash(
    tmp_path: Path, monkeypatch
) -> None:
    # Given the same mutation drafted with and without an origin binding
    monkeypatch.setenv("CALENDAR_GATE_DIR", str(tmp_path / "gate"))
    fields = {
        "action": "delete", "argv": ("gws", "calendar", "events", "delete"),
        "calendar_id": "primary", "event_id": "evt1", "summary": "private",
        "start": "", "end": "", "channel_id": "dm",
    }
    legacy = calendar_gate.create_draft(**fields)

    # When the origin-bound draft is created
    record = calendar_gate.create_draft(
        **fields, origin_channel_id=ORIGIN_CHANNEL, origin_message_id=ORIGIN_MESSAGE
    )

    # Then the binding is persisted and the legacy content hash is unchanged
    stored = json.loads(
        (tmp_path / "gate" / "drafts" / f"{record['id']}.json").read_text(encoding="utf-8")
    )
    assert stored["origin_channel_id"] == ORIGIN_CHANNEL
    assert stored["origin_message_id"] == ORIGIN_MESSAGE
    assert stored["sha256"] == legacy["sha256"]


def test_draft_subcommands_thread_origin_into_the_record(tmp_path: Path, monkeypatch) -> None:
    # Given channel-initiated draft instructions carrying their origin refs
    monkeypatch.setenv("CALENDAR_GATE_DIR", str(tmp_path / "gate"))
    peers = tmp_path / "peers.yaml"
    _ = peers.write_text(
        'version: 1\npeers:\n  agent-cha:\n    bot_user_id: "111111111111111111"\n'
        "    bot_name: Owner-Agent\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CALENDAR_PEERS_CONFIG", str(peers))
    origin = {"origin_channel_id": ORIGIN_CHANNEL, "origin_message_id": ORIGIN_MESSAGE}
    monkeypatch.setattr(calendar_cli.calendar_preflight, "read_event", lambda *_args: {
        "id": "evt1", "summary": "private", "start": {"date": "2031-10-01"}, "end": {"date": "2031-10-02"},
    })

    # When each draft-creating subcommand runs
    assert calendar_cli.cmd_draft_create(
        SimpleNamespace(text="내일 오후 3시 실험 미팅", summary="", calendar="primary",
                        channel_id="dm", digest_day="", **origin)
    ) == 0
    assert calendar_cli.cmd_draft_update(
        SimpleNamespace(text="", summary="새 제목", calendar="primary", channel_id="dm",
                        event_id="evt1", **origin)
    ) == 0
    assert calendar_cli.cmd_draft_delete(
        SimpleNamespace(label="private", calendar="primary", channel_id="dm",
                        event_id="evt1", **origin)
    ) == 0

    # Then every stored draft carries the origin binding for result routing
    stored = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (tmp_path / "gate" / "drafts").glob("*.json")
    ]
    assert len(stored) == 3
    assert {record["origin_channel_id"] for record in stored} == {ORIGIN_CHANNEL}
    assert {record["origin_message_id"] for record in stored} == {ORIGIN_MESSAGE}


def test_draft_subcommand_parsers_accept_origin_flags() -> None:
    # Given/When the three draft subcommands are parsed with origin flags
    parser = calendar_cli.build_parser()
    parsed = [
        parser.parse_args([
            "draft-create", "--text", "내일 오후 3시 미팅",
            "--origin-channel-id", ORIGIN_CHANNEL, "--origin-message-id", ORIGIN_MESSAGE,
        ]),
        parser.parse_args([
            "draft-update", "--event-id", "evt1", "--summary", "새 제목",
            "--origin-channel-id", ORIGIN_CHANNEL, "--origin-message-id", ORIGIN_MESSAGE,
        ]),
        parser.parse_args([
            "draft-delete", "--event-id", "evt1",
            "--origin-channel-id", ORIGIN_CHANNEL, "--origin-message-id", ORIGIN_MESSAGE,
        ]),
    ]

    # Then each namespace carries them for create_draft
    for args in parsed:
        assert args.origin_channel_id == ORIGIN_CHANNEL
        assert args.origin_message_id == ORIGIN_MESSAGE


def test_notify_result_falls_back_to_owner_when_helper_is_unavailable(monkeypatch, capsys):
    # Given: the interop runtime lacks origin_notice (stale runtime / sandbox)
    import calendar_confirm

    notices: list[tuple[str, str]] = []
    monkeypatch.setattr(calendar_confirm, "owner_id", lambda: "owner-1")
    monkeypatch.setattr(
        calendar_confirm, "send_owner_dm", lambda owner, content: notices.append((owner, content))
    )

    def missing():
        raise ImportError("No module named 'automation'")

    monkeypatch.setattr(calendar_confirm, "_origin_notice", missing)
    # When: an origin-bound result is delivered
    calendar_confirm.notify_result(
        {"id": "abc123", "origin_channel_id": "200000000000000001", "origin_message_id": "m-1"},
        "⛔ 캘린더 등록 취소 (draft abc123)",
    )
    # Then: the owner still gets it through the legacy path, with a marker
    assert notices == [("owner-1", "⛔ 캘린더 등록 취소 (draft abc123)")]
    assert "NOTIFY-HELPER-MISSING" in capsys.readouterr().err
