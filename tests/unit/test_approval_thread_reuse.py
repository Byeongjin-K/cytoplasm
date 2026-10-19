"""Reuse regressions driven through the real mail compose CLI and binding directory."""
from __future__ import annotations

from pathlib import Path
from urllib.error import HTTPError
from email.message import Message

import pytest

from automation.interop.approval_directory import DiscordChannelDirectory
from automation.interop.approval_surface import ApprovalKind, RequestThread, resolve_new_binding
from automation.interop.owner_message import discord_link
from tests.unit.test_approval_directory import FakeApi
from tests.unit.test_mail_compose_gate import (
    AGENT_CHAT_CHANNEL, _compose_env, triage_approval, triage_cli, triage_confirm,
    triage_core, triage_gate, triage_mode,
)

GUILD = "300000000000000001"
THREAD = "300000000000000002"
CARD = "300000000000000003"
ORIGIN = "300000000000000004"
NAME = "✉️ 메일 발신 · request"
PREFIXES = ("✅ 완료 · ", "⛔ 취소 · ", "⌛ 만료 · ", "❗ 실패 · ")


def _compose_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str,
) -> FakeApi:
    _compose_env(monkeypatch, tmp_path)
    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(Path(__file__).resolve().parents[2]))
    monkeypatch.setattr(triage_mode, "effective_mode", lambda: "full-go")
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: "333")
    monkeypatch.setattr(triage_confirm, "bot_token", lambda: "identity-a")
    api = FakeApi("identity-a", {
        ("GET", f"/channels/{THREAD}"): {
            "type": 11, "name": name, "parent_id": AGENT_CHAT_CHANNEL, "guild_id": GUILD,
            "thread_metadata": {"archived": True},
        },
        ("POST", f"/channels/{THREAD}/messages"): {"id": CARD},
        ("PATCH", f"/channels/{THREAD}"): {},
        **{
            ("PUT", f"/channels/{THREAD}/messages/{CARD}/reactions/{triage_confirm.quote(emoji, safe='')}/@me"): None
            for emoji in ("✅", "⛔")
        },
    })
    monkeypatch.setattr(triage_confirm, "_api", api)
    return api


def _compose() -> int:
    args = triage_cli.build_parser().parse_args([
        "compose", "--to", "reader@example.invalid", "--subject", "request", "--body", "body",
        "--origin-channel-id", THREAD, "--origin-message-id", ORIGIN,
    ])
    return triage_cli.cmd_compose(args)


@pytest.mark.parametrize("prefix", PREFIXES)
def test_compose_restores_in_progress_name_when_reusing_terminal_thread(
    prefix: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a terminal owner thread; posting the new card reopens it.
    api = _compose_api(tmp_path, monkeypatch, prefix + NAME)
    # When: the real compose command posts its approval into that thread.
    assert _compose() == 0
    # Then: only the terminal prefix is removed, after the exact card bytes land.
    card_index = next(i for i, call in enumerate(api.calls) if call[:2] == ("POST", f"/channels/{THREAD}/messages"))
    rename_index = next(i for i, call in enumerate(api.calls) if call[0] == "PATCH")
    assert card_index < rename_index
    assert api.calls[rename_index][2] == {"name": NAME, "archived": False}
    draft, = triage_gate.list_drafts()
    assert draft["sha256"] == triage_core.draft_sha256(draft)
    assert api.calls[card_index][2] == {"content": triage_approval._approval_content(draft, "")}


def test_compose_has_card_url_when_owner_thread_supplies_guild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: no cached guild, but the existing owner thread's GET supplies it.
    _compose_api(tmp_path, monkeypatch, NAME)
    # When: compose persists the binding and prints its pointer.
    assert _compose() == 0
    # Then: persisted coordinates identify the actual approval card without rehashing it.
    draft, = triage_gate.list_drafts()
    assert draft["approval_guild_id"] == GUILD
    link = discord_link(space="guild", guild_id=GUILD, channel_id=THREAD, message_id=CARD)
    assert capsys.readouterr().out.splitlines()[-1] == (
        f"APPROVAL-THREAD draft={draft['id']} url={link.url}"
    )


def test_compose_keeps_card_when_restoring_thread_name_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: the card post works but Discord refuses the cosmetic rename.
    api = _compose_api(tmp_path, monkeypatch, PREFIXES[0] + NAME)
    api.responses[("PATCH", f"/channels/{THREAD}")] = HTTPError(
        "https://discord.test", 403, "refused", Message(), None,
    )
    # When: compose posts into the reused thread.
    assert _compose() == 0
    # Then: the approval remains live and there is exactly one failure diagnostic.
    draft, = triage_gate.list_drafts()
    assert draft["message_id"] == CARD and draft["status"] == "pending"
    assert [line.split()[0] for line in capsys.readouterr().err.splitlines()] == ["THREAD-RENAME-FAIL"]


def test_new_binding_preserves_live_guild_when_owner_thread_is_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: no cache, a live owner-thread description, and no new thread POST.
    api = _compose_api(tmp_path, monkeypatch, NAME)
    directory = DiscordChannelDirectory(None, "333", api)
    # When: the shared RequestThread binding path reuses the owner's thread.
    binding = resolve_new_binding(
        ApprovalKind.MAIL_COMPOSE, directory, "333",
        request=RequestThread("request", THREAD, ORIGIN),
    )
    # Then: every producer receives the verified guild as routing metadata.
    assert (binding.channel_id, binding.guild_id) == (THREAD, GUILD)


def test_compose_still_adds_reactions_when_interop_runtime_lacks_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: the node's interop runtime predates resume_thread (mail skill deployed first).
    api = _compose_api(tmp_path, monkeypatch, PREFIXES[0] + NAME)

    def old_runtime() -> object:
        raise AttributeError("resume_thread")

    monkeypatch.setattr(triage_confirm, "_origin_notice", old_runtime)
    # When: compose posts into the reused thread.
    assert _compose() == 0
    # Then: the card still gets both approval reactions and one rename diagnostic.
    puts = [call for call in api.calls if call[0] == "PUT"]
    assert len(puts) == 2
    draft, = triage_gate.list_drafts()
    assert draft["message_id"] == CARD and draft["status"] == "pending"
    assert [line.split()[0] for line in capsys.readouterr().err.splitlines()] == ["THREAD-RENAME-FAIL"]
