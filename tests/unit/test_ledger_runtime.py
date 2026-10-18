"""The narrow shipped repair runtime must execute its selected ledger CLI."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from automation.deploy_declarations import all_declarations
from automation.ledger import migration, store


def test_shipped_repair_runtime_executes_ledger_show(tmp_path: Path, monkeypatch) -> None:
    # Given: only the files the deployer actually declares, not the full checkout.
    repo = Path(__file__).resolve().parents[2]
    declaration = next(row for row in all_declarations(repo)
                       if row.destination == ".hermes/repair-report-runtime/.current")
    runtime = tmp_path / "runtime"
    for relative in declaration.attr("files").split(","):
        target = runtime / "automation" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repo / "automation" / relative, target)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    migration.import_snapshot(store.Store(), [{"project_id": None, "slug": "default"}], [
        {"task": {"id": "t_one", "project_id": None, "title": "repair", "status": "blocked"},
         "events": [{"kind": "blocked", "payload": {"kind": "needs_input"}}], "comments": []},
    ])
    # When: the shipped consumer selects and spawns the new CLI from its own runtime.
    script = (
        "import json; from automation.repair.repair_report_consumer import card_state; "
        "from dataclasses import asdict; print(json.dumps(asdict(card_state('t_one'))))"
    )
    result = subprocess.run([sys.executable, "-c", script], cwd=tmp_path, check=True,
                            capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(runtime)})
    # Then: a missing store/CLI module cannot be hidden by the development checkout.
    assert json.loads(result.stdout) == {"status": "blocked", "blocked_kind": "needs_input"}
