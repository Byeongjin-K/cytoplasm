"""Hermes Kanban adapter for human-input proposal sections."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .proposal_knowledge import module


_CARD_ID = re.compile(r"\bt_[a-zA-Z0-9]+\b")


class KanbanError(RuntimeError):
    """A required Hermes Kanban mutation did not complete."""


def board_name(slug: str) -> str:
    """Keep proposal cards isolated from unrelated personal work."""
    return f"proposal-{slug}"


@dataclass(frozen=True, slots=True)
class KanbanClient:
    """CLI-only adapter; no worker dispatch command exists in this surface."""

    board: str

    def _run(self, command: tuple[str, ...]) -> str:
        environment = {**os.environ, "PATH": f"{Path.home() / '.local/bin'}:{os.environ.get('PATH', '')}"}
        routing = module("automation.ledger.switch")
        try:
            with routing.command_lock():
                completed = subprocess.run(
                    (*routing.command_prefix(), *command[2:]),
                    cwd=Path.home(),
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise KanbanError(error.__class__.__name__) from error
        if completed.returncode != 0:
            raise KanbanError(f"kanban rc={completed.returncode}")
        return completed.stdout

    def ensure_board(self) -> None:
        """Create the isolated board once, accepting an already-created board."""
        command = ("hermes", "kanban", "boards", "create", self.board, "--name", self.board.replace("-", " ").title())
        environment = {**os.environ, "PATH": f"{Path.home() / '.local/bin'}:{os.environ.get('PATH', '')}"}
        routing = module("automation.ledger.switch")
        with routing.command_lock():
            completed = subprocess.run((*routing.command_prefix(), *command[2:]), cwd=Path.home(),
                                       env=environment, capture_output=True, text=True, check=False)
        if completed.returncode == 0:
            return
        boards = self._run(("hermes", "kanban", "boards", "list", "--json"))
        if self.board not in boards:
            raise KanbanError("proposal board creation failed")

    def create_section(self, slug: str, key: str, title: str) -> str:
        """Create a needs-input card with a reason, never a ready worker task."""
        body = f"Proposal {slug}; section {key}. Human draft or contributed material is required."
        output = self._run(
            (
                "hermes",
                "kanban",
                "--board",
                self.board,
                "create",
                title,
                "--body",
                body,
                "--initial-status",
                "blocked",
                "--json",
            )
        )
        card_id = _card_id_from_output(output)
        _ = self._run(
            (
                "hermes",
                "kanban",
                "--board",
                self.board,
                "block",
                "--kind",
                "needs_input",
                card_id,
                "Awaiting human-provided section material; no autonomous worker is permitted.",
            )
        )
        return card_id

    def complete_section(self, card_id: str, key: str) -> None:
        """Directly complete a drafted section without promoting it to Ready."""
        _ = self._run(
            (
                "hermes",
                "kanban",
                "--board",
                self.board,
                "complete",
                card_id,
                "--result",
                f"Proposal section {key} has a private draft.",
            )
        )


def _card_id_from_output(output: str) -> str:
    match = _CARD_ID.search(output)
    if match is None:
        raise KanbanError("kanban card id is missing")
    return match.group(0)
