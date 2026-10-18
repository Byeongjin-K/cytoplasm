"""Card B exercises the actual SQLite/CLI contracts, independent of live Hermes."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from automation.ledger import migration, store, switch


def exported() -> tuple[list[dict], list[dict]]:
    board = {"slug": "default", "project_id": None, "name": "Personal", "archived": False}
    cards = [
        {"task": {"id": "t_one", "project_id": None, "title": "<repair>",
                  "body": "private", "status": "blocked", "created_at": "2026-01-01",
                  "completed_at": None},
         "events": [{"kind": "blocked", "created_at": "2026-01-02",
                     "payload": {"kind": "needs_input", "reason": "review"}}],
         "comments": [{"author": "repair", "body": "a comment", "created_at": "2026-01-01"}]},
        {"task": {"id": "t_old", "project_id": None, "title": "archived",
                  "status": "archived", "created_at": "2026-01-01"}, "events": [], "comments": []},
    ]
    return [board], cards


def test_migration_preserves_details_and_is_idempotent(tmp_path: Path) -> None:
    # Given: an export with blocked and archived cards.
    ledger = store.Store(tmp_path / "ledger.db")
    boards, cards = exported()
    # When: the same export is applied twice.
    first = migration.import_snapshot(ledger, boards, cards)
    second = migration.import_snapshot(ledger, boards, cards)
    # Then: identities, timestamps, reasons and comments survive; the receipt contains counts only.
    assert first == second == {"boards": 1, "cards": 2, "statuses": {"archived": 1, "blocked": 1}}
    assert ledger.show("t_one") == cards[0]
    assert len(ledger.cards()) == 2
    assert ledger.path.stat().st_mode & 0o777 == 0o600


def test_migration_refuses_mismatch_without_activation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: the destination drops a row during readback.
    ledger = store.Store(tmp_path / "ledger.db")
    monkeypatch.setattr(migration, "counts", lambda *_args: {"boards": 0, "cards": 0, "statuses": {}})
    # When / Then: equality failure rolls back data and the routing bit.
    with pytest.raises(migration.MigrationError):
        migration.import_snapshot(ledger, *exported())
    assert not ledger.active()
    assert ledger.cards() == []


def test_duplicate_ids_are_refused(tmp_path: Path) -> None:
    # Given: a corrupt source export.
    boards, cards = exported()
    # When / Then: duplicates cannot be hidden by upsert or equal status counts.
    with pytest.raises(migration.MigrationError):
        migration.import_snapshot(store.Store(tmp_path / "db"), boards, cards + cards[:1])


def test_cli_shapes_used_by_repair_meeting_and_proposal(tmp_path: Path) -> None:
    # Given: a real isolated CLI, not an adapter mock.
    environment = {**os.environ, "XDG_STATE_HOME": str(tmp_path)}
    def run(*args: str) -> str:
        return subprocess.check_output(
            [sys.executable, "-m", "automation.ledger", *args], env=environment, text=True,
        )
    # When: callers' exact create/block/comment/show/complete operations execute.
    run("boards", "create", "proposal-demo", "--name", "Proposal")
    assert "proposal-demo" in run("boards", "list", "--json")
    created = json.loads(run("create", "A", "--body", "B", "--idempotency-key", "key", "--json"))
    card_id = created["id"]
    assert json.loads(run("create", "A", "--idempotency-key", "key", "--json"))["id"] == card_id
    run("comment", card_id, "comment", "--author", "repair-detector")
    run("block", "--kind", "needs_input", card_id, "review")
    shown = json.loads(run("show", card_id, "--json"))
    # Then: all shapes consumed by the three production parsers exist.
    assert card_id.startswith("t_")
    assert shown["task"]["status"] == "blocked"
    assert shown["events"][-1]["payload"]["kind"] == "needs_input"
    assert "status: blocked" in run("show", card_id)
    run("unblock", card_id)
    run("complete", card_id, "--result", "done")
    assert json.loads(run("show", card_id, "--json"))["task"]["status"] == "done"
    assert len(json.loads(run("list", "--json"))) == 1


def test_release_switch_is_exact_and_missing_is_off(tmp_path: Path) -> None:
    # Given: a host with no explicit opt-in.
    assert switch.release_switch(tmp_path) == "off"
    flag = tmp_path / ".hermes/release-flags/ledger"
    flag.parent.mkdir(parents=True)
    flag.write_bytes(b"on\n")
    # When / Then: near-matches fail closed.
    with pytest.raises(ValueError):
        switch.release_switch(tmp_path)
    flag.write_bytes(b"on")
    assert switch.release_switch(tmp_path) == "on"


def test_prefix_off_and_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a private runtime with a migrated ledger.
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert switch.command_prefix() == ("hermes", "kanban")
    migration.import_snapshot(store.Store(), *exported())
    # When / Then: only a verified committed migration enables the replacement CLI.
    assert switch.command_prefix()[:2] == (sys.executable, "-B")
    assert switch.command_prefix()[-1].endswith("ledger/__main__.py")
