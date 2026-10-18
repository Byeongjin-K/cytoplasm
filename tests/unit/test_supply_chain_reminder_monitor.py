"""Exercise release surface validation and persist reminder failures beyond the journal."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from automation import supply_chain_watch_cli as cli
from automation.interop.approval_surface import ApprovalSurfaceError, ChannelFacts, POLICY_VERSION
from automation.supply_chain_plan import PendingRequest
from automation.supply_chain_records import EnumerationResult
from automation.supply_chain_watch import TickResult


@pytest.mark.parametrize("broken", [False, True])
def test_release_reminder_uses_release_binding_and_records_failure(tmp_path, monkeypatch, broken) -> None:
    channel = "123456789012345678"
    posted = datetime(2026, 8, 1, tzinfo=UTC)
    message = str((int(posted.timestamp() * 1000) - 1_420_070_400_000) << 22)
    pending = tmp_path / "pending"
    pending.mkdir()
    (pending / "demo.json").write_text(json.dumps({
        "kind": "release", "surface": "skill-approvals", "channel_id": channel,
        "policy_version": str(POLICY_VERSION), "message_id": message, "action_hash": "a" * 64,
    }))
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"owner_id": "22", "deploy_approvals_channel_id": channel}))
    sent = []

    class Directory:
        def skill_approvals(self):
            return channel

        def describe(self, _channel):
            if broken:
                raise ApprovalSurfaceError("unavailable")
            return ChannelFacts(0, "renamed-approvals", (), guild_id="987654321098765432")

    def api(method, path, payload=None):
        if method == "GET":
            return {"guild_id": "987654321098765432"}
        sent.append((path, payload))
        return {"id": "sent"}

    identity = SimpleNamespace(directory=lambda: Directory(), api=api, interop_config=config)
    request = PendingRequest("release:demo", "release", "demo", "demo")
    monkeypatch.setenv("SUPPLY_CHAIN_WATCH_STATE", str(tmp_path / "tick.json"))
    monkeypatch.setattr(cli.skill_gate, "GATE_DIR", tmp_path)
    monkeypatch.setattr(cli.skill_gate, "_identity", lambda: identity)
    monkeypatch.setattr(cli.skill_gate, "_owner_id", lambda: "22")
    monkeypatch.setattr(cli.skill_gate, "_owner_decision", lambda *_: "absent")
    monkeypatch.setattr(cli, "watch_tick", lambda *_, **__: EnumerationResult(
        (TickResult(request, "retain", "unanswered"),), True,
    ))
    monkeypatch.setattr(cli, "run_shadow_check", lambda: ())

    first, second = cli.main(), cli.main()

    state = json.loads((tmp_path / "tick.json").read_text())
    if broken:
        assert (first, second) == (1, 1)
        assert state["reminder_failures"][request.key]["attempts"] == 2
        assert state["reminder_failures"][request.key]["fingerprint"] == "ApprovalSurfaceError"
        assert sent == []
        broken = False
        assert cli.main() == 0
        assert len(sent) == 1
        assert json.loads((tmp_path / "tick.json").read_text()).get("reminder_failures", {}) == {}
    else:
        assert (first, second) == (0, 0)
        assert len(sent) == 1
        assert state.get("reminder_failures", {}) == {}
