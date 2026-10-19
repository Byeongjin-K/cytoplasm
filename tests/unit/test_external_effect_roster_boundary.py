from __future__ import annotations

import ast
from pathlib import Path

_GATE = (
    Path(__file__).resolve().parents[2]
    / "automation"
    / "interop"
    / "approval_types.py"
)


def test_external_effect_gate_remains_roster_free_and_single_owner_bound() -> None:
    source = _GATE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    approval_context = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ApprovalContext"
    )
    owner_fields = [
        statement.target.id
        for statement in approval_context.body
        if isinstance(statement, ast.AnnAssign)
        and isinstance(statement.target, ast.Name)
        and "owner" in statement.target.id
    ]

    assert "roster" not in source.casefold()
    assert owner_fields == ["owner_id"]
