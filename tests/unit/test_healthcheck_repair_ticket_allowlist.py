"""Repair tickets from checks on another node must be allowed by the primary node's wrapper.

`report_repair` sends every failed check's ticket to PRIMARY_NODE, but the wrapper generator only
allowed a check's repair command on the node the check runs on. On 2026-10-18 the RAG node's
`embedding` check failed once and its ticket was refused with rc=126 on the primary node, so no
ticket was filed. Kept in its own file: tests/AGENTS.md forbids adding cases to files whose output
hashes the FS3 replay gate pins.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import Final

_REPO: Final = Path(__file__).resolve().parents[2]
_GENERATOR: Final = _REPO / "automation" / "healthcheck_probe_wrapper.sh"
_HEALTHCHECK: Final = _REPO / "automation" / "healthcheck.sh"
_EXAMPLE: Final = _REPO / "configs" / "node.example.toml"


def _env(tmp_path: Path) -> dict[str, str]:
    return {**os.environ, "HOME": str(tmp_path), "HEALTHCHECK_SSH_USER": "", "HEALTHCHECK_SSH_IDENTITY": "",
            "HEALTHCHECK_NODE_CONFIG_PATH": str(_EXAMPLE)}


def _shell(script: str, tmp_path: Path) -> str:
    return subprocess.run(("bash", "-c", script), capture_output=True, text=True, check=True,
                          env=_env(tmp_path), timeout=60).stdout


def test_primary_wrapper_allows_repair_tickets_of_checks_on_the_rag_node(tmp_path: Path) -> None:
    # Given: the repair command the sweep sends to the primary node when the RAG node's embedding check fails.
    names = _shell(f'source "{_HEALTHCHECK}" >/dev/null 2>&1; printf "%s %s" "$PRIMARY_NODE" "$RAG_NODE"', tmp_path).split()
    primary, rag = names
    assert primary != rag
    command = _shell(f'source "{_HEALTHCHECK}" >/dev/null 2>&1; healthcheck_repair_command "{rag} embedding"', tmp_path)
    command_hash = hashlib.sha256(command.encode()).hexdigest()
    # When
    primary_wrapper = _shell(f'bash "{_GENERATOR}" --print "{primary}"', tmp_path)
    # Then: the primary node's wrapper lets that ticket through.
    assert command_hash in primary_wrapper
