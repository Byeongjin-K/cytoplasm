"""Pure stdlib contract; no I/O/network/clock. Contract 1 / render versions are append-only.
Public: Ref, Action, Approval, Result, Periodic, OwnerMessage, render, discord_link,
LinkStatus, LinkResult, OwnerMessageError, Space. Private text: owner_message_text.
Malformed fields/unsupported versions raise OwnerMessageError; declared types render.
"""
from __future__ import annotations

from dataclasses import MISSING, dataclass, fields
from datetime import datetime
from enum import StrEnum
from typing import Final, assert_never

from .owner_message_text import (
    Action as Action, Approval as Approval, OwnerMessage as OwnerMessage,
    Periodic as Periodic, Ref as Ref, Result as Result, Space as Space,
    Presentation, render_v1, render_v2, render_v4, resource_url as _resource_url,
)

__all__: Final = (
    "Ref", "Action", "Approval", "Result", "Periodic", "OwnerMessage", "render",
    "discord_link", "LinkStatus", "LinkResult", "OwnerMessageError", "Space",
)


class LinkStatus(StrEnum):
    """DELETED: approval_reminder.resolve_source_link lookup only; never discord_link/render."""
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"
    DELETED = "deleted"


@dataclass(frozen=True, slots=True)
class LinkResult:
    status: LinkStatus
    url: str | None = None
    detail: str | None = None


class OwnerMessageError(ValueError):
    """지원하지 않는 버전 또는 잘못된 필드. detail은 값이 아닌 필드 경로다."""

    def __init__(self, contract_version: int | None = None, render_version: str | None = None,
                 *, detail: str = "message") -> None:
        self.contract_version: int | None = contract_version
        self.render_version: str | None = render_version
        self.detail: str = detail
        super().__init__(f"invalid owner message field: {detail}")


def _require(condition: bool, field: str) -> None:
    if not condition:
        raise OwnerMessageError(detail=field)


def _model(value: OwnerMessage | Ref | Action | Approval | Result | Periodic, kinds: tuple[type, ...], field: str) -> None:
    _require(any(type(value) is kind for kind in kinds), field)
    for slot in fields(value):
        _require(getattr(value, slot.name, MISSING) is not MISSING, f"{field}.{slot.name}")


def _datetime(value: datetime, field: str) -> str:
    try:
        return value.isoformat()
    except BaseException as error:  # B1: arbitrary producer tzinfo failures become field-aware refusals.
        raise OwnerMessageError(detail=f"message.detail.{field}") from error


def _validate(message: OwnerMessage, destination: Ref) -> None:
    """Public boundary only: reject runtime shapes before any formatting operation."""
    _model(message, (OwnerMessage,), "message")
    refs = [(message.location, "message.location"), (destination, "destination")]
    for value, field in ((message.subject_key, "subject_key"), (message.subject, "subject"),
                         (message.fact, "fact"), (message.render_version, "render_version")):
        _require(type(value) is str, f"message.{field}")
    _require(message.agent_next is None or type(message.agent_next) is str, "message.agent_next")
    _require(type(message.contract_version) is int, "message.contract_version")
    _require(type(message.recovery) is Action or (type(message.recovery) is str
             and message.recovery in ("not_applicable", "irreversible")), "message.recovery")
    actions = [(message.owner, "message.owner")]
    match message.recovery:
        case Action() as action:
            actions.append((action, "message.recovery"))
        case "not_applicable" | "irreversible":
            pass
        case _:
            assert_never(message.recovery)
    for action, field in actions:
        _model(action, (Action,), field)
        _require(type(action.verb) is str and action.verb in ("none", "react", "reply", "open"), f"{field}.verb")
        _require(action.argument is None or type(action.argument) is str, f"{field}.argument")
        if action.target is not None:
            refs.append((action.target, f"{field}.target"))
    for ref, field in refs:
        _model(ref, (Ref,), field)
        _require(type(ref.scope) is str and ref.scope in ("self", "channel", "message", "resource", "none"), f"{field}.scope")
        _require(type(ref.space) is str and ref.space in ("guild", "dm", "unknown"), f"{field}.space")
        for value, name in ((ref.guild_id, "guild_id"), (ref.channel_id, "channel_id"),
                            (ref.message_id, "message_id"), (ref.url, "url")):
            _require(value is None or type(value) is str, f"{field}.{name}")
        _require(ref.search is None or (type(ref.search) is tuple and len(ref.search) == 2
                 and all(type(part) is str for part in ref.search)), f"{field}.search")
    _model(message.detail, (Approval, Result, Periodic), "message.detail")
    match message.detail:
        case Approval(expires_at=expiry, cancel_effect=effect):
            _require(expiry is None or type(expiry) is datetime, "message.detail.expires_at")
            _require(type(effect) is str, "message.detail.cancel_effect")
        case Result(outcome=outcome):
            _require(type(outcome) is str and outcome in ("executed", "cancelled", "expired"), "message.detail.outcome")
        case Periodic(start=start, end=end):
            _require(type(start) is datetime, "message.detail.start")
            _require(type(end) is datetime, "message.detail.end")
        case _:
            assert_never(message.detail)


def _text(value: str) -> bool:
    return type(value) is str


def _snowflake(value: str) -> bool:
    return _text(value) and value.isascii() and value.isdecimal() and bool(value.strip("0"))


def discord_link(
    *, space: Space, channel_id: str | None, message_id: str | None = None,
    guild_id: str | None = None,
) -> LinkResult:
    """순수 좌표 링크. Four keyword parameters preserve the agreed shared API."""
    if not _text(space) or space not in ("guild", "dm", "unknown"):
        return LinkResult(LinkStatus.INVALID, detail="공간 형식 오류")
    match space:
        case "unknown":
            return LinkResult(LinkStatus.UNAVAILABLE, detail="공간 미상")
        case "guild" | "dm":
            pass
        case _:
            assert_never(space)
    if any(value is not None and not _snowflake(value)
           for value in (channel_id, message_id, guild_id)):
        return LinkResult(LinkStatus.INVALID, detail="좌표 형식 오류")
    if channel_id is None:
        return LinkResult(LinkStatus.UNAVAILABLE, detail="위치 좌표 없음")
    match space:
        case "guild":
            if guild_id is None:
                return LinkResult(LinkStatus.UNAVAILABLE, detail="서버 좌표 없음")
            root = guild_id
        case "dm":
            root = "@me"
        case _:
            assert_never(space)
    suffix = "" if message_id is None else f"/{message_id}"
    return LinkResult(LinkStatus.AVAILABLE, f"https://discord.com/channels/{root}/{channel_id}{suffix}")


def _reference(ref: Ref, destination: Ref) -> str:
    match ref.scope:
        case "none":
            return "해당 없음"
        case "self":
            return "이 메시지"
        case "channel" | "message":
            if ref.channel_id is not None and destination.channel_id is not None and ref.channel_id == destination.channel_id:
                return "여기"
            match ref.scope:
                case "channel":
                    message_id = None
                case "message":
                    message_id = ref.message_id
                case _:
                    assert_never(ref.scope)
            link = discord_link(space=ref.space, guild_id=ref.guild_id,
                                channel_id=ref.channel_id, message_id=message_id)
        case "resource":
            url, reason = _resource_url(ref.url)
            link = LinkResult(LinkStatus.AVAILABLE if url is not None else LinkStatus.UNAVAILABLE,
                              url, reason)
        case _:
            assert_never(ref.scope)
    if link.url is not None:
        return link.url
    fallback = f"링크 없음 ({link.detail})"
    if ref.search is not None:
        label, key = ref.search
        return f"{fallback}; 검색: {label} / {key}"
    return fallback


def _action(action: Action, location: str, destination: Ref) -> str:
    match action.verb:
        case "none":
            return "조치 없음"
        case "react":
            verb = "반응"
        case "reply":
            verb = "답글"
        case "open":
            verb = "열기"
        case _:
            assert_never(action.verb)
    target = "대상 미정" if action.target is None else _reference(action.target, destination)
    if target == location:
        target = "위 위치"
    argument = "" if action.argument is None else f" {action.argument}"
    return f"{target} · {verb}{argument}"


def render(message: OwnerMessage, *, destination: Ref) -> str:
    """Validate once; replay v1 or present Discord v2/v3 without changing the contract."""
    _validate(message, destination)
    if message.contract_version != 1 or message.render_version not in {
        "owner-ko-v1", "owner-ko-v2", "owner-ko-v3", "owner-ko-v4",
    }:
        field = "contract_version" if message.contract_version != 1 else "render_version"
        raise OwnerMessageError(message.contract_version, message.render_version, detail=f"message.{field}")
    location = _reference(message.location, destination)
    # v2 omits local location rows, so actions cannot point "above" at such a row.
    action_location = "" if message.render_version != "owner-ko-v1" and location in {
        "이 메시지", "여기", "해당 없음",
    } else location
    match message.recovery:
        case "not_applicable":
            recovery = "해당 없음"
        case "irreversible":
            recovery = "되돌릴 수 없음"
        case Action() as action:
            recovery = _action(action, action_location, destination)
        case _:
            assert_never(message.recovery)
    owner = _action(message.owner, action_location, destination)
    presentation = Presentation(location, owner, recovery)
    if message.render_version == "owner-ko-v1":
        return render_v1(message, presentation, _datetime)
    if message.render_version == "owner-ko-v4":
        return render_v4(message, presentation, _datetime)
    return render_v2(
        message, presentation, _datetime, full_reference=message.render_version == "owner-ko-v3",
    )
