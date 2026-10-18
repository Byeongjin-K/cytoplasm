"""The owner's live page reads ledger data; exported project files never carry it."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from automation.ledger import migration, store
from automation.project_board.board_store import BoardStore
from automation.project_board.dashboard import render_dashboard
from automation.project_board.ledger_view import load_ledger, render_ledger


def test_readonly_view_escapes_cards_and_never_creates_missing_db(tmp_path: Path, monkeypatch) -> None:
    # Given: an empty account state directory.
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert load_ledger() is None
    assert not store.default_path().exists()
    ledger = store.Store()
    board = {"project_id": "p_a", "slug": "default", "name": "<board>"}
    card = {"task": {"id": "t_a", "project_id": "p_a", "title": "<script>alert(1)</script>",
                     "status": "blocked", "body": "PRIVATE-BODY"}, "events": [], "comments": []}
    migration.import_snapshot(ledger, [board], [card])
    before = ledger.path.read_bytes()
    # When: the live section reads and renders the ledger.
    html = render_ledger(load_ledger())
    # Then: the DB is byte-unchanged and the contents are text, never executable markup.
    assert ledger.path.read_bytes() == before
    assert "&lt;script&gt;" in html and "<script>" not in html
    assert "PRIVATE-BODY" not in html
    assert 'id="ledger"' in html and 'type="checkbox"' not in html


def test_live_section_and_navigation_are_not_in_export(tmp_path: Path) -> None:
    # Given: the existing dashboard snapshot plus a private ledger view.
    snapshot = BoardStore(tmp_path / "board.db").snapshot(today=date(2026, 10, 18))
    ledger = {"boards": [{"name": "Repair", "project_id": "p_a"}],
              "cards": [{"id": "t_a", "project_id": "p_a", "title": "PRIVATE-TITLE", "status": "done"}]}
    # When: both delivery surfaces are rendered.
    served = render_dashboard(snapshot, mode="served", ledger=ledger)
    exported = render_dashboard(snapshot, mode="file", ledger=ledger)
    # Then: navigation/content is live-only; the file has no private ledger data.
    assert 'href="#ledger"' in served and "PRIVATE-TITLE" in served
    assert 'id="ledger"' not in exported and "PRIVATE-TITLE" not in exported
