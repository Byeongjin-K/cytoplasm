"""P5: a release-written explicit mode removes the peer dependency, never owner checks."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from automation.memory_relocate import choice_accounts, peer_mode
from automation.memory_relocate.choice_files import Mailbox
from automation.memory_relocate.choices import ChoiceError
from test_memory_relocate_per_line import line as line
from memory_choice_fakes import OWNER


class _User:
    def __init__(self, name: str, uid: int) -> None:
        self.pw_name = name
        self.pw_uid = uid
        self.pw_gid = 100


def test_retired_delete_never_reads_peer_and_keeps_backup_contract(line, monkeypatch) -> None:
    # Given: a frozen owner DELETE with a retired peer and no receipt.
    offline, request, agent = line
    agent = replace(agent, accounts=replace(agent.accounts, peer=None))
    offline.transport.reactors[(request.record.message_id, "🗑️")] = ((OWNER, False),)
    original = Mailbox.read
    def own_only(self, name):
        assert self.path == offline.accounts.agent.path
        return original(self, name)
    monkeypatch.setattr(Mailbox, "read", own_only)
    assert agent.probe(request.record) == "approved"
    # When: deletion runs without a peer completion.
    outcome = agent.delete(request.record)
    # Then: only own native files changed; backups and explicit retirement receipt remain.
    assert outcome.deleted
    assert outcome.backup_path is not None
    assert len(tuple(offline.memory.glob("*.deleted-*"))) == 2
    for name in ("USER.md", "MEMORY.md"):
        assert request.members[0][1] not in (offline.memory / name).read_text()
    receipt = next(agent.native.receipts.glob("*.peer-retired.json"))
    assert json.loads(receipt.read_text()) == {"peer_retired": True, "scope": "agent", "status": "done"}
    assert not tuple(offline.accounts.agent.path.glob("*.decision.json"))


def test_default_delete_still_requires_peer_receipt(line) -> None:
    # Given: no retirement opt-in and a valid frozen DELETE.
    offline, request, agent = line
    offline.transport.reactors[(request.record.message_id, "🗑️")] = ((OWNER, False),)
    assert agent.probe(request.record) == "approved"
    before = (offline.memory / "USER.md").read_bytes()
    # When: no peer executor has run.
    outcome = agent.delete(request.record)
    # Then: default waits rather than silently deleting only one account.
    assert not outcome.deleted and outcome.reason == "peer_pending"
    assert (offline.memory / "USER.md").read_bytes() == before


def test_retired_mode_still_refuses_changed_approval(line) -> None:
    # Given: a DELETE frozen before the physical card changed.
    offline, request, agent = line
    agent = replace(agent, accounts=replace(agent.accounts, peer=None))
    offline.transport.reactors[(request.record.message_id, "🗑️")] = ((OWNER, False),)
    assert agent.probe(request.record) == "approved"
    offline.transport.messages[request.record.message_id] = "changed"
    # When: retired-mode deletion revalidates the card.
    with pytest.raises(ChoiceError, match="card_binding"):
        agent.delete(request.record)
    # Then: retirement is not approval.
    assert not tuple(offline.memory.glob("*.deleted-*"))


def test_setting_absent_is_legacy_and_release_seed_enables_retirement(tmp_path, monkeypatch) -> None:
    # Given: an isolated agent home with no setting.
    monkeypatch.setenv("HOME", str(tmp_path))
    assert not peer_mode.peer_retired()
    seed = Path(__file__).resolve().parents[2] / "automation/memory_relocate/peer-mode.json"
    target = tmp_path / ".hermes/memory-relocate/peer-mode.json"
    target.parent.mkdir(parents=True)
    # When: the release installs its declared bytes.
    target.write_bytes(seed.read_bytes())
    # Then: explicit boolean true selects retirement; malformed settings fail closed.
    assert peer_mode.peer_retired()
    target.write_text(json.dumps({"peer_retired": "true"}))
    with pytest.raises(ChoiceError, match="peer_mode_invalid"):
        peer_mode.peer_retired()


def test_retired_accounts_does_not_resolve_or_check_peer(tmp_path, monkeypatch) -> None:
    # Given: the peer Unix account has already been removed.
    from types import SimpleNamespace
    fields = ("agent_account", "peer_account", "ops_account", "service_group")
    config = SimpleNamespace(**dict(zip(fields, ("agent", "peer", "ops", "team"))))
    looked_up = []
    def lookup(name):
        looked_up.append(name)
        assert name != "peer"
        uid = {"agent": 1001}.get(name, 1002)
        return _User(name, uid)
    monkeypatch.setattr(choice_accounts, "peer_retired", lambda: True)
    monkeypatch.setattr(choice_accounts, "_root_owned", lambda _path: None)
    monkeypatch.setattr(choice_accounts, "load_node_config", lambda _path: config)
    users = choice_accounts.pwd
    monkeypatch.setattr(users, "getpwnam", lookup)
    monkeypatch.setattr(choice_accounts.grp, "getgrnam", lambda _name: SimpleNamespace(gr_gid=100))
    monkeypatch.setattr(choice_accounts.os, "getgrouplist", lambda *_args: [100])
    monkeypatch.setattr(choice_accounts, "check_topology", lambda *_args: None)
    checked = []
    monkeypatch.setattr(Mailbox, "check", lambda self: checked.append(self.path))
    # When: the production account builder runs.
    result = choice_accounts.accounts()
    # Then: no peer identity or path was opened.
    assert result.peer is None and looked_up == ["agent", "ops"]
    assert checked == [choice_accounts.MAILBOX_ROOT / "agent"]
