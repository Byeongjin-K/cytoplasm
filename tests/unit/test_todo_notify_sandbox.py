"""Dummy-secret todo notice regression kept separate from FS3-pinned test evidence.

The deploy sandbox runs ``skills/todo/scripts/scenario.sh`` with a ``DUMMY-*``
``AUTOPHAGY_DEMO_SECRET`` but does not set ``E2E_TEST_MODE``. Its verified-write
notice must therefore skip before any Discord transport is opened.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from automation.interop.effect_approval import ApprovalContext

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "todo" / "scripts"))

import todo_approval_runtime  # noqa: E402
import todo_cli  # noqa: E402


def test_created_notice_with_scenario_dummy_secret_skips_before_discord_attempt(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The dummy-secret sandbox condition is a no-notify result-notice path."""
    monkeypatch.setenv("AUTOPHAGY_DEMO_SECRET", "DUMMY-todo-sandbox")
    attempts: list[object] = []
    monkeypatch.setattr(
        todo_approval_runtime,
        "origin_record",
        lambda _action_hash: {"id": "sha256:sandbox", "channel_id": "approvals"},
    )

    def attempted_notify(*_args: object, **_kwargs: object) -> None:
        attempts.append(object())
        raise todo_approval_runtime.TodoApprovalError("dummy sandbox has no Discord identity")

    monkeypatch.setattr(todo_approval_runtime, "notify_result", attempted_notify)

    todo_cli._notify_created(
        "sha256:sandbox",
        "task-sandbox-1",
        "승인된 합성 과제",
        ApprovalContext(approval_log=None, owner_id="owner-sandbox", e2e_test_mode=False),
    )

    captured = capsys.readouterr()
    assert "NOTIFY-SKIP hash=sha256:sandbox reason=dummy_secret" in captured.err
    assert "NOTIFY-FAIL" not in captured.err
    assert attempts == []
