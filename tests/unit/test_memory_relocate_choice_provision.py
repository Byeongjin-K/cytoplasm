from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from automation.memory_relocate import choice_accounts, peer_approval_config
from automation.memory_relocate.choice_files import Mailbox
from automation.memory_relocate.choices import ChoiceError

_GROUP = os.getgid()
_CHANNEL = "123456789012345678"


def _handoff(tmp_path: Path) -> Path:
    handoff = tmp_path / "handoff"
    handoff.mkdir()
    handoff.chmod(0o2750)
    return handoff


def test_ops_creates_one_sticky_group_writable_directory_and_never_repairs_it(tmp_path: Path) -> None:
    # Given: the existing ops hand-off directory.
    shared = _handoff(tmp_path) / "memory-choices"

    # When: ops provisions twice.
    choice_accounts.provision_shared(shared, _GROUP)
    choice_accounts.provision_shared(shared, _GROUP)

    # Then: setgid + sticky + group-writable, nothing for others, and it passes the exact check.
    info = shared.lstat()
    assert stat.S_IMODE(info.st_mode) == 0o3770
    assert info.st_gid == _GROUP
    choice_accounts._handoff_owned(shared, os.getuid(), _GROUP, 0o3770)  # pyright: ignore[reportPrivateUsage]

    # And: a shared directory someone loosened is refused, not quietly repaired.
    shared.chmod(0o777)
    choice_accounts.provision_shared(shared, _GROUP)
    with pytest.raises(ChoiceError, match="handoff_permissions"):
        choice_accounts._handoff_owned(shared, os.getuid(), _GROUP, 0o3770)  # pyright: ignore[reportPrivateUsage]


def test_handoff_parent_must_not_be_writable_by_the_group_or_be_a_link(tmp_path: Path) -> None:
    handoff = _handoff(tmp_path)
    handoff.chmod(0o2770)
    with pytest.raises(ChoiceError, match="handoff_permissions"):
        choice_accounts._handoff_owned(handoff, os.getuid(), _GROUP)  # pyright: ignore[reportPrivateUsage]
    link = tmp_path / "link"
    link.symlink_to(handoff)
    with pytest.raises(ChoiceError, match="handoff_owner_required"):
        choice_accounts._handoff_owned(link, os.getuid(), _GROUP)  # pyright: ignore[reportPrivateUsage]


def test_topology_still_requires_a_root_owned_place_above_the_handoff(tmp_path: Path) -> None:
    handoff = _handoff(tmp_path)
    with pytest.raises(ChoiceError, match="root_owned_path_required"):
        choice_accounts.check_topology(handoff, handoff / "memory-choices", os.getuid(), _GROUP)


def test_each_executor_creates_only_its_own_exact_mailbox(tmp_path: Path) -> None:
    shared = _handoff(tmp_path) / "memory-choices"
    choice_accounts.provision_shared(shared, _GROUP)
    mailbox = Mailbox(shared / "agent", os.getuid(), _GROUP)

    choice_accounts.provision_mailbox(mailbox)
    choice_accounts.provision_mailbox(mailbox)

    info = mailbox.path.lstat()
    assert (stat.S_IMODE(info.st_mode), info.st_gid) == (0o750, _GROUP)


def test_a_squatted_or_linked_mailbox_is_refused(tmp_path: Path) -> None:
    shared = _handoff(tmp_path) / "memory-choices"
    choice_accounts.provision_shared(shared, _GROUP)
    (shared / "peer").symlink_to(tmp_path)
    with pytest.raises(ChoiceError, match="mailbox_permissions"):
        choice_accounts.provision_mailbox(Mailbox(shared / "peer", os.getuid(), _GROUP))


def test_peer_config_holds_only_the_channel_and_is_private(tmp_path: Path) -> None:
    path = tmp_path / "peer" / "approval-config.json"

    assert peer_approval_config.write_config(_CHANNEL, path) is True
    assert peer_approval_config.write_config(_CHANNEL, path) is False

    assert json.loads(path.read_text(encoding="utf-8")) == {"agent_chat_channel_id": _CHANNEL}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(ValueError, match="malformed"):
        _ = peer_approval_config.write_config("12; rm -rf /", path)


def test_agent_channel_is_read_only_when_well_formed(tmp_path: Path) -> None:
    interop = tmp_path / "config.json"
    _ = interop.write_text(json.dumps({"agent_chat_channel_id": _CHANNEL}), encoding="utf-8")
    assert peer_approval_config.agent_channel(interop) == _CHANNEL
    _ = interop.write_text(json.dumps({"owner_id": _CHANNEL}), encoding="utf-8")
    with pytest.raises(ValueError, match="missing or malformed"):
        _ = peer_approval_config.agent_channel(interop)
