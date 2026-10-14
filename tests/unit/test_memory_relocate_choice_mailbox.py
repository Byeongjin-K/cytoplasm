"""Mailbox permissions are independently checked; shared bytes are not approval."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from automation.memory_relocate.choice_files import Mailbox
from automation.memory_relocate.choices import ChoiceError

NAME = "a" * 64 + ".json"


def _mailbox(root: Path) -> Mailbox:
    root.mkdir(mode=0o750)
    return Mailbox(root, os.getuid(), os.getgid())


def test_mailbox_when_published_is_readable_without_world_access(tmp_path: Path) -> None:
    # Given: a provisioned account-owned directory.
    mailbox = _mailbox(tmp_path / "box")
    # When: a request is published atomically.
    mailbox.publish(NAME, "synthetic request")
    # Then: the other group participant may read; world/group writers may not.
    assert mailbox.read(NAME) == "synthetic request"
    assert (mailbox.path / NAME).stat().st_mode & 0o777 == 0o640


def test_mailbox_when_receipt_is_owned_by_other_account_is_rejected(tmp_path: Path) -> None:
    # Given: an agent-owned file offered as a peer-owned receipt.
    own = _mailbox(tmp_path / "box")
    own.publish(NAME, "forged approval")
    peer = Mailbox(own.path, os.getuid() + 1, os.getgid())
    # When / Then: even syntactically valid receipt text is not trusted.
    with pytest.raises(ChoiceError, match="mailbox_permissions"):
        peer.read(NAME)


@pytest.mark.parametrize("mode", [0o644, 0o660, 0o666])
def test_mailbox_when_file_permissions_are_widened_is_rejected(tmp_path: Path, mode: int) -> None:
    # Given: a mailbox file with unauthorized readers or writers.
    mailbox = _mailbox(tmp_path / "box")
    mailbox.publish(NAME, "private request")
    (mailbox.path / NAME).chmod(mode)
    # When / Then: no request contents cross the trust boundary.
    with pytest.raises(ChoiceError, match="file_permissions"):
        mailbox.read(NAME)


def test_mailbox_when_file_is_symlink_refuses_target(tmp_path: Path) -> None:
    # Given: a symlink to a valid-looking external file.
    mailbox = _mailbox(tmp_path / "box")
    target = tmp_path / "target"
    target.write_text("forged", encoding="utf-8")
    (mailbox.path / NAME).symlink_to(target)
    # When / Then: O_NOFOLLOW prevents trusting it.
    with pytest.raises(OSError):
        mailbox.read(NAME)


def test_mailbox_when_name_contains_traversal_is_rejected(tmp_path: Path) -> None:
    # Given: an attacker-chosen pathname instead of the derived request identity.
    mailbox = _mailbox(tmp_path / "box")
    # When / Then: no traversal is attempted.
    with pytest.raises(ChoiceError, match="mailbox_name"):
        mailbox.read("../outside.json")


def test_mailbox_when_unprovisioned_never_creates_shared_paths(tmp_path: Path) -> None:
    # Given: root has not prepared the mailbox.
    mailbox = Mailbox(tmp_path / "absent", os.getuid(), os.getgid())
    # When / Then: runtime refuses rather than winning a predictable-name race.
    with pytest.raises(FileNotFoundError):
        mailbox.publish(NAME, "request")
    assert not mailbox.path.exists()
