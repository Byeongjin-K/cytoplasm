"""Explicit-only Obsidian date metadata normalization."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Final

from ..documents import LogicalDocument

_EXPLICIT_DATE: Final = re.compile(
    r"^\s*(?P<year>20\d{2})[-/.]?(?P<month>\d{2})[-/.]?(?P<day>\d{2})(?:[T\s].*)?\s*$"
)
_CALLOUT_DATE: Final = re.compile(
    r"^>\s*(?P<key>created|modified|updated|date)\s*:\s*(?P<value>.+?)\s*$",
    re.IGNORECASE,
)
_PATH_DATE: Final = re.compile(r"^20\d{6}\.md$")


def _normalize_explicit_date(value: str) -> str | None:
    match = _EXPLICIT_DATE.fullmatch(value)
    if match is None:
        return None
    try:
        parts = (int(match.group(key)) for key in ("year", "month", "day"))
        return date(*parts).isoformat()
    except ValueError:
        return None


def explicit_date_metadata(document: LogicalDocument, relative: str) -> dict[str, str]:
    """Return dates stated in YAML/callouts; paths supply only a provenance basis."""
    if not document.chunks:
        return {}
    metadata = document.chunks[0].metadata
    callouts: dict[str, str] = {}
    for line in "\n".join(chunk.content for chunk in document.chunks).splitlines():
        match = _CALLOUT_DATE.fullmatch(line)
        if match is not None:
            callouts.setdefault(match.group("key").casefold(), match.group("value"))

    def first_date(keys: tuple[str, ...]) -> tuple[str | None, str | None]:
        for key in keys:
            normalized = _normalize_explicit_date(metadata.get(key) or callouts.get(key, ""))
            if normalized is not None:
                return normalized, key
        return None, None

    event_date, event_key = first_date(("date", "created"))
    document_updated, _updated_key = first_date(("modified", "updated"))
    extra: dict[str, str] = {}
    if event_date is not None:
        extra["event_date"] = event_date
        extra["date_basis"] = "created" if event_key == "created" else "day"
    elif _PATH_DATE.fullmatch(Path(relative).name):
        extra["date_basis"] = "path"
    if document_updated is not None:
        extra["document_updated"] = document_updated
    return extra
