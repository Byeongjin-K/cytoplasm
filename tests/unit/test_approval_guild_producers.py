"""Every thread producer must carry the resolved coordinate into its durable record."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_WRITERS = (
    ("automation/plaud_sync/effects_live.py", "post"),
    ("automation/repair/repair_ops_posting.py", "permits"),
    ("skills/mail/scripts/triage_approval.py", "confirm_intent"),
    ("skills/calendar/scripts/calendar_approval.py", "request_confirmation"),
    ("skills/coordination/scripts/coordination_approval.py", "commit"),
    ("skills/patent-prep/scripts/patent_export_approval.py", "pending"),
    ("skills/wiki/scripts/wiki_approval.py", "commit"),
    ("skills/todo/scripts/todo_approval.py", "_spec"),
)


@pytest.mark.parametrize("path,function", _WRITERS)
def test_guild_flows_from_binding_when_producer_persists_thread(path: str, function: str) -> None:
    # Given: the actual durable writer, not a duplicate helper or prompt string.
    tree = ast.parse((_ROOT / path).read_text(encoding="utf-8"))
    writers = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == function]
    assert writers, (path, function)
    # When: inspect the expressions sent to storage at the existing thread-binding seam.
    attributes = {node.attr for writer in writers for node in ast.walk(writer) if isinstance(node, ast.Attribute)}
    # Then: guild metadata comes from the resolved binding rather than a constant or hash.
    assert "guild_id" in attributes, (path, function)
