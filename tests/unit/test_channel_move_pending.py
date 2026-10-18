"""Read exact live stores without mutating state, opening threads, or executing approvals."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from automation.interop import channel_move
from tests.unit.test_agent_chat_channel_move import NEW, OLD

NOTICE = "444444444444444444"


@pytest.mark.parametrize("store,container,kind", [
    ("mail-triage/drafts/card.json", None, "mail-reply"),
    ("calendar-gate/drafts/card.json", None, "calendar"),
    ("todo-approvals/pending/card.json", None, "todo"),
    ("wiki-gate/drafts/card.json", None, "wiki"),
    ("project-board/proposals.json", "proposals", "project-board"),
    ("obsidian-write-requests/requests.json", "requests", "obsidian-write"),
    ("obsidian-write/requests/card.json", None, "obsidian-write"),
    ("memory-curator/relocations.json", "relocations", "obsidian-write"),
    ("memory-relocate/relocations.json", "relocations", "obsidian-write"),
])
def test_pending_inventory_covers_live_stores_and_masks_titles(
    tmp_path: Path, store: str, container: str | None, kind: str,
) -> None:
    # Given
    path = tmp_path / ".hermes" / store
    path.parent.mkdir(parents=True)
    record = {
        "id": "card-id", "status": "pending", "channel_id": "321", "message_id": "654",
        "approval_guild_id": "777", "subject": "mail subject", "title": "private calendar title",
        "relpath": "000_PARA/private/Note.md",
    }
    payload = record
    if container:
        payload = {container: {"key": record} if container == "relocations" else [record]}
    path.write_text(json.dumps(payload))
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    calls = []
    def api(endpoint):
        calls.append(endpoint)
        if endpoint == "/channels/321":
            return {"parent_id": OLD}
        return [{"id": "owner", "bot": True}, {"id": "other", "bot": False}]
    # When
    cards = channel_move.pending(tmp_path, frozenset({OLD, NOTICE}), api, "owner")
    # Then
    assert len(cards) == 1 and cards[0].kind == kind
    assert cards[0].title == (
        "mail subject" if kind == "mail-reply" else
        "private calendar title" if kind == "todo" else
        "Note.md" if kind == "obsidian-write" else "card-id"
    )
    assert cards[0].link.endswith("/777/321/654")
    assert all(endpoint.startswith("/channels/") for endpoint in calls)
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


@pytest.mark.parametrize("state", ["executed", "expired", "approved", "cancelled", "abandoned", "applied", "written"])
def test_terminal_records_do_not_dereference_deleted_channels(tmp_path: Path, state: str) -> None:
    # Given
    path = tmp_path / ".hermes/mail-triage/drafts/closed.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"status": state, "channel_id": OLD, "message_id": "654"}))
    def forbidden(_endpoint):
        pytest.fail("closed records must not read Discord")
    # When / Then
    assert channel_move.pending(tmp_path, frozenset({OLD}), forbidden, "owner") == ()


@pytest.mark.parametrize("case", ["owner-approved", "owner-cancelled", "deleted", "unrelated", "notice"])
def test_inventory_checks_owner_reactions_and_both_retiring_channels(tmp_path: Path, case: str) -> None:
    # Given
    path = tmp_path / ".hermes/mail-triage/drafts/card.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"id": "card-id", "status": "pending",
                               "channel_id": NOTICE if case == "notice" else "321", "message_id": "654"}))
    def api(endpoint):
        if case == "deleted":
            raise HTTPError("https://example.invalid", 404, "Unknown Channel", {}, None)
        if endpoint == "/channels/321":
            return {"parent_id": NEW if case == "unrelated" else OLD}
        selected = "%E2%9C%85" if case == "owner-approved" else "%E2%9B%94"
        return [{"id": "owner", "bot": False}] if case.startswith("owner-") and selected in endpoint else []
    # When
    cards = channel_move.pending(tmp_path, frozenset({OLD, NOTICE}), api, "owner")
    # Then
    assert len(cards) == (1 if case == "notice" else 0)
