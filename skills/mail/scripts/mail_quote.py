"""Quote the answered mail below a reply, the way a mail client's reply does.

The vendored mailon runtime has no reply command — every outbound mail is a
fresh compose (``mailon.main send --body``) — so a reply used to carry ONLY the
newly written text (owner request 2026-09-01). This module renders the original
(Outlook-style Korean header block + body) from what ``mail_wrapper get --body``
returns, and computes the reply-all Cc set. Pure: no I/O, stdlib only.

The quote goes into the SENT body (the frozen argv) only. A draft's ``body``
stays the owner-reviewed reply text and the approval message notes the quote in
one line — dumping it would blow the Discord 2,000-char post limit.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo
from typing import Final

SEPARATOR: Final = "-----원본 메시지-----"
_SEOUL: Final = ZoneInfo("Asia/Seoul")
MAX_QUOTE_CHARS: Final = 20_000
_BODY_HEADING: Final = "## Body"
_EMPTY_BODY_PLACEHOLDER: Final = "(빈 본문)"  # what mailon.writer stores for an empty body
_FRONTMATTER_KEYS: Final = frozenset({"from", "to", "cc", "date", "subject"})
_ADDR: Final = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_YAML_ESCAPE: Final = re.compile(r"\\(.)")


@dataclass(frozen=True, slots=True)
class Original:
    """Headers and text of the mail being answered."""

    sender: str
    to: str
    cc: str
    date: str
    subject: str
    body: str


def parse_original(detail: Mapping[str, object]) -> Original:
    """Read the answered mail from a wrapper ``get --body`` payload.

    ``body`` is the mailon markdown document (YAML front matter + ``## Body``).
    Front-matter values win over the row metadata; a missing document, a plain
    text body, or an unparsable front matter degrade to the row's sender /
    subject / date and the raw text — never an exception.
    """
    raw = detail.get("body")
    text = raw if isinstance(raw, str) else ""
    fields, remainder = _split_frontmatter(text)
    return Original(
        sender=fields.get("from") or _text(detail.get("sender")),
        to=fields.get("to", ""),
        cc=fields.get("cc", ""),
        date=fields.get("date") or _text(detail.get("date")),
        subject=fields.get("subject") or _text(detail.get("subject")),
        body=_body_text(remainder),
    )


def render_quote(original: Original) -> str:
    """The header block plus the (capped) original body, ready to append."""
    lines = [SEPARATOR, f"보낸 사람: {original.sender}"]
    if original.date:
        lines.append(f"보낸 날짜: {_display_date(original.date)}")
    if original.to:
        lines.append(f"받는 사람: {original.to}")
    if original.cc:
        lines.append(f"참조: {original.cc}")
    lines.append(f"제목: {original.subject}")
    header = "\n".join(lines) + "\n"
    body = _capped(original.body)
    return header if not body else f"{header}\n{body}"


def with_quote(body: str, quote: str) -> str:
    """The text that is actually sent: the reply text, a blank line, the quote."""
    return body if not quote else f"{body}\n\n{quote}"


def reply_all_cc(original: Original, *, to: str, owner: str) -> str:
    """Original To ∪ Cc as a comma-joined Cc, minus the reply target and the owner.

    Addresses are lowercased and de-duplicated in first-seen order; display
    names are dropped because the send argv carries bare addresses.
    """
    excluded = {to.strip().lower(), owner.strip().lower()}
    kept: list[str] = []
    addresses: list[str] = _ADDR.findall(f"{original.to}, {original.cc}")
    for address in addresses:
        lowered = address.lower()
        if lowered not in excluded and lowered not in kept:
            kept.append(lowered)
    return ", ".join(kept)


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    closing = next((index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
    if closing is None:
        return {}, text
    fields: dict[str, str] = {}
    for line in lines[1:closing]:
        key, _separator, value = line.partition(":")
        name = key.strip().lower()
        if name in _FRONTMATTER_KEYS and name not in fields:
            fields[name] = _unquote(value.strip())
    return fields, "\n".join(lines[closing + 1 :])


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return _YAML_ESCAPE.sub(r"\1", value[1:-1])
    return value


def _body_text(remainder: str) -> str:
    lines = remainder.splitlines()
    heading = next((index for index, line in enumerate(lines) if line.strip() == _BODY_HEADING), None)
    body = remainder.strip() if heading is None else "\n".join(lines[heading + 1 :]).strip()
    return "" if body == _EMPTY_BODY_PLACEHOLDER else body


def _display_date(value: str) -> str:
    """Show the original's date in Korea time; MailOn stores it as naive UTC (2026-10-19)."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(_SEOUL).strftime("%Y-%m-%d %H:%M")


def _capped(body: str) -> str:
    if len(body) <= MAX_QUOTE_CHARS:
        return body
    return (
        body[:MAX_QUOTE_CHARS].rstrip()
        + f"\n…(원문 {len(body)}자 중 {MAX_QUOTE_CHARS}자까지 인용)"
    )
