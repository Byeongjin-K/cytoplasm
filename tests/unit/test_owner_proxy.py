"""소유자 대리 요청 경로 — 허용된 대리 봇 하나의 검증된 글만 에이전트 턴을 연다.

`interop_bot_prose`(봇 산문 차단)의 유일한 예외를 고정한다. 받는 쪽 조건이 하나라도 빠지면
예전처럼 버려야 하고, 받은 뒤에도 외부효과 게이트와 ✅ 판정은 그대로여야 한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from automation.interop import owner_proxy

OWNER = "100000000000000001"
PROXY_BOT = "100000000000000002"
OTHER_BOT = "100000000000000003"
GUILD = "100000000000000010"
OTHER_GUILD = "100000000000000011"
AGENT_CHAT = "100000000000000020"
ORIGIN_CHANNEL = "100000000000000030"
ORIGIN_THREAD = "100000000000000031"
UNRELATED_CHANNEL = "100000000000000040"
NOW = 1_790_000_000.0
PREFIX = "[도리 대리 · 소유자 요청 "
#: 비권장 옛 접두어 — 전환 기간 동안 이미 보낸 요청과 재전송을 계속 받는다.
LEGACY_PREFIX = "[오모냥 대리 · 소유자 요청 "


def snowflake(at: float, sequence: int = 0) -> str:
    return str(((int(at * 1000) - 1420070400000) << 22) | sequence)


def proxy_text(
    guild: str, channel: str, message: str, body: str = "내일 오후 3시 회의 일정 등록해줘", prefix: str = PREFIX
) -> str:
    return f"{prefix}https://discord.com/channels/{guild}/{channel}/{message}] {body}"


@dataclass
class FakeReader:
    channels: dict[str, Mapping[str, object]] = field(default_factory=dict)
    messages: dict[str, Mapping[str, object]] = field(default_factory=dict)
    fail: bool = False
    calls: list[str] = field(default_factory=list)

    def channel(self, channel_id: str) -> Mapping[str, object]:
        self.calls.append(f"channel:{channel_id}")
        if self.fail:
            raise OSError("discord unavailable")
        return self.channels[channel_id]

    def message(self, channel_id: str, message_id: str) -> Mapping[str, object]:
        self.calls.append(f"message:{message_id}")
        if self.fail:
            raise OSError("discord unavailable")
        return self.messages[message_id]


def reader_with(origin_id: str, *, channel: str = ORIGIN_CHANNEL, author: str = OWNER, bot: bool = False) -> FakeReader:
    return FakeReader(
        channels={
            ORIGIN_CHANNEL: {"id": ORIGIN_CHANNEL, "type": 0, "guild_id": GUILD},
            ORIGIN_THREAD: {"id": ORIGIN_THREAD, "type": 11, "guild_id": GUILD, "parent_id": ORIGIN_CHANNEL},
            UNRELATED_CHANNEL: {"id": UNRELATED_CHANNEL, "type": 0, "guild_id": GUILD},
        },
        messages={origin_id: {"id": origin_id, "channel_id": channel, "author": {"id": author, "bot": bot}}},
    )


CONFIG = owner_proxy.ProxyConfig(
    bot_id=PROXY_BOT, origin_channel_id=ORIGIN_CHANNEL, agent_chat_channel_id=AGENT_CHAT, owner_id=OWNER
)


def message(text: str, *, author: str = PROXY_BOT, channel: str = AGENT_CHAT, parent: str | None = None, guild: str | None = GUILD) -> owner_proxy.ProxyMessage:
    return owner_proxy.ProxyMessage(author, channel, parent, guild, "100000000000000099", text)


def verify(msg: owner_proxy.ProxyMessage, reader: FakeReader, tmp_path: Path, now: float = NOW):
    return owner_proxy.verify(msg, CONFIG, reader, owner_proxy.FileLedger(tmp_path / "ledger.json"), now)


@pytest.mark.parametrize("prefix", [PREFIX, LEGACY_PREFIX], ids=["dori", "legacy"])
def test_a_verified_proxy_request_is_accepted(tmp_path: Path, prefix: str) -> None:
    origin = snowflake(NOW - 60)
    verdict = verify(message(proxy_text(GUILD, ORIGIN_CHANNEL, origin, prefix=prefix)), reader_with(origin), tmp_path)

    assert isinstance(verdict, owner_proxy.Accepted)
    assert verdict.origin_message_id == origin
    assert verdict.body == "내일 오후 3시 회의 일정 등록해줘"


def test_an_origin_inside_a_thread_of_the_origin_channel_is_accepted(tmp_path: Path) -> None:
    origin = snowflake(NOW - 60)
    msg = message(proxy_text(GUILD, ORIGIN_THREAD, origin), channel="100000000000000021", parent=AGENT_CHAT)

    assert isinstance(verify(msg, reader_with(origin, channel=ORIGIN_THREAD), tmp_path), owner_proxy.Accepted)


@pytest.mark.parametrize(
    ("msg_kwargs", "text_args", "reader_kwargs", "reason"),
    [
        ({"author": OTHER_BOT}, (GUILD, ORIGIN_CHANNEL), {}, "not_proxy_bot"),
        ({"channel": UNRELATED_CHANNEL}, (GUILD, ORIGIN_CHANNEL), {}, "outside_agent_chat"),
        ({"guild": OTHER_GUILD}, (GUILD, ORIGIN_CHANNEL), {}, "guild_mismatch"),
        ({}, (OTHER_GUILD, ORIGIN_CHANNEL), {}, "guild_mismatch"),
        ({}, (GUILD, UNRELATED_CHANNEL), {"channel": UNRELATED_CHANNEL}, "origin_channel"),
        ({}, (GUILD, ORIGIN_CHANNEL), {"author": "100000000000000005"}, "origin_author"),
        ({}, (GUILD, ORIGIN_CHANNEL), {"bot": True}, "origin_author"),
    ],
)
def test_each_identity_condition_is_enforced(tmp_path: Path, msg_kwargs, text_args, reader_kwargs, reason) -> None:
    origin = snowflake(NOW - 60)
    verdict = verify(message(proxy_text(*text_args, origin), **msg_kwargs), reader_with(origin, **reader_kwargs), tmp_path)

    assert verdict == owner_proxy.Rejected(reason)


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("내일 오후 3시 회의 일정 등록해줘", "no_prefix"),
        (f"{PREFIX}원문 없음] 일정 등록해줘", "link_count"),
        (
            f"{PREFIX}https://discord.com/channels/{GUILD}/{ORIGIN_CHANNEL}/{snowflake(NOW - 60)}] "
            f"https://discord.com/channels/{GUILD}/{ORIGIN_CHANNEL}/{snowflake(NOW - 30)} 둘 다 처리해줘",
            "link_count",
        ),
        (f"{PREFIX}https://discord.com/channels/{GUILD}/{ORIGIN_CHANNEL}/{snowflake(NOW - 60)}]   ", "empty_body"),
    ],
)
def test_malformed_proxy_text_is_rejected_before_any_lookup(tmp_path: Path, text: str, reason: str) -> None:
    reader = reader_with(snowflake(NOW - 60))

    assert verify(message(text), reader, tmp_path) == owner_proxy.Rejected(reason)
    assert reader.calls == []


def test_an_origin_older_than_the_window_is_rejected(tmp_path: Path) -> None:
    origin = snowflake(NOW - owner_proxy.MAX_AGE_SECONDS - 1)

    assert verify(message(proxy_text(GUILD, ORIGIN_CHANNEL, origin)), reader_with(origin), tmp_path) == owner_proxy.Rejected("origin_stale")


def test_the_same_origin_is_accepted_once_and_a_correction_is_a_new_origin(tmp_path: Path) -> None:
    first = snowflake(NOW - 60)
    correction = snowflake(NOW - 10)

    assert isinstance(verify(message(proxy_text(GUILD, ORIGIN_CHANNEL, first)), reader_with(first), tmp_path), owner_proxy.Accepted)
    assert verify(message(proxy_text(GUILD, ORIGIN_CHANNEL, first)), reader_with(first), tmp_path) == owner_proxy.Rejected("origin_reused")
    assert isinstance(verify(message(proxy_text(GUILD, ORIGIN_CHANNEL, correction)), reader_with(correction), tmp_path), owner_proxy.Accepted)


def test_a_rejected_origin_does_not_burn_its_ledger_entry(tmp_path: Path) -> None:
    origin = snowflake(NOW - 60)
    assert verify(message(proxy_text(GUILD, ORIGIN_CHANNEL, origin)), reader_with(origin, author=OTHER_BOT), tmp_path) == owner_proxy.Rejected("origin_author")

    assert isinstance(verify(message(proxy_text(GUILD, ORIGIN_CHANNEL, origin)), reader_with(origin), tmp_path), owner_proxy.Accepted)


def test_an_unreadable_origin_fails_closed(tmp_path: Path) -> None:
    origin = snowflake(NOW - 60)
    reader = reader_with(origin)
    reader.fail = True

    assert verify(message(proxy_text(GUILD, ORIGIN_CHANNEL, origin)), reader, tmp_path) == owner_proxy.Rejected("origin_unreadable")


def test_a_corrupt_ledger_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "ledger.json").write_text("not json", encoding="utf-8")
    origin = snowflake(NOW - 60)

    assert verify(message(proxy_text(GUILD, ORIGIN_CHANNEL, origin)), reader_with(origin), tmp_path) == owner_proxy.Rejected("ledger_unavailable")


OWNER_WEBHOOK = "100000000000000050"
WEBHOOK_CONFIG = owner_proxy.ProxyConfig(
    bot_id=PROXY_BOT, origin_channel_id=ORIGIN_CHANNEL, agent_chat_channel_id=AGENT_CHAT, owner_id=OWNER,
    owner_webhook_ids=frozenset({OWNER_WEBHOOK}),
)


def webhook_reader(origin_id: str, *, channel: str = ORIGIN_CHANNEL, webhook: str = OWNER_WEBHOOK, author: str | None = None) -> FakeReader:
    reader = reader_with(origin_id, channel=channel)
    reader.messages[origin_id] = {
        "id": origin_id, "channel_id": channel, "webhook_id": webhook,
        "author": {"id": author or webhook, "bot": True, "username": "owner-voice"},
    }
    return reader


def test_an_origin_posted_by_the_registered_owner_webhook_is_accepted(tmp_path: Path) -> None:
    origin = snowflake(NOW - 60)
    verdict = owner_proxy.verify(
        message(proxy_text(GUILD, ORIGIN_CHANNEL, origin)), WEBHOOK_CONFIG, webhook_reader(origin),
        owner_proxy.FileLedger(tmp_path / "ledger.json"), NOW,
    )

    assert isinstance(verdict, owner_proxy.Accepted)
    assert verdict.via == "owner-webhook"


@pytest.mark.parametrize(
    ("config", "reader_kwargs", "origin_channel"),
    [
        (CONFIG, {}, ORIGIN_CHANNEL),  # 웹훅을 등록하지 않았다
        (WEBHOOK_CONFIG, {"webhook": "100000000000000051"}, ORIGIN_CHANNEL),  # 다른 웹훅
        (WEBHOOK_CONFIG, {"author": "100000000000000052"}, ORIGIN_CHANNEL),  # 작성자가 그 웹훅이 아니다
        (WEBHOOK_CONFIG, {"channel": ORIGIN_THREAD}, ORIGIN_THREAD),  # 원문 채널 자체가 아니라 그 스레드
    ],
)
def test_a_webhook_origin_is_trusted_only_for_the_registered_id_in_the_origin_channel(
    tmp_path: Path, config: owner_proxy.ProxyConfig, reader_kwargs: dict[str, str], origin_channel: str
) -> None:
    origin = snowflake(NOW - 60)
    verdict = owner_proxy.verify(
        message(proxy_text(GUILD, origin_channel, origin)), config, webhook_reader(origin, **reader_kwargs),
        owner_proxy.FileLedger(tmp_path / "ledger.json"), NOW,
    )

    assert verdict == owner_proxy.Rejected("origin_author")


def test_malformed_webhook_ids_keep_the_webhook_path_closed() -> None:
    base = {"agent_chat_channel_id": AGENT_CHAT, "owner_id": OWNER, "owner_proxy_bot_id": PROXY_BOT,
            "owner_proxy_origin_channel_id": ORIGIN_CHANNEL}

    assert owner_proxy.load_config({**base, "owner_proxy_origin_webhook_ids": [OWNER_WEBHOOK]}) == WEBHOOK_CONFIG
    assert owner_proxy.load_config({**base, "owner_proxy_origin_webhook_ids": OWNER_WEBHOOK}) == CONFIG
    assert owner_proxy.load_config({**base, "owner_proxy_origin_webhook_ids": [OWNER_WEBHOOK, "x"]}) == CONFIG


def test_the_path_is_closed_until_both_private_keys_exist() -> None:
    base = {"agent_chat_channel_id": AGENT_CHAT, "owner_id": OWNER}

    assert owner_proxy.load_config(base) is None
    assert owner_proxy.load_config({**base, "owner_proxy_bot_id": PROXY_BOT}) is None
    assert owner_proxy.load_config({**base, "owner_proxy_bot_id": PROXY_BOT, "owner_proxy_origin_channel_id": ORIGIN_CHANNEL}) == CONFIG


def test_a_dori_prefix_whose_origin_is_not_the_owner_is_rejected_by_verify(tmp_path: Path) -> None:
    origin = snowflake(NOW - 60)
    verdict = verify(message(proxy_text(GUILD, ORIGIN_CHANNEL, origin)), reader_with(origin, author=OTHER_BOT), tmp_path)

    assert verdict == owner_proxy.Rejected("origin_author")


@pytest.mark.parametrize(
    ("text", "kind"),
    [(PREFIX + "x] y", "current"), (LEGACY_PREFIX + "x] y", "legacy"), ("[누군가 대리 · 소유자 요청 x] y", "other"), ("그냥 글", "none")],
)
def test_prefix_kind_separates_the_dori_name_from_the_deprecated_one(text: str, kind: str) -> None:
    assert owner_proxy.prefix_kind(text) == kind


@pytest.mark.parametrize("author", [OTHER_BOT, "100000000000000004"])
def test_admit_never_trusts_another_bot(tmp_path: Path, author: str) -> None:
    origin = snowflake(NOW - 60)
    verdict = owner_proxy.admit(
        message(proxy_text(GUILD, ORIGIN_CHANNEL, origin), author=author), CONFIG, reader_with(origin),
        owner_proxy.FileLedger(tmp_path / "ledger.json"), NOW,
    )

    assert verdict == owner_proxy.Rejected("not_proxy_bot")


def test_admit_keeps_a_proxy_bot_request_without_a_valid_link() -> None:
    verdict = owner_proxy.admit(
        message(f"{PREFIX}원문 없음] 일정 등록해줘"), CONFIG, FakeReader(), owner_proxy.FileLedger(Path("/nonexistent")), NOW
    )

    assert verdict == owner_proxy.Accepted(
        link=None, origin_message_id=None, body="일정 등록해줘", via="dori", origin_failure="link_count"
    )


def test_a_proxy_bot_reaction_is_never_an_owner_decision() -> None:
    from automation.plaud_sync.approval_gate import _owner_reacted as plaud_owner_reacted
    from automation.repair.repair_ops_approval_gate import owner_reacted as repair_owner_reacted

    for owner_reacted in (plaud_owner_reacted, repair_owner_reacted):
        assert owner_reacted(((PROXY_BOT, True),), OWNER) is False
        assert owner_reacted(((PROXY_BOT, False),), OWNER) is False
        assert owner_reacted(((OWNER, False),), OWNER) is True


@pytest.mark.parametrize(("text", "content", "expected"), [
    ("attachment text\n\nauthored", "authored", "authored"),
    ("attachment text\n\nauthored", "different", "attachment text\n\nauthored"),
    ("attachment text", "", "attachment text"),
])
def test_only_the_authored_tail_is_used_for_proxy_origin_validation(
    text: str, content: str, expected: str,
) -> None:
    from types import SimpleNamespace

    event = SimpleNamespace(raw_message=SimpleNamespace(content=content))
    assert owner_proxy._authored_content(event, text) == expected
