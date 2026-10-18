"""Q236: new release/supply-chain cards follow the sender; old bindings still drain."""
import json

import pytest

from automation import notice_bot, skill_gate_surface
from automation.interop import approval_directory
from automation.interop.approval_surface import ApprovalKind
from tests.unit.test_notice_bot import CHANNEL, installed


@pytest.mark.parametrize("kind", [ApprovalKind.RELEASE, ApprovalKind.SKILL_DEPLOY])
@pytest.mark.parametrize("overridden", [True, False])
def test_new_binding_follows_sender_or_personal_channel(tmp_path, monkeypatch, kind, overridden):
    previous = "555555555555555555"
    monkeypatch.setenv("HOME", str(tmp_path))
    config = tmp_path / "interop.json"
    config.write_text(json.dumps({"owner_id": "123", "personal_approvals_channel_id": previous}))
    monkeypatch.setenv("INTEROP_CONFIG", str(config))
    if overridden:
        installed(tmp_path)
    requests = []

    def api(method, path, payload=None):
        requests.append((method, path))
        return {"id": path.rsplit("/", 1)[-1], "type": 0, "name": "custom-name",
                "recipients": [], "guild_id": "123"}

    identity = skill_gate_surface.GateIdentity("test-token", api, tmp_path / "gate", config)
    binding = skill_gate_surface.surface_for(kind, identity).new()
    expected = CHANNEL if overridden else previous
    assert binding.channel_id == expected
    assert approval_directory._configured_approvals_channel() == expected
    assert requests
    assert all(method == "GET" for method, _path in requests)
    assert notice_bot.notice_channel() == (CHANNEL if overridden else None)
