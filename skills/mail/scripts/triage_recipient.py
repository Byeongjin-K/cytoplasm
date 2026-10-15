"""Recipient-role detection: is the owner a To or only a Cc (참조) recipient?

The mailon markdown body (returned verbatim by the W4-1 wrapper ``get --body``)
starts with a YAML-ish frontmatter block carrying ``to:``/``cc:`` lines. This
module compares those addresses against the owner address (``MAILON_ID`` —
the login account IS the institutional email, W0-7c) and yields a role:

- ``"to"``      owner appears in To (reply expectations unchanged)
- ``"cc"``      owner appears ONLY in Cc — not a reply target (owner decision
                2026-07-19: digest/summaries must bucket these as 참조)
- ``"unknown"`` owner address unavailable or frontmatter absent/unparsable —
                callers keep today's behavior (fail-open)

Owner address: ``MAILON_ID`` env first, then a ``~/.env.secrets`` KEY=VALUE
self-load fallback (watcher-cron 규약 — Hermes no-agent cron does not inject
the secrets file). stdlib only.
"""
from __future__ import annotations

import os
import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

_ADDR = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_SECRETS_LINE = re.compile(r"^(?:export\s+)?MAILON_ID\s*=\s*(.+?)\s*$")


def owner_address(*, secrets_path: Path | None = None) -> str:
    """Owner institutional address: env MAILON_ID, else ~/.env.secrets, else ''."""
    value = os.environ.get("MAILON_ID", "").strip()
    if value:
        return value
    path = secrets_path if secrets_path is not None else Path.home() / ".env.secrets"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    for line in text.splitlines():
        matched = _SECRETS_LINE.match(line.strip())
        if matched:
            return matched.group(1).strip().strip("'\"")
    return ""


def parse_recipients(markdown: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(to_addresses, cc_addresses) from the leading frontmatter block only.

    Addresses are lowercased. Any shape problem yields empty tuples — the
    caller degrades to role ``unknown`` (today's behavior).
    """
    if not markdown.startswith("---"):
        return (), ()
    parts = markdown.split("---", 2)
    if len(parts) < 3:
        return (), ()
    found: dict[str, tuple[str, ...]] = {}
    for line in parts[1].splitlines():
        key = line.split(":", 1)[0].strip().lower()
        if key in ("to", "cc") and key not in found:
            value = line.split(":", 1)[1]
            found[key] = tuple(addr.lower() for addr in _ADDR.findall(value))
    return found.get("to", ()), found.get("cc", ())


def recipient_role(markdown: str, owner: str) -> str:
    """``"to"`` | ``"cc"`` | ``"unknown"`` for the owner in this mail."""
    owner_normalized = (owner or "").strip().lower()
    if not owner_normalized:
        return "unknown"
    to_addresses, cc_addresses = parse_recipients(markdown)
    if owner_normalized in to_addresses:
        return "to"
    if owner_normalized in cc_addresses:
        return "cc"
    return "unknown"


_REPLY_PREFIX = re.compile(
    r"^\s*(?:(?:re|fw|fwd|회신|답장|전달)\s*(?:\[\d+\])?\s*:|\[(?:re|fw|fwd|회신|답장|전달)\])\s*",
    re.IGNORECASE,
)


_FORWARD_PREFIX = re.compile(r"^\s*(?:(?:fw|fwd|전달)\s*:|\[(?:fw|fwd|전달)\])", re.IGNORECASE)


def thread_subject(subject: str) -> str:
    """Subject without any leading ``Re:``/``FW:``/``[RE]`` chain, whitespace-folded."""
    text = " ".join(subject.split())
    while True:
        stripped = _REPLY_PREFIX.sub("", text, count=1)
        if stripped == text:
            return text.lower()
        text = stripped


def _utc(stamp: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def reply_candidates(subject: str, received: str, sent_mails: Iterable[dict]) -> list[dict]:
    """Owner sent mails in the same thread after ``received``, oldest first.

    Same thread = equal subject once reply/forward prefixes are stripped (the
    webmail keeps no In-Reply-To header in state.db). A sent mail that is itself
    a forward (``FW:``) passes the mail on rather than answering it, so it is not
    a candidate. An unparseable date on either side matches nothing, so the
    reply flag stays (fail-open).
    """
    received_at = _utc(received)
    key = thread_subject(subject)
    if received_at is None or not key:
        return []
    hits = []
    for mail in sent_mails:
        sent_subject = str(mail.get("subject") or "")
        if _FORWARD_PREFIX.match(sent_subject):
            continue
        sent_at = _utc(str(mail.get("date") or ""))
        if sent_at and sent_at > received_at and thread_subject(sent_subject) == key:
            hits.append((sent_at, mail))
    return [mail for _, mail in sorted(hits, key=lambda pair: pair[0])]


def addressed_to(markdown: str, sender: str) -> bool:
    """True when the sender's address is a To or Cc recipient of this mail."""
    found = _ADDR.findall(sender)
    if not found:
        return False
    to_addresses, cc_addresses = parse_recipients(markdown)
    return found[-1].lower() in to_addresses + cc_addresses


_SUBJECT_STOPWORDS = frozenset({"re", "fw", "fwd"})
_CREATED_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _subject_tokens(subject: str) -> frozenset[str]:
    tokens = re.findall(r"[0-9A-Za-z가-힣]{2,}", subject.lower())
    return frozenset(token for token in tokens if token not in _SUBJECT_STOPWORDS)


def _addresses(joined: str) -> frozenset[str]:
    return frozenset(
        part.strip().lower() for part in joined.split(",") if part.strip())


def related_recipient_gap(
    new_to: str,
    new_subject: str,
    drafts: Iterable[dict],
    *,
    now_utc: str,
    window_hours: int = 24,
) -> list[str]:
    """Recipients of recent related SENT composes missing from a new compose.

    Deterministic assist for the contextual-omission failure (2026-07-20: a
    follow-up confirmation mail silently dropped one participant). Considers
    only executed compose drafts within the window whose subject shares at
    least one meaningful token; returns the sorted missing addresses. Any
    unparsable record is skipped (warning-only surface — never blocks).
    """
    try:
        now = datetime.strptime(now_utc, _CREATED_FORMAT)
    except (TypeError, ValueError):
        return []
    window = timedelta(hours=window_hours)
    new_tokens = _subject_tokens(new_subject)
    new_addresses = _addresses(new_to)
    union: set[str] = set()
    for draft in drafts:
        if draft.get("kind") != "compose" or draft.get("status") != "executed":
            continue
        try:
            created = datetime.strptime(str(draft.get("created")), _CREATED_FORMAT)
        except (TypeError, ValueError):
            continue
        if not (timedelta(0) <= now - created <= window):
            continue
        if not (new_tokens & _subject_tokens(str(draft.get("subject") or ""))):
            continue
        union |= _addresses(str(draft.get("to") or ""))
    return sorted(union - new_addresses)
