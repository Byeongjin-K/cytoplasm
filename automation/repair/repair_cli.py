#!/usr/bin/env python3
"""Agent-side repair detector and command entry point."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.ledger.switch import command_prefix, serialized
from automation.repair.repair_command import manual_location, parse_repair_command
from automation.repair.repair_capability import publish
from automation.repair.repair_core import KanbanPort, PrivateLogPort, RepairEvent, RepairRegistry, RepairService
from automation.repair.repair_redaction import redact


CARD_ID: Final = re.compile(r"\bt_[A-Za-z0-9]+\b")
_BOARD_TIMEOUT: Final = 60.0
# The status read sits on the detect path and falls back to the previous dedup behaviour
# when it fails, so an unresponsive board must cost seconds, not a full mutation budget.
_STATUS_READ_TIMEOUT: Final = 10.0


class RepairCliError(RuntimeError):
    """Raised for a masked, operational repair mutation failure."""


@dataclass(frozen=True, slots=True)
class HermesKanban(KanbanPort):
    """CLI adapter that creates deliberately blocked and unassigned repair cards."""

    def create(self, title: str, body: str, idempotency_key: str) -> str:
        output = self._run(
            "create",
            title,
            "--body",
            body,
            "--idempotency-key",
            idempotency_key,
            "--json",
        )
        matched = CARD_ID.search(output)
        if matched is None:
            raise RepairCliError("kanban card id missing")
        return matched.group(0)

    def comment(self, ticket_id: str, text: str) -> None:
        self._run("comment", ticket_id, text, "--author", "repair-detector")

    def block_for_repair(self, ticket_id: str, reason: str) -> None:
        self._run("block", "--kind", "needs_input", ticket_id, reason)

    def is_closed(self, ticket_id: str) -> bool:
        """Report whether the deduplicated card is already finished.

        The card status is read here instead of importing
        repair_report_consumer.card_state, because that module pulls the Discord
        transport and the report queue into the detect path; reading one JSON field
        is cheaper than that coupling.
        """
        payload = json.loads(self._run("show", ticket_id, "--json", timeout=_STATUS_READ_TIMEOUT))
        if not isinstance(payload, dict):
            raise RepairCliError("kanban show response is invalid")
        task = payload.get("task")
        if not isinstance(task, dict):
            raise RepairCliError("kanban show response shape is invalid")
        status = task.get("status")
        if not isinstance(status, str):
            raise RepairCliError("kanban card status is invalid")
        return status in {"archived", "done"}

    @staticmethod
    @serialized
    def _run(*args: str, timeout: float = _BOARD_TIMEOUT) -> str:
        completed = subprocess.run(
            (*command_prefix(), *args),
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
        )
        if completed.returncode != 0:
            raise RepairCliError(f"kanban rc={completed.returncode}: {redact(completed.stderr)[:200]}")
        return completed.stdout


@dataclass(frozen=True, slots=True)
class OpsPrivateLogs(PrivateLogPort):
    """Send raw logs only to the ops forced-command SSH receiver."""

    identity: Path

    def write(self, ticket_id: str, occurrence: int, raw_log: str) -> str:
        payload = json.dumps({"ticket_id": ticket_id, "occurrence": occurrence, "raw_log": raw_log})
        completed = subprocess.run(
            (
                "ssh",
                "-i",
                str(self.identity),
                "-o",
                "BatchMode=yes",
                "-o",
                "IdentitiesOnly=yes",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                "ConnectTimeout=15",
                "ops@127.0.0.1",
                "repair-log",
            ),
            capture_output=True,
            check=False,
            input=payload,
            text=True,
            timeout=90,
        )
        if completed.returncode != 0:
            raise RepairCliError(f"ops repair-log rc={completed.returncode}: {redact(completed.stderr)[:200]}")
        return _private_path(completed.stdout, ticket_id, occurrence)


def _private_path(raw: str, ticket_id: str, occurrence: int) -> str:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RepairCliError("ops repair-log returned invalid JSON") from error
    if not isinstance(payload, dict):
        raise RepairCliError("ops repair-log returned invalid result")
    path = payload.get("path")
    expected = f"/srv/autophagy-private/repair-logs/{ticket_id}/occurrence-{occurrence}.log"
    if path != expected:
        raise RepairCliError("ops repair-log returned unexpected path")
    return expected


def _service() -> RepairService:
    state_file = Path(os.environ.get("REPAIR_STATE_FILE", "~/.hermes/repair-tickets.json")).expanduser()
    identity = Path(os.environ.get("REPAIR_SSH_IDENTITY", "~/.ssh/autophagy-repair-log")).expanduser()
    kanban = HermesKanban()
    return RepairService(kanban, OpsPrivateLogs(identity), RepairRegistry(state_file), kanban.is_closed)


def _detect(source: str, location: str, raw_log: str) -> int:
    result = _service().record(RepairEvent(source=source, location=location, raw_log=raw_log))
    try:
        publish(result.ticket_id, result.occurrence)
    except Exception as error:  # noqa: BLE001 - optional publication must never break detection
        print(f"repair capability publish skipped: {error.__class__.__name__}", file=sys.stderr)
    print(json.dumps({"ticket": result.ticket_id, "occurrence": result.occurrence, "created": result.created}))
    return 0


def main() -> int:
    """Parse local failures; stdout is limited to ticket identifiers and counts."""
    parser = argparse.ArgumentParser(prog="repair-cli")
    commands = parser.add_subparsers(dest="command", required=True)
    detect = commands.add_parser("detect")
    detect.add_argument("--source", required=True)
    detect.add_argument("--location", required=True)
    log_input = detect.add_mutually_exclusive_group(required=True)
    log_input.add_argument("--log-file", type=Path)
    log_input.add_argument("--stdin", action="store_true")
    manual = commands.add_parser("manual")
    manual.add_argument("message")
    args = parser.parse_args()
    if args.command == "detect":
        raw_log = sys.stdin.read() if args.stdin else args.log_file.read_text(encoding="utf-8")
        return _detect(args.source, args.location, raw_log)
    parsed = parse_repair_command(args.message)
    if parsed is None:
        raise RepairCliError("manual repair phrase required")
    return _detect("manual-repair", manual_location(parsed.message), parsed.message)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RepairCliError, subprocess.SubprocessError) as error:
        print(f"repair error: {redact(str(error))[:300]}", file=sys.stderr)
        raise SystemExit(1)
