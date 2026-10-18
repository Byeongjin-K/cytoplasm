"""Existing configs stop scanning dead Hermes sources, retaining other documents."""
import json
from dataclasses import replace

from automation.rag_ingest.config import load_config
from automation.rag_ingest.pipeline import collect_documents
from automation.rag_ingest.sources import discord_team
from tests.unit.test_rag_ingest_config import write_config


def test_existing_config_disables_only_legacy_inputs(tmp_path):
    path = write_config(tmp_path)
    raw = json.loads(path.read_text())
    raw["hermes_db"] = str(tmp_path / "state.db")
    raw["discord"] = {"enabled": True, "guild_id": "guild"}
    path.write_text(json.dumps(raw))
    secrets = tmp_path / "secrets"
    secrets.write_text(secrets.read_text() + "DISCORD_BOT_TOKEN=test-token\n")
    config = load_config(path)
    assert config.hermes_db is None
    assert config.discord.agents_log_channel == ""
    assert config.discord.team_channel == "team"
    assert config.discord.enabled


def test_legacy_inputs_can_be_explicitly_reenabled(tmp_path):
    path = write_config(tmp_path)
    raw = json.loads(path.read_text())
    raw.update({"hermes_db": str(tmp_path / "state.db"), "index_hermes_conversations": True,
                "discord": {"enabled": True, "guild_id": "guild", "index_agents_log": True}})
    path.write_text(json.dumps(raw))
    secrets = tmp_path / "secrets"
    secrets.write_text(secrets.read_text() + "DISCORD_BOT_TOKEN=test-token\n")
    config = load_config(path)
    assert config.hermes_db == tmp_path / "state.db"
    assert config.discord.agents_log_channel == "agents-log"


def test_disabled_agents_log_is_neither_resolved_nor_fetched(tmp_path, monkeypatch):
    from tests.unit.test_rag_ingest_discord_identity import _config

    config = _config(tmp_path)
    config = replace(config, discord=replace(config.discord, agents_log_channel=""))
    requests = []

    def request(path, _token, _log):
        requests.append(path)
        if path.startswith("/guilds/"):
            return [{"id": "team-id", "name": "team"}]
        if path == "/users/@me":
            return {"id": "self"}
        return []

    monkeypatch.setattr(discord_team, "_request", request)
    state = {"sources": {"old-conversation": {"point_ids": ["point"]}}}
    docs, present = collect_documents(config, state, set(), {"discord"}, [], [])
    assert docs == []
    assert present == {}
    assert any("/channels/team-id/messages" in path for path in requests)
    assert state["sources"]["old-conversation"]["point_ids"] == ["point"]
