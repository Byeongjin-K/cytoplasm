"""Every session in this repo reads the root AGENTS.md on every model call; keep reference maps out of it.

2026-10-19 measurement (omo -p in this checkout): 181,338 context tokens before the session read anything,
47,146 without AGENTS.md. The CODE MAP section alone cost about 62k of them, so it moved to CODE-MAP.md,
which a session reads only when it needs a module.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

_REPO_ROOT: Final = Path(__file__).resolve().parents[2]
_AGENTS: Final = _REPO_ROOT / "AGENTS.md"
_CODE_MAP: Final = _REPO_ROOT / "CODE-MAP.md"
#: 155,7xx bytes right after the move (304,174 before). Headroom for new owner rules, not for maps or tables:
#: move reference material to CODE-MAP.md or docs/ instead of raising this.
_MAX_BYTES: Final = 175_000


def _section(text: str, heading: str) -> str:
    parts = re.split(r"(?m)^(?=## )", text)
    found = [part for part in parts if part.startswith(f"## {heading}\n")]
    assert len(found) == 1, f"expected one '## {heading}' section, found {len(found)}"
    return found[0]


def test_agents_md_holds_only_a_pointer_to_the_code_map() -> None:
    pointer = _section(_AGENTS.read_text(encoding="utf-8"), "CODE MAP")
    assert "[CODE-MAP.md](CODE-MAP.md)" in pointer
    assert len(pointer.splitlines()) <= 4, (
        "the CODE MAP belongs in CODE-MAP.md, not in AGENTS.md"
    )
    assert len(_section(_CODE_MAP.read_text(encoding="utf-8"), "CODE MAP")) > 10 * len(
        pointer
    )


def test_agents_md_stays_within_its_byte_budget() -> None:
    size = len(_AGENTS.read_bytes())
    assert size <= _MAX_BYTES, (
        f"AGENTS.md is {size} bytes (budget {_MAX_BYTES}); every session pays it on every call. "
        "Move maps, tables and history to CODE-MAP.md or docs/ and link them."
    )
