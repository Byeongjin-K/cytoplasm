"""Mail approval card v4: who, subject, body and the reaction line; bindings stay in the record.

Owner 2026-10-19 00:00 KST: draft ids, hashes and the cancel/next/undo lines are internal and
must not show in Discord. The card still binds to its draft: the record keeps the sha256 of
the exact posted text (``card_sha256``), so a v4 message must equal what was posted.

v5 (owner 2026-10-19 12:15 KST "앞으로 이런 실수 없도록 해"): the long-mail card also lists the
attachments and which file a Drive share sends to whom, and both card sizes say Google may mail
its own invite (owner Q273, 2026-10-19 12:31 KST).
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping

VERSION = "5"
COMPACT_VERSIONS = frozenset({"4", "5"})
_FILE_HASH = re.compile(r" · `[0-9a-f]{64}`")
_HIDDEN = ("- draft:", "- action hash:", "- 반응(기본):", "- body sha256:")
_SHARE_PEOPLE = "  - 보기 권한(reader)만:"
INVITE_NOTICE = "  - 받는 분에게 구글 계정이 없으면 구글이 초대 알림 메일도 보냅니다."


def render(legacy: str, instruction: str, *, invite_notice: bool = False) -> str:
    lines = legacy.splitlines()
    title = lines[0].removeprefix("[mail-triage] ").replace(" (DM 확정)", "")
    kept: list[str] = []
    for line in lines[1:]:
        if line.startswith(_HIDDEN):
            continue
        kept.append(_FILE_HASH.sub("", line))
        if invite_notice and line.startswith(_SHARE_PEOPLE):
            kept.append(INVITE_NOTICE)
    return "\n".join([f"📨 **{title}**", *kept, instruction])


def digest(content: str) -> str:
    return hashlib.sha256(content.strip().encode("utf-8")).hexdigest()


def references(record: Mapping[str, object], content: str, token: str) -> bool:
    """Is this Discord text the card posted for the record (v4) or one naming its hash (v1-v3)?"""
    if record.get("render_version") in COMPACT_VERSIONS:
        expected = record.get("card_sha256")
        return isinstance(expected, str) and bool(expected) and digest(content) == expected
    return bool(token) and token in content
