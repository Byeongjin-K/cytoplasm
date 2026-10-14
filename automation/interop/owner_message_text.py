"""Owner presentation values and versioned text; public boundary is owner_message."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, TypeAlias, assert_never
from urllib.parse import urlsplit

Space: TypeAlias = Literal["guild", "dm", "unknown"]


@dataclass(frozen=True, slots=True)
class Ref:
    scope: Literal["self", "channel", "message", "resource", "none"]
    space: Space = "unknown"
    guild_id: str | None = None
    channel_id: str | None = None
    message_id: str | None = None
    url: str | None = None
    search: tuple[str, str] | None = None


@dataclass(frozen=True, slots=True)
class Action:
    verb: Literal["none", "react", "reply", "open"]
    target: Ref | None = None
    argument: str | None = None


@dataclass(frozen=True, slots=True)
class Approval:
    expires_at: datetime | None
    cancel_effect: str


@dataclass(frozen=True, slots=True)
class Result:
    outcome: Literal["executed", "cancelled", "expired"]


@dataclass(frozen=True, slots=True)
class Periodic:
    start: datetime
    end: datetime


@dataclass(frozen=True, slots=True)
class OwnerMessage:
    subject_key: str
    subject: str
    fact: str
    location: Ref
    owner: Action
    agent_next: str | None
    recovery: Literal["not_applicable", "irreversible"] | Action
    detail: Approval | Result | Periodic
    contract_version: int = 1
    render_version: str = "owner-ko-v1"


@dataclass(frozen=True, slots=True)
class Presentation:
    """Destination-resolved references, shared by the versioned presentations."""

    location: str
    owner: str
    recovery: str


def resource_url(url: str | None) -> tuple[str | None, str | None]:
    """Return usable producer URL or the frozen v1 fallback reason."""
    if url is None:
        return None, "주소 없음"
    try:
        parsed = urlsplit(url)
        if (parsed.scheme in ("http", "https") and parsed.hostname
                and parsed.username is None and parsed.password is None
                and parsed.port != 0 and not any(
                    char <= " " or char.isspace() or char in '<>"\\'
                    for char in url)):
            return url, None
    except ValueError:
        return None, "주소 파싱 오류"
    return None, "주소 형식 오류"


def result_text(outcome: Literal["executed", "cancelled", "expired"]) -> str:
    """Frozen v1 result wording, separated to keep the public boundary within its LOC limit."""
    match outcome:
        case "executed":
            return "실행 완료"
        case "cancelled":
            return "취소됨"
        case "expired":
            return "만료됨"
        case _:
            assert_never(outcome)


def render_v1(
    message: OwnerMessage, refs: Presentation, timestamp: Callable[[datetime, str], str],
) -> str:
    """Frozen five-line bytes; timestamp retains the public boundary's typed refusal."""
    recovery = refs.recovery
    match message.detail:
        case Approval(expires_at=expires_at, cancel_effect=cancel_effect):
            expiry = "기한 없음" if expires_at is None else timestamp(expires_at, "expires_at")
            detail = f"승인 요청; 만료: {expiry}"
            recovery += f"; 취소 시: {cancel_effect}"
        case Result(outcome=outcome):
            detail = result_text(outcome)
        case Periodic(start=start, end=end):
            detail = f"관측: {timestamp(start, 'start')} ~ {timestamp(end, 'end')}"
        case _:
            assert_never(message.detail)
    agent_next = "추가 실행 없음" if message.agent_next is None else message.agent_next
    return "\n".join(" ".join(line.split()) for line in (
        f"대상: {message.subject} ({message.subject_key})",
        f"사실: {message.fact} ({detail})",
        f"위치: {refs.location}",
        f"인계: 소유자: {refs.owner}; 다음: {agent_next}",
        f"되돌리기: {recovery}",
    ))


def human_timestamp(iso: str) -> str:
    """An ISO datetime's own wall time and offset; no timezone conversion."""
    day, _, clock = iso.partition("T")
    offset_at = max(clock.find("+"), clock.find("-"))
    offset = f" ({clock[offset_at:]})" if offset_at >= 0 else ""
    return f"{day} {clock[:5]}{offset}"


def render_v4(
    message: OwnerMessage, refs: Presentation, timestamp: Callable[[datetime, str], str],
) -> str:
    """Compact presentation preserving exact fact bytes; older versions remain frozen."""
    match message.detail:
        case Approval(expires_at=expiry):
            detail = "" if expiry is None else f"\n만료: {timestamp(expiry, 'expires_at')}"
        case Result(outcome=outcome):
            if message.agent_next is None and message.recovery == "not_applicable" and refs.location == "이 메시지":
                return f"{message.fact} · {refs.owner}"
            detail = f"\n{result_text(outcome)}"
        case Periodic(start=start, end=end):
            detail = f"\n관측: {timestamp(start, 'start')} ~ {timestamp(end, 'end')}"
        case _:
            assert_never(message.detail)
    location = "" if refs.location in {"이 메시지", "여기", "해당 없음"} else f"\n위치: {refs.location}"
    recovery = "" if message.recovery == "not_applicable" else f"\n되돌리기: {refs.recovery}"
    owner = (
        message.owner.argument
        if message.owner.verb == "react" and message.owner.target == Ref("self") and message.owner.argument is not None
        else refs.owner
    )
    return (
        f"{message.subject}\n{message.fact}\n{owner}{detail}{location}\n"
        f"다음: {message.agent_next or '추가 실행 없음'}{recovery}\n"
        f"-# 참조: `{message.subject_key}`"
    )


def render_v2(
    message: OwnerMessage, refs: Presentation, timestamp: Callable[[datetime, str], str],
    *, full_reference: bool = False,
) -> str:
    """Discord layout; only fact trailing whitespace is removed, never its line breaks.

    v2 shows the first 8 reference characters (frozen bytes). v3 is the same layout
    with the whole key: a shared prefix such as Plaud's ``of_`` left 8 characters
    that looked complete but could not identify the subject.
    """
    owner = refs.owner
    # A local reaction already identifies its target; do not repeat "위 위치 · 반응".
    if message.owner.verb == "react" and message.owner.target is not None:
        target = message.owner.target
        if target.scope == "self":
            argument = message.owner.argument or "✅ 실행 / ⛔ 취소"
            owner = argument if argument.startswith("이 메시지에") else f"이 메시지에 {argument}"
    trailing: list[str] = []
    match message.detail:
        case Approval(expires_at=expiry, cancel_effect=effect):
            icon = "🔔"
            expires = "만료 없음" if expiry is None else f"만료 {human_timestamp(timestamp(expiry, 'expires_at'))}"
            decision = f"**결정:** {owner} · {expires}"
            trailing.append(f"취소 시: {effect}")
        case Result(outcome=outcome):
            match outcome:
                case "executed":
                    icon = "✅"
                case "cancelled":
                    icon = "⛔"
                case "expired":
                    icon = "⌛"
                case _:
                    assert_never(outcome)
            decision = f"**결과:** {result_text(outcome)} · {owner}"
        case Periodic(start=start, end=end):
            icon = "📊"
            decision = (
                f"관측: {human_timestamp(timestamp(start, 'start'))}"
                f" ~ {human_timestamp(timestamp(end, 'end'))}\n조치: {owner}"
            )
        case _:
            assert_never(message.detail)
    if refs.location not in {"이 메시지", "여기", "해당 없음"}:
        trailing.append(f"위치: {refs.location}")
    agent_next = "추가 실행 없음" if message.agent_next is None else message.agent_next
    trailing.append(f"다음: {agent_next}")
    if message.recovery != "not_applicable":
        trailing.append(f"되돌리기: {refs.recovery}")
    subject = " ".join(message.subject.split())
    reference = message.subject_key if full_reference else message.subject_key[:8]
    return "\n".join((
        f"**{icon} {subject}**",
        *("> " + line.rstrip() for line in message.fact.split("\n")),
        "",
        decision,
        *(" ".join(line.split()) for line in trailing),
        f"-# 참조: `{reference}`",
    ))
