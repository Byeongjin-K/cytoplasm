"""Naming and placement rules for the single Drive outputs tree (pure logic).

Owns the ONLY registry of output categories so no caller hardcodes a folder
name: ``<root>/<category>/<YYYY>/<date-prefixed file | bundle folder>/<file>``
with the root counted as depth 1 and depth 5 as the hard ceiling (a bundle's
own files sit at the ceiling; shallower placements such as a budget sheet at
``예산/<YYYY>/<sheet>`` stay legal). Gate-only kinds (patent) are refused here
— they belong to their dedicated export gate.

No Drive I/O lives in this module; ``automation.drive_outputs`` is the publish
facade that turns these names into gws calls.
"""

from __future__ import annotations

import os
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final, Literal, TypeAlias, get_args

Periodicity: TypeAlias = Literal["weekly", "oneshot", "monthly"]
_PERIODICITIES: Final = frozenset(get_args(Periodicity))

OUTPUTS_ROOT_ENV: Final = "DRIVE_OUTPUTS_ROOT"
DEFAULT_OUTPUTS_ROOT: Final = "autophagy"
MAX_DEPTH: Final = 5


class TaxonomyError(ValueError):
    """A kind, periodicity or path breaks the outputs convention (fail closed)."""


@dataclass(frozen=True, slots=True)
class Category:
    """One output category. ``skill_owned`` names the command that must produce it.

    A category a skill owns cannot be published by hand: the skill's pipeline is where
    the artifact gets its side effects (a meeting's action-item ledger and its
    management numbers, a transcript's glossary pass). A document that skipped the
    pipeline looks identical in the folder and is missing exactly what the pipeline
    adds. The registry carries the fact; ``drive_publish_cli`` enforces it, and the
    facade does NOT — the skills themselves publish through the facade.
    """

    folder: str
    periodicity: Periodicity
    gate_only: bool = False
    always_bundle: bool = False
    skill_owned: str = ""


CATEGORIES: Final[dict[str, Category]] = {
    "report": Category(folder="주간동향", periodicity="weekly", always_bundle=True),
    "proposal": Category(folder="제안서", periodicity="oneshot"),
    "budget": Category(folder="예산", periodicity="monthly"),
    "meeting": Category(
        folder="회의록", periodicity="oneshot",
        skill_owned="skills/meeting/scripts/meeting_cli.py ingest --project <과제명>",
    ),
    "transcript": Category(
        folder="전사본", periodicity="oneshot",
        skill_owned="skills/speechtotext/scripts/speechtotext_cli.py",
    ),
    "audio": Category(
        folder="녹음원본", periodicity="oneshot",
        skill_owned="plaud_sync",
    ),
    "procurement": Category(folder="구매", periodicity="oneshot"),
    "doctype": Category(folder="문서", periodicity="oneshot"),
    "patent": Category(folder="특허", periodicity="oneshot", gate_only=True),
    # A file the owner sends a named person through the mail approval card (2026-10-14).
    # The upload happens only after that card's ✅, so a hand publish has no place here.
    "share": Category(
        folder="공유", periodicity="oneshot",
        skill_owned="skills/mail/scripts/triage_cli.py compose --drive-share <파일> --share-with <주소>",
    ),
}


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def outputs_root() -> str:
    """Name of the single Drive root folder holding every published output."""
    return _nfc(os.environ.get(OUTPUTS_ROOT_ENV) or DEFAULT_OUTPUTS_ROOT)


def category(kind: str) -> Category:
    """Registry lookup — gate-only kinds resolve here (the migrator moves them)."""
    found = CATEGORIES.get(kind)
    if found is None:
        raise TaxonomyError(f"unknown outputs kind: {kind!r}")
    return found


def period_key(periodicity: Periodicity, on: date) -> str:
    """Date prefix for a category: ISO week, month or day."""
    if periodicity not in _PERIODICITIES:
        raise TaxonomyError(f"unknown periodicity: {periodicity!r}")
    if periodicity == "weekly":
        iso = on.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if periodicity == "monthly":
        return f"{on.year:04d}-{on.month:02d}"
    return on.isoformat()


def category_parts(kind: str) -> tuple[str, ...]:
    """State-file folder parts for a kind: root / category (without a year)."""
    found = category(kind)
    if found.gate_only:
        raise TaxonomyError(
            f"{kind!r} is gate-only — publish it through its dedicated gate",
        )
    return (outputs_root(), _nfc(found.folder))


def folder_parts(kind: str, year: int, *, project: str | None = None) -> tuple[str, ...]:
    """Publish-path folders for a kind: root / category / [project] / year.

    The project segment is data, like the year — not a registry entry. Only the
    category's folder NAME belongs to the registry, and a research project is not a
    category: transcripts and minutes of one project belong together, and the owner's
    glossary for that project lives beside them. Callers that do not name a project
    get exactly the path they got before, which is what keeps the other six skills
    publishing through this facade untouched.
    """
    found = category(kind)
    if found.gate_only:
        raise TaxonomyError(
            f"{kind!r} is gate-only — publish it through its dedicated gate",
        )
    if project is not None:
        return (*project_parts(kind, project), str(year))
    return (outputs_root(), _nfc(found.folder), str(year))


def project_parts(kind: str, project: str) -> tuple[str, ...]:
    """The project folder itself — root / category / project.

    One research project's transcripts, its minutes and the owner's glossary for it
    belong together, so a caller must be able to name that folder without re-deriving
    the tree by hand. The category name still comes from the registry.
    """
    found = category(kind)
    if found.gate_only:
        raise TaxonomyError(
            f"{kind!r} is gate-only — publish it through its dedicated gate",
        )
    named = _nfc(project).strip()
    if not named or "/" in named or "\\" in named:
        raise TaxonomyError(f"invalid project segment: {project!r}")
    return (outputs_root(), _nfc(found.folder), named)


def artifact_name(period: str, title: str, suffix: str) -> str:
    """File name with the date prefix that keeps re-runs on a single copy."""
    return _nfc(f"{period}_{title}{suffix}")


def bundle_name(period: str, title: str) -> str:
    """Folder name for a multi-artifact output — the file name without a suffix."""
    return artifact_name(period, title, "")


def ensure_depth(parts: Sequence[str]) -> tuple[str, ...]:
    """Return the NFC path parts, refusing anything past the depth ceiling."""
    if len(parts) > MAX_DEPTH:
        joined = "/".join(parts)
        raise TaxonomyError(
            f"outputs depth {len(parts)} exceeds the maximum of {MAX_DEPTH}: {joined}",
        )
    return tuple(_nfc(part) for part in parts)
