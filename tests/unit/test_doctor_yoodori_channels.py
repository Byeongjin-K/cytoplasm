"""The deployed sender must not probe channels belonging to the retired gateway."""
from pathlib import Path

from automation.doctor import facts
from automation.install import discord_check
from automation.install.checks import Status


def test_yoodori_probe_reads_only_live_channels(tmp_path, monkeypatch):
    import json

    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "agents_log_channel_id": "old-log", "interop_channel_id": "old-interop",
        "personal_approvals_channel_id": "approval",
    }))
    paths = []

    def fetch(path):
        paths.append(path)
        if path == "/users/@me":
            return discord_check.ApiResponse(200, {"id": "bot", "username": "sender", "bot": True})
        if path == "/applications/@me":
            return discord_check.ApiResponse(200, {"flags": 1 << 18})
        if path.endswith("messages?limit=1"):
            return discord_check.ApiResponse(200, [])
        return discord_check.ApiResponse(200, {"id": path.rsplit("/", 1)[-1], "type": 0})

    monkeypatch.setattr(discord_check, "_urllib_fetch", lambda _token: fetch)
    results = facts._yoodori_probe("test-token", config, "live")
    assert {r.name for r in results if r.name.startswith("channel[")} == {
        "channel[yoodori]", "channel[approvals]",
    }
    assert all(r.status is Status.PASS for r in results)
    assert not any("old-" in path or "/approval" in path for path in paths)


def test_yoodori_probe_keeps_inaccessible_live_channel_a_failure(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(discord_check, "_urllib_fetch", lambda _token: lambda _path:
                        discord_check.ApiResponse(403) if _path.startswith("/channels/") else
                        discord_check.ApiResponse(200, {"id": "bot", "username": "sender", "bot": True}))
    results = facts._yoodori_probe("test-token", tmp_path / "missing", "live")
    assert next(r for r in results if r.name == "channel[approvals]").status is Status.FAIL


def test_gather_dispatches_override_sender_to_live_channel_probe(tmp_path, monkeypatch):
    from tests.unit.test_doctor import _connected_home, _run
    from tests.unit.test_notice_bot import CHANNEL, installed

    home = _connected_home(tmp_path)
    installed(home)
    requests = []

    def fetch(path):
        requests.append(path)
        if path == "/users/@me":
            return discord_check.ApiResponse(200, {"id": "bot", "username": "sender", "bot": True})
        if path == "/applications/@me":
            return discord_check.ApiResponse(200, {"flags": 1 << 18})
        if path.endswith("messages?limit=1"):
            return discord_check.ApiResponse(200, [])
        return discord_check.ApiResponse(200, {"type": 0, "name": "arbitrary-name"})

    monkeypatch.setattr(discord_check, "_urllib_fetch", lambda _token: fetch)
    result = facts.gather(account="agent", role="agent", home=home, gateway_unit="hermes-gateway.service",
                          online=True, run=_run(), uid=1000)
    assert result.notice_bot_override
    assert requests
    assert all(CHANNEL in path for path in requests if path.startswith("/channels/"))
    assert all(item.status is Status.PASS for item in result.discord)
