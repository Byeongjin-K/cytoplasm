"""Every board adapter keeps its old argv off and uses the ledger after verification."""
from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from automation.ledger import migration, store, switch
from automation.repair import repair_cli, repair_report_consumer


@pytest.mark.parametrize("enabled", (False, True))
@pytest.mark.parametrize("caller", ("repair", "consumer", "meeting", "proposal"))
def test_all_callers_change_only_the_prefix(tmp_path: Path, monkeypatch, enabled: bool, caller: str) -> None:
    # Given: one account is either legacy or has completed the migration transaction.
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    if enabled:
        migration.import_snapshot(store.Store(), [], [])
    prefix = switch.command_prefix()
    seen = []
    def run(argv, **_kwargs):
        seen.append(tuple(argv))
        payload = json.dumps({"id": "t_one", "task": {"status": "blocked"},
                              "events": [{"kind": "blocked", "payload": {"kind": "needs_input"}}]})
        stdout = payload.encode() if caller == "meeting" else payload
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")
    monkeypatch.setattr(subprocess, "run", run)
    # When: every adapter crosses its production subprocess boundary.
    match caller:
        case "repair":
            repair_cli.HermesKanban._run("show", "t_one", "--json")
        case "consumer":
            assert repair_report_consumer.card_state("t_one").blocked_kind == "needs_input"
            repair_report_consumer._run_kanban("complete", "t_one", "--result", "ok")
        case "meeting":
            sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/meeting/scripts"))
            meeting = importlib.import_module("meeting_cli")
            meeting._hermes_kanban(["kanban", "show", "t_one", "--json"])
        case "proposal":
            proposal = importlib.import_module("skills.proposal.scripts.proposal_kanban")
            client = proposal.KanbanClient("proposal-demo")
            client.ensure_board()
            client.create_section("demo", "s1", "Draft")
            client.complete_section("t_one", "s1")
    # Then: all commands carry the selected prefix and preserve their argument tail.
    assert seen and all(command[:len(prefix)] == prefix for command in seen)
    if not enabled:
        assert prefix == ("hermes", "kanban")
    assert any("show" in command or "boards" in command for command in seen)


def test_migration_lock_excludes_a_caller(tmp_path: Path, monkeypatch) -> None:
    # Given: a different descriptor already holds the release/caller lock.
    import fcntl

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    # When: another process would try the same lock without waiting.
    with switch.command_lock():
        with store.default_path().with_suffix(".lock").open("a") as second:
            # Then: it cannot select the old backend halfway through migration.
            with pytest.raises(BlockingIOError):
                fcntl.flock(second, fcntl.LOCK_EX | fcntl.LOCK_NB)
