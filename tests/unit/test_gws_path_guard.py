"""Pin the PATH-level ``gws`` guard that ``conftest.py`` installs.

Split from ``test_unit_home_isolation.py`` per tests/AGENTS.md: HOME isolation
protects the developer's files, this protects the owner's live Google account —
``gws`` holds real credentials in the OS keyring, which a HOME redirect cannot
revoke. A per-file autouse fixture covers neither another test file, another
skill, nor a child process; PATH covers all three.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from importlib import import_module
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]

_RESOLVERS = (
    ("todo_cli", "skills/todo/scripts", "TODO_GWS_BIN"),
    ("calendar_gate", "skills/calendar/scripts", "CALENDAR_GWS_BIN"),
)


def _guard() -> str:
    guard = os.environ.get("AUTOPHAGY_UNIT_GWS_GUARD", "")
    assert guard, "conftest must publish the gws guard path"
    return guard


def test_which_gws_resolves_to_the_refusing_guard() -> None:
    assert shutil.which("gws") == _guard()


def test_the_guard_refuses_instead_of_running_the_command() -> None:
    proc = subprocess.run(  # noqa: S603
        [_guard(), "tasks", "tasks", "insert"], capture_output=True, text=True, check=False
    )
    assert proc.returncode != 0
    assert "UNIT-TEST-GWS-BLOCKED" in proc.stderr


@pytest.mark.parametrize(("module", "scripts", "override"), _RESOLVERS)
def test_every_skill_binary_resolver_lands_on_the_guard(
    module: str, scripts: str, override: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(override, raising=False)
    if scripts:
        sys.path.insert(0, str(_REPO / scripts))
    assert import_module(module).gws_bin() == _guard()
