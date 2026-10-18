"""Real sender entrypoints consume the same override, not merely the same string literal."""
from __future__ import annotations

import io
import json
from dataclasses import replace
from urllib.error import URLError

import pytest

from automation import notice_bot, owner_notice, skill_gate_surface
from automation.cron_timer import Job
from automation.cron_timer_run import deliver
from automation.interop import discord_transport, reaction_approval
from automation.interop.approval_surface import ApprovalKind
from tests.unit.test_notice_bot import CHANNEL, OLD, VALUE, installed


@pytest.fixture
def switched(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", OLD)
    installed(tmp_path)
    return tmp_path


@pytest.mark.parametrize("name", ["notify_owner", "notify_owner_dm"])
def test_notice_entrypoint_uses_new_identity_and_destination(switched, monkeypatch, name):
    monkeypatch.setenv("AUTOPHAGY_OWNER_ID", "123")
    monkeypatch.setenv("OWNER_NOTICE_CHANNEL_ID", "555555555555555555")
    calls = []
    monkeypatch.setattr(owner_notice, "send_notice", lambda *args: calls.append(args))
    monkeypatch.setattr(owner_notice, "owner_dm_channel",
                        lambda *args: notice_bot.notice_channel())
    assert getattr(owner_notice, name)("test notice")
    assert calls == [(VALUE, CHANNEL, "test notice")]


def test_plain_transport_overrides_even_explicit_legacy_token(switched, monkeypatch):
    calls = []
    def send(request, **kwargs):
        calls.append(request.get_header("Authorization"))
        return io.BytesIO(b'{"id":"123"}')
    monkeypatch.setattr(discord_transport, "urlopen", send)
    assert discord_transport.DiscordTransport(OLD, CHANNEL).send("test")[0].message_id == "123"
    assert calls == [f"Bot {VALUE}"]


def test_reaction_transport_resolves_explicit_legacy_token(switched):
    assert reaction_approval.DiscordTransport(OLD, "123").token == VALUE


def test_existing_reaction_transport_picks_up_cutover_before_post(switched, monkeypatch):
    (switched / notice_bot.OVERRIDE).unlink()
    transport = reaction_approval.DiscordTransport(OLD, "123")
    installed(switched)
    headers = []
    class Connection:
        def request(self, method, path, **kwargs):
            headers.append(kwargs["headers"]["Authorization"])
        def getresponse(self):
            from types import SimpleNamespace

            return SimpleNamespace(status=200, read=lambda: b'{"id":"456"}')
        def close(self):
            return None
    monkeypatch.setattr(reaction_approval, "HTTPSConnection", lambda *a, **k: Connection())
    assert transport.post_message(CHANNEL, "test") == "456"
    assert headers == [f"Bot {VALUE}"]


def test_timer_ignores_both_legacy_token_and_home_thread(switched):
    (switched / ".hermes/.env").write_text(
        f'DISCORD_BOT_TOKEN="{OLD}"\nDISCORD_HOME_CHANNEL_THREAD_ID=555555555555555555\n'
    )
    calls = []
    assert deliver(switched, Job("test", "* * * * *", "test.py"), 0, "body",
                   lambda *args: calls.append(args)) == "sent"
    assert calls == [(VALUE, CHANNEL, "body")]


def test_supply_chain_new_and_stored_bindings_preserve_their_own_channels(switched):
    previous = "555555555555555555"
    config = switched / "interop.json"
    config.write_text(json.dumps({"owner_id": "123", "deploy_approvals_channel_id": previous}))
    def api(method, path, payload=None):
        return {"type": 0, "name": "custom-name", "recipients": [], "guild_id": "123"}
    identity = skill_gate_surface.GateIdentity(OLD, api, switched / "gate", config)
    surface = skill_gate_surface.surface_for(ApprovalKind.RELEASE, identity)
    fresh = surface.new()
    assert fresh.channel_id == CHANNEL
    record = {"kind": fresh.kind.value, "surface": fresh.surface.value,
              "policy_version": str(fresh.policy_version), "channel_id": CHANNEL}
    assert surface.stored(record).channel_id == CHANNEL
    assert surface.stored({**record, "channel_id": previous}).channel_id == previous


def test_doctor_adds_override_pass_without_legacy_secret(switched):
    from automation.doctor.capabilities import evaluate
    from automation.install.checks import Status
    from tests.unit.test_doctor import _facts

    facts = _facts(switched)
    assert facts.notice_bot_override
    finding = next(item for item in evaluate(facts) if item.key == "notice-bot")
    assert finding.status is Status.PASS
    assert finding.detail == notice_bot.NOTICE
    assert not any(item.key == "notice-bot" for item in evaluate(replace(facts, notice_bot_override=False)))


def test_check_failure_never_echoes_transport_exception(switched, monkeypatch, capsys):
    def failed(*args, **kwargs):
        raise URLError(VALUE)
    monkeypatch.setattr("urllib.request.urlopen", failed)
    assert notice_bot.main(["check", "--expected-id", "123"]) == 3
    output = capsys.readouterr()
    assert VALUE not in output.out + output.err


def test_bad_stdin_cannot_replace_installed_override(switched, monkeypatch):
    before = (switched / notice_bot.OVERRIDE).read_bytes()
    monkeypatch.setattr("sys.stdin", io.StringIO('{"token":"changed credential","channel_id":"' + CHANNEL + '"}'))
    assert notice_bot.main(["install", "--digest", "f" * 64, "--channel", CHANNEL]) == 3
    assert (switched / notice_bot.OVERRIDE).read_bytes() == before
