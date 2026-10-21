from __future__ import annotations

from pathlib import Path

import pytest

from automation.regression_bank.scenario_registry import ScenarioRegistry, ScenarioValidationError
from automation.repair.repair_ops_adapters import normalize_bank_scenario


def test_bank_scenario_when_ticket_has_underscores_then_generated_id_passes_strict_registry(tmp_path: Path) -> None:
    # Given: a real repair plan derives an invalid underscore-bearing id from its ticket.
    scenario = tmp_path / "scenario.yaml"
    _ = scenario.write_text(
        "\n".join(
            (
                "version: 1",
                "id: w6-w62prod_20260719092741",
                'title: "repair scenario"',
                "driver: tests/e2e/drivers/w4_local.sh",
                "actor: tests/e2e/drivers/w4_mail_triage_actor.py",
                "cases:",
                "  - id: triage_draft",
                "    kind: happy",
                "    steps:",
                "      - repair verifies the offline actor",
                "    expect:",
                "      process_exit: 0",
                "      error: null",
                "",
            )
        ),
        encoding="utf-8",
    )
    registry = ScenarioRegistry(Path(__file__).resolve().parents[2])
    with pytest.raises(ScenarioValidationError, match="lowercase hyphenated"):
        _ = registry.validate(scenario)

    # When: production plan loading normalizes the bank scenario identity from that ticket.
    normalized = normalize_bank_scenario("t_w62prod_20260719092741", scenario)

    # Then: the strict registry accepts the deterministic lowercase-hyphenated result.
    assert normalized == "w6-w62prod-20260719092741"
    validated = registry.validate(scenario)
    assert validated.scenario_id == normalized
