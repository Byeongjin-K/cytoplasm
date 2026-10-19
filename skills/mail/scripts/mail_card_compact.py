"""Mail approval card v4: who, subject, body and the reaction line; bindings stay in the record.

Owner 2026-10-19 00:00 KST: draft ids, hashes and the cancel/next/undo lines are internal and
must not show in Discord. The card still binds to its draft: the record keeps the sha256 of
the exact posted text (``card_sha256``), so a v4 message must equal what was posted.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping

VERSION = "5"
DIGEST_VERSIONS = frozenset({"4", "5", "6"})  # verify by the stored digest of the posted text
_FILE_HASH = re.compile(r" · `[0-9a-f]{64}`")
_HIDDEN = ("- draft:", "- action hash:", "- 반응(기본):", "- body sha256:")
_SHARE = "- Drive 공유:"
# Owner Q273 (2026-10-19 12:31 KST): an address with no Google account is shared with Google's invite.
INVITE_NOTICE = "- 받는 분에게 구글 계정이 없으면 구글이 초대 알림 메일도 보냅니다."


def _size(value: object) -> str:
    size = int(str(value))
    if size < 1024:
        return f"{size} B"
    return f"{size / 1024:.1f} KB" if size < 1024 * 1024 else f"{size / 1024 / 1024:.1f} MB"


def share_line(share: Mapping[str, object]) -> str:
    """v5: what gets shared with whom, in one line the owner can read (2026-10-19)."""
    name = str(share["source_name"]).replace("`", "'")
    people = ", ".join(f"`{person}`" for person in share["grantees"])  # type: ignore[union-attr]
    return f"- 보낼 때 `{name}`({_size(share['size_bytes'])}) 공유 링크가 채워짐(공유 대상: {people}, 보기)"


def render(
    legacy: str, instruction: str, share: Mapping[str, object] | None = None, *,
    format_test: bool = False,
) -> str:
    """v4 when ``share`` is None; v5 swaps the internal share block for :func:`share_line`."""
    lines = legacy.splitlines()
    title = lines[0].removeprefix("[mail-triage] ").replace(" (DM 확정)", "")
    kept: list[str] = []
    in_share = False
    # A forward with no note of its own showed an empty code block (live QA 1, 2026-10-19).
    for start in range(1, len(lines) - 3):
        if lines[start:start + 4] == ["- 본문:", "```", "", "```"]:
            lines = [*lines[:start], *lines[start + 4:]]
            break
    for line in lines[1:]:
        if share is not None and line.startswith(_SHARE):
            in_share = True
            kept.extend((share_line(share), INVITE_NOTICE))
            continue
        if in_share and line.startswith("  "):
            continue
        in_share = False
        if not line.startswith(_HIDDEN):
            kept.append(_FILE_HASH.sub("", line))
    test_lines = []
    if format_test:
        from mail_reply_format import TEST_CARD_LINE

        test_lines.append(TEST_CARD_LINE)
    return "\n".join([f"📨 **{title}**", *test_lines, *kept, instruction])


def digest(content: str) -> str:
    return hashlib.sha256(content.strip().encode("utf-8")).hexdigest()


def references(record: Mapping[str, object], content: str, token: str) -> bool:
    """Is this Discord text the card posted for the record (v4) or one naming its hash (v1-v3)?"""
    if record.get("render_version") in DIGEST_VERSIONS:
        expected = record.get("card_sha256")
        return isinstance(expected, str) and bool(expected) and digest(content) == expected
    return bool(token) and token in content
