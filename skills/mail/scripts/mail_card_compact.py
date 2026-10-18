"""Mail approval card v4: who, subject, body and the reaction line; bindings stay in the record.

Owner 2026-10-19 00:00 KST: draft ids, hashes and the cancel/next/undo lines are internal and
must not show in Discord. The card still binds to its draft: the record keeps the sha256 of
the exact posted text (``card_sha256``), so a v4 message must equal what was posted.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping

VERSION = "4"
_FILE_HASH = re.compile(r" · `[0-9a-f]{64}`")
_HIDDEN = ("- draft:", "- action hash:", "- 반응(기본):", "- body sha256:")


def render(legacy: str, instruction: str) -> str:
    lines = legacy.splitlines()
    title = lines[0].removeprefix("[mail-triage] ").replace(" (DM 확정)", "")
    kept = [_FILE_HASH.sub("", line) for line in lines[1:] if not line.startswith(_HIDDEN)]
    return "\n".join([f"📨 **{title}**", *kept, instruction])


def digest(content: str) -> str:
    return hashlib.sha256(content.strip().encode("utf-8")).hexdigest()


def references(record: Mapping[str, object], content: str, token: str) -> bool:
    """Is this Discord text the card posted for the record (v4) or one naming its hash (v1-v3)?"""
    if record.get("render_version") == VERSION:
        expected = record.get("card_sha256")
        return isinstance(expected, str) and bool(expected) and digest(content) == expected
    return bool(token) and token in content
