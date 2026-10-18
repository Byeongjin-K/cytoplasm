"""Send repair lifecycle reports and reconcile them within Discord watermarks."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, Protocol, TypeAlias
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from automation.interop.chunker import chunk_message
from automation.interop.discord_transport import DISCORD_API, SentMessage
from automation.interop.report import ReportStatus, TaskReport, format_report, parse_report
from automation.repair.repair_report_queue import ReportRequest


JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)
Fetcher: TypeAlias = Callable[[str], JsonValue]
Budget: TypeAlias = Callable[[], None]

PHRASE: Final[dict[str, str]] = {
    "applied": "수리 패치를 적용하고 회귀 검증을 통과했습니다",
    "sandbox_rejected": "샌드박스 검증에서 거절되어 패치를 적용하지 않았습니다",
    "bank_red": "회귀 뱅크 상태가 red여서 패치를 적용하지 않았습니다",
    "bank_failed_reverted": "회귀 뱅크 실패로 패치를 되돌렸습니다",
    "owner_cancelled": "소유자가 승인을 취소했습니다",
    "approval_expired": "승인 대기가 만료되었습니다",
    "unspecified": "자동 수리가 중단되어 티켓을 다시 열었습니다",
}
STATUS: Final[dict[str, ReportStatus]] = {
    "complete": ReportStatus.DONE,
    "reopen": ReportStatus.BLOCKED,
}
_bot_user_id_cache: str | None = None


@dataclass(frozen=True, slots=True)
class InteropConfigError(ValueError):
    """The private interop configuration lacks a required string identifier."""

    field: str

    def __str__(self) -> str:
        return f"interop config missing required string field: {self.field}"


@dataclass(frozen=True, slots=True)
class DiscordResponseError(ValueError):
    """A Discord response does not match the expected JSON shape."""

    detail: str

    def __str__(self) -> str:
        return f"invalid Discord response: {self.detail}"


class ReportTransport(Protocol):
    def send(self, body: str) -> tuple[SentMessage, ...]: ...


def load_config() -> dict[str, str]:
    """Load only the identity and report-channel fields from private config."""
    path = Path(os.environ.get("INTEROP_CONFIG", "~/.hermes/interop/config.json")).expanduser()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise InteropConfigError("JSON object")
    result: dict[str, str] = {}
    for field in ("agent_id", "agents_log_channel_id"):
        value = payload.get(field)
        if not isinstance(value, str) or not value:
            raise InteropConfigError(field)
        result[field] = value
    from automation.notice_bot import notice_channel

    result["agents_log_channel_id"] = notice_channel() or result["agents_log_channel_id"]
    return result


def _fetch(path: str) -> JsonValue:
    from automation.notice_bot import resolve_token

    request = Request(
        DISCORD_API + path,
        headers={
            "Authorization": f"Bot {resolve_token(required=True)}",
            "User-Agent": "DiscordBot (https://github.com/orientpine/autophagy-agents, 0)",
        },
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def bot_user_id(*, fetcher: Fetcher | None = None) -> str:
    """Return the authenticated Discord bot id, fetching it at most once."""
    global _bot_user_id_cache  # noqa: PLW0603
    if _bot_user_id_cache is not None:
        return _bot_user_id_cache
    payload = (fetcher or _fetch)("/users/@me")
    if not isinstance(payload, dict):
        raise DiscordResponseError("bot identity must be an object")
    identifier = payload.get("id")
    if not isinstance(identifier, str) or not identifier.isdecimal():
        raise DiscordResponseError("bot identity must contain a decimal string id")
    _bot_user_id_cache = identifier
    return identifier


def _messages(payload: JsonValue) -> list[dict[str, JsonValue]]:
    if not isinstance(payload, list):
        raise DiscordResponseError("messages endpoint must return an array of objects")
    narrowed = [item for item in payload if isinstance(item, dict)]
    if len(narrowed) != len(payload):
        raise DiscordResponseError("messages endpoint must return an array of objects")
    return narrowed


def channel_watermark(*, fetcher: Fetcher | None = None) -> str:
    """Return the newest report-channel message id, or ``0`` for an empty channel."""
    config = load_config()
    page = _messages((fetcher or _fetch)(f"/channels/{config['agents_log_channel_id']}/messages?limit=1"))
    if not page:
        return "0"
    identifier = page[0].get("id")
    if not isinstance(identifier, str) or not identifier.isdecimal():
        raise DiscordResponseError("watermark must be a decimal string id")
    return identifier


def _send_direct(body: str, *, budget: Budget | None) -> tuple[SentMessage, ...]:
    from automation.notice_bot import resolve_token

    config = load_config()
    token = resolve_token(required=True)
    sent: list[SentMessage] = []
    for chunk in chunk_message(body):
        request = Request(
            f"{DISCORD_API}/channels/{config['agents_log_channel_id']}/messages",
            data=json.dumps({"content": chunk}).encode("utf-8"),
            headers={
                "Authorization": f"Bot {token}",
                "Content-Type": "application/json",
                "User-Agent": "DiscordBot (https://github.com/orientpine/autophagy-agents, 0)",
            },
            method="POST",
        )
        while True:
            if budget is not None:
                budget()
            try:
                with urlopen(request, timeout=30) as response:  # noqa: S310
                    payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(payload, dict) or not isinstance(payload.get("id"), str):
                    raise DiscordResponseError("sent message must contain a string id")
                sent.append(SentMessage(payload["id"]))
                break
            except HTTPError as error:
                if error.code != 429:
                    raise
                retry_after = error.headers.get("Retry-After")
                time.sleep(float(retry_after) if retry_after else 1.0)
    return tuple(sent)


def send_report(
    request: ReportRequest,
    timestamp: datetime,
    *,
    transport: ReportTransport | None = None,
    budget: Budget | None = None,
) -> str:
    """Render and send one repair report, returning only the final id suffix."""
    config = load_config()
    report = TaskReport(
        agent_id=config["agent_id"],
        task_id=request.ticket_id,
        status=STATUS[request.operation],
        summary=PHRASE[request.reason_code],
        links=(),
        timestamp=timestamp,
    )
    sent = transport.send(format_report(report)) if transport is not None else _send_direct(
        format_report(report), budget=budget,
    )
    return sent[-1].message_id[-4:]


def _iter_window(
    *, upper: str, lower: str, cursor: str | None, max_pages: int = 50, fetcher: Fetcher,
) -> tuple[list[dict[str, JsonValue]], str, bool]:
    """Read newest-to-oldest pages within the half-open window ``(lower, upper]``."""
    if int(upper) <= int(lower) or (cursor is not None and int(cursor) <= int(lower)):
        return [], cursor or upper, True
    config = load_config()
    current = upper if cursor is None else cursor
    before = str(int(upper) + 1) if cursor is None else cursor
    collected: list[dict[str, JsonValue]] = []
    for _ in range(max_pages):
        page = _messages(fetcher(
            f"/channels/{config['agents_log_channel_id']}/messages?before={before}&limit=100"
        ))
        if not page:
            return collected, current, True
        identifiers = [str(item.get("id", "")) for item in page]
        if any(not identifier.isdecimal() for identifier in identifiers):
            raise DiscordResponseError("message id must be a decimal string")
        collected.extend(
            item for item, identifier in zip(page, identifiers, strict=True)
            if int(lower) < int(identifier) <= int(upper)
        )
        current = min(identifiers, key=int)
        if int(current) <= int(lower):
            return collected, current, True
        before = current
    return collected, current, False


def _matches(
    message: dict[str, JsonValue], *, author_id: str, agent_id: str, task_id: str,
    status: ReportStatus, timestamp_iso: str | None,
) -> bool:
    author = message.get("author")
    if not isinstance(author, dict) or author.get("id") != author_id:
        return False
    content = message.get("content")
    if not isinstance(content, str):
        return False
    report = parse_report(content)
    if report is None:
        return False
    same_report = report.agent_id == agent_id and report.task_id == task_id and report.status is status
    return same_report and (timestamp_iso is None or report.timestamp.isoformat() == timestamp_iso)


def find_report(
    *, task_id: str, status: ReportStatus, timestamp_iso: str, upper: str, lower: str,
    cursor: str | None, fetcher: Fetcher | None = None,
) -> tuple[bool, str, bool]:
    """Find one exact report occurrence and return resumable traversal state."""
    reader = fetcher or _fetch
    messages, next_cursor, exhausted = _iter_window(
        upper=upper, lower=lower, cursor=cursor, fetcher=reader,
    )
    config = load_config()
    author_id = bot_user_id(fetcher=reader)
    found = any(_matches(
        item, author_id=author_id, agent_id=config["agent_id"], task_id=task_id,
        status=status, timestamp_iso=timestamp_iso,
    ) for item in messages)
    return found, next_cursor, exhausted


def find_any_report(
    *, task_id: str, status: ReportStatus, lower: str, fetcher: Fetcher | None = None,
) -> bool:
    """Search the complete current watermark window without timestamp filtering."""
    reader = fetcher or _fetch
    upper = channel_watermark(fetcher=reader)
    cursor: str | None = None
    config = load_config()
    author_id = bot_user_id(fetcher=reader)
    while True:
        messages, cursor, exhausted = _iter_window(
            upper=upper, lower=lower, cursor=cursor, fetcher=reader,
        )
        if any(_matches(
            item, author_id=author_id, agent_id=config["agent_id"], task_id=task_id,
            status=status, timestamp_iso=None,
        ) for item in messages):
            return True
        if exhausted:
            return False
