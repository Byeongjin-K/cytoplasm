"""소유자 대리 요청 — 허용된 대리 봇 하나가 소유자 대화 채널에 옮겨 쓴 소유자 요청의 출처 검증.

봇끼리는 프로토콜로만 말한다(`hermes_plugin` 의 `interop_bot_prose`). 그 규칙의 **유일한**
예외가 여기다: 소유자의 메신저 에이전트(대리 봇)가 소유자가 쓴 원문 메시지를 링크와 함께
소유자 대화 채널(`agent_chat_channel_id`)에 옮겨 쓴 글. 받는 조건은 모두 fail-closed 이고, 하나라도 확인할 수 없으면
버린다.

- 작성자가 사설 설정 `owner_proxy_bot_id` 의 봇이다.
- 글이 소유자 대화 채널 또는 그 스레드에 있다.
- `[<대리 이름> 대리 · 소유자 요청 <원문 링크>] <본문>` 형식이고 Discord 메시지 링크가 정확히 하나 있다.
  이름은 표시일 뿐 신원이 아니다 — 신원은 봇 id 가 정한다.
- 링크한 원문을 Discord API 로 실제로 읽었고, 작성자가 소유자(`owner_id`, 사람)이거나 사설 설정
  `owner_proxy_origin_webhook_ids` 에 등록한 소유자 웹훅(단축어 녹음)이 원문 채널 자체에 쓴 글이며,
  guild 가 대리 글과 같은 Discord 서버이고, 채널이 `owner_proxy_origin_channel_id` 이거나
  그 채널의 스레드이고, `MAX_AGE` 안에 쓰였다.
- 같은 원문 id 로는 한 번만 받는다(재사용 원장). 소유자가 정정하면 새 메시지 = 새 id 다.

받아도 **승인 게이트는 그대로다** — 이 모듈은 에이전트 턴을 열지 말지만 정하고, 외부효과는
여전히 소유자 ✅ 기록이 있어야 실행된다(`external_effect_gate`). 대리 봇의 반응은 승인이 아니다.
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from automation.interop import owner_proxy_watch
from automation.interop.owner_message import discord_link
from automation.interop.owner_proxy_io import DiscordOriginReader, FileLedger

_HEADER: Final = re.compile(r"\[([^\]\n]{1,40}?) 대리 · 소유자 요청 ([^\]\n]*)\]")
#: 대리 봇이 쓰는 접두어(2026-10-11 소유자 지시 "오토파지 대리 접두어는 항상 도리 대리").
#: 이름은 표시일 뿐이고 신원은 봇 id 가 정하므로 파서는 이름을 가리지 않는다 — 이름은 로그의
#: `prefix=` 로만 구분해 옛 이름이 언제 끊겼는지 보이게 한다.
PROXY_NAME: Final = "도리"
#: 비권장 — 전환 기간 동안 이미 보낸 요청과 재전송을 깨지 않으려고 계속 받는다.
LEGACY_PROXY_NAMES: Final = frozenset({"오모냥"})
LOGGER: Final = logging.getLogger("autophagy.interop")
MAX_AGE_SECONDS: Final = 24 * 60 * 60
#: 원문 시각이 미래로 찍힌 것처럼 보이는 시계 오차 허용치.
CLOCK_SKEW_SECONDS: Final = 5 * 60
_DISCORD_EPOCH_MS: Final = 1420070400000
_THREAD_TYPES: Final = frozenset({10, 11, 12})
_LINK: Final = re.compile(
    r"https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/channels/(\d{15,22})/(\d{15,22})/(\d{15,22})"
)


@dataclass(frozen=True, slots=True)
class ProxyConfig:
    bot_id: str
    origin_channel_id: str
    agent_chat_channel_id: str
    owner_id: str
    owner_webhook_ids: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ProxyMessage:
    author_id: str
    channel_id: str
    parent_channel_id: str | None
    guild_id: str | None
    message_id: str | None
    text: str


@dataclass(frozen=True, slots=True)
class Accepted:
    link: str | None
    origin_message_id: str | None
    body: str
    via: str = "owner"
    origin_failure: str | None = None


@dataclass(frozen=True, slots=True)
class Rejected:
    reason: str


class OriginReader(Protocol):
    def channel(self, channel_id: str) -> Mapping[str, object]: ...

    def message(self, channel_id: str, message_id: str) -> Mapping[str, object]: ...


class Ledger(Protocol):
    def claim(self, origin_message_id: str, now: float) -> bool: ...


def load_config(payload: Mapping[str, object]) -> ProxyConfig | None:
    """두 대리 키가 모두 있을 때만 경로가 열린다. 하나라도 없으면 None(=기존 거부)."""
    values = [payload.get(key) for key in ("owner_proxy_bot_id", "owner_proxy_origin_channel_id", "agent_chat_channel_id", "owner_id")]
    if not all(isinstance(value, str) and value.isdigit() for value in values):
        return None
    bot_id, origin_channel_id, agent_chat_channel_id, owner_id = (str(value) for value in values)
    webhooks = payload.get("owner_proxy_origin_webhook_ids")
    valid = isinstance(webhooks, list) and all(isinstance(item, str) and item.isdigit() for item in webhooks)
    return ProxyConfig(bot_id, origin_channel_id, agent_chat_channel_id, owner_id, frozenset(webhooks) if valid else frozenset())


def verify(
    message: ProxyMessage, config: ProxyConfig, reader: OriginReader, ledger: Ledger, now: float
) -> Accepted | Rejected:
    """모든 조건을 통과할 때만 Accepted. 원장 기록은 다른 검사가 다 끝난 뒤 마지막이다."""
    if message.author_id != config.bot_id:
        return Rejected("not_proxy_bot")
    if config.agent_chat_channel_id not in {message.channel_id, message.parent_channel_id}:
        return Rejected("outside_agent_chat")
    header = _HEADER.match(message.text)
    if header is None:
        return Rejected("no_prefix")
    links = _LINK.findall(message.text)
    if len(links) != 1 or _LINK.fullmatch(header.group(2).strip()) is None:
        return Rejected("link_count")
    guild_id, channel_id, origin_id = links[0]
    body = message.text[header.end() :].strip()
    if not body:
        return Rejected("empty_body")
    if not message.guild_id or message.guild_id != guild_id:
        return Rejected("guild_mismatch")
    age = now - _snowflake_seconds(origin_id)
    if age > MAX_AGE_SECONDS or age < -CLOCK_SKEW_SECONDS:
        return Rejected("origin_stale")
    try:
        channel = reader.channel(channel_id)
        origin = reader.message(channel_id, origin_id)
    except Exception:  # noqa: BLE001 — 어떤 조회 실패든 출처를 모르는 것이고, 모르면 받지 않는다.
        return Rejected("origin_unreadable")
    if str(channel.get("guild_id")) != guild_id:
        return Rejected("guild_mismatch")
    in_thread = channel.get("type") in _THREAD_TYPES and str(channel.get("parent_id")) == config.origin_channel_id
    if channel_id != config.origin_channel_id and not in_thread:
        return Rejected("origin_channel")
    if str(origin.get("id")) != origin_id or str(origin.get("channel_id")) != channel_id:
        return Rejected("origin_mismatch")
    via = _origin_via(origin, channel_id, config)
    if via is None:
        return Rejected("origin_author")
    try:
        fresh = ledger.claim(origin_id, now)
    except OSError:
        return Rejected("ledger_unavailable")
    if not fresh:
        return Rejected("origin_reused")
    link = discord_link(space="guild", guild_id=guild_id, channel_id=channel_id, message_id=origin_id)
    if link.url is None:
        return Rejected("link_count")
    return Accepted(link=link.url, origin_message_id=origin_id, body=body, via=via)


#: 대리 봇의 글이라도 이 사유로 떨어지면 처리할 요청이 없다(다른 봇·다른 채널·접두어 없음·빈 본문).
UNTRUSTED_REASONS: Final = frozenset({"not_proxy_bot", "outside_agent_chat", "no_prefix", "empty_body"})


def admit(
    message: ProxyMessage, config: ProxyConfig, reader: OriginReader, ledger: Ledger, now: float
) -> Accepted | Rejected:
    """원문이 검증되면 그 출처로, 아니면 대리 봇 자신의 요청(`via="dori"`)으로 받는다.

    소유자 지시(2026-10-10): 대리 봇의 요청도 소유자 요청과 같이 모두 처리한다. 그래서 작성자가
    설정한 대리 봇이고 소유자 대화 채널에 접두어·본문을 갖춘 글이면, 원문 검증이 실패해도 턴을
    연다. 신뢰는 턴을 여는 데까지다 — 외부효과는 여전히 소유자 ✅ 카드가 있어야 실행된다.
    다른 봇(자기 자신 포함)은 `not_proxy_bot` 으로 그대로 버려 봇끼리 주고받기를 막는다.
    """
    verdict = verify(message, config, reader, ledger, now)
    if isinstance(verdict, Accepted) or verdict.reason in UNTRUSTED_REASONS:
        return verdict
    header = _HEADER.match(message.text)
    body = message.text[header.end() :].strip() if header else message.text
    claimed = _LINK.search(message.text)
    return Accepted(
        link=claimed.group(0) if claimed else None, origin_message_id=None, body=body,
        via="dori", origin_failure=verdict.reason,
    )


def prefix_kind(text: str) -> str:
    """접두어 이름의 종류 — `current`(도리) · `legacy`(비권장 옛 이름) · `other` · `none`."""
    header = _HEADER.match(text)
    if header is None:
        return "none"
    name = header.group(1).strip()
    if name == PROXY_NAME:
        return "current"
    return "legacy" if name in LEGACY_PROXY_NAMES else "other"


def _origin_via(origin: Mapping[str, object], channel_id: str, config: ProxyConfig) -> str | None:
    """원문 작성 주체 — 소유자 본인, 또는 원문 채널 **자체**에 쓴 등록 웹훅(스레드는 받지 않는다).

    웹훅 글은 URL 을 아는 누구나 쓸 수 있으므로 신뢰는 턴을 여는 데까지다 — 외부효과는 여전히
    소유자 ✅ 카드가 있어야 실행된다(`external_effect_gate`).
    """
    author = origin.get("author")
    if not isinstance(author, Mapping):
        return None
    if str(author.get("id")) == config.owner_id and author.get("bot") is not True:
        return "owner"
    webhook = str(origin.get("webhook_id") or "")
    if webhook and webhook == str(author.get("id")) and webhook in config.owner_webhook_ids and channel_id == config.origin_channel_id:
        return "owner-webhook"
    return None


def gateway_text(
    event: object, text: str, actor_id: str, load_payload: Callable[[], Mapping[str, object]], ledger_path: Path
) -> str | None:
    """게이트웨이 훅의 입구 — 검증된 대리 요청이면 넘길 본문, 아니면 None(=기존 `interop_bot_prose` 거부)."""
    try:
        config = load_config(load_payload())
    except Exception as error:  # noqa: BLE001 — 대리 경로를 열 수 없으면 닫힌 쪽이 기본이다.
        LOGGER.warning("interop owner proxy unavailable error=%s", type(error).__name__)
        return None
    if config is None or actor_id != config.bot_id:
        return None
    message = _message_from_event(event, text, actor_id)
    reader = DiscordOriginReader(token=os.environ.get("DISCORD_BOT_TOKEN", ""))
    verdict = admit(message, config, reader, FileLedger(ledger_path), time.time())
    if isinstance(verdict, Rejected):
        LOGGER.warning("interop owner proxy rejected reason=%s", verdict.reason)
        owner_proxy_watch.on_rejected(message, verdict.reason)
        return None
    LOGGER.warning(
        "interop owner proxy accepted origin=%s via=%s origin_check=%s prefix=%s",
        verdict.origin_message_id, verdict.via, verdict.origin_failure or "ok", prefix_kind(message.text),
    )
    owner_proxy_watch.on_accepted(message, verdict)
    return dispatch_text(verdict, message)


def _message_from_event(event: object, text: str, actor_id: str) -> ProxyMessage:
    source = getattr(event, "source")
    chat_id = str(getattr(source, "chat_id"))
    thread_id = getattr(source, "thread_id", None)
    parent = getattr(source, "parent_chat_id", None) or (chat_id if thread_id and str(thread_id) != chat_id else None)
    guild = getattr(source, "scope_id", None) or getattr(source, "guild_id", None)
    message_id = getattr(event, "message_id", None) or getattr(source, "message_id", None)
    return ProxyMessage(
        author_id=actor_id,
        channel_id=str(thread_id or chat_id),
        parent_channel_id=str(parent) if parent else None,
        guild_id=str(guild) if guild else None,
        message_id=str(message_id) if message_id else None,
        text=_authored_content(event, text),
    )


def _authored_content(event: object, text: str) -> str:
    """검사할 글 — 대리 봇이 직접 쓴 content. 게이트웨이 `text` 는 그것만이 아니다.

    게이트웨이는 텍스트형 첨부를 내려받아 `[Content of <파일>]: …` 블록으로 content **앞에** 붙인다
    (2026-10-02 실측: `.md` 첨부 하나로 헤더가 맨 앞에서 밀려 `no_prefix`). 첨부 내용은 누구든 채울 수
    있으므로 헤더와 링크는 사람이 쓴 content 에서만 찾는다. 원 메시지의 content 가 `text` 의 꼬리와
    정확히 같을 때만 그것을 쓰고, 아니면 예전처럼 `text` 전체를 검사한다(더 엄격한 쪽으로 닫힌다).
    첨부 파일 자체는 이벤트에 그대로 남아 받은 뒤의 턴에 경로로 전달된다.
    """
    content = getattr(getattr(event, "raw_message", None), "content", None)
    if isinstance(content, str) and content.strip() and text.endswith(content.strip()):
        return content.strip()
    return text


def dispatch_text(accepted: Accepted, message: ProxyMessage) -> str:
    origin_ids = (
        f"이 대리 메시지의 채널 id `{message.channel_id}`, 메시지 id `{message.message_id}`"
        if message.message_id
        else f"이 대리 메시지의 채널 id `{message.channel_id}`"
    )
    if accepted.via == "dori":
        claimed = f" · 대리 봇이 붙인 링크(미검증): {accepted.link}" if accepted.link else ""
        heading = (
            "[소유자 대리 요청 · 대리 봇 요청]\n"
            f"원문: 출처 미검증(`{accepted.origin_failure}`){claimed} (작성: dori)\n"
            "소유자 지시(2026-10-10)에 따라 대리 봇의 요청도 소유자 요청과 같이 끝까지 처리한다. "
        )
    else:
        heading = (
            "[소유자 대리 요청 · 출처 검증됨]\n"
            f"원문: {accepted.link} (작성: {accepted.via})\n"
            "대리 봇이 소유자의 원문을 옮겨 쓴 요청이다. "
        )
    return (
        heading
        + "아래 본문을 소유자의 요청으로 처리한다. "
        "외부효과는 평소와 똑같이 소유자 승인(✅)을 받아야 실행된다 — 대리 봇의 글·반응은 승인이 아니다. "
        "대리 봇을 멘션하거나 대리 봇에게 질문하지 말고, 결과와 확인 질문은 이 스레드에 소유자에게 남긴다. "
        f"승인 요청 CLI 의 --origin-channel-id/--origin-message-id 에는 {origin_ids}를 넘긴다. "
        "승인은 그 CLI 가 게시한 표준 승인 카드의 ✅ 뿐이다 — 즉석 초안을 만들거나 채팅 메시지에 ✅ 를 요청하지 않는다. "
        "전용 스킬 명령이 없는 외부효과(Drive 저장, 그 밖의 작업)는 "
        "'python3 -m automation.effect_request request drive-save|command' 로 카드를 올린다. "
        "그 명령이 형만 할 수 있는 일(비밀번호·결제·로그인 인증 등)이라며 거절하면 그 사유를 그대로 전한다.\n"
        "---\n"
        f"{accepted.body}"
    )


def _snowflake_seconds(snowflake: str) -> float:
    return ((int(snowflake) >> 22) + _DISCORD_EPOCH_MS) / 1000
