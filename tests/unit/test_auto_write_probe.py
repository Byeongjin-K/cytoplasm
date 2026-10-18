"""The release receipt cannot claim automatic-write settings were deployed by guessing."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from automation import auto_write, deploy_all_observe, deploy_all_probe, notice_bot_release
from tests.unit.test_deploy_all_undeclared import _runtime


@pytest.mark.parametrize("mode,observed,expected", [
    ("off", "", 0), ("off", auto_write.surface("plaud")[1], 1),
    ("plaud", auto_write.surface("plaud")[1], 0), ("plaud", "", 1),
    ("plaud", "?", 4),
])
def test_probe_refuses_stale_or_unreadable_mode(tmp_path, monkeypatch, capsys, mode, observed, expected):
    # Given: a complete fake release and an independently observed node setting.
    runtime, live, digest = _runtime(tmp_path)
    monkeypatch.setattr(deploy_all_probe, "inspect_mounts",
                        lambda *_: SimpleNamespace(stale=(), unmounted=(), orphaned=()))
    monkeypatch.setattr(deploy_all_probe, "_read_home",
                        lambda account, path: observed if path.endswith(".mode") else digest)
    monkeypatch.setattr(deploy_all_probe, "_list_home", lambda *_: (".hermes/scripts/declared.py",))
    monkeypatch.setattr(deploy_all_observe, "observe_node", lambda *a, **k: [])
    monkeypatch.setattr(notice_bot_release, "observe", lambda *a: {"agent": "ok"})
    args = ["--runtime-root", str(runtime), "--live-root", str(live), "--dori-agent-chat", "off",
            "--hermes-gateways", "running", "--auto-write", mode]
    # When: the release asks for actions then a clean receipt.
    assert deploy_all_probe.main([*args, "--format", "actions"]) == expected
    actions = capsys.readouterr().out
    assert ("ACT|auto-write|converge" in actions) == (expected == 1)
    assert deploy_all_probe.main([*args, "--format", "receipt"]) == expected
    output = capsys.readouterr().out
    # Then: only a matching node setting receives a successful receipt.
    if expected == 0:
        assert json.loads(output)["auto_write"] == {
            "enabled": [] if mode == "off" else ["plaud"], "kinds": {"plaud": "ok"},
        }
    else:
        assert not output
