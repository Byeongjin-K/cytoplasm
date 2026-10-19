"""P5: a release-written explicit mode removes the peer dependency, never owner checks."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from automation.memory_relocate import choice_accounts, peer_mode
from automation.memory_relocate.choice_files import Mailbox
from automation.memory_relocate.choices import ChoiceError


class _User:
    def __init__(self, name: str, uid: int) -> None:
        self.pw_name = name
        self.pw_uid = uid
        self.pw_gid = 100


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
