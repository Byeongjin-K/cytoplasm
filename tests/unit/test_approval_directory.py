from __future__ import annotations

import hashlib
import json
from dataclasses import fields
from pathlib import Path
from urllib.error import HTTPError

import pytest

from automation.interop.approval_directory import DiscordChannelDirectory, JsonValue
from automation.interop.approval_surface import ApprovalKind, ApprovalSurfaceError, ChannelFacts

RequestKey = tuple[str, str]
RecordedCall = tuple[str, str, dict[str, JsonValue] | None]
ApiResponse = JsonValue | BaseException

# A value shaped like a credential-bearing request. It is embedded in every injected
# failure below so that any assertion about the raised message also proves the message
# carries nothing from the untrusted `api` callable's own exception text.
SECRET_CANARY = "Bot-CANARY-nf1-do-not-leak"


class _LocalGateError(RuntimeError):
    """Stands in for a caller-side failure raised before any network call happens."""


class FakeApi:
    def __init__(self, token: str, responses: dict[RequestKey, ApiResponse]) -> None:
        self.token = token
        self.calls: list[RecordedCall] = []
        self.responses = responses

    def __call__(
        self,
        method: str,
        path: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> JsonValue:
        self.calls.append((method, path, payload))
        try:
            response = self.responses[(method, path)]
        except KeyError as error:
            raise AssertionError(f"unexpected Discord request: {method} {path}") from error
        if isinstance(response, BaseException):
            raise response
        return response


@pytest.fixture
def interop_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "interop.json"
    monkeypatch.setenv("INTEROP_CONFIG", str(path))
    return path


def _directory(fake: FakeApi, cache_path: Path | None = None) -> DiscordChannelDirectory:
    return DiscordChannelDirectory(token=fake.token, owner_id="owner", api=fake, cache_path=cache_path)


def _fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _guild_scan_responses(channel_id: str) -> dict[RequestKey, ApiResponse]:
    return {
        ("GET", "/users/@me/guilds"): [{"id": "guild"}],
        ("GET", "/guilds/guild/channels"): [{"id": channel_id, "type": 0, "name": "approvals"}],
    }


def test_owner_dm_posts_recipient_to_the_instance_bound_api(interop_config: Path) -> None:
    # Given: a fake API bound to this directory's bot token.
    fake = FakeApi("bot-a", {("POST", "/users/@me/channels"): {"id": "dm-a"}})
    directory = _directory(fake)

    # When: the owner DM is opened.
    channel_id = directory.owner_dm()

    # Then: this token-bound API opened exactly the owner's DM.
    assert channel_id == "dm-a"
    assert fake.calls == [("POST", "/users/@me/channels", {"recipient_id": "owner"})]


def test_injected_api_owns_auth_when_no_token_is_available(interop_config: Path) -> None:
    # Given: a caller-owned API adapter whose credential is unavailable to the directory.
    fake = FakeApi("adapter-owned", {("POST", "/users/@me/channels"): {"id": "dm-a"}})
    directory = DiscordChannelDirectory(token=None, owner_id="owner", api=fake)

    # When: the directory resolves and describes through that injected adapter.
    channel_id = directory.owner_dm()

    # Then: no fake token is needed; the adapter is the sole authentication boundary.
    assert channel_id == "dm-a"
    assert fake.calls == [("POST", "/users/@me/channels", {"recipient_id": "owner"})]


def test_tokenless_injected_api_never_reuses_or_writes_a_token_cache(
    interop_config: Path,
    tmp_path: Path,
) -> None:
    # Given: a stale token-keyed cache and an injected adapter with its own credential.
    cache_path = tmp_path / "channel.json"
    original = json.dumps(
        {"token_fingerprint": _fingerprint("unrelated"), "approvals_channel_id": "stale"}
    )
    cache_path.write_text(original, encoding="utf-8")
    fake = FakeApi("adapter-owned", _guild_scan_responses("scanned"))
    directory = DiscordChannelDirectory(
        token=None,
        owner_id="owner",
        api=fake,
        cache_path=cache_path,
    )

    # When: skill approvals are resolved.
    channel_id = directory.skill_approvals()

    # Then: the scan result is used without fingerprinting a credential the directory does not own.
    assert channel_id == "scanned"
    assert cache_path.read_text(encoding="utf-8") == original


def test_two_bot_tokens_never_share_a_dm_channel_id(interop_config: Path, tmp_path: Path) -> None:
    # Given: two bot-specific APIs and one shared skill-channel cache path.
    first = FakeApi("bot-a", {("POST", "/users/@me/channels"): {"id": "dm-a"}})
    second = FakeApi("bot-b", {("POST", "/users/@me/channels"): {"id": "dm-b"}})
    cache_path = tmp_path / "channel.json"

    # When: both bots open their own DM.
    first_id = _directory(first, cache_path).owner_dm()
    second_id = _directory(second, cache_path).owner_dm()

    # Then: neither identity nor disk state is shared across bot tokens.
    assert (first_id, second_id) == ("dm-a", "dm-b")
    assert len(first.calls) == len(second.calls) == 1
    assert not cache_path.exists()


def test_owner_dm_memoises_per_instance(interop_config: Path) -> None:
    # Given: one owner-DM response.
    fake = FakeApi("bot-a", {("POST", "/users/@me/channels"): {"id": "dm-a"}})
    directory = _directory(fake)

    # When: the DM is requested twice.
    channel_ids = (directory.owner_dm(), directory.owner_dm())

    # Then: both calls use the one instance-local result.
    assert channel_ids == ("dm-a", "dm-a")
    assert len(fake.calls) == 1


def test_describe_maps_discord_channel_facts_faithfully(interop_config: Path) -> None:
    # Given: a complete Discord channel response.
    fake = FakeApi("bot-a", {("GET", "/channels/42"): {
        "type": 1,
        "name": "owner-dm",
        "recipients": [{"id": "owner"}, {"id": "bot-a"}],
    }})

    # When: facts are requested.
    facts = _directory(fake).describe("42")

    # Then: every approval-relevant field is preserved.
    assert facts == ChannelFacts(1, "owner-dm", ("owner", "bot-a"))


def test_describe_refuses_a_404(interop_config: Path) -> None:
    # Given: Discord reports the channel absent.
    error = HTTPError("https://discord.test/channels/404", 404, "missing", None, None)
    fake = FakeApi("bot-a", {("GET", "/channels/404"): error})

    # When / Then: facts are unavailable and cannot be assumed valid.
    with pytest.raises(ApprovalSurfaceError):
        _directory(fake).describe("404")


def test_skill_approvals_refuses_two_guild_matches(interop_config: Path) -> None:
    # Given: two guilds each expose an approvals channel.
    fake = FakeApi("bot-a", {
        ("GET", "/users/@me/guilds"): [{"id": "one"}, {"id": "two"}],
        ("GET", "/guilds/one/channels"): [{"id": "11", "type": 0, "name": "approvals"}],
        ("GET", "/guilds/two/channels"): [{"id": "22", "type": 0, "name": "approvals"}],
    })

    # When / Then: ambiguity fails closed.
    with pytest.raises(ApprovalSurfaceError):
        _directory(fake).skill_approvals()


def test_skill_approvals_refuses_zero_guild_matches(interop_config: Path) -> None:
    # Given: no guild channel is named approvals.
    fake = FakeApi("bot-a", {
        ("GET", "/users/@me/guilds"): [{"id": "guild"}],
        ("GET", "/guilds/guild/channels"): [{"id": "other", "type": 0, "name": "general"}],
    })

    # When / Then: absence fails closed.
    with pytest.raises(ApprovalSurfaceError):
        _directory(fake).skill_approvals()


def test_skill_approvals_ignores_a_flow_specific_env_var(
    interop_config: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a stale per-flow override left in an operator's environment, and the
    # configured channel R3 left as the compatibility source that still counts.
    monkeypatch.setenv("TRIAGE_APPROVALS_CHANNEL_ID", "999999999999999999")
    interop_config.write_text(json.dumps({"personal_approvals_channel_id": "configured"}), encoding="utf-8")
    fake = FakeApi("bot-a", {})

    # When: this flow resolves its skill approvals surface.
    channel_id = _directory(fake).skill_approvals()

    # Then: the retired env name is not consulted at all — config decides, and the
    # bogus id never reaches a record. AS-3.2 removed the branch, so no caller can
    # re-wire one either: the constructor no longer carries the seam.
    assert channel_id == "configured"
    assert fake.calls == []
    assert "approval_env_var" not in {field.name for field in fields(DiscordChannelDirectory)}


def test_skill_approvals_prefers_config_over_cache(interop_config: Path, tmp_path: Path) -> None:
    # Given: both the configured value and a current-token cached value.
    interop_config.write_text(json.dumps({"personal_approvals_channel_id": "configured"}), encoding="utf-8")
    cache_path = tmp_path / "channel.json"
    cache_path.write_text(
        json.dumps({"token_fingerprint": _fingerprint("bot-a"), "approvals_channel_id": "cached"}),
        encoding="utf-8",
    )
    fake = FakeApi("bot-a", {})

    # When: the channel is resolved.
    channel_id = _directory(fake, cache_path).skill_approvals()

    # Then: the config branch wins before disk cache.
    assert channel_id == "configured"
    assert fake.calls == []


def test_skill_approvals_uses_only_a_current_token_cache(interop_config: Path, tmp_path: Path) -> None:
    # Given: a cache entry keyed for this bot token.
    cache_path = tmp_path / "channel.json"
    cache_path.write_text(
        json.dumps({"token_fingerprint": _fingerprint("bot-a"), "approvals_channel_id": "cached"}),
        encoding="utf-8",
    )
    fake = FakeApi("bot-a", {})

    # When: the channel is resolved without a configured value.
    channel_id = _directory(fake, cache_path).skill_approvals()

    # Then: the matching cache is used and no guild scan occurs.
    assert channel_id == "cached"
    assert fake.calls == []


def test_skill_approvals_ignores_a_different_bot_cache(interop_config: Path, tmp_path: Path) -> None:
    # Given: the cache belongs to another bot token.
    cache_path = tmp_path / "channel.json"
    cache_path.write_text(
        json.dumps({"token_fingerprint": _fingerprint("bot-b"), "approvals_channel_id": "other-bot"}),
        encoding="utf-8",
    )
    fake = FakeApi("bot-a", _guild_scan_responses("scanned"))

    # When: bot-a resolves the channel.
    channel_id = _directory(fake, cache_path).skill_approvals()

    # Then: it scans instead of reusing bot-b's cache entry.
    assert channel_id == "scanned"
    assert [call[:2] for call in fake.calls] == [
        ("GET", "/users/@me/guilds"),
        ("GET", "/guilds/guild/channels"),
    ]


def test_guild_scan_writes_a_private_cache(interop_config: Path, tmp_path: Path) -> None:
    # Given: one uniquely matching guild channel and an empty cache path.
    cache_path = tmp_path / "channel.json"
    fake = FakeApi("bot-a", _guild_scan_responses("scanned"))

    # When: scanning resolves the channel.
    channel_id = _directory(fake, cache_path).skill_approvals()

    # Then: the resolved value is stored with owner-only file mode.
    assert channel_id == "scanned"
    assert cache_path.stat().st_mode & 0o777 == 0o600
    assert json.loads(cache_path.read_text(encoding="utf-8")) == {
        "token_fingerprint": _fingerprint("bot-a"),
        "approvals_channel_id": "scanned",
    }


def test_describe_refuses_a_malformed_api_body(interop_config: Path) -> None:
    # Given: an unexpected channel body.
    fake = FakeApi("bot-a", {("GET", "/channels/42"): {"type": "one", "name": "approvals"}})

    # When / Then: malformed facts cannot become a trusted surface.
    with pytest.raises(ApprovalSurfaceError):
        _directory(fake).describe("42")


def test_skill_approvals_refuses_a_malformed_cache_without_guild_fallback(
    interop_config: Path,
    tmp_path: Path,
) -> None:
    # Given: an unreadable cache and a guild response that would otherwise resolve.
    cache_path = tmp_path / "channel.json"
    cache_path.write_text("not-json", encoding="utf-8")
    fake = FakeApi("bot-a", _guild_scan_responses("should-not-be-used"))

    # When / Then: cache failure does not silently switch to guild scan.
    with pytest.raises(ApprovalSurfaceError):
        _directory(fake, cache_path).skill_approvals()
    assert fake.calls == []


def test_request_when_the_injected_api_fails_locally_then_names_only_the_cause_type(
    interop_config: Path,
) -> None:
    # Given: the injected api fails before any network call, with a secret in its text.
    cause = _LocalGateError(f"DISCORD_BOT_TOKEN 없음 ({SECRET_CANARY})")
    fake = FakeApi("bot-a", {("POST", "/users/@me/channels"): cause})

    # When: the owner DM is opened.
    with pytest.raises(ApprovalSurfaceError) as excinfo:
        _directory(fake).owner_dm()

    # Then: the cause type names the failure while its untrusted text stays behind.
    message = str(excinfo.value)
    assert "cause=_LocalGateError" in message
    assert SECRET_CANARY not in message
    assert excinfo.value.__cause__ is cause


def test_request_when_discord_returns_http_error_then_exposes_only_the_status(
    interop_config: Path,
) -> None:
    # Given: Discord rejects the call and the error carries the request url and body.
    cause = HTTPError(
        url=f"https://discord.com/api/v10/users/@me/channels?t={SECRET_CANARY}",
        code=401,
        msg=f"Unauthorized {SECRET_CANARY}",
        hdrs=None,
        fp=None,
    )
    fake = FakeApi("bot-a", {("POST", "/users/@me/channels"): cause})

    # When: the owner DM is opened.
    with pytest.raises(ApprovalSurfaceError) as excinfo:
        _directory(fake).owner_dm()

    # Then: only the integer status crosses the boundary — never url, msg or body.
    message = str(excinfo.value)
    assert "http_status=401" in message
    assert "cause=HTTPError" in message
    assert SECRET_CANARY not in message
    assert "https://" not in message


def test_request_when_causes_differ_then_the_two_messages_differ(interop_config: Path) -> None:
    # Given: the same endpoint failing for two structurally different reasons.
    path = "/users/@me/channels"
    local = FakeApi("bot-a", {("POST", path): _LocalGateError(f"no token ({SECRET_CANARY})")})
    remote = FakeApi(
        "bot-a",
        {("POST", path): HTTPError(f"https://discord.test{path}", 401, "Unauthorized", None, None)},
    )

    # When: both are driven through the same request.
    with pytest.raises(ApprovalSurfaceError) as local_error:
        _directory(local).owner_dm()
    with pytest.raises(ApprovalSurfaceError) as remote_error:
        _directory(remote).owner_dm()

    # Then: a deploy log can tell a missing credential apart from a rejected call.
    assert str(local_error.value) != str(remote_error.value)


def test_request_when_the_api_raises_an_approval_surface_error_then_it_passes_through_unchanged(
    interop_config: Path,
) -> None:
    # Given: the api itself already refused with a surface-level diagnosis.
    cause = ApprovalSurfaceError("guild list response is malformed")
    fake = FakeApi("bot-a", {("POST", "/users/@me/channels"): cause})

    # When: the owner DM is opened.
    with pytest.raises(ApprovalSurfaceError) as excinfo:
        _directory(fake).owner_dm()

    # Then: the original diagnosis is neither rewritten nor re-wrapped.
    assert str(excinfo.value) == "guild list response is malformed"
    assert excinfo.value.__cause__ is None


def test_request_when_wrapping_a_cause_then_the_message_stays_short_enough_to_survive_redaction(
    interop_config: Path,
) -> None:
    # Given: a wrapped failure on the longest approval-path endpoint.
    fake = FakeApi(
        "bot-a",
        {("POST", "/users/@me/channels"): _LocalGateError(f"boom ({SECRET_CANARY})")},
    )

    # When: the owner DM is opened.
    with pytest.raises(ApprovalSurfaceError) as excinfo:
        _directory(fake).owner_dm()

    # Then: skills/mail/scripts/triage_cli.py:125 logs redact(str(error))[:120], so a
    # longer message would slice the new diagnostic out of the very log this fixes.
    assert len(str(excinfo.value)) <= 120


def _agent_chat_config(interop_config: Path, channel_id: str) -> None:
    interop_config.write_text(
        json.dumps({"agent_chat_channel_id": channel_id}), encoding="utf-8",
    )


def test_agent_chat_thread_refuses_an_unset_config_key(interop_config: Path) -> None:
    # Given: an interop config carrying no agent-chat channel id.
    interop_config.write_text("{}", encoding="utf-8")
    fake = FakeApi("bot-a", {})
    directory = _directory(fake)

    # When / Then: resolution fails closed before any Discord request is made.
    with pytest.raises(ApprovalSurfaceError):
        _ = directory.agent_chat_thread(ApprovalKind.TODO)
    assert fake.calls == []


def test_agent_chat_thread_reuses_an_active_thread(interop_config: Path) -> None:
    # Given: an active 승인-todo thread already hangs under the configured channel.
    _agent_chat_config(interop_config, "chan-1")
    fake = FakeApi("bot-a", {
        ("GET", "/channels/chan-1"): {"id": "chan-1", "type": 0, "guild_id": "guild-1"},
        ("GET", "/guilds/guild-1/threads/active"): {"threads": [
            {"id": "thread-9", "name": "승인-todo", "parent_id": "chan-1", "type": 11},
        ]},
    })
    directory = _directory(fake)

    # When: the todo approval surface is resolved.
    thread_id = directory.agent_chat_thread(ApprovalKind.TODO)

    # Then: the existing thread is reused and nothing is created.
    assert thread_id == "thread-9"
    assert all(method != "POST" for method, _path, _payload in fake.calls)


def test_agent_chat_thread_ignores_a_foreign_or_misnamed_thread(interop_config: Path) -> None:
    # Given: active threads that either hang elsewhere or carry another kind's name.
    _agent_chat_config(interop_config, "chan-1")
    fake = FakeApi("bot-a", {
        ("GET", "/channels/chan-1"): {"id": "chan-1", "type": 0, "guild_id": "guild-1"},
        ("GET", "/guilds/guild-1/threads/active"): {"threads": [
            {"id": "thread-7", "name": "승인-todo", "parent_id": "chan-2", "type": 11},
            {"id": "thread-8", "name": "승인-wiki", "parent_id": "chan-1", "type": 11},
        ]},
        ("GET", "/channels/chan-1/threads/archived/public"): {"threads": []},
        ("POST", "/channels/chan-1/threads"): {"id": "thread-new"},
    })
    directory = _directory(fake)

    # When / Then: neither candidate matches, so a fresh thread is created.
    assert directory.agent_chat_thread(ApprovalKind.TODO) == "thread-new"


def test_agent_chat_thread_revives_an_archived_thread(interop_config: Path) -> None:
    # Given: the 승인-todo thread exists but has been auto-archived.
    _agent_chat_config(interop_config, "chan-1")
    fake = FakeApi("bot-a", {
        ("GET", "/channels/chan-1"): {"id": "chan-1", "type": 0, "guild_id": "guild-1"},
        ("GET", "/guilds/guild-1/threads/active"): {"threads": []},
        ("GET", "/channels/chan-1/threads/archived/public"): {"threads": [
            {"id": "thread-5", "name": "승인-todo", "parent_id": "chan-1", "type": 11},
        ]},
    })
    directory = _directory(fake)

    # When / Then: the archived thread is reused (posting into it un-archives it).
    assert directory.agent_chat_thread(ApprovalKind.TODO) == "thread-5"
    assert all(method != "POST" for method, _path, _payload in fake.calls)


def test_agent_chat_thread_creates_one_when_absent(interop_config: Path) -> None:
    # Given: no matching thread exists in any state.
    _agent_chat_config(interop_config, "chan-1")
    fake = FakeApi("bot-a", {
        ("GET", "/channels/chan-1"): {"id": "chan-1", "type": 0, "guild_id": "guild-1"},
        ("GET", "/guilds/guild-1/threads/active"): {"threads": []},
        ("GET", "/channels/chan-1/threads/archived/public"): {"threads": []},
        ("POST", "/channels/chan-1/threads"): {"id": "thread-new"},
    })
    directory = _directory(fake)

    # When: the surface is resolved.
    thread_id = directory.agent_chat_thread(ApprovalKind.TODO)

    # Then: one channel thread is created with the frozen name and 7-day archive window.
    assert thread_id == "thread-new"
    created = [payload for method, path, payload in fake.calls if method == "POST"]
    assert created == [{"name": "승인-todo", "auto_archive_duration": 10080, "type": 11}]


def test_describe_maps_a_thread_parent_faithfully(interop_config: Path) -> None:
    # Given: Discord describes a public thread under its parent channel.
    fake = FakeApi("bot-a", {("GET", "/channels/thread-9"): {
        "id": "thread-9", "type": 11, "name": "승인-todo", "parent_id": "chan-1",
    }})
    directory = _directory(fake)

    # When / Then: the parent survives into the channel facts.
    assert directory.describe("thread-9") == ChannelFacts(11, "승인-todo", (), "chan-1")


# ------------------------------------------------ per-request thread (2026-09-01)
# 소유자 결정: 승인은 kind별 고정 스레드가 아니라 요청별 스레드에 게시된다 — 지시 메시지가
# #agent-chat 소속이면 그 메시지에 앵커(결과 통지가 쓰는 바로 그 스레드), 아니면 채널 스레드.

def _request_thread(**overrides: object):
    from automation.interop.approval_surface import RequestThread

    spec: dict[str, object] = {"title": "세미나 안내"}
    spec.update(overrides)
    return RequestThread(**spec)  # type: ignore[arg-type]


def test_request_thread_anchors_on_an_agent_chat_instruction_message(interop_config: Path) -> None:
    # Given: the instruction came from the configured agent-chat channel.
    _agent_chat_config(interop_config, "chan-1")
    fake = FakeApi("bot-a", {
        ("POST", "/channels/chan-1/messages/msg-1/threads"): {"id": "thread-anchored"},
        ("POST", "/channels/thread-anchored/messages"): {"id": "title-1"},
    })
    directory = _directory(fake)

    # When: the per-request thread is resolved.
    thread_id = directory.agent_chat_request_thread(
        ApprovalKind.MAIL_COMPOSE,
        _request_thread(origin_channel_id="chan-1", origin_message_id="msg-1"),
    )

    # Then: the thread hangs on the instruction message with the labelled name.
    assert thread_id == "thread-anchored"
    assert fake.calls == [(
        "POST", "/channels/chan-1/messages/msg-1/threads",
        {"name": "✉️ 메일 발신 · 세미나 안내", "auto_archive_duration": 10080},
    ), (
        # The owner's message is the thread's starter, so the title goes in first.
        "POST", "/channels/thread-anchored/messages", {"content": "## ✉️ 메일 발신 · 세미나 안내"},
    )]


def test_request_thread_title_failure_never_blocks_the_approval(interop_config: Path) -> None:
    # Given: the thread opens on the owner's message but the title post is refused.
    _agent_chat_config(interop_config, "chan-1")
    fake = FakeApi("bot-a", {
        ("POST", "/channels/chan-1/messages/msg-1/threads"): {"id": "thread-anchored"},
        ("POST", "/channels/thread-anchored/messages"): HTTPError(
            "https://discord.test/x", 403, "forbidden", None, None,
        ),
    })

    # When / Then: the thread still resolves, so the approval card can be posted.
    assert _directory(fake).agent_chat_request_thread(
        ApprovalKind.MAIL_COMPOSE,
        _request_thread(origin_channel_id="chan-1", origin_message_id="msg-1"),
    ) == "thread-anchored"


def test_request_thread_reuses_the_message_thread_on_400(interop_config: Path) -> None:
    # Given: the instruction message already carries a thread (result notice opened it).
    _agent_chat_config(interop_config, "chan-1")
    fake = FakeApi("bot-a", {
        ("POST", "/channels/chan-1/messages/msg-1/threads"): HTTPError(
            f"https://discord.test/{SECRET_CANARY}", 400, "exists", None, None,
        ),
    })
    directory = _directory(fake)

    # When / Then: the message id doubles as the thread id — no second thread.
    assert directory.agent_chat_request_thread(
        ApprovalKind.MAIL_COMPOSE,
        _request_thread(origin_channel_id="chan-1", origin_message_id="msg-1"),
    ) == "msg-1"


def test_request_thread_other_http_errors_fail_closed_without_leaking(interop_config: Path) -> None:
    # Given: Discord refuses the anchored thread for another reason.
    _agent_chat_config(interop_config, "chan-1")
    fake = FakeApi("bot-a", {
        ("POST", "/channels/chan-1/messages/msg-1/threads"): HTTPError(
            f"https://discord.test/{SECRET_CANARY}", 403, "forbidden", None, None,
        ),
    })
    directory = _directory(fake)

    # When / Then: the surface fails closed and the message carries no url.
    with pytest.raises(ApprovalSurfaceError) as raised:
        _ = directory.agent_chat_request_thread(
            ApprovalKind.MAIL_COMPOSE,
            _request_thread(origin_channel_id="chan-1", origin_message_id="msg-1"),
        )
    assert SECRET_CANARY not in str(raised.value)
    assert "http_status=403" in str(raised.value)


def test_request_thread_creates_a_channel_thread_without_an_origin(interop_config: Path) -> None:
    # Given: a cron-born request with no instruction message at all.
    _agent_chat_config(interop_config, "chan-1")
    fake = FakeApi("bot-a", {
        ("POST", "/channels/chan-1/messages"): {"id": "msg-new"},
        ("POST", "/channels/chan-1/messages/msg-new/threads"): {"id": "thread-new"},
    })
    directory = _directory(fake)

    # When: the per-request thread is resolved.
    thread_id = directory.agent_chat_request_thread(ApprovalKind.REPAIR, _request_thread(title="t_abc"))

    # Then: the request announces itself under agent-chat and the thread hangs on that
    # message, carrying the request name — never a shared per-kind thread, and never a
    # bare channel thread the owner cannot see (2026-09-09).
    assert thread_id == "thread-new"
    assert fake.calls[-1] == (
        "POST", "/channels/chan-1/messages/msg-new/threads",
        {"name": "🛠️ 수리 · t_abc", "auto_archive_duration": 10080},
    )
    assert ("POST", "/channels/chan-1/threads") not in [
        (method, path) for method, path, _payload in fake.calls
    ]


def test_request_thread_ignores_an_origin_outside_agent_chat(interop_config: Path) -> None:
    # Given: the instruction came from a channel the owner-only surface must not use.
    _agent_chat_config(interop_config, "chan-1")
    fake = FakeApi("bot-a", {
        ("POST", "/channels/chan-1/messages"): {"id": "msg-new"},
        ("POST", "/channels/chan-1/messages/msg-new/threads"): {"id": "thread-new"},
    })
    directory = _directory(fake)

    # When / Then: the approval still lands under agent-chat, not on the foreign message.
    assert directory.agent_chat_request_thread(
        ApprovalKind.TODO,
        _request_thread(title="보고서", origin_channel_id="chan-lab", origin_message_id="msg-9"),
    ) == "thread-new"
    assert all(
        "chan-lab" not in path and "msg-9" not in path
        for _method, path, _payload in fake.calls
    )


def test_request_thread_refuses_an_unset_config_key(interop_config: Path) -> None:
    # Given: no agent-chat channel is configured.
    interop_config.write_text("{}", encoding="utf-8")
    fake = FakeApi("bot-a", {})
    directory = _directory(fake)

    # When / Then: fail closed before any Discord request.
    with pytest.raises(ApprovalSurfaceError):
        _ = directory.agent_chat_request_thread(ApprovalKind.TODO, _request_thread())
    assert fake.calls == []


def test_request_thread_announces_the_request_with_a_guidance_message(
    interop_config: Path,
) -> None:
    # Given: a cron-born request (a plaud lifelog note) with no instruction message.
    # Discord renders a bare channel thread as a contentless "started a thread" line, so
    # the owner cannot tell an approval is waiting (2026-09-09 owner report; t_e23d85a1).
    _agent_chat_config(interop_config, "chan-1")
    fake = FakeApi("bot-a", {
        ("POST", "/channels/chan-1/messages"): {"id": "msg-new"},
        ("POST", "/channels/chan-1/messages/msg-new/threads"): {"id": "thread-anchored"},
    })
    directory = _directory(fake)

    # When: the per-request thread is resolved.
    thread_id = directory.agent_chat_request_thread(
        ApprovalKind.OBSIDIAN_WRITE,
        _request_thread(title="2026-09-09-110506--cdd47d0c7384.md"),
    )

    # Then: the channel carries a readable notice and the thread hangs on THAT message.
    assert thread_id == "thread-anchored"
    assert [(method, path) for method, path, _payload in fake.calls] == [
        ("POST", "/channels/chan-1/messages"),
        ("POST", "/channels/chan-1/messages/msg-new/threads"),
    ]
    notice = fake.calls[0][2]
    assert notice is not None
    content = notice["content"]
    assert isinstance(content, str)
    assert "승인" in content
    assert "2026-09-09-110506--cdd47d0c7384.md" in content
