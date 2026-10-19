"""The retired wiki skill cannot install a new approval watcher."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from automation.deploy_declarations import parse_declaration_file

REPO = Path(__file__).resolve().parents[2]


def test_wiki_declarations_when_retired_have_no_active_deliveries() -> None:
    # Given the release's wiki deployment declarations.
    manifest = "skills/wiki/deploy-manifest.txt"
    declarations = parse_declaration_file(manifest, (REPO / manifest).read_text(encoding="utf-8"))
    # When deciding which deliveries may still be installed.
    active = tuple(row.destination for row in declarations if row.policy != "retired")
    # Then no watcher or skill delivery remains active.
    assert declarations
    assert active == ()


def test_wiki_deploy_when_retired_refuses_before_connecting(tmp_path: Path) -> None:
    # Given no node configuration or deployment credentials.
    environment = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "DEPLOY_SSH_HOST": ""}
    # When an old caller tries to deploy the retired skill.
    result = subprocess.run(
        ("bash", str(REPO / "skills/wiki/deploy.sh")),
        env=environment, capture_output=True, text=True, check=False, timeout=10,
    )
    # Then deployment is refused locally, not treated as a missing host.
    assert result.returncode == 4
    assert result.stderr.startswith("WIKI-RETIRED:")
