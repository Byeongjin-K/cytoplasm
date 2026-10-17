from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from automation.interop import approval_surface
from automation.interop.approval_lease import ApprovalLease
from automation.interop.approval_lifecycle import ApprovalRequest, DecisionWatcher, WatchOutcome, WatchVerdict
from automation.interop.approval_surface import (
    ApprovalBinding,
    ApprovalKind,
    ApprovalSurface,
    ChannelFacts,
    RequestThread,
)
from automation.repair.repair_ops_approval import repair_action_hash
from automation.repair import repair_ops_binding
from automation.repair import repair_ops_cli
from automation.repair import repair_ops_reaction_watch
from automation.repair import repair_ops_discord
from automation.repair.repair_ops_discord import RepairDiscordApi, RepairDiscordError
from automation.repair.repair_ops_pending import (
    PendingRepairApproval,
    PendingRepairApprovalStore,
    PostingOwnerApproval,
    approval_request_content,
)
from automation.repair.repair_ops_reaction_watch import RepairApprovalWatcher
from automation.repair.repair_patch_binding import content_action_hash, parse_patch_changes


OWNER_ID = "280680578404010625"
APPROVALS_CHANNEL_ID = "1528936606946122421"
OTHER_CHANNEL_ID = "1528936606946122422"
OPS_OWNER_DM_CHANNEL_ID = "1528936606946122423"
AGENT_OWNER_DM_CHANNEL_ID = "1528936606946122424"
AGENT_CHAT_CHANNEL_ID = "1528936606946122430"
OPS_THREAD_ID = "1528936606946122431"
REQUEST_THREAD_ID = "1528936606946122432"
TICKET = "t_2578c8ed"
PATCH_BYTES = (
    "diff --git a/automation/mod.py b/automation/mod.py\n"
    "--- a/automation/mod.py\n"
    "+++ b/automation/mod.py\n"
    "@@ -1,2 +1,2 @@\n context\n-old\n+new\n"
).encode()
PATCH_SHA256 = "260e4060e17b03469cc39ab00d442db269adef55e4c6528269c6f54f1a34b410"
# The preimage is (ticket, patch name, patch bytes digest, file summary) and
# nothing else — pinned here so a routing change cannot quietly move it.
V2_ACTION_HASH = "sha256:166cda09f58867ae81a312f419ab5cdd9b3e16fee1f34594f0a28d525a581915"
SOURCE = f"/srv/autophagy-private/repair-plans/{TICKET}/patch.diff"
# Frozen v2 renderer output for PATCH_BYTES, captured from the shipped renderer
# before the request thread existed. The watcher compares a posted message to a
# re-render by exact equality, so the thread id must never enter this text.
V2_GOLDEN = (
    "[repair] 승인 요청\n"
    f"- ticket: `{TICKET}`\n"
    f"- action_hash: `{V2_ACTION_HASH}`\n"
    f"- patch_sha256: `{PATCH_SHA256}`\n"
    "- changed_files: 1 total, +1/-1\n"
    "  - automation/mod.py (+1/-1)\n"
    "- repair_nonce: `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`\n"
    "- sandbox: PASS (offline-subset bank + repro GREEN)\n"
    f"- patch_body: 비노출 — ops 호스트의 `{SOURCE}` 에서 확인\n"
    "- cha가 이 메시지에 ✅ 승인 또는 ⛔ 취소 리액션"
)
NOW = datetime(2026, 7, 26, 9, 0, tzinfo=UTC)
BINDING = ApprovalBinding(
    ApprovalKind.REPAIR,
    ApprovalSurface.SKILL_APPROVALS,
    APPROVALS_CHANNEL_ID,
    4,
)


@dataclass(frozen=True, slots=True)
class _PostingTransport:
    posts: list[str] = field(default_factory=list)
    reactions: list[tuple[str, str]] = field(default_factory=list)

    def post_approval(self, content: str) -> str:
        self.posts.append(content)
        return "message-1"

    def add_reaction(self, message_id: str, emoji: str) -> None:
        self.reactions.append((message_id, emoji))


@dataclass(frozen=True, slots=True)
class _Directory:
    facts: ChannelFacts
    configured: str = APPROVALS_CHANNEL_ID

    def owner_dm(self) -> str:
        return "1528936606946122423"

    def skill_approvals(self) -> str:
        return self.configured

    def describe(self, channel_id: str) -> ChannelFacts:
        if channel_id != APPROVALS_CHANNEL_ID:
            raise AssertionError(f"unexpected channel lookup: {channel_id}")
        return self.facts


@dataclass(frozen=True, slots=True)
class _OpsDirectory:
    thread_id: str
    thread_facts: ChannelFacts | None
    thread_calls: list[str] = field(default_factory=list)
    request_specs: list[tuple[ApprovalKind, RequestThread]] = field(default_factory=list)

    def owner_dm(self) -> str:
        raise AssertionError("v8: a new repair binding must not resolve the owner DM")

    def skill_approvals(self) -> str:
        return APPROVALS_CHANNEL_ID

    def agent_chat(self) -> str:
        return AGENT_CHAT_CHANNEL_ID

    def agent_chat_thread(self, kind: ApprovalKind) -> str:
        self.thread_calls.append(self.thread_id)
        return self.thread_id

    def agent_chat_request_thread(self, kind: ApprovalKind, request: RequestThread) -> str:
        """Record the spec: the real directory OPENS a thread here, once per request."""
        self.request_specs.append((kind, request))
        return REQUEST_THREAD_ID

    def describe(self, channel_id: str) -> ChannelFacts:
        if channel_id == APPROVALS_CHANNEL_ID:
            return ChannelFacts(0, "approvals", ())
        if channel_id == REQUEST_THREAD_ID:
            return ChannelFacts(11, f"수리 · {TICKET}", (), AGENT_CHAT_CHANNEL_ID)
        if channel_id == self.thread_id and self.thread_facts is not None:
            return self.thread_facts
        raise approval_surface.ApprovalSurfaceError("repair credential cannot describe this channel")


@dataclass(frozen=True, slots=True)
class _Commands:
    def apply(self, pending: PendingRepairApproval) -> bool:
        del pending
        return True

    def discard(self, pending: PendingRepairApproval, reason: str) -> bool:
        del pending, reason
        return True


def test_repair_request_persists_its_binding(tmp_path: Path) -> None:
    # Given: the ops posting flow has the resolved guild binding.
    store = PendingRepairApprovalStore(tmp_path / "pending")
    approval = PostingOwnerApproval(
        OWNER_ID,
        store,
        _PostingTransport(),
        now=lambda: NOW,
        nonce=lambda: "a" * 32,
        binding=BINDING,
    )

    # When: it posts one repair approval request.
    patch = tmp_path / "plans" / "repair-1" / "patch.diff"
    patch.parent.mkdir(parents=True, exist_ok=True)
    _ = patch.write_text(
        "diff --git a/automation/mod.py b/automation/mod.py\n"
        "--- a/automation/mod.py\n"
        "+++ b/automation/mod.py\n"
        "@@ -1,2 +1,2 @@\n context\n-old\n+new\n",
        encoding="utf-8",
    )
    assert approval.permits("repair-1", patch) is False
    pending = store.get("repair-1")

    # Then: all binding facts round-trip as a concrete Discord snowflake.
    assert pending is not None
    assert (
        pending.kind,
        pending.surface,
        pending.channel_id,
        pending.policy_version,
    ) == (
        ApprovalKind.REPAIR,
        ApprovalSurface.SKILL_APPROVALS,
        APPROVALS_CHANNEL_ID,
        BINDING.policy_version,
    )
    assert pending.channel_id.isdigit()
    assert pending.channel_id != "approvals"


def test_repair_watch_reads_the_channel_from_the_stored_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a record is bound to #approvals while a resolver would now choose another id.
    pending = PendingRepairApproval(
        "repair-1",
        "patch.diff",
        repair_action_hash("repair-1", "patch.diff"),
        "nonce",
        "message-1",
        NOW,
        kind=ApprovalKind.REPAIR,
        surface=ApprovalSurface.SKILL_APPROVALS,
        channel_id=APPROVALS_CHANNEL_ID,
        policy_version=approval_surface.POLICY_VERSION,
    )
    store = PendingRepairApprovalStore(tmp_path / "pending")
    store.save(pending)
    seen_channels: list[str] = []

    def unexpected_resolution(
        kind: ApprovalKind,
        directory: approval_surface.ChannelDirectory,
        owner_id: str,
        skill_channel_id: str | None = None,
    ) -> ApprovalBinding:
        del kind, directory, owner_id, skill_channel_id
        return ApprovalBinding(
            ApprovalKind.REPAIR,
            ApprovalSurface.SKILL_APPROVALS,
            OTHER_CHANNEL_ID,
            approval_surface.POLICY_VERSION,
        )

    def capture_request(
        request: ApprovalRequest,
        watcher: DecisionWatcher,
        lease: ApprovalLease,
    ) -> WatchVerdict:
        del watcher, lease
        seen_channels.append(request.channel_id)
        return WatchVerdict(WatchOutcome.WAITING)

    monkeypatch.setattr(approval_surface, "resolve_new_binding", unexpected_resolution)
    monkeypatch.setattr(repair_ops_reaction_watch, "resolve_owner_decision", capture_request)
    watcher = RepairApprovalWatcher(
        store,
        _PostingTransport(),
        _Commands(),
        OWNER_ID,
        tmp_path / "audit.jsonl",
        now=lambda: NOW,
    )

    # When: the watcher maps the stored pending request to the shared lifecycle.
    watcher.run_once()

    # Then: no newly resolved channel can retarget the existing approval.
    assert seen_channels == [APPROVALS_CHANNEL_ID]


def test_assert_surface_accepts_the_guild_approvals_channel_and_refuses_a_mismatch() -> None:
    # Given: the repair bot's directory identifies the real guild approval surface.
    valid_directory = _Directory(ChannelFacts(0, "approvals", ()))
    discord = RepairDiscordApi("ops-token", BINDING, valid_directory, OWNER_ID)

    # When / Then: the exact policy binding is accepted.
    discord.assert_surface(BINDING)

    # Given: the configured channel was renamed in Discord (owner 2026-10-18).
    renamed_directory = _Directory(ChannelFacts(0, "release-approvals", ()))
    renamed = RepairDiscordApi("ops-token", BINDING, renamed_directory, OWNER_ID)
    # When / Then: its configured id still identifies the approval surface.
    renamed.assert_surface(BINDING)

    # Given: the snowflake is neither named approvals nor the configured channel.
    invalid_directory = _Directory(
        ChannelFacts(0, "other-channel", ()), configured="1528936606946122499",
    )
    mismatched = RepairDiscordApi("ops-token", BINDING, invalid_directory, OWNER_ID)

    # When / Then: channel facts that contradict the binding fail closed.
    with pytest.raises(RepairDiscordError, match="approval surface"):
        mismatched.assert_surface(BINDING)


def test_repair_request_posts_to_its_own_agent_chat_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a posting caller explicitly names the ticket whose thread it needs.
    directory = _OpsDirectory(
        OPS_THREAD_ID,
        ChannelFacts(11, "승인-repair", (), AGENT_CHAT_CHANNEL_ID),
    )
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "credential")
    monkeypatch.setenv("AUTOPHAGY_OWNER_ID", OWNER_ID)
    monkeypatch.setattr(repair_ops_discord, "directory_for_ops", lambda token, owner: directory)

    # When: repair resolves the transport for a new owner approval request.
    discord = repair_ops_discord.configured_discord(TICKET)

    # Then: posting resolves the ticket thread, never the legacy kind thread.
    assert directory.thread_calls == []
    assert directory.request_specs == [(ApprovalKind.REPAIR, RequestThread(title=TICKET))]
    assert discord.binding == ApprovalBinding(
        ApprovalKind.REPAIR,
        ApprovalSurface.AGENT_CHAT_THREAD,
        REQUEST_THREAD_ID,
        approval_surface.POLICY_VERSION,
    )
    assert all(
        approval_surface.surface_at_policy(ApprovalKind.REPAIR, version)
        is ApprovalSurface.SKILL_APPROVALS
        for version in range(5)
    )
    assert approval_surface.surface_at_policy(ApprovalKind.REPAIR, 7) is ApprovalSurface.OWNER_DM


def test_repair_refuses_a_thread_its_credential_cannot_describe(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: the resolved thread cannot be described with the repair credential
    # (예: 초대 전 — 게시 불가로 fail-closed, ON-4 롤아웃 전제).
    directory = _OpsDirectory(AGENT_OWNER_DM_CHANNEL_ID, None)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "credential")
    monkeypatch.setenv("AUTOPHAGY_OWNER_ID", OWNER_ID)
    monkeypatch.setattr(repair_ops_discord, "directory_for_ops", lambda token, owner: directory)

    pending = PendingRepairApproval(
        TICKET, "patch.diff", repair_action_hash(TICKET, "patch.diff"), "nonce", "message", NOW,
        kind=ApprovalKind.REPAIR, surface=ApprovalSurface.AGENT_CHAT_THREAD,
        channel_id=AGENT_OWNER_DM_CHANNEL_ID, policy_version=approval_surface.POLICY_VERSION,
    )
    # When / Then: readers validate the stored destination, never resolve a new one.
    with pytest.raises(RepairDiscordError, match="stored repair approval surface is invalid"):
        _ = repair_ops_discord.configured_discord().for_pending(pending)


def test_repair_binds_a_new_request_to_a_thread_named_for_its_ticket() -> None:
    # Given: the ops directory can open one thread per approval request.
    directory = _OpsDirectory(
        OPS_THREAD_ID,
        ChannelFacts(11, "승인-repair", (), AGENT_CHAT_CHANNEL_ID),
    )

    # When: repair resolves the binding for one ticket.
    binding = repair_ops_binding.new_binding(directory, OWNER_ID, TICKET)

    # Then: the ticket id — never a diagnosis or a patch line — titles the thread,
    # the binding channel IS that thread, and the shared per-kind thread is not
    # touched, so two tickets can never share one approval conversation.
    assert directory.request_specs == [(ApprovalKind.REPAIR, RequestThread(title=TICKET))]
    assert binding == ApprovalBinding(
        ApprovalKind.REPAIR,
        ApprovalSurface.AGENT_CHAT_THREAD,
        REQUEST_THREAD_ID,
        approval_surface.POLICY_VERSION,
    )
    assert directory.thread_calls == []


def test_repair_cli_gives_each_ticket_its_own_approval_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the ops entry point configured for one ticket, with no E2E override.
    directory = _OpsDirectory(
        OPS_THREAD_ID,
        ChannelFacts(11, "승인-repair", (), AGENT_CHAT_CHANNEL_ID),
    )
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "credential")
    monkeypatch.setenv("AUTOPHAGY_OWNER_ID", OWNER_ID)
    monkeypatch.setenv("REPAIR_APPROVAL_PENDING_ROOT", str(tmp_path / "pending"))
    monkeypatch.delenv("E2E_TEST_MODE", raising=False)
    monkeypatch.delenv("REPAIR_E2E_SECRET", raising=False)
    monkeypatch.setattr(repair_ops_discord, "directory_for_ops", lambda token, owner: directory)
    config = repair_ops_cli.RepairOpsConfig(
        TICKET, tmp_path / "checkout", tmp_path / "logs",
        tmp_path / "plans", tmp_path / "approvals.jsonl",
        None, None,
    )

    approval = repair_ops_cli._approval(config)
    assert isinstance(approval, PostingOwnerApproval)
    source = tmp_path / "patch.diff"
    source.write_bytes(PATCH_BYTES)
    http = _FakeDiscordHttp()
    monkeypatch.setattr(repair_ops_discord, "_open_discord", http)
    # When: the CLI's approval posts the prepared patch to its resolved surface.
    approval.permits(TICKET, source)
    # Then: the same policy still gives this ticket its own request thread.
    assert directory.request_specs == [(ApprovalKind.REPAIR, RequestThread(title=TICKET))]
    assert [channel for channel, _ in http.posts] == [REQUEST_THREAD_ID]


def test_repair_record_carries_its_approval_thread_without_moving_the_hash(tmp_path: Path) -> None:
    # Given: the posting flow bound to one request's own thread.
    store = PendingRepairApprovalStore(tmp_path / "pending")
    transport = _PostingTransport()
    binding = ApprovalBinding(
        ApprovalKind.REPAIR,
        ApprovalSurface.AGENT_CHAT_THREAD,
        REQUEST_THREAD_ID,
        approval_surface.POLICY_VERSION,
    )
    approval = PostingOwnerApproval(
        OWNER_ID,
        store,
        transport,
        now=lambda: NOW,
        nonce=lambda: "a" * 32,
        binding=binding,
    )
    patch = tmp_path / "plans" / TICKET / "patch.diff"
    patch.parent.mkdir(parents=True, exist_ok=True)
    _ = patch.write_bytes(PATCH_BYTES)

    # When: one request is posted and read back from disk.
    assert approval.permits(TICKET, patch) is False
    pending = store.get(TICKET)

    # Then: the record names the approval thread it was posted into...
    assert pending is not None
    assert pending.approval_thread_id == binding.channel_id == REQUEST_THREAD_ID

    # ...while the owner's consent still covers exactly the patch and nothing about
    # routing: the hash is re-derivable from the patch bytes alone...
    assert pending.action_hash == content_action_hash(
        TICKET, "patch.diff", PATCH_SHA256, parse_patch_changes(PATCH_BYTES)
    )
    assert pending.action_hash == V2_ACTION_HASH

    # ...and new cards replay their stored version; records without a render
    # version still reproduce the frozen v2 bytes on the same thread.
    assert transport.posts == [approval_request_content(pending)]
    assert approval_request_content(replace(pending, patch_source_path=SOURCE, render_version=None)) == V2_GOLDEN


def test_a_record_written_before_request_threads_still_loads(tmp_path: Path) -> None:
    # Given: a record left by a release that had no per-request thread.
    store = PendingRepairApprovalStore(tmp_path / "pending")
    legacy = PendingRepairApproval(
        TICKET,
        "patch.diff",
        repair_action_hash(TICKET, "patch.diff"),
        "nonce",
        "message-1",
        NOW,
    )
    store.save(legacy)

    # When / Then: it decodes unchanged and answers "no thread" instead of raising —
    # a schema-age refusal here would paralyse every outstanding repair approval.
    loaded = store.get(TICKET)
    assert loaded == legacy
    assert loaded is not None
    assert loaded.approval_thread_id == ""


@dataclass
class _ThreadOpeningOpsDirectory:
    """Ops directory that OPENS one thread per request and describes what it opened."""

    opened: list[RequestThread] = field(default_factory=list)

    def thread_id(self, index: int) -> str:
        """Discord answers a DISTINCT snowflake for every thread it creates."""
        return str(int(REQUEST_THREAD_ID) + index)

    def owner_dm(self) -> str:
        raise AssertionError("v8: a new repair binding must not resolve the owner DM")

    def skill_approvals(self) -> str:
        return APPROVALS_CHANNEL_ID

    def agent_chat(self) -> str:
        return AGENT_CHAT_CHANNEL_ID

    def agent_chat_thread(self, kind: ApprovalKind) -> str:
        del kind
        raise AssertionError("a request must never fall back to the shared kind thread")

    def agent_chat_request_thread(self, kind: ApprovalKind, request: RequestThread) -> str:
        del kind
        self.opened.append(request)
        return self.thread_id(len(self.opened) - 1)

    def describe(self, channel_id: str) -> ChannelFacts:
        for index, request in enumerate(self.opened):
            if channel_id == self.thread_id(index):
                return ChannelFacts(11, f"수리 · {request.title}", (), AGENT_CHAT_CHANNEL_ID)
        raise approval_surface.ApprovalSurfaceError("repair credential cannot describe this channel")


@dataclass(frozen=True, slots=True)
class _FakeHttpResponse:
    body: bytes

    def read(self) -> bytes:
        return self.body

    def __enter__(self) -> _FakeHttpResponse:
        return self

    def __exit__(self, *exception: object) -> bool:
        return False


@dataclass
class _FakeDiscordHttp:
    """Offline Discord REST for the ops repair transport (post/poll/delete)."""

    posts: list[tuple[str, str]] = field(default_factory=list)
    messages: dict[str, str] = field(default_factory=dict)

    def __call__(self, request: Request) -> _FakeHttpResponse:
        path = request.full_url.removeprefix(repair_ops_discord.DISCORD_API)
        parts = path.strip("/").split("/")
        payload = {} if request.data is None else json.loads(request.data)
        method = request.method
        if method == "POST" and parts[-1] == "messages":
            message_id = f"msg-{len(self.posts) + 1}"
            self.posts.append((parts[1], str(payload["content"])))
            self.messages[message_id] = str(payload["content"])
            return _FakeHttpResponse(json.dumps({"id": message_id}).encode())
        message_id = parts[3] if len(parts) > 3 else ""
        if method == "PUT":
            return _FakeHttpResponse(b"")
        if method == "DELETE":
            _ = self.messages.pop(message_id, None)
            return _FakeHttpResponse(b"")
        if method == "GET" and "reactions" in parts:
            return _FakeHttpResponse(b"[]")
        if method == "GET":
            if message_id not in self.messages:
                raise HTTPError(request.full_url, 404, "missing", Message(), None)
            return _FakeHttpResponse(json.dumps({"content": self.messages[message_id]}).encode())
        raise AssertionError(f"unexpected Discord call: {method} {path}")


def test_repair_re_request_and_supersede_reuse_the_first_ticket_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the ops entry point posting for one ticket, with no E2E override.
    directory = _ThreadOpeningOpsDirectory()
    http = _FakeDiscordHttp()
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "credential")
    monkeypatch.setenv("AUTOPHAGY_OWNER_ID", OWNER_ID)
    monkeypatch.setenv("REPAIR_APPROVAL_PENDING_ROOT", str(tmp_path / "pending"))
    monkeypatch.delenv("E2E_TEST_MODE", raising=False)
    monkeypatch.delenv("REPAIR_E2E_SECRET", raising=False)
    monkeypatch.setattr(repair_ops_discord, "directory_for_ops", lambda token, owner: directory)
    monkeypatch.setattr(repair_ops_discord, "_open_discord", http)
    config = repair_ops_cli.RepairOpsConfig(
        TICKET,
        tmp_path / "checkout",
        tmp_path / "logs",
        tmp_path / "plans",
        tmp_path / "approvals.jsonl",
        None,
        None,
    )
    patch = tmp_path / "plans" / TICKET / "patch.diff"
    patch.parent.mkdir(parents=True, exist_ok=True)
    _ = patch.write_bytes(PATCH_BYTES)
    store = PendingRepairApprovalStore(tmp_path / "pending")

    # When: the ticket is requested, requested again unchanged, then with a changed patch.
    assert repair_ops_cli._approval(config).permits(TICKET, patch) is False  # pyright: ignore[reportPrivateUsage]
    first = store.get(TICKET)
    assert repair_ops_cli._approval(config).permits(TICKET, patch) is False  # pyright: ignore[reportPrivateUsage]
    _ = patch.write_bytes(PATCH_BYTES.replace(b"+new", b"+something else entirely"))
    assert repair_ops_cli._approval(config).permits(TICKET, patch) is False  # pyright: ignore[reportPrivateUsage]

    # Then: one ticket keeps ONE thread — no empty orphan per re-request or supersede.
    assert directory.opened == [RequestThread(title=TICKET)]
    assert [channel for channel, _ in http.posts] == [directory.thread_id(0)] * 2

    # ...and neither the consent hash nor the rendered v2 request moved a byte.
    second = store.get(TICKET)
    assert first is not None and second is not None
    assert first.action_hash == V2_ACTION_HASH
    assert second.action_hash != V2_ACTION_HASH
    assert [content for _, content in http.posts] == [
        approval_request_content(first),
        approval_request_content(second),
    ]
