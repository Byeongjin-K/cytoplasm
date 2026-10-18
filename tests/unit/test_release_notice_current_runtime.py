"""Current-runtime notice regressions are new cases outside FS3-pinned test files."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from automation import deploy_receipt, owner_notice, release_applied_notice, release_applied_state, release_models
from automation.interop.owner_message import OwnerMessage


def test_applied_notice_ignores_gateway_models_and_only_lists_real_owner_actions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an older sender supplies a gateway summary with a retirement and a real hold.
    head = "a" * 40
    pointer = tmp_path / "current"
    pointer.symlink_to(tmp_path / head)
    monkeypatch.setenv("NODE_RELEASE_CURRENT", str(pointer))
    receipt = {
        "version": 2, "pending_unknown": False,
        "held": [{"reason": "peer-retired"}, {"reason": "owner-pending"}],
    }
    facts: list[str] = []

    def capture(content: str, *, message: OwnerMessage | None = None) -> bool:
        assert message is not None
        facts.append(message.fact)
        return True

    monkeypatch.setattr(owner_notice, "notify_owner", capture)
    models = json.dumps({
        "agent": {"main": "obsolete-primary", "fallback": "obsolete-fallback", "max_turns": "500"},
        "peer": {"main": "obsolete-primary", "fallback": "obsolete-fallback", "max_turns": "500"},
    })
    # When: the real node sender builds and hands off its envelope.
    rc = release_applied_notice.main([
        "send", "--version", "v2.0.1", "--head", head, "--models", models,
        "--pending", json.dumps(deploy_receipt.pending_lines(receipt)),
    ])
    # Then: neither obsolete model facts nor intentional retirement become owner work.
    assert rc == 0
    assert len(facts) == 1
    assert facts[0] == (
        f"릴리스 v2.0.1 가 적용되었습니다. (HEAD {head[:12]})\n"
        "남은 소유자 조치:\n- 보류 1건 — owner-pending"
    )


def test_workstation_sender_does_not_probe_or_forward_gateway_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: reading either gateway's model settings is forbidden in this scenario.
    def forbidden() -> str:
        pytest.fail("applied notices must not probe Hermes model settings")

    commands: list[tuple[str, ...]] = []

    def run(
        command: tuple[str, ...], *, env: dict[str, str], check: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert env["RELEASE_APPROVAL_MODULE"] == "automation.release_applied_notice"
        assert check is False
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(release_models, "probe", forbidden)
    monkeypatch.setattr(release_applied_state.subprocess, "run", run)
    monkeypatch.setenv("RELEASE_APPROVAL_CMD", "fake-sender")
    head = "a" * 40
    sweep = release_applied_state.Sweep(tmp_path, tmp_path, head, head)
    # When
    sweep._notify(head, "v2.0.1")
    # Then: only version and deployment identity cross the sender boundary.
    assert commands == [("fake-sender", "send", "--version", "v2.0.1", "--head", head)]
    assert (tmp_path / "notified" / head).is_file()


@pytest.mark.parametrize("reason", ["retired", "peer-retired", "agent-retired"])
def test_retired_holds_remain_in_the_receipt_but_are_not_owner_actions(reason: str) -> None:
    # Given: intentional retirement is the only hold in a completely observed receipt.
    receipt = {"version": 2, "pending_unknown": False, "held": [{"reason": reason}]}
    before = json.dumps(receipt, sort_keys=True)
    # When
    lines = deploy_receipt.pending_lines(receipt)
    # Then: receipt evidence is preserved and there is no empty owner-action heading.
    assert lines == ()
    assert json.dumps(receipt, sort_keys=True) == before


@pytest.mark.parametrize("reason", [
    "roster-required", "roster-unverified", "roster-sibling", "owner-pending", "retirement-review", "",
])
def test_other_holds_still_require_visible_owner_guidance(reason: str) -> None:
    # Given / When
    lines = deploy_receipt.pending_lines({
        "version": 2, "pending_unknown": False, "held": [{"reason": reason}],
    })
    # Then: none of the non-retirement holds is hidden.
    assert lines[0] == "남은 소유자 조치:"
    assert len(lines) == 2
    assert lines[1].startswith("- 보류 1건 — ")
