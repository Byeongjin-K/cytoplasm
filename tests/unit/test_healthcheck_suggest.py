"""Telling the operator which declaration to paste, instead of making them derive it.

The declaration was shipped with documentation and nothing else, so a new operator had to
read `docs/guide/operations.md`, decide which service groups their installation runs, and
translate that into a variable — three steps in which only the last is mechanical. The
installation that prompted the feature had already worked out the answer the hard way, by
watching probes fail for services it never installed
(``docs/troubleshooting/신규-노드-설치-공백.md`` §5).

`--suggest` closes that gap from the other end: the node reports which groups look absent
and prints the line to paste. It deliberately uses only probe commands the SSH forced-command
allowlist already carries, so an operator can run it without regenerating anything.

The one thing it must never do is claim certainty it does not have. `systemctl is-active`
answers "inactive" both for a unit nobody installed and for one that just died, so a group
whose probes all fail is a QUESTION for the operator, not a conclusion. A partial failure is
never a question: some of the group answered, so the service is there and something is wrong.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Final

_REPO: Final = Path(__file__).resolve().parents[2]
_SUGGEST: Final = _REPO / "automation" / "healthcheck_suggest.sh"
_REGISTRY: Final = _REPO / "automation" / "healthcheck_registry.sh"


def _render(rows: str) -> subprocess.CompletedProcess[str]:
    """Feed per-group tallies to the reporter, which is pure: stdin in, advice out."""
    return subprocess.run(
        ("bash", "-c", f'set -euo pipefail; source "{_SUGGEST}"; healthcheck_suggest_render'),
        input=rows, capture_output=True, text=True, check=False,
    )


def _declaration(output: str) -> str:
    """The value from the single paste-ready line, or '' when none was offered."""
    for line in output.splitlines():
        if "HEALTHCHECK_SERVICES=" in line:
            return line.split("HEALTHCHECK_SERVICES=", 1)[1].strip().strip('"')
    return ""


def test_a_group_whose_every_probe_failed_is_offered_for_removal() -> None:
    # Given: report-hub answered nothing at all, while core and rag are fine.
    result = _render("core 0 14\nreport-hub 3 3\nrag 0 5\n")

    # Then: the operator is handed the line to paste, without report-hub.
    assert result.returncode == 0, result.stderr
    assert _declaration(result.stdout) == "core rag"


def test_a_partly_failing_group_is_never_offered_for_removal() -> None:
    # Given: two of report-hub's three probes answered, so the service is installed and
    # something is wrong with it.
    result = _render("core 0 14\nreport-hub 1 3\nrag 0 5\n")

    # Then: nothing is proposed. Offering to stop watching a service that is half up would
    # turn an outage into a configuration change.
    assert result.returncode == 0, result.stderr
    assert _declaration(result.stdout) == ""


def test_core_is_never_offered_for_removal() -> None:
    # Given: the shared SSH path is down, so every probe including core's has failed.
    result = _render("core 14 14\nreport-hub 3 3\nrag 5 5\n")

    # Then: no declaration is proposed at all. Under a fleet-wide fault the tallies say
    # nothing about what is installed, and core is not optional in any case.
    assert result.returncode == 0, result.stderr
    assert _declaration(result.stdout) == ""


def test_a_healthy_installation_is_told_to_change_nothing() -> None:
    # Given: everything answered.
    result = _render("core 0 14\nreport-hub 0 3\nrag 0 5\n")

    # Then: no line to paste, and it says so rather than printing an empty suggestion.
    assert result.returncode == 0, result.stderr
    assert _declaration(result.stdout) == ""
    assert result.stdout.strip(), "a healthy run must still report what it found"


def test_the_uncertainty_is_stated_rather_than_hidden() -> None:
    # Given: a group that answered nothing.
    result = _render("core 0 14\nreport-hub 3 3\nrag 0 5\n")

    # Then: the output says the probes cannot tell "never installed" from "down right now",
    # because acting on the wrong reading silently stops watching a broken service.
    assert "not installed" in result.stdout and "down" in result.stdout


def test_the_offered_declaration_is_one_the_registry_accepts() -> None:
    # Given: the line this tool tells an operator to paste.
    offered = _declaration(_render("core 0 14\nreport-hub 3 3\nrag 0 5\n").stdout)
    assert offered

    # When: it is fed to the consumer that will actually read it.
    accepted = subprocess.run(
        ("bash", "-c",
         'set -euo pipefail\n'
         'eval "$(python3 automation/node_config_sh.py --print-env)"\n'
         'PRIMARY_NODE="$NODE_PRIMARY_NODE_NAME"; RAG_NODE="$NODE_RAG_NODE_NAME"\n'
         'PEER_GATEWAY_CONFIG=/pinned/p.json\n'
         f'HEALTHCHECK_SERVICES={offered!r} source "{_REGISTRY}"\n'
         'printf "%s\\n" "${LIVE_CHECKS[@]}"\n'),
        cwd=_REPO, capture_output=True, text=True, check=False,
    )

    # Then: it is accepted and it really does drop that group. A suggestion the registry
    # would refuse is worse than none — the operator would trust it and the sweep would stop.
    assert accepted.returncode == 0, accepted.stderr
    rows = [line for line in accepted.stdout.splitlines() if line]
    assert rows and not [row for row in rows if "report-hub" in row]


def test_a_failing_optional_check_says_so_where_it_fails() -> None:
    # Given: the name of a check belonging to an optional group.
    hint = subprocess.run(
        ("bash", "-c",
         'set -euo pipefail\n'
         'eval "$(python3 automation/node_config_sh.py --print-env)"\n'
         'PRIMARY_NODE="$NODE_PRIMARY_NODE_NAME"; RAG_NODE="$NODE_RAG_NODE_NAME"\n'
         'PEER_GATEWAY_CONFIG=/pinned/p.json\n'
         f'source "{_REGISTRY}"; source "{_SUGGEST}"\n'
         'healthcheck_optional_group_hint "$RAG_NODE embedding"\n'),
        cwd=_REPO, capture_output=True, text=True, check=False,
    )

    # Then: the failure itself carries the remedy. The operator met this as a bare FAIL and
    # had to find the documentation; the answer belongs at the moment of the question.
    assert hint.returncode == 0, hint.stderr
    assert "rag" in hint.stdout and "--suggest" in hint.stdout


def test_a_failing_core_check_offers_no_such_hint() -> None:
    # Given: a core check, which no installation may decline.
    hint = subprocess.run(
        ("bash", "-c",
         'set -euo pipefail\n'
         'eval "$(python3 automation/node_config_sh.py --print-env)"\n'
         'PRIMARY_NODE="$NODE_PRIMARY_NODE_NAME"; RAG_NODE="$NODE_RAG_NODE_NAME"\n'
         'PEER_GATEWAY_CONFIG=/pinned/p.json\n'
         f'source "{_REGISTRY}"; source "{_SUGGEST}"\n'
         'healthcheck_optional_group_hint "$PRIMARY_NODE signed update trust"\n'),
        cwd=_REPO, capture_output=True, text=True, check=False,
    )

    # Then: silence. Suggesting that a core failure might be a configuration choice would
    # invite an operator to switch off the thing that just broke.
    assert hint.returncode == 0, hint.stderr
    assert hint.stdout.strip() == ""


def test_a_narrowed_wrapper_still_allows_every_advisor_probe() -> None:
    """Discovery must not be constrained by the declaration it is meant to revise."""
    command = (
        "bash",
        "automation/healthcheck_allowlist_manifest.sh",
        "--probe-hashes",
    )

    def manifest(services: str | None) -> str:
        environment = {
            key: value for key, value in os.environ.items()
            if key != "HEALTHCHECK_SERVICES"
        }
        environment["HEALTHCHECK_NODE_CONFIG_PATH"] = str(
            _REPO / "configs" / "node.example.toml"
        )
        if services is not None:
            environment["HEALTHCHECK_SERVICES"] = services
        return subprocess.run(
            command,
            cwd=_REPO,
            env=environment,
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    # The advisor examines the full catalog, so narrowing the sweep must not narrow the
    # wrapper manifest. Otherwise denied commands become apparent service absence.
    full = manifest(None)
    narrowed = manifest("core")
    assert full.strip(), "the production command recorder must emit real commands"
    assert "report-hub collector" not in full
    assert narrowed == full


def test_all_remote_probes_failing_never_produces_removal_advice() -> None:
    # Pure renderer input carries a remote transport summary after the group tallies.
    result = _render("core 12 14\nreport-hub 3 3\nrag 5 5\nremote 20 20\n")
    assert result.returncode != 0
    assert "HEALTHCHECK_SERVICES=" not in result.stdout
    assert "SSH" in result.stdout or "transport" in result.stdout
