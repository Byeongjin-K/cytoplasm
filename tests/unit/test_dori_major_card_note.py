"""The installer-default major note must fit the one-line operator slot of the release card.

On 2026-10-18 the P6 card was refused before posting: ``release_notes.major_note`` put the
"P5 삭제 미포함" line after a newline, and ``ReleaseSpec`` rejects any newline in the note.
The existing tests checked the note text and the card separately, never the note on a card.
"""
from __future__ import annotations

from dataclasses import replace

from automation.release_notes import major_note
from tests.unit.test_release_approval import _spec
from tests.unit.test_release_notes import _plan

_SIGNAL = ("automation/install/profiles.py:DEFAULT_PROFILE",)


def test_the_installer_default_note_renders_on_a_release_card() -> None:
    note = major_note(_plan((), signals=_SIGNAL))
    card = replace(_spec(), render_version=6, major_note=note).render()
    assert "P5 삭제 미포함" in note
    assert note in card
