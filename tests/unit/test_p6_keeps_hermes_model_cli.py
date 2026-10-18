"""P6 and later P5 removal keep the Hermes CLI, its config and its login.

Node batch jobs (digest triage, research trends, project board analysis, meeting
minutes) call the account's main model through ``automation/codex_llm.py``, which
runs the account's ``hermes`` binary with ``~/.hermes/config.yaml``. Removing the
gateway, cron, plugins and interop is the Dori migration; removing the CLI, the
config or the auth file would make every one of those jobs fail.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
_KEPT = re.compile(r"\.local/bin/hermes|\.hermes/config\.yaml|\.hermes/auth\.json|\.hermes/kanban\.db")
_REMOVAL = re.compile(r"\brm\b|unlink|rmtree|shutil\.move|os\.remove|uninstall")


def _sources() -> list[Path]:
    root = REPO / "automation"
    return [p for p in root.rglob("*") if p.suffix in {".py", ".sh"} and "__pycache__" not in p.parts]


def test_no_shipped_code_removes_the_hermes_model_cli_config_or_login() -> None:
    hits = [
        f"{path.relative_to(REPO)}:{number}"
        for path in _sources()
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
        if _KEPT.search(line) and _REMOVAL.search(line)
    ]
    assert hits == []


def test_batch_model_calls_still_resolve_the_account_hermes_binary() -> None:
    source = (REPO / "automation" / "codex_llm.py").read_text(encoding="utf-8")
    assert 'shutil.which("hermes"' in source


def test_keep_guard_recognizes_kanban_removal() -> None:
    line = 'rm -f "$HOME/.hermes/kanban.db"'
    assert _KEPT.search(line) and _REMOVAL.search(line)


def test_the_major_notes_state_that_the_cli_config_and_login_stay() -> None:
    notes = (REPO / "docs" / "releases" / "dori-major.md").read_text(encoding="utf-8")
    assert "CLI·config.yaml·로그인" in notes
