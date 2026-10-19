"""Operational alerts go to the pair fault channel, not the owner's channel.

Owner directive faults-to-pair (2026-10-18) and lead 2026-10-19: release convergence and trust
checks, the release backlog, release-approval retirement, managed-skill arrival, the self-skill
and memory-curator audits and the meeting plugin-update notice are warnings for YooDori and
ZzangDori, not decisions for the owner. Every notify_owner call in those senders asks for the
fault channel (unset keeps the old destination). New file so the recorded notice cases keep
their shape.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
SENDERS = (
    "automation/deploy_reconcile_notice.py",
    "automation/deploy_reconcile_trust.py",
    "automation/release_retire.py",
    "automation/managed_sync/cron/managed_sync_watch.py",
    "automation/selfskill_audit/report.py",
    "automation/memory_curator/effects.py",
    "skills/meeting/scripts/meeting_deploy_notice.py",
)


def _calls(path: Path) -> list[ast.Call]:
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if name == "notify_owner":
                found.append(node)
    return found


@pytest.mark.parametrize("sender", SENDERS)
def test_every_owner_notice_from_an_ops_sender_asks_for_the_fault_channel(sender: str) -> None:
    calls = _calls(_REPO / sender)
    assert calls, sender
    for call in calls:
        fault = next((k.value for k in call.keywords if k.arg == "fault"), None)
        assert isinstance(fault, ast.Constant) and fault.value is True, (sender, call.lineno)
