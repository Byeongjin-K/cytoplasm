"""With peer retired, the skill sandbox never stages into the agent's live skill root.

2026-10-19: once peer was retired the sandbox ran as agent, and deploy-skill.sh staged the skill
under the agent's real ~/.hermes/skills for about a minute per deploy. The two-minute shadow check
saw that copy and reported SHADOWS-GOVERNED mail during the v2.6.2 and v2.6.3 applies. The
sandbox now has a home of its own under ~/.hermes/skill-gate/sandbox-home in that mode; with peer
active the staging path is unchanged. The functions are cut from the script and run against a
stub run_as, so the test exercises the shipped command strings.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "automation/deploy-skill.sh"
_FUNCTIONS = ("push_skill", "remove_peer_skill_copy", "hermes_lists_skill")


def _function(name: str) -> str:
    lines = _SCRIPT.read_text(encoding="utf-8").split("\n")
    start = next(i for i, line in enumerate(lines) if line.startswith(f"{name}() {{"))
    end = next(i for i in range(start, len(lines)) if lines[i] == "}")
    return "\n".join(lines[start : end + 1])


def _run(tmp_path: Path, sandbox_home: str) -> subprocess.CompletedProcess[str]:
    home = tmp_path / "agent"
    (home / ".local/bin").mkdir(parents=True)
    hermes = home / ".local/bin/hermes"
    hermes.write_text('#!/bin/bash\nfor d in "$HOME"/.hermes/skills/*/; do n=$(basename "$d"); printf "│ %s │\\n" "$n"; done\n')
    hermes.chmod(0o755)
    source = tmp_path / "src/demo"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("---\nname: demo\n---\n")
    report = tmp_path / "report"
    script = "\n".join([
        "set -euo pipefail",
        f"export HOME={home}",
        "die() { echo \"DIE $*\" >&2; exit 9; }",
        "run_as() { bash -c \"$2\"; }",
        "skill_archive_stream() { tar -czf - -C \"$(dirname \"$1\")\" \"$2\"; }",
        "SANDBOX_ACCOUNT=agent",
        f"SANDBOX_HOME='{sandbox_home}'",
        *(_function(name) for name in _FUNCTIONS),
        f"push_skill agent {source} demo",
        f"find {home}/.hermes -path '*/demo/SKILL.md' > {report}",
        f'hermes_lists_skill agent demo "$SANDBOX_HOME" && echo LISTED >> {report}',
        "remove_peer_skill_copy demo",
        f"find {home}/.hermes -name SKILL.md >> {report}; echo END >> {report}",
    ])
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60, check=False)
    result.stdout = report.read_text() if report.exists() else ""
    return result


def test_a_retired_peer_sandbox_stages_outside_the_live_skill_root(tmp_path: Path) -> None:
    result = _run(tmp_path, "$HOME/.hermes/skill-gate/sandbox-home")
    home = tmp_path / "agent"
    assert result.returncode == 0, result.stderr
    staged, listed, *after = result.stdout.splitlines()
    assert staged == f"{home}/.hermes/skill-gate/sandbox-home/.hermes/skills/demo/SKILL.md"
    assert listed == "LISTED"
    assert after == ["END"]
    assert not (home / ".hermes/skills/demo").exists()
    assert not (home / ".hermes/skill-gate/sandbox-home").exists()


def test_an_active_peer_sandbox_keeps_its_own_skill_root(tmp_path: Path) -> None:
    result = _run(tmp_path, "$HOME")
    home = tmp_path / "agent"
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [f"{home}/.hermes/skills/demo/SKILL.md", "LISTED", "END"]


@pytest.mark.parametrize("needle", ["\\$HOME/.hermes/skills/$SKILL", "\\$REAL_HOME/.hermes/skills"])
def test_no_sandbox_command_reads_the_real_skill_root(needle: str) -> None:
    lines = [line for line in _SCRIPT.read_text(encoding="utf-8").split("\n") if 'run_as "$SANDBOX_ACCOUNT"' in line]
    assert len(lines) >= 4
    for line in lines:
        assert needle not in line, line
