from __future__ import annotations

import importlib
import json
import sys
from argparse import Namespace
from datetime import UTC, datetime
from email.message import Message
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.error import HTTPError
from urllib.parse import unquote

import pytest

from automation.interop.approval_surface import POLICY_VERSION

_REPO = Path(__file__).resolve().parents[2]
_COORDINATION = _REPO / "skills" / "coordination" / "scripts"
_CALENDAR = _REPO / "skills" / "calendar" / "scripts"
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_CALENDAR))
sys.path.insert(0, str(_COORDINATION))

io = importlib.import_module("coordinate_io")
coordination_lifecycle = importlib.import_module("coordination_lifecycle")
coordination_pending = importlib.import_module("coordination_pending")

OWNER = "owner-coordination"
ORIGIN_CHANNEL = "origin-chan-1"
ORIGIN_MESSAGE = "origin-msg-1"
SLOT = "2026-07-28T09:00:00+09:00"
KEY = f"coord:{SLOT}"
OWNER_DM_CHANNEL_ID = "1526487936065952385"
AGENT_CHAT_CHANNEL_ID = "1526487936065952390"
AGENT_CHAT_THREAD_ID = "1526487936065952391"
AGENT_CHAT_GUILD_ID = "1526487936065952392"
REQUEST_THREAD_ID = "1526487936065952400"


class AgentChatDirectory:
    """Fake directory recording the request spec each new binding asks for."""

    def __init__(self) -> None:
        self.specs: list[tuple[object, object]] = []

    def agent_chat(self) -> str:
        return AGENT_CHAT_CHANNEL_ID

    def agent_chat_thread(self, _kind: object) -> str:
        return AGENT_CHAT_THREAD_ID

    def agent_chat_request_thread(self, kind: object, request: object) -> str:
        self.specs.append((kind, request))
        return REQUEST_THREAD_ID

    def describe(self, channel_id: str):
        assert channel_id in {AGENT_CHAT_THREAD_ID, REQUEST_THREAD_ID}
        surface = importlib.import_module("automation.interop.approval_surface")
        return surface.ChannelFacts(11, "🤝 일정 조율 · 요청", (), AGENT_CHAT_CHANNEL_ID)


class FakeDiscord:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.contents: dict[str, str] = {}
        self.approved: set[str] = set()
        self.deleted: set[str] = set()
        self.post_channels: list[str] = []
        self.posts = 0
        self.request_threads: list[str] = []
        self.request_notice_ids: list[str] = []

    def request_thread_id(self, index: int) -> str:
        return f"{int(REQUEST_THREAD_ID) + index}"

    def owner_channel(self, _owner_id: str) -> str:
        return "dm-1"

    def post(self, _channel_id: str, content: str) -> str:
        self.posts += 1
        self.post_channels.append(_channel_id)
        message_id = f"msg-{self.posts}"
        self.contents[message_id] = content
        self.calls.append(f"POST:{message_id}")
        return message_id

    def add_reaction(self, _channel_id: str, _message_id: str, _emoji: str) -> None:
        return None

    def api(
        self, method: str, path: str, _payload: dict[str, str] | None = None
    ) -> Any:
        parts = path.strip("/").split("/")
        if method == "POST" and path == "/users/@me/channels":
            return {"id": OWNER_DM_CHANNEL_ID}
        if method == "POST" and path == f"/channels/{AGENT_CHAT_CHANNEL_ID}/messages":
            notice_id = f"notice-{len(self.request_notice_ids) + 1}"
            self.request_notice_ids.append(notice_id)
            return {"id": notice_id}
        if (
            method == "POST"
            and len(parts) == 5
            and parts[:3] == ["channels", AGENT_CHAT_CHANNEL_ID, "messages"]
            and parts[4] == "threads"
        ):
            assert parts[3] in self.request_notice_ids
            self.request_threads.append(str((_payload or {})["name"]))
            return {"id": self.request_thread_id(len(self.request_threads) - 1)}
        if method == "POST" and path == f"/channels/{AGENT_CHAT_CHANNEL_ID}/threads":
            self.request_threads.append(str((_payload or {})["name"]))
            return {"id": self.request_thread_id(len(self.request_threads) - 1)}
        if (
            method == "GET"
            and len(parts) == 2
            and parts[1] in {self.request_thread_id(index) for index in range(8)}
        ):
            return {
                "id": parts[1], "name": "🤝 일정 조율 · 요청",
                "parent_id": AGENT_CHAT_CHANNEL_ID, "type": 11,
            }
        if method == "GET" and path == f"/channels/{OWNER_DM_CHANNEL_ID}":
            return {"id": OWNER_DM_CHANNEL_ID, "name": "", "recipients": [{"id": OWNER}], "type": 1}
        if method == "GET" and path == f"/channels/{AGENT_CHAT_CHANNEL_ID}":
            return {"id": AGENT_CHAT_CHANNEL_ID, "guild_id": AGENT_CHAT_GUILD_ID, "name": "agent-chat", "type": 0}
        if method == "GET" and path == f"/guilds/{AGENT_CHAT_GUILD_ID}/threads/active":
            return {"threads": [{"id": AGENT_CHAT_THREAD_ID, "name": "승인-coordination", "parent_id": AGENT_CHAT_CHANNEL_ID, "type": 11}]}
        if method == "GET" and path == f"/channels/{AGENT_CHAT_THREAD_ID}":
            return {"id": AGENT_CHAT_THREAD_ID, "name": "승인-coordination", "parent_id": AGENT_CHAT_CHANNEL_ID, "type": 11}
        message_id = parts[3] if len(parts) > 3 else ""
        if method == "DELETE":
            self.calls.append(f"DELETE:{message_id}")
            self.deleted.add(message_id)
            return None
        if method == "GET" and len(parts) > 5:
            emoji = unquote(parts[5].split("?", 1)[0])
            if emoji == "✅" and message_id in self.approved:
                return [{"id": OWNER, "bot": False}]
            return []
        if method == "GET":
            if message_id in self.deleted:
                raise HTTPError("https://discord.invalid", 404, "missing", Message(), None)
            return {"id": message_id, "content": self.contents.get(message_id, "")}
        raise AssertionError(f"unexpected Discord call: {method} {path}")


@pytest.fixture
def coordination_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[FakeDiscord, list[str]]:
    calls: list[str] = []
    fake = FakeDiscord(calls)
    interop = tmp_path / "interop.json"
    interop.write_text(
        json.dumps({"agent_id": "agent", "owner_id": OWNER, "agent_chat_channel_id": AGENT_CHAT_CHANNEL_ID}),
        encoding="utf-8",
    )
    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(_REPO))
    monkeypatch.setenv("CALENDAR_GATE_DIR", str(tmp_path / "calendar-gate"))
    monkeypatch.setenv("COORDINATION_PENDING_CONFIRMS", str(tmp_path / "pending.jsonl"))
    monkeypatch.setenv("COORDINATION_STATE_DIR", str(tmp_path / "coordination"))
    monkeypatch.setenv("INTEROP_CONFIG", str(interop))
    monkeypatch.setattr(io, "post_message", fake.post)
    monkeypatch.setattr(io, "add_reaction", fake.add_reaction)
    monkeypatch.setattr(io, "api", fake.api)
    monkeypatch.setattr(io, "obs", lambda **_fields: None)
    return fake, calls


def _approval() -> ModuleType:
    return importlib.import_module("coordination_approval")


def _args(summary: str = "peer meeting") -> Namespace:
    return Namespace(
        summary=summary,
        duration_min=30,
        calendar="primary",
        e2e_confirm=False,
        peer="agent-peer",
        origin_channel_id=ORIGIN_CHANNEL,
        origin_message_id=ORIGIN_MESSAGE,
    )


def _request(summary: str = "peer meeting", correlation: str = "coord-run") -> int:
    return coordination_lifecycle.owner_leg(
        _args(summary), {"owner_id": OWNER}, correlation, None, SLOT
    )


def _store() -> coordination_pending.PendingConfirmStore:
    return coordination_pending.PendingConfirmStore()


def test_second_confirm_same_slot_and_hash_posts_nothing_and_keeps_message_id(
    coordination_env: tuple[FakeDiscord, list[str]],
) -> None:
    fake, _calls = coordination_env
    assert _request(correlation="coord-first") == 7

    assert _request(correlation="coord-second") == 7

    entries = tuple(entry for entry in _store().load() if entry.key == KEY)
    assert fake.posts == 1
    assert fake.post_channels == [fake.request_thread_id(0)]
    assert len(entries) == 1
    assert entries[0].dm_message_id == "msg-1"


def test_changed_content_deletes_message_before_dropping_row_then_posts_once(
    coordination_env: tuple[FakeDiscord, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, calls = coordination_env
    assert _request("before") == 7
    original_drop = getattr(coordination_pending.PendingConfirmStore, "drop", None)

    def recording_drop(store: coordination_pending.PendingConfirmStore, entry) -> None:
        calls.append(f"DROP:{entry.dm_message_id}")
        assert original_drop is not None
        original_drop(store, entry)

    monkeypatch.setattr(coordination_pending.PendingConfirmStore, "drop", recording_drop, raising=False)
    assert _request("after") == 7

    assert calls.index("DELETE:msg-1") < calls.index("DROP:msg-1") < calls.index("POST:msg-2")
    entries = tuple(entry for entry in _store().load() if entry.key == KEY)
    assert fake.posts == 2
    assert fake.post_channels == [fake.request_thread_id(0), fake.request_thread_id(0)]
    assert [entry.dm_message_id for entry in entries] == ["msg-2"]


def test_duplicate_and_superseding_requests_reuse_the_first_request_thread(
    coordination_env: tuple[FakeDiscord, list[str]],
) -> None:
    # Given: one owner-leg request already posted into its own thread
    fake, _calls = coordination_env
    assert _request("before", correlation="coord-first") == 7

    # When: the same slot is requested again (same hash) and then with new content
    assert _request("before", correlation="coord-second") == 7
    assert _request("after", correlation="coord-third") == 7

    # Then: one approval key keeps ONE thread — no empty orphan per retry or supersede
    assert len(fake.request_threads) == 1
    assert fake.post_channels == [fake.request_thread_id(0), fake.request_thread_id(0)]


def test_owner_already_approved_defers_without_deleting_or_dropping(
    coordination_env: tuple[FakeDiscord, list[str]],
) -> None:
    fake, calls = coordination_env
    assert _request("before") == 7
    before = _store().path.read_bytes()
    fake.approved.add("msg-1")

    with pytest.raises(io.CoordinationError):
        _request("after")

    assert "DELETE:msg-1" not in calls
    assert fake.posts == 1
    assert fake.post_channels == [fake.request_thread_id(0)]
    assert _store().path.read_bytes() == before


def test_watcher_lease_causes_producer_to_change_nothing(
    coordination_env: tuple[FakeDiscord, list[str]],
) -> None:
    fake, _calls = coordination_env
    approval = _approval()

    with approval.confirm_lease().hold(KEY) as owned:
        assert owned is True
        with pytest.raises(io.CoordinationError):
            _request()

    assert fake.posts == 0
    assert _store().load() == ()


def test_corrupt_row_refuses_without_posting(
    coordination_env: tuple[FakeDiscord, list[str]],
) -> None:
    fake, _calls = coordination_env
    _store().path.parent.mkdir(parents=True, exist_ok=True)
    _store().path.write_text("{not-json\n", encoding="utf-8")

    with pytest.raises(io.CoordinationError):
        _request()

    assert fake.posts == 0
    assert _store().path.read_text(encoding="utf-8") == "{not-json\n"


def test_owner_leg_persists_the_instruction_origin_into_the_pending_record(
    coordination_env: tuple[FakeDiscord, list[str]],
) -> None:
    # Given / When: a channel-instructed request reaches the owner approval leg
    fake, _calls = coordination_env
    assert _request() == 7

    # Then: the stored pending record carries the origin binding for result routing
    [entry] = tuple(entry for entry in _store().load() if entry.key == KEY)
    assert entry.origin_channel_id == ORIGIN_CHANNEL
    assert entry.origin_message_id == ORIGIN_MESSAGE
    assert entry.dm_channel_id == fake.request_thread_id(0)
    assert entry.channel_id == fake.request_thread_id(0)
    assert entry.surface == "agent-chat-thread"
    assert entry.policy_version == POLICY_VERSION


def test_owner_leg_opens_a_request_thread_labelled_as_the_team_post_does(
    coordination_env: tuple[FakeDiscord, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the directory records the spec each new binding asks for
    _fake, _calls = coordination_env
    binding = importlib.import_module("coordination_binding")
    directory = AgentChatDirectory()
    monkeypatch.setattr(binding, "approval_directory", lambda _owner_id=None: directory)

    # When: a channel-instructed coordination request reaches the owner approval leg
    assert coordination_lifecycle.owner_leg(
        _args(), {"owner_id": OWNER}, "coord-run", None, SLOT
    ) == 7

    # Then: the request thread is named with the label #team already publishes
    surface = importlib.import_module("automation.interop.approval_surface")
    [(kind, request)] = directory.specs
    assert kind == surface.ApprovalKind.COORDINATION
    assert request.title == "coord-run"
    assert request.origin_channel_id == ORIGIN_CHANNEL
    assert request.origin_message_id == ORIGIN_MESSAGE
    assert surface.request_thread_name(kind, request) == "🤝 일정 조율 · coord-run"


def test_new_binding_falls_back_to_the_pending_id_without_a_label(
    coordination_env: tuple[FakeDiscord, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a pending record with no coordination label
    _fake, _calls = coordination_env
    binding = importlib.import_module("coordination_binding")
    directory = AgentChatDirectory()
    monkeypatch.setattr(binding, "approval_directory", lambda _owner_id=None: directory)

    # When: the producer resolves that request's binding
    payload = _approval().CoordinationApprovalPayload(
        draft={"id": "abc123", "sha256": "sha-1"}, slot=SLOT, summary="피어 미팅",
        correlation="", duration_min=30, content="",
    )
    resolved = binding.new_binding(OWNER, payload)

    # Then: the thread is titled with the pending id instead
    [(_kind, request)] = directory.specs
    assert request.title == "abc123"
    assert resolved.channel_id == REQUEST_THREAD_ID


def test_owner_leg_persists_the_approval_thread_without_changing_the_hash(
    coordination_env: tuple[FakeDiscord, list[str]],
) -> None:
    # Given / When: a coordination approval is posted into its own request thread
    fake, _calls = coordination_env
    assert _request() == 7

    # Then: the pending record carries the approval thread the result notice returns to
    [entry] = tuple(entry for entry in _store().load() if entry.key == KEY)
    assert entry.approval_thread_id == entry.channel_id == fake.request_thread_id(0)
    assert entry.origin_record()["approval_thread_id"] == fake.request_thread_id(0)

    # …and the approval binding hash is untouched by the new field
    import calendar_gate

    draft = calendar_gate.load_draft(entry.draft_id)
    assert entry.sha256 == draft["sha256"]


def test_coordination_legacy_record_derives_key_from_slot(
    coordination_env: tuple[FakeDiscord, list[str]],
) -> None:
    _fake, _calls = coordination_env
    store = _store()
    base = {
        "correlation": "coord-legacy", "created": "2026-07-26T00:00:00Z",
        "dm_channel_id": "dm-1", "draft_id": "abc123", "duration_min": 30,
        "sha256": "sha-123", "slot": SLOT, "summary": "peer meeting",
    }
    legacy = {**base, "dm_message_id": "legacy-msg"}
    current = coordination_pending.PendingConfirm(
        draft_id="def456", sha256="sha-456", dm_channel_id="dm-1",
        dm_message_id="current-msg", slot=SLOT, summary="peer meeting",
        correlation="coord-current", duration_min=30,
        created=datetime(2026, 7, 26, tzinfo=UTC), key=KEY,
    )
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps(legacy) + "\n" + current.as_json() + "\n", encoding="utf-8")

    requests = _approval().CoordinationApprovalGate(None, store, OWNER).outstanding(KEY)

    assert [request.message_id for request in requests] == ["legacy-msg", "current-msg"]
    assert {request.key for request in requests} == {KEY}
