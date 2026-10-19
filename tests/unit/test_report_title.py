"""The report owns its document title; a model draft contributes body sections."""
from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills"))
report_core = import_module("report.scripts.report_core")


@pytest.mark.parametrize("prefix", ["", "\n\n"])
def test_report_has_one_document_title_when_draft_repeats_note_title(
    tmp_path: Path, prefix: str,
) -> None:
    # Given: a note and a model draft that uses the source title as its own H1.
    path = tmp_path / "note.md"
    path.write_text("# Fixture study\n\n## Findings\n\nMeasured result.\n", encoding="utf-8")
    notes = report_core.load_material((path,))
    draft = prefix + notes[0].body

    # When: the generator wraps the draft in a report.
    report = report_core.assemble_report(notes[0].title, notes, draft)

    # Then: the document has one H1, while source attribution and content survive.
    assert [line for line in report.splitlines() if line.startswith("# ")] == [
        "# Fixture study",
    ]
    assert "## Findings\n\nMeasured result." in report
    assert "- Fixture study" in report
