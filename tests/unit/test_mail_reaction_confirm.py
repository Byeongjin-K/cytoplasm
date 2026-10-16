from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import get_type_hints
from urllib.error import HTTPError

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))

from automation.interop.approval_surface import (  # noqa: E402
    ApprovalKind,
    ApprovalSurface,
    ChannelFacts,
    RequestThread,
    required_surface,
)
from automation.interop.discord_transport import (  # noqa: E402
    DiscordTransport,
    SentMessage,
)
import triage_cli  # noqa: E402
import triage_approval  # noqa: E402
import triage_binding  # noqa: E402
import triage_confirm  # noqa: E402
import triage_core  # noqa: E402
import triage_gate  # noqa: E402
import triage_mode  # noqa: E402
import triage_pipeline  # noqa: E402


OWNER_ID = "owner-1"
APPROVALS_CHANNEL = "100000000000000001"
DM_CHANNEL = "100000000000000002"
AGENT_CHAT_CHANNEL = "100000000000000003"
AGENT_CHAT_THREAD = "100000000000000004"
ANNOUNCEMENT_MESSAGE_ID = "announcement-message-1"
MESSAGE_ID = "message-1"


def _draft(*, message_id: str = MESSAGE_ID) -> dict:
    record = {
        "argv": ["python3", "-m", "mailon.main", "send", "--to", "owner@example.invalid"],
        "body": "안녕하세요.",
        "category": "important",
        "flags": ["reply_needed"],
        "channel_id": APPROVALS_CHANNEL,
        "id": "abc123",
        "kind": "reply",
        "mail_subject": "일정 문의",
        "message_id": message_id,
        "sender": "발신자 <sender@example.invalid>",
        "sender_masked": triage_core.mask_value("발신자 <sender@example.invalid>"),
        "sensitive": False,
        "surface": "skill-approvals",
        "status": "pending",
        "subject": "Re: 일정 문의",
        "tags": [],
        "to": "owner@example.invalid",
        "uid": "u-1",
        "uid_opaque": triage_core.mask_value("u-1"),
        "policy_version": 1,
    }
    record["sha256"] = triage_core.draft_sha256(record)
    return record


def _reaction_api(draft: dict, users_by_emoji: dict[str, list[dict]]):
    def request(method: str, path: str, payload: dict | None = None):
        del payload
        if method == "GET" and path == f"/channels/{APPROVALS_CHANNEL}":
            return {"type": 0, "name": "approvals", "recipients": []}
        if method == "GET" and path == f"/channels/{APPROVALS_CHANNEL}/messages/{MESSAGE_ID}":
            return {"content": f"draft sha256:{draft['sha256']}"}
        for emoji, users in users_by_emoji.items():
            quoted = triage_confirm.quote(emoji, safe="")
            if method == "GET" and path.endswith(f"/reactions/{quoted}?limit=100"):
                return users
        if method == "GET" and "/reactions/" in path:
            return []
        raise AssertionError(f"unexpected Discord call: {method} {path}")

    return request


def _run_watch(monkeypatch: pytest.MonkeyPatch, draft: dict, users_by_emoji: dict[str, list[dict]]):
    sent: list[triage_gate.Approval] = []
    discarded: list[str] = []
    notices: list[str] = []
    monkeypatch.setattr(triage_mode, "effective_mode", lambda: "full-go")
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER_ID)
    monkeypatch.setattr(triage_confirm, "_api", _reaction_api(draft, users_by_emoji))
    monkeypatch.setattr(triage_gate, "list_drafts", lambda: [draft])
    monkeypatch.setattr(triage_gate, "execute_draft", lambda _draft, approval: sent.append(approval))
    monkeypatch.setattr(triage_gate, "discard_draft", lambda draft_id: discarded.append(draft_id))
    monkeypatch.setattr(triage_confirm, "dm_owner", lambda content: notices.append(content))
    monkeypatch.setattr(triage_cli, "cmd_process", lambda _args: 0)

    assert triage_cli.cmd_watch(argparse.Namespace()) == 0
    return sent, discarded, notices


def test_watch_sends_once_when_owner_approves(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a pending draft with an owner-only approval reaction
    draft = _draft()
    # When: the production watch tick resolves the approval message
    sent, discarded, notices = _run_watch(
        monkeypatch, draft, {triage_confirm.APPROVE_EMOJI: [{"id": OWNER_ID, "bot": False}]}
    )
    # Then: the frozen draft is sent exactly once, discard untouched, and the
    # owner receives a send-success DM (owner request 2026-07-20)
    assert len(sent) == 1
    assert discarded == []
    assert len(notices) == 1
    assert "발송 완료" in notices[0]
    assert "Re: 일정 문의" in notices[0]
    assert "owner@example.invalid" in notices[0]
    assert "abc123" in notices[0]


def test_watch_discards_when_owner_cancels(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a pending draft with an owner-only cancellation reaction
    draft = _draft()
    # When: the production watch tick resolves the approval message
    sent, discarded, notices = _run_watch(
        monkeypatch, draft, {triage_confirm.CANCEL_EMOJI: [{"id": OWNER_ID, "bot": False}]}
    )
    # Then: the draft is discarded, no mail is sent, and the owner notice names
    # the mail (subject·recipient·draft id) instead of a bare fixed phrase
    assert sent == []
    assert discarded == [draft["id"]]
    assert len(notices) == 1
    assert "발송 취소" in notices[0]
    assert "Re: 일정 문의" in notices[0]
    assert "owner@example.invalid" in notices[0]
    assert "abc123" in notices[0]


def test_new_reply_draft_posts_to_the_agent_chat_thread(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: a sensitive reply draft and a Discord fake that can describe the approval thread
    requests: list[tuple[str, str, dict | None]] = []
    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(_REPO))
    monkeypatch.setenv("TRIAGE_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("TRIAGE_MAIL_HOME", str(tmp_path / "mail"))
    monkeypatch.setenv("TRIAGE_MAILON_PYTHON", "python3")
    interop = tmp_path / "interop-config.json"
    interop.write_text(
        json.dumps({"owner_id": OWNER_ID, "agent_chat_channel_id": AGENT_CHAT_CHANNEL}),
        encoding="utf-8",
    )
    monkeypatch.setenv("INTEROP_CONFIG", str(interop))
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER_ID)

    def request(method: str, path: str, payload: dict | None = None):
        requests.append((method, path, payload))
        if method == "POST" and path == "/users/@me/channels":
            return {"id": DM_CHANNEL}
        if method == "GET" and path == f"/channels/{DM_CHANNEL}":
            return {"type": 1, "name": "", "recipients": [{"id": OWNER_ID}]}
        if method == "GET" and path == f"/channels/{AGENT_CHAT_CHANNEL}":
            return {"type": 0, "name": "agent-chat", "guild_id": "guild-1"}
        if method == "POST" and path == f"/channels/{AGENT_CHAT_CHANNEL}/messages":
            return {"id": ANNOUNCEMENT_MESSAGE_ID}
        if method == "POST" and path == (
            f"/channels/{AGENT_CHAT_CHANNEL}/messages/{ANNOUNCEMENT_MESSAGE_ID}/threads"
        ):
            return {"id": AGENT_CHAT_THREAD}
        if method == "GET" and path == f"/channels/{AGENT_CHAT_THREAD}":
            return {"type": 11, "name": "↩️ 메일 회신 · Re: 민감 문의", "parent_id": AGENT_CHAT_CHANNEL}
        if method == "GET" and path == f"/channels/{APPROVALS_CHANNEL}":
            return {"type": 0, "name": "approvals", "recipients": []}
        if method == "POST" and path.endswith("/messages"):
            return {"id": MESSAGE_ID}
        if method == "PUT":
            return None
        raise AssertionError(f"unexpected Discord call: {method} {path}")

    monkeypatch.setattr(triage_confirm, "_api", request)
    monkeypatch.setattr(
        triage_pipeline.triage_llm,
        "draft_reply",
        lambda **_kwargs: ("Re: 민감 문의", "민감 회신 전문", "test-provider"),
    )
    monkeypatch.setattr(
        triage_confirm,
        "dm_owner",
        lambda _content: pytest.fail("sensitive reply must not post a second owner-DM message"),
    )

    # When: the reply pipeline drafts and posts the sensitive approval
    actions = triage_pipeline._draft_and_post(
        {
            "uid": "u-dm",
            "sender": "발신자 <sender@example.invalid>",
            "subject": "민감 문의",
            "body": "원본 민감 메일 본문",
        },
        SimpleNamespace(category="important", flags=lambda: ("reply_needed",)),
        post=True,
    )

    # Then: the request got its OWN thread, named after this mail...
    [draft] = triage_gate.list_drafts()
    assert [
        (item[1], item[2])
        for item in requests
        if item[0] == "POST" and item[1].endswith("/threads")
    ] == [
        (
            f"/channels/{AGENT_CHAT_CHANNEL}/messages/{ANNOUNCEMENT_MESSAGE_ID}/threads",
            {"name": "↩️ 메일 회신 · Re: 민감 문의", "auto_archive_duration": 10080},
        )
    ]
    # ...and the one approval message is in that thread with full reply and hash
    posts = [
        item
        for item in requests
        if item[0] == "POST"
        and item[1].endswith("/messages")
        and item[1] != f"/channels/{AGENT_CHAT_CHANNEL}/messages"
    ]
    assert actions == [f"draft:{draft['id']}", f"posted:{MESSAGE_ID}"]
    assert len(posts) == 1
    _method, channel_path, payload = posts[0]
    assert channel_path == f"/channels/{AGENT_CHAT_THREAD}/messages"
    assert payload is not None
    assert "민감 회신 전문" in str(payload["content"])
    assert draft["sha256"] in str(payload["content"])
    assert [item[:2] for item in requests if item[0] == "PUT"] == [
        ("PUT", f"/channels/{AGENT_CHAT_THREAD}/messages/{MESSAGE_ID}/reactions/%E2%9C%85/@me"),
        ("PUT", f"/channels/{AGENT_CHAT_THREAD}/messages/{MESSAGE_ID}/reactions/%E2%9B%94/@me"),
    ]


def test_v1_bound_reply_pending_is_still_consumable(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a reply record stored with the R1 guild binding and an owner ✅ there
    draft = _draft()
    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(_REPO))
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER_ID)
    monkeypatch.setattr(
        triage_confirm,
        "_api",
        _reaction_api(draft, {triage_confirm.APPROVE_EMOJI: [{"id": OWNER_ID, "bot": False}]}),
    )

    # When: current policy has moved new replies to an agent-chat thread, but the stored binding is read
    binding = triage_approval.stored_binding(draft)
    action = triage_confirm.resolve_reaction(draft)

    # Then: the stored v1 guild message remains valid and consumable without retargeting
    assert required_surface(ApprovalKind.MAIL_REPLY) is ApprovalSurface.AGENT_CHAT_THREAD
    assert binding.channel_id == APPROVALS_CHANNEL
    assert binding.surface is ApprovalSurface.SKILL_APPROVALS
    assert action == triage_confirm.APPROVE_EMOJI


def test_post_survives_reaction_http_error(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    draft = _draft(message_id="")
    monkeypatch.setattr(triage_confirm, "post_approval_request", lambda _content, _channel: MESSAGE_ID)

    def fail_reaction(_message_id: str, emoji: str, _channel_id: str) -> None:
        raise HTTPError("https://discord.invalid", 503, "Service Unavailable", {}, None)

    monkeypatch.setattr(triage_confirm, "add_reaction", fail_reaction)

    posted = triage_approval.MailApprovalGate(draft).post(
        SimpleNamespace(channel_id=APPROVALS_CHANNEL)
    )

    assert posted.message_id == MESSAGE_ID
    assert posted.channel_id == APPROVALS_CHANNEL
    assert "APPROVAL-REACTION-FAIL" in capsys.readouterr().err


def test_supersede_deletes_from_the_bound_channel_not_the_guild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a superseded approval message that was posted in the owner's DM
    dm_channel = "100000000000000002"
    calls: list[tuple[str, str]] = []

    def record(method: str, path: str, payload: dict | None = None):
        del payload
        calls.append((method, path))
        return None

    monkeypatch.setattr(triage_confirm, "_api", record)
    # When: the superseded message is deleted
    draft = {**_draft(), "channel_id": dm_channel, "surface": "owner-dm"}
    request = triage_approval.lifecycle().ApprovalRequest(
        key="mail:reply:u-1",
        action_hash=draft["sha256"],
        message_id=MESSAGE_ID,
        channel_id=dm_channel,
        created_at="2026-07-26T00:00:00Z",
    )
    triage_approval.MailApprovalGate(draft).delete(request)
    # Then: deletion uses the channel where the message was actually posted
    assert ("DELETE", f"/channels/{dm_channel}/messages/{MESSAGE_ID}") in calls
    assert ("DELETE", f"/channels/{APPROVALS_CHANNEL}/messages/{MESSAGE_ID}") not in calls


def test_pending_drafts_accepts_legacy_records_with_missing_kind_and_channel(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given: seven production-shaped legacy records beside a current pending draft
    monkeypatch.setenv("TRIAGE_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("TRIAGE_MAIL_HOME", str(tmp_path / "mail"))
    drafts = tmp_path / "gate" / "drafts"
    drafts.mkdir(parents=True)
    current = _draft(message_id="")
    (drafts / "current.json").write_text(json.dumps(current), encoding="utf-8")
    for index in range(7):
        legacy = {**_draft(message_id=""), "id": f"legacy{index}", "kind": None, "channel_id": None}
        (drafts / f"legacy{index}.json").write_text(json.dumps(legacy), encoding="utf-8")
    # When: the lifecycle reads every pending draft
    pending = triage_approval._pending_drafts()
    # Then: legacy records are consumable and do not reject the whole store
    assert len(pending) == 8
    assert {key for _path, _record, key in pending} == {"mail:reply:u-1"}


def test_watch_prefers_owner_cancel_when_both_reactions_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the owner has reacted with both outcomes on the same bound message
    draft = _draft()
    # When: the production watch tick resolves reactions
    sent, discarded, notices = _run_watch(
        monkeypatch,
        draft,
        {
            triage_confirm.APPROVE_EMOJI: [{"id": OWNER_ID, "bot": False}],
            triage_confirm.CANCEL_EMOJI: [{"id": OWNER_ID, "bot": False}],
        },
    )
    # Then: cancellation has strict precedence and prevents delivery
    assert sent == []
    assert discarded == [draft["id"]]
    assert len(notices) == 1
    assert "발송 취소" in notices[0]


@pytest.mark.parametrize("user", [{"id": OWNER_ID, "bot": True}, {"id": "other", "bot": False}])
def test_watch_ignores_bot_or_non_owner_reactions(
    monkeypatch: pytest.MonkeyPatch, user: dict
) -> None:
    # Given: the only approval reaction is from a bot or someone other than the owner
    draft = _draft()
    # When: the production watch tick resolves reactions
    sent, discarded, notices = _run_watch(
        monkeypatch, draft, {triage_confirm.APPROVE_EMOJI: [user]}
    )
    # Then: the draft remains pending with no external effect
    assert sent == [] and discarded == [] and notices == []


def test_reaction_resolver_rejects_message_without_draft_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: the approval message references no hash for this frozen draft
    draft = _draft()
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER_ID)
    monkeypatch.setattr(
        triage_confirm,
        "_api",
        lambda method, path, _payload=None: {"type": 0, "name": "approvals", "recipients": []}
        if method == "GET" and path == f"/channels/{APPROVALS_CHANNEL}"
        else {"content": "unrelated approval message"},
    )
    # When / Then: the resolver fails closed before accepting the reaction
    with pytest.raises(triage_gate.GateError, match="해시"):
        triage_confirm.resolve_reaction(draft)


def test_watch_preadds_approve_then_cancel_reactions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a pending draft that has not yet been posted to #approvals
    draft = _draft(message_id="")
    requests: list[tuple[str, str]] = []
    monkeypatch.setenv("TRIAGE_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("TRIAGE_MAIL_HOME", str(tmp_path / "mail"))
    monkeypatch.setattr(triage_mode, "effective_mode", lambda: "full-go")
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER_ID)
    monkeypatch.setattr(triage_gate, "list_drafts", lambda: [draft])
    monkeypatch.setattr(
        triage_gate,
        "set_message_id",
        lambda item, message_id, _channel_id, **_metadata: {
            **item,
            "message_id": message_id,
        },
    )
    monkeypatch.setattr(triage_gate, "set_approval_binding", lambda item, **_binding: item)
    monkeypatch.setattr(triage_cli, "cmd_process", lambda _args: 0)

    def request(method: str, path: str, payload: dict | None = None):
        del payload
        requests.append((method, path))
        if method == "GET" and path == f"/channels/{APPROVALS_CHANNEL}":
            return {"type": 0, "name": "approvals", "recipients": []}
        if method == "POST":
            return {"id": MESSAGE_ID}
        if method == "GET" and path == f"/channels/{APPROVALS_CHANNEL}/messages/{MESSAGE_ID}":
            return {"content": f"draft sha256:{draft['sha256']}"}
        if method == "GET":
            return []
        return None

    monkeypatch.setattr(triage_confirm, "_api", request)
    # When: the watch tick posts a missing approval request
    assert triage_cli.cmd_watch(argparse.Namespace()) == 0
    # Then: the bot pre-adds the exact reactions in approve, then cancel order
    assert [item for item in requests if item[0] == "PUT"] == [
        ("PUT", f"/channels/{APPROVALS_CHANNEL}/messages/{MESSAGE_ID}/reactions/%E2%9C%85/@me"),
        ("PUT", f"/channels/{APPROVALS_CHANNEL}/messages/{MESSAGE_ID}/reactions/%E2%9B%94/@me"),
    ]


def test_watch_survives_transient_reaction_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: the reaction lookup hits a transient Discord failure (HTTP 429)
    draft = _draft()
    sent: list[triage_gate.Approval] = []
    discarded: list[str] = []

    def rate_limited(method: str, path: str, payload: dict | None = None):
        del method, payload
        if path == f"/channels/{APPROVALS_CHANNEL}":
            return {"type": 0, "name": "approvals", "recipients": []}
        raise triage_confirm.HTTPError("https://discord.test", 429, "Too Many Requests", None, None)

    monkeypatch.setattr(triage_mode, "effective_mode", lambda: "full-go")
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER_ID)
    monkeypatch.setattr(triage_confirm, "_api", rate_limited)
    monkeypatch.setattr(triage_gate, "list_drafts", lambda: [draft])
    monkeypatch.setattr(triage_gate, "execute_draft", lambda _draft, approval: sent.append(approval))
    monkeypatch.setattr(triage_gate, "discard_draft", lambda draft_id: discarded.append(draft_id))
    monkeypatch.setattr(triage_cli, "cmd_process", lambda _args: 0)

    # When: the watch tick runs into the rate limit for this draft
    rc = triage_cli.cmd_watch(argparse.Namespace())

    # Then: the tick survives (rc=0), the draft stays pending for the next tick
    assert rc == 0
    assert sent == [] and discarded == []


def test_watch_send_notification_failure_never_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an approved draft whose success-notification DM will fail
    draft = _draft()
    sent: list[triage_gate.Approval] = []
    monkeypatch.setattr(triage_mode, "effective_mode", lambda: "full-go")
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER_ID)
    monkeypatch.setattr(
        triage_confirm, "_api",
        _reaction_api(draft, {triage_confirm.APPROVE_EMOJI: [{"id": OWNER_ID, "bot": False}]}),
    )
    monkeypatch.setattr(triage_gate, "list_drafts", lambda: [draft])
    monkeypatch.setattr(triage_gate, "execute_draft", lambda _d, approval: sent.append(approval))

    def _boom(_content: str) -> str:
        raise RuntimeError("discord down")

    monkeypatch.setattr(triage_confirm, "dm_owner", _boom)
    # When/Then: the tick still succeeds and the committed send is not undone
    assert triage_cli.cmd_watch(argparse.Namespace()) == 0
    assert len(sent) == 1


def test_dm_owner_chunks_long_content(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: an owner DM whose Korean and ASCII content exceeds one Discord message
    original = ("민감한 초안 review-123 " * 300)[:4500]
    chunks: list[str] = []
    transport = DiscordTransport(token="dummy-token", channel_id="chan-1")

    def send_chunk(_transport: DiscordTransport, chunk: str) -> SentMessage:
        chunks.append(chunk)
        return SentMessage(message_id=str(len(chunks)))

    monkeypatch.setattr(DiscordTransport, "_send_chunk", send_chunk)
    monkeypatch.setattr(triage_confirm, "_dm_transport", lambda _channel_id: transport)
    monkeypatch.setattr(
        triage_approval,
        "approval_directory",
        lambda: SimpleNamespace(owner_dm=lambda: "chan-1"),
    )

    # When: the owner DM is sent through the shared transport
    message_id = triage_confirm.dm_owner(original)

    # Then: three ordered, bounded chunks are sent and the last ID is returned
    assert len(chunks) == 3
    assert all(len(chunk) <= 2000 for chunk in chunks)
    assert chunks == [original[:2000], original[2000:4000], original[4000:]]
    assert "".join(chunks) == original
    assert message_id == "3"


def test_dm_owner_single_send_short_content(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: an owner DM within Discord's message limit
    original = "짧은 owner DM short-내용"
    chunks: list[str] = []
    transport = DiscordTransport(token="dummy-token", channel_id="chan-1")

    def send_chunk(_transport: DiscordTransport, chunk: str) -> SentMessage:
        chunks.append(chunk)
        return SentMessage(message_id=str(len(chunks)))

    monkeypatch.setattr(DiscordTransport, "_send_chunk", send_chunk)
    monkeypatch.setattr(triage_confirm, "_dm_transport", lambda _channel_id: transport)
    monkeypatch.setattr(
        triage_approval,
        "approval_directory",
        lambda: SimpleNamespace(owner_dm=lambda: "chan-1"),
    )

    # When: the short owner DM is sent through the shared transport
    message_id = triage_confirm.dm_owner(original)

    # Then: one unchanged chunk is sent and its ID is returned
    assert chunks == [original]
    assert message_id == "1"


# ------------------------------------------------- origin-thread result routing

ORIGIN_CHANNEL = "200000000000000001"
ORIGIN_MESSAGE = "origin-message-1"
THREAD_ID = "300000000000000001"


def _origin_draft() -> dict:
    return {**_draft(), "origin_channel_id": ORIGIN_CHANNEL, "origin_message_id": ORIGIN_MESSAGE}


def _origin_api(
    draft: dict,
    users_by_emoji: dict[str, list[dict]],
    thread_calls: list[dict | None],
    *,
    thread_fail: bool = False,
):
    base = _reaction_api(draft, users_by_emoji)

    def request(method: str, path: str, payload: dict | None = None):
        if method == "POST" and path == (
            f"/channels/{ORIGIN_CHANNEL}/messages/{ORIGIN_MESSAGE}/threads"
        ):
            if thread_fail:
                raise RuntimeError("thread API down")
            thread_calls.append(payload)
            return {"id": THREAD_ID}
        return base(method, path, payload)

    return request


def _run_origin_watch(
    monkeypatch: pytest.MonkeyPatch,
    draft: dict,
    users_by_emoji: dict[str, list[dict]],
    *,
    thread_fail: bool = False,
):
    sent: list[triage_gate.Approval] = []
    discarded: list[str] = []
    dm_notices: list[str] = []
    thread_calls: list[dict | None] = []
    thread_posts: list[tuple[str, str]] = []
    monkeypatch.setattr(triage_mode, "effective_mode", lambda: "full-go")
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER_ID)
    monkeypatch.setattr(
        triage_confirm, "_api",
        _origin_api(draft, users_by_emoji, thread_calls, thread_fail=thread_fail),
    )
    monkeypatch.setattr(triage_gate, "list_drafts", lambda: [draft])
    monkeypatch.setattr(triage_gate, "execute_draft", lambda _d, approval: sent.append(approval))
    monkeypatch.setattr(triage_gate, "discard_draft", lambda draft_id: discarded.append(draft_id))
    monkeypatch.setattr(triage_confirm, "dm_owner", lambda content: dm_notices.append(content))

    class _ThreadTransport:
        def __init__(self, channel_id: str) -> None:
            self.channel_id = channel_id

        def send(self, content: str) -> tuple[SentMessage, ...]:
            thread_posts.append((self.channel_id, content))
            return (SentMessage(message_id="thread-post-1"),)

    monkeypatch.setattr(triage_confirm, "_dm_transport", _ThreadTransport)
    monkeypatch.setattr(triage_cli, "cmd_process", lambda _args: 0)
    assert triage_cli.cmd_watch(argparse.Namespace()) == 0
    return sent, discarded, dm_notices, thread_calls, thread_posts


def test_watch_posts_send_result_to_origin_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a draft carrying its origin channel/message and an owner-only ✅
    draft = _origin_draft()
    # When: the production watch tick resolves the approval
    sent, discarded, dm_notices, thread_calls, thread_posts = _run_origin_watch(
        monkeypatch, draft, {triage_confirm.APPROVE_EMOJI: [{"id": OWNER_ID, "bot": False}]}
    )
    # Then: the result lands in the origin-channel thread, never the owner DM
    assert len(sent) == 1 and discarded == []
    assert dm_notices == []
    assert len(thread_calls) == 1
    assert [channel for channel, _content in thread_posts] == [THREAD_ID]
    content = thread_posts[0][1]
    assert "발송 완료" in content
    assert "Re: 일정 문의" in content
    assert "owner@example.invalid" in content
    assert "abc123" in content


def test_watch_posts_cancel_result_to_origin_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: an origin-bound draft the owner cancels with ⛔
    draft = _origin_draft()
    # When: the production watch tick resolves the cancellation
    sent, discarded, dm_notices, _thread_calls, thread_posts = _run_origin_watch(
        monkeypatch, draft, {triage_confirm.CANCEL_EMOJI: [{"id": OWNER_ID, "bot": False}]}
    )
    # Then: the discard result lands in the origin thread with full mail context
    assert sent == []
    assert discarded == [draft["id"]]
    assert dm_notices == []
    assert [channel for channel, _content in thread_posts] == [THREAD_ID]
    content = thread_posts[0][1]
    assert "발송 취소" in content
    assert "Re: 일정 문의" in content
    assert "owner@example.invalid" in content
    assert "abc123" in content
    assert triage_confirm.CANCEL_EMOJI in content


def test_watch_falls_back_to_dm_when_thread_creation_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: an origin-bound approved draft whose thread creation will fail
    draft = _origin_draft()
    # When: the watch tick sends and tries to report to the origin thread
    sent, _discarded, dm_notices, _thread_calls, thread_posts = _run_origin_watch(
        monkeypatch, draft,
        {triage_confirm.APPROVE_EMOJI: [{"id": OWNER_ID, "bot": False}]},
        thread_fail=True,
    )
    # Then: the committed send survives and the result falls back to the owner DM
    assert len(sent) == 1
    assert thread_posts == []
    assert len(dm_notices) == 1 and "발송 완료" in dm_notices[0]
    assert "NOTIFY-THREAD-FAIL" in capsys.readouterr().err


def test_notify_result_falls_back_to_owner_when_helper_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: the interop runtime lacks origin_notice (stale runtime / sandbox)
    notices: list[str] = []
    monkeypatch.setattr(triage_confirm, "dm_owner", lambda content: notices.append(content) or "dm-1")

    def missing():
        raise ImportError("No module named 'automation'")

    monkeypatch.setattr(triage_confirm, "_origin_notice", missing)
    # When: an origin-bound result is delivered
    result = triage_confirm.notify_result(_origin_draft(), "결과")
    # Then: the owner still gets it through the legacy path, with a marker
    assert result == "dm-1" and notices == ["결과"]
    assert "NOTIFY-HELPER-MISSING" in capsys.readouterr().err


# ------------------------------------------------ per-request approval thread (S6)

REQUEST_THREAD = "400000000000000001"
REQUEST_THREAD_NAME = "↩️ 메일 회신 · Re: 일정 문의"


class _RequestThreadDirectory:
    """Directory that records the thread spec the mail binding asks for."""

    def __init__(self) -> None:
        self.requests: list[tuple[object, object]] = []

    def owner_dm(self) -> str:
        return DM_CHANNEL

    def skill_approvals(self) -> str:
        return APPROVALS_CHANNEL

    def agent_chat(self) -> str:
        return AGENT_CHAT_CHANNEL

    def agent_chat_thread(self, kind: object) -> str:
        raise AssertionError(f"mail must not reuse the per-kind thread: {kind}")

    def agent_chat_request_thread(self, kind: object, request: object) -> str:
        self.requests.append((kind, request))
        return REQUEST_THREAD

    def describe(self, channel_id: str) -> ChannelFacts:
        if channel_id == REQUEST_THREAD:
            return ChannelFacts(11, REQUEST_THREAD_NAME, (), AGENT_CHAT_CHANNEL)
        raise AssertionError(f"unexpected channel: {channel_id}")


def _unbound_draft() -> dict:
    record = {
        **_draft(message_id=""),
        "channel_id": "",
        "surface": None,
        "policy_version": None,
        "origin_channel_id": ORIGIN_CHANNEL,
        "origin_message_id": ORIGIN_MESSAGE,
    }
    record["sha256"] = triage_core.draft_sha256(record)
    return record


def test_stored_binding_declares_read_only_field_types() -> None:
    contract = get_type_hints(triage_binding.stored_binding)["return"]
    assert contract is not object
    for name, expected in {
        "surface": str, "channel_id": str, "policy_version": int, "guild_id": str | None,
    }.items():
        field = getattr(contract, name)
        assert isinstance(field, property)
        assert field.fset is None
        assert field.fget is not None
        assert get_type_hints(field.fget)["return"] == expected
    assert get_type_hints(triage_binding.approval_thread_id)["binding"] is contract


def test_new_binding_opens_a_thread_for_this_request(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: an unposted reply draft carrying the owner's instruction origin
    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(_REPO))
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER_ID)
    directory = _RequestThreadDirectory()
    monkeypatch.setattr(triage_binding, "approval_directory", lambda: directory)

    # When: the gate resolves a fresh binding for it
    binding = triage_approval.stored_binding(_unbound_draft())

    # Then: the binding IS this request's own thread, asked for with the draft subject
    assert binding.channel_id == REQUEST_THREAD
    assert binding.surface is ApprovalSurface.AGENT_CHAT_THREAD
    assert directory.requests == [(
        ApprovalKind.MAIL_REPLY,
        RequestThread(
            title="Re: 일정 문의",
            origin_channel_id=ORIGIN_CHANNEL,
            origin_message_id=ORIGIN_MESSAGE,
        ),
    )]


def test_confirm_intent_persists_the_request_thread_outside_the_action_hash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: a freshly created reply draft in an isolated store
    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(_REPO))
    monkeypatch.setenv("TRIAGE_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("TRIAGE_MAIL_HOME", str(tmp_path / "mail"))
    monkeypatch.setenv("TRIAGE_MAILON_PYTHON", "python3")
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER_ID)
    monkeypatch.setattr(triage_binding, "approval_directory", lambda: _RequestThreadDirectory())
    draft = triage_gate.create_draft(
        uid="u-1", sender="발신자 <s@example.invalid>", mail_subject="일정 문의",
        to="owner@example.invalid", subject="Re: 일정 문의", body="본문", category="important", flags=("reply_needed",), kind="reply",
        origin_channel_id=ORIGIN_CHANNEL, origin_message_id=ORIGIN_MESSAGE,
    )

    # When: the approval intent is built — the one place the binding is decided
    intent = triage_approval.confirm_intent(draft)

    # Then: the record remembers the thread the result notice must return to...
    [stored] = triage_gate.list_drafts()
    assert intent.channel_id == REQUEST_THREAD
    assert stored["approval_thread_id"] == REQUEST_THREAD
    # Unknown guild coordinates stay absent through intent persistence and reload.
    assert "approval_guild_id" not in draft
    assert "approval_guild_id" not in stored
    assert triage_binding.stored_binding(stored).guild_id is None
    # ...and that memory is outside the approval action hash.
    assert stored["sha256"] == draft["sha256"] == triage_core.draft_sha256(stored)
    assert intent.action_hash == draft["sha256"]


def _thread_draft() -> dict:
    record = {
        **_draft(),
        "approval_thread_id": REQUEST_THREAD,
        "channel_id": REQUEST_THREAD,
        "origin_channel_id": ORIGIN_CHANNEL,
        "origin_message_id": ORIGIN_MESSAGE,
        "policy_version": 8,
        "surface": "agent-chat-thread",
    }
    record["sha256"] = triage_core.draft_sha256(record)
    return record


@pytest.mark.parametrize(("metadata", "expected"), [
    pytest.param({"approval_guild_id": 123}, "123", id="json-number-123"),
    pytest.param({}, None, id="absent"),
    pytest.param({"approval_guild_id": None}, None, id="null"),
    pytest.param({"approval_guild_id": "111"}, "111", id="string"),
    pytest.param({"approval_guild_id": False}, "False", id="boolean"),
    pytest.param({"approval_guild_id": 123.5}, "123.5", id="float"),
    pytest.param({"approval_guild_id": []}, "[]", id="array"),
    pytest.param({"approval_guild_id": {}}, "{}", id="object"),
])
def test_json_loaded_stored_binding_preserves_contract_and_existing_card(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    metadata: dict[str, object], expected: str | None,
) -> None:
    from automation.interop.owner_message import LinkStatus, discord_link

    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(_REPO))
    monkeypatch.setenv("TRIAGE_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("TRIAGE_MAIL_HOME", str(tmp_path / "mail"))
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER_ID)
    interop = tmp_path / "interop.json"
    interop.write_text(json.dumps({"agent_chat_channel_id": AGENT_CHAT_CHANNEL}), encoding="utf-8")
    monkeypatch.setenv("INTEROP_CONFIG", str(interop))
    record = {**_thread_draft(), "created": triage_core.utc_now(), **metadata}
    path = triage_gate._public_drafts_dir() / f"{record['id']}.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    before = path.read_bytes()
    draft = triage_gate.load_draft(str(record["id"]))
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(triage_confirm, "_api", _thread_api(draft, {}, calls))

    def forbid_render(*_args: object, **_kwargs: object) -> str:
        pytest.fail("an existing card must not be rendered again")

    monkeypatch.setattr(triage_core, "render_approvals_message", forbid_render)
    binding = triage_binding.stored_binding(draft)
    assert binding.guild_id == expected
    assert binding.guild_id is None or isinstance(binding.guild_id, str)
    assert isinstance(binding.surface, str)
    assert isinstance(binding.channel_id, str)
    assert isinstance(binding.policy_version, int)
    assert (binding.surface, binding.channel_id, binding.policy_version) == (
        ApprovalSurface.AGENT_CHAT_THREAD, REQUEST_THREAD, 8,
    )
    link = discord_link(
        space="unknown" if expected is None else "guild",
        guild_id=binding.guild_id, channel_id=binding.channel_id, message_id="888",
    )
    if expected is not None and expected.isdigit():
        assert link.url == f"https://discord.com/channels/{expected}/{REQUEST_THREAD}/888"
    else:
        assert link.url is None
        assert link.status is (LinkStatus.UNAVAILABLE if expected is None else LinkStatus.INVALID)
    assert triage_approval.post_for_approval(draft) == MESSAGE_ID
    assert path.read_bytes() == before
    assert draft == json.loads(before)
    assert all(call[0] == "GET" for call in calls)


@pytest.mark.parametrize(("field", "value"), [
    ("channel_id", 123), ("channel_id", None),
    ("surface", 123), ("surface", None),
    ("policy_version", "8"), ("policy_version", 8.0), ("policy_version", None),
])
def test_json_loaded_stored_binding_rejects_other_member_type_mismatches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, field: str, value: object,
) -> None:
    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(_REPO))
    monkeypatch.setenv("TRIAGE_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("TRIAGE_MAIL_HOME", str(tmp_path / "mail"))
    directory = _RequestThreadDirectory()
    monkeypatch.setattr(triage_binding, "approval_directory", lambda: directory)
    record = {**_thread_draft(), field: value}
    path = triage_gate._public_drafts_dir() / f"{record['id']}.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(triage_gate.GateError) as caught:
        triage_binding.stored_binding(triage_gate.load_draft(str(record["id"])))
    assert caught.value.exit_code == 3
    assert path.read_bytes() == before
    assert directory.requests == []


def _thread_api(draft: dict, users_by_emoji: dict[str, list[dict]], calls: list[tuple]):
    def request(method: str, path: str, payload: dict | None = None):
        calls.append((method, path, payload))
        if method == "GET" and path == f"/channels/{REQUEST_THREAD}":
            return {
                "id": REQUEST_THREAD, "type": 11,
                "name": REQUEST_THREAD_NAME, "parent_id": AGENT_CHAT_CHANNEL,
            }
        if method == "GET" and path == f"/channels/{REQUEST_THREAD}/messages/{MESSAGE_ID}":
            return {"content": f"draft sha256:{draft['sha256']}"}
        for emoji, users in users_by_emoji.items():
            quoted = triage_confirm.quote(emoji, safe="")
            if method == "GET" and path.endswith(f"/reactions/{quoted}?limit=100"):
                return users
        if method == "GET" and "/reactions/" in path:
            return []
        if method == "PATCH" and path == f"/channels/{REQUEST_THREAD}":
            return {"id": REQUEST_THREAD}
        raise AssertionError(f"unexpected Discord call: {method} {path}")

    return request


def _run_thread_watch(
    monkeypatch: pytest.MonkeyPatch, draft: dict, users_by_emoji: dict[str, list[dict]]
):
    sent: list[triage_gate.Approval] = []
    dm_notices: list[str] = []
    calls: list[tuple] = []
    thread_posts: list[tuple[str, str]] = []
    monkeypatch.setattr(triage_mode, "effective_mode", lambda: "full-go")
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER_ID)
    monkeypatch.setattr(triage_confirm, "_api", _thread_api(draft, users_by_emoji, calls))
    monkeypatch.setattr(triage_gate, "list_drafts", lambda: [draft])
    monkeypatch.setattr(triage_gate, "execute_draft", lambda _d, approval: sent.append(approval))
    monkeypatch.setattr(triage_gate, "discard_draft", lambda _draft_id: None)
    monkeypatch.setattr(triage_confirm, "dm_owner", lambda content: dm_notices.append(content))

    class _ThreadTransport:
        def __init__(self, channel_id: str) -> None:
            self.channel_id = channel_id

        def send(self, content: str) -> tuple[SentMessage, ...]:
            thread_posts.append((self.channel_id, content))
            return (SentMessage(message_id="thread-post-1"),)

    monkeypatch.setattr(triage_confirm, "_dm_transport", _ThreadTransport)
    monkeypatch.setattr(triage_cli, "cmd_process", lambda _args: 0)
    assert triage_cli.cmd_watch(argparse.Namespace()) == 0
    return sent, dm_notices, calls, thread_posts


def test_watch_closes_the_request_thread_when_the_mail_is_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a draft whose approval lives in its own request thread, approved with ✅
    draft = _thread_draft()
    # When: the production watch tick sends it
    sent, dm_notices, calls, thread_posts = _run_thread_watch(
        monkeypatch, draft, {triage_confirm.APPROVE_EMOJI: [{"id": OWNER_ID, "bot": False}]}
    )
    # Then: the result lands in that same thread — no new thread, no owner fallback...
    assert len(sent) == 1 and dm_notices == []
    assert [method for method, _path, _payload in calls if method == "POST"] == []
    assert [channel for channel, _content in thread_posts] == [REQUEST_THREAD]
    assert "발송 완료" in thread_posts[0][1]
    # ...and the thread is renamed with its terminal status and archived.
    assert [(path, payload) for method, path, payload in calls if method == "PATCH"] == [
        (f"/channels/{REQUEST_THREAD}",
         {"archived": True, "name": f"✅ 완료 · {REQUEST_THREAD_NAME}"}),
    ]


def test_watch_closes_the_request_thread_when_the_owner_cancels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the same request-thread draft, cancelled with ⛔
    draft = _thread_draft()
    # When: the production watch tick resolves the cancellation
    sent, dm_notices, calls, thread_posts = _run_thread_watch(
        monkeypatch, draft, {triage_confirm.CANCEL_EMOJI: [{"id": OWNER_ID, "bot": False}]}
    )
    # Then: the cancel notice lands in the thread, which is closed as cancelled
    assert sent == [] and dm_notices == []
    assert [channel for channel, _content in thread_posts] == [REQUEST_THREAD]
    assert "발송 취소" in thread_posts[0][1]
    assert [(path, payload) for method, path, payload in calls if method == "PATCH"] == [
        (f"/channels/{REQUEST_THREAD}",
         {"archived": True, "name": f"⛔ 취소 · {REQUEST_THREAD_NAME}"}),
    ]
