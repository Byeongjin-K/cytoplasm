"""Release authorization and migration failure are exercised without node effects."""
from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from automation.ledger import migration, release, store, switch
from automation.release_spec import ReleaseSpec


def test_card_surface_is_hash_bound_and_old_card_replays() -> None:
    # Given: a complete release card with and without the new surface.
    plain = ReleaseSpec(version="v2.1.1", head_sha="a" * 40, release_nonce="b" * 32,
                        surface_digests=(("repo", "c" * 64),), patch_notes="test", render_version=6)
    changed = ReleaseSpec(version="v2.1.1", head_sha="a" * 40, release_nonce="b" * 32,
                          surface_digests=(*plain.surface_digests, switch.surface()),
                          patch_notes="test", render_version=6)
    # When / Then: the shipped approval text and the hash refer to the same effect.
    assert switch.CARD_LINE in changed.render()
    assert switch.CARD_LINE not in plain.render()
    assert changed.action_hash() != plain.action_hash()


def test_unauthorized_switch_does_not_call_ssh(tmp_path: Path, monkeypatch) -> None:
    # Given: release authorization fails.
    calls = []
    def run(argv, **_kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 4)
    monkeypatch.setattr(subprocess, "run", run)
    # When: a release attempts the migration.
    result = release.deploy(tmp_path, "node-alias", "/runtime", "a" * 40, "on")
    # Then: the only call was the authorization check, never a node write.
    assert result == 4 and len(calls) == 1 and calls[0][1] == "authorize-ledger"


def source(tmp_path: Path) -> tuple[list[dict], list[dict]]:
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE tasks (id TEXT, project_id TEXT, status TEXT, idempotency_key TEXT)")
        db.execute("INSERT INTO tasks VALUES ('t_one',NULL,'blocked','meeting:key')")
        db.execute("INSERT INTO tasks VALUES ('t_old',NULL,'archived',NULL)")
    boards = [{"project_id": None, "slug": "personal", "is_current": True,
               "name": "Personal", "db_path": str(path)}]
    cards = [{"task": {"id": key, "project_id": None, "status": status, "title": key},
              "comments": [], "events": []} for key, status in (("t_one", "blocked"), ("t_old", "archived"))]
    return boards, cards


def test_release_import_rollback_and_rerun_are_idempotent(tmp_path: Path, monkeypatch) -> None:
    # Given: an isolated real source DB and complete CLI export.
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    boards, cards = source(tmp_path)
    monkeypatch.setattr(migration, "export_hermes", lambda: (boards, cards))
    original = Path(boards[0]["db_path"]).read_bytes()
    # When: on -> on -> off -> on is applied.
    receipt = release.install("on")
    assert release.install("on") == receipt
    release.install("off")
    assert not switch.enabled()
    assert release.install("on") == receipt
    # Then: the existing card ID owns its idempotency key and the legacy DB is untouched.
    assert store.Store().current_board() == "personal"
    assert store.Store().create("retry", board="personal", dedup="meeting:key")["id"] == "t_one"
    assert Path(boards[0]["db_path"]).read_bytes() == original
    assert receipt == {"boards": 1, "cards": 2, "statuses": {"archived": 1, "blocked": 1}}
    with store.Store().connect() as db:
        assert json.loads(db.execute("SELECT value FROM metadata WHERE key='migration'").fetchone()[0]) == receipt


def test_source_count_mismatch_leaves_callers_legacy(tmp_path: Path, monkeypatch) -> None:
    # Given: the CLI silently omitted the archived card.
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    boards, cards = source(tmp_path)
    monkeypatch.setattr(migration, "export_hermes", lambda: (boards, cards[:1]))
    # When / Then: comparing the source DB, not just the truncated export, refuses activation.
    with pytest.raises(migration.MigrationError):
        release.install("on")
    assert not switch.enabled() and not store.default_path().exists()


def test_changed_legacy_source_after_rollback_is_not_silently_ignored(tmp_path: Path, monkeypatch) -> None:
    # Given: a migration was rolled back, then a legacy card changed.
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    boards, cards = source(tmp_path)
    monkeypatch.setattr(migration, "export_hermes", lambda: (boards, cards))
    release.install("on")
    release.install("off")
    cards[0]["task"]["title"] = "changed on legacy"
    # When / Then: re-enabling refuses instead of discarding that change.
    with pytest.raises(migration.MigrationError):
        release.install("on")
    assert switch.command_prefix() == ("hermes", "kanban")


def test_export_includes_archived_boards_and_card_details(tmp_path: Path, monkeypatch) -> None:
    # Given: a fake Hermes transport containing the real export shapes, including null default IDs.
    boards, cards = source(tmp_path)
    seen = []
    def run(argv, **_kwargs):
        tail = argv[2:]
        seen.append(tail)
        if tail[:2] == ["boards", "list"]:
            value = boards
        elif "list" in tail:
            value = [card["task"] for card in cards]
        else:
            value = next(card for card in cards if card["task"]["id"] == tail[-2])
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(value))
    monkeypatch.setattr(subprocess, "run", run)
    # When: the actual exporter runs.
    actual = migration.export_hermes()
    # Then: it did not rely on default list filtering, and every show document was kept.
    assert actual == (boards, cards)
    assert "--all" in seen[0]
    assert any("--archived" in command for command in seen)
    assert sum("show" in command for command in seen) == 2


def test_release_cli_returns_nonzero_and_explains_mismatch(tmp_path: Path, monkeypatch, capsys) -> None:
    # Given: a partial source export.
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    boards, cards = source(tmp_path)
    monkeypatch.setattr(migration, "export_hermes", lambda: (boards, cards[:1]))
    # When: the release entrypoint is used rather than the Python helper.
    rc = release.main(["install", "--mode", "on"])
    # Then: no caller mode changed; the failure identifies the equality gate.
    assert rc == 4 and not switch.enabled()
    assert "source/export status counts mismatch" in capsys.readouterr().err
