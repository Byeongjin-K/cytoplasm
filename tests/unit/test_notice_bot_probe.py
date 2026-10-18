"""Sender readback belongs to the same observation snapshot as the rest of a release."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from automation import deploy_all_observe, deploy_all_probe, notice_bot_release
from tests.unit.test_deploy_all_undeclared import _runtime
from tests.unit.test_notice_bot import CHANNEL
from tests.unit.test_notice_bot_release import DIGEST


@pytest.mark.parametrize("mode,state,expected", [("off", "ok", 0), ("off", "drift", 1),
                                               (DIGEST, "ok", 0), (DIGEST, "drift", 1)])
def test_cli_snapshot_includes_all_sender_accounts(tmp_path, monkeypatch, capsys, mode, state, expected):
    runtime, live, digest = _runtime(tmp_path)
    monkeypatch.setattr(deploy_all_probe, "inspect_mounts",
                        lambda *_: SimpleNamespace(stale=(), unmounted=(), orphaned=()))
    monkeypatch.setattr(deploy_all_probe, "_read_home",
                        lambda account, path: "" if path.endswith(".mode") else digest)
    monkeypatch.setattr(deploy_all_probe, "_list_home", lambda *_: (".hermes/scripts/declared.py",))
    monkeypatch.setattr(deploy_all_observe, "observe_node", lambda *a, **k: [])
    calls = []
    def observe(root, wanted, channel):
        calls.append((root, wanted, channel))
        return {"agent": state, "peer": "ok", "ops": "ok"}
    monkeypatch.setattr(notice_bot_release, "observe", observe)
    args = ["--runtime-root", str(runtime), "--live-root", str(live), "--dori-agent-chat", "off",
            "--hermes-gateways", "running", "--notice-bot", mode, "--yoodori-channel", CHANNEL]
    assert deploy_all_probe.main([*args, "--format", "actions"]) == expected
    actions = capsys.readouterr().out
    assert ("ACT|notice-bot|converge" in actions) == (state == "drift")
    assert calls == [(runtime, mode, CHANNEL)]
    assert deploy_all_probe.main([*args, "--format", "receipt"]) == expected
    output = capsys.readouterr().out
    if expected == 0:
        assert json.loads(output)["notice_bot"] == {
            "digest": mode, "accounts": {"agent": "ok", "peer": "ok", "ops": "ok"},
        }
    else:
        assert output == ""
