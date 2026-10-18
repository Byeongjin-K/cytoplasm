"""Cardless writes after the YooDori gate verifies the owner origin and switch."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import todo_cli as todo
import todo_preflight as preflight
from todo_cli_model import TaskRequest, TodoError, TodoReconciliationRequiredError
from todo_execution_claim import JsonValue

if TYPE_CHECKING:
    from automation.interop.external_effect_gate import ApprovalContext, ExternalEffectDecision


def write_now(args: argparse.Namespace) -> int:
    """Reuse the guarded connector; never mint an approval or post a card."""
    channel, message = args.origin_channel_id, args.origin_message_id
    if not channel.isascii() or not channel.isdecimal() or not message.isascii() or not message.isdecimal():
        raise TodoError("owner command requires numeric origin coordinates", 3)
    request = TaskRequest(args.tasklist, args.title, args.notes, args.due)
    context = todo.gate_module().ApprovalContext(None, todo.owner_id(), False)
    identity = json.dumps([channel, message, todo.insert_argv(request)], separators=(",", ":"))
    root = Path(os.environ.get("TODO_APPROVAL_ROOT", "~/.hermes/todo-approvals")).expanduser()
    path = root / "owner-command" / f"{hashlib.sha256(identity.encode()).hexdigest()}.json"
    audit: dict[str, str] = {
        "method": "owner_command",
        "origin_channel_id": channel,
        "origin_message_id": message,
        "state": "write_started",
        "timestamp": datetime.now(UTC).isoformat(),
    }

    def authorized(argv: Sequence[str], bound: ApprovalContext) -> ExternalEffectDecision:
        # The caller's gate already authenticated the owner message and release switch.
        # Retain the canonical argv hash without fabricating a manual-reaction approval.
        return replace(todo.evaluate(argv, context=bound), allowed=True, reason="owner_command")

    def claimed_execute(argv: list[str]) -> dict[str, JsonValue]:
        if argv[3] == "insert":
            audit["action_hash"] = authorized(argv, context).action_hash
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                raise TodoReconciliationRequiredError(
                    "owner command already started; inspect its receipt before retrying"
                ) from None
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(audit, sort_keys=True))
                handle.flush()
                os.fsync(handle.fileno())
        return todo.run_gws(argv)

    try:
        result = preflight.create_task(preflight.TodoPreflightBindings(
            request, claimed_execute, context, todo.insert_argv, todo.get_argv, authorized,
        ))
        audit.update(
            state="verified", task_id=result.task_id, verification="tasks.tasks.get",
        )
        temporary = path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(audit, sort_keys=True))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except preflight.TodoPreflightError as error:
        raise TodoError(str(error), error.exit_code) from None
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise TodoError(f"owner command failed: {type(error).__name__}", 5) from None
    print(f"OWNER-COMMAND-WRITTEN todo {result.task_id}")
    return 0
