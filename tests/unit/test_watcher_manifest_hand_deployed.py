"""Hand-deployed account-home artefacts must remain drift-detectable."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Final

_REPO: Final = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from automation.watcher_manifest import all_rows  # noqa: E402


_EXPECTED: Final = {
    ("agent", ".hermes/scripts/repair_report_consume_watch.py"): (
        "automation/repair/cron/repair_report_consume_watch.py"
    ),
    ("agent", ".hermes/plugins/05-skill-generation/__init__.py"): (
        "automation/skill_generation/plugin/__init__.py"
    ),
    ("agent", ".hermes/plugins/05-skill-generation/plugin.yaml"): (
        "automation/skill_generation/plugin/plugin.yaml"
    ),
}


def test_hand_deployed_account_home_artefacts_are_declared_with_deployers() -> None:
    rows = {(row.account, row.destination): row.source for row in all_rows(_REPO)}
    assert _EXPECTED.keys() <= rows.keys()

    for pair, expected_source in _EXPECTED.items():
        source = rows[pair]
        assert source == expected_source
        package = Path(*Path(source).parts[:2])
        assert (_REPO / package / "deploy.sh").is_file(), (
            f"{source} 소유 패키지에 deploy.sh가 없다: {package}/deploy.sh"
        )
