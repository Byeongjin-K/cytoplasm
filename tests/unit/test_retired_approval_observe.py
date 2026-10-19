"""Release observations use the declared retired-approval cleanup probes."""
from __future__ import annotations

from pathlib import Path

import pytest

from automation import deploy_all_observe as observe
from automation.deploy_declarations import parse_declaration_file


@pytest.mark.parametrize(
    "artifact",
    (
        ("retired-interop-v1", ".hermes/plugins/interop-protocol", "interop-plugin"),
        ("retired-wiki-script-v1", ".hermes/scripts/wiki_confirm_reaction_watch.py", "wiki-confirm-watch"),
    ),
)
@pytest.mark.parametrize("observation", ((0, "ok", "ok"), (0, "stale", "stale"), (1, "", "unknown")))
def test_retired_approval_artifact_when_observed_uses_its_cleanup_probe(
    tmp_path: Path, artifact: tuple[str, str, str], observation: tuple[int, str, str],
) -> None:
    # Given a required absence declaration and the cleanup probe's result.
    algorithm, destination, name = artifact
    returncode, output, state = observation
    declaration, = parse_declaration_file(
        "automation/retired_jobs/deploy-manifest.txt",
        f"agent|automation/retired_jobs|{destination}|v2:derived;algorithm={algorithm}\n",
    )
    calls: list[tuple[str, tuple[str, ...]]] = []

    def probe(account: str, arguments: tuple[str, ...]) -> tuple[int, str]:
        calls.append((account, arguments))
        return returncode, output

    # When observing the artifact without changing the account.
    result = observe._derived(declaration, tmp_path, probe, {})

    # Then the exact retirement check determines convergence, not the mail runtime probe.
    assert calls == [("agent", ("retired-job", str(tmp_path), name))]
    assert result.state == state
