"""Every session in this repo reads the root AGENTS.md on every model call; keep it to short directives.

2026-10-19 measurements (omo -p in this checkout): 181,338 context tokens before a session read anything with
the old 304 KB AGENTS.md, 47,146 without it. The CODE MAP moved to CODE-MAP.md (119,811), then the owner's rule
sections moved verbatim to RULES.md with a short directive each left here (owner decision Q290; 71,988).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

_REPO_ROOT: Final = Path(__file__).resolve().parents[2]
_AGENTS: Final = _REPO_ROOT / "AGENTS.md"
_CODE_MAP: Final = _REPO_ROOT / "CODE-MAP.md"
_RULES: Final = _REPO_ROOT / "RULES.md"
#: 51,001 bytes after Q290. Headroom for a new directive, not for an original: put the full rule text in RULES.md.
_MAX_BYTES: Final = 60_000


def _sections(text: str) -> dict[str, str]:
    parts = [part for part in re.split(r"(?m)^(?=## )", text) if part.startswith("## ")]
    return {part.splitlines()[0]: part for part in parts}


def _title(heading: str) -> str:
    return heading.removeprefix("## ").split(" (")[0].strip()


def test_agents_md_holds_only_a_pointer_to_the_code_map() -> None:
    pointer = _sections(_AGENTS.read_text(encoding="utf-8"))["## CODE MAP"]
    assert "[CODE-MAP.md](CODE-MAP.md)" in pointer
    assert len(pointer.splitlines()) <= 4, (
        "the CODE MAP belongs in CODE-MAP.md, not in AGENTS.md"
    )
    assert len(
        _sections(_CODE_MAP.read_text(encoding="utf-8"))["## CODE MAP"]
    ) > 10 * len(pointer)


def test_every_rule_original_in_rules_md_has_its_short_directive_and_pointer_in_agents_md() -> (
    None
):
    agents = _sections(_AGENTS.read_text(encoding="utf-8"))
    rules = _sections(_RULES.read_text(encoding="utf-8"))
    assert rules, "RULES.md holds the originals of the owner's rules"
    for heading, original in rules.items():
        assert heading in agents, (
            f"{heading}: the rule has no short directive in AGENTS.md"
        )
        short = agents[heading]
        assert f"[RULES.md](RULES.md) 「{_title(heading)}」" in short, (
            f"{heading}: AGENTS.md does not point to its original"
        )
        assert len(short) < len(original), (
            f"{heading}: AGENTS.md holds the original instead of a short directive"
        )


def test_agents_md_stays_within_its_byte_budget() -> None:
    size = len(_AGENTS.read_bytes())
    assert size <= _MAX_BYTES, (
        f"AGENTS.md is {size} bytes (budget {_MAX_BYTES}); every session pays it on every call. "
        "Put the full rule in RULES.md and a short directive here; maps and tables go to CODE-MAP.md."
    )
