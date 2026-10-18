"""Release-owned sender credentials: no secret in public output or argv."""
from __future__ import annotations

import io
import json
from hashlib import sha256
from pathlib import Path

import pytest

from automation import notice_bot

VALUE = "synthetic notice credential"
OLD = "synthetic previous credential"
CHANNEL = "444444444444444444"


def installed(home: Path) -> Path:
    path = home / ".hermes/notice-bot.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"token": VALUE, "channel_id": CHANNEL}))
    path.chmod(0o600)
    return path


def test_absent_override_preserves_explicit_and_environment_tokens(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", OLD)
    assert notice_bot.resolve_token() == OLD
    assert notice_bot.resolve_token("explicit credential") == "explicit credential"
    assert notice_bot.notice_channel() is None


@pytest.mark.parametrize("failure", ["none", "mode", "shape", "symlink"])
def test_override_wins_only_when_valid(tmp_path, monkeypatch, capsys, failure):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = installed(tmp_path)
    if failure == "mode":
        path.chmod(0o644)
    elif failure == "shape":
        path.write_text(VALUE)
    elif failure == "symlink":
        path.rename(path.with_suffix(".target"))
        path.symlink_to(path.with_suffix(".target"))
    assert notice_bot.resolve_token(OLD) == (VALUE if failure == "none" else OLD)
    assert notice_bot.notice_channel() == (CHANNEL if failure == "none" else None)
    output = capsys.readouterr()
    assert VALUE not in output.out + output.err
    assert bool(output.err) == (failure != "none")


def test_install_stdin_then_restore_keeps_original_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", OLD)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"token": VALUE, "channel_id": CHANNEL})))
    digest = sha256(VALUE.encode()).hexdigest()
    assert notice_bot.main(["install", "--digest", digest, "--channel", CHANNEL]) == 0
    assert notice_bot.resolve_token() == VALUE
    assert notice_bot.main(["installed", "--digest", digest, "--channel", CHANNEL]) == 0
    assert notice_bot.main(["install", "--digest", "off", "--channel", "off"]) == 0
    assert notice_bot.resolve_token() == OLD


def test_read_only_check_outputs_identity_only(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    installed(tmp_path)
    calls = []
    def get(request, **kwargs):
        calls.append((request.get_method(), request.full_url, request.get_header("Authorization")))
        return io.BytesIO(b'{"id":"123456789012345678","bot":true}')
    monkeypatch.setattr("urllib.request.urlopen", get)
    assert notice_bot.main(["check", "--expected-id", "123456789012345678"]) == 0
    assert capsys.readouterr().out == "NOTICE-BOT bot_id=123456789012345678 match=yes\n"
    assert calls == [("GET", "https://discord.com/api/v10/users/@me", f"Bot {VALUE}")]


def test_release_and_ops_senders_use_override(tmp_path, monkeypatch):
    from automation import skill_gate
    from automation.repair.repair_ops_discord import configured_setup

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", OLD)
    monkeypatch.setenv("AUTOPHAGY_OWNER_ID", "123")
    installed(tmp_path)
    assert skill_gate._token() == VALUE
    assert configured_setup().token == VALUE
