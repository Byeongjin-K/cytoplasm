"""An old node whose runtime has no gate yet can take the interop bundle.

PR #104 gap 3 (2026-10-19): on a September node, step (b) checked the new rule file with the old
interop_runtime, which has no external_effect_gate, so the deploy blocked before the step that
replaces that runtime. New file so the recorded interop deploy cases keep their shape.
"""
from __future__ import annotations

from tests.unit.test_interop_deploy import _ACCOUNTS, Node
from tests.unit.test_interop_deploy import node as node  # noqa: F401


def test_a_runtime_without_the_gate_is_checked_against_the_release_tree(node: Node) -> None:
    for account in _ACCOUNTS:
        (node.homes / account / ".hermes/interop_runtime/automation/interop/external_effect_gate.py").unlink()
    result = node.run()
    assert "does not read the new rule file" not in result.stderr, result.stderr
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_runtime_whose_gate_reads_fewer_rules_still_blocks(node: Node) -> None:
    gate = node.homes / _ACCOUNTS[0] / ".hermes/interop_runtime/automation/interop/external_effect_gate.py"
    gate.write_text(gate.read_text() + "\n_load = load_denylist\n"
                    "def load_denylist(path):\n    return _load(path)[:1]\n")
    result = node.run()
    assert result.returncode == 5 and "does not read the new rule file" in result.stderr
