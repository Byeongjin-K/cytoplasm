"""Mailbox topology for the two unprivileged memory executors, provisioned without root.

The owner asked that an approved release needs no node work (2026-10-14 22:29), so the
release provisions this itself with the accounts it already runs as. The parent is the ops
hand-off directory that already serves the repair report queue (ops-owned, service group,
setgid, group read-only, opened by fixed file names only). Inside it, ops keeps one
shared directory that is group-writable but sticky: each executor creates only its own
mailbox there and cannot rename or remove the other's. Every check stays fail-closed.
"""
from __future__ import annotations

import grp
import os
import pwd
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from automation.node_config import SYSTEM_NODE_CONFIG_PATH, load_node_config

from .choice_files import Mailbox
from .choices import ChoiceError
from .peer_mode import peer_retired

HANDOFF_ROOT: Final = Path("/srv/autophagy-repair-report-queue")
MAILBOX_ROOT: Final = HANDOFF_ROOT / "memory-choices"
_SHARED_MODE: Final = 0o3770  # setgid keeps the service group; sticky stops cross-account rename


@dataclass(frozen=True, slots=True)
class Accounts:
    agent: Mailbox
    peer: Mailbox | None


def _root_owned(path: Path) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ChoiceError("root_owned_path_required")


def _handoff_owned(path: Path, owner: int, group: int, mode: int | None = None) -> None:
    """The ops hand-off parents: right owner and group, and nobody else may rewrite them."""
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or (
        info.st_uid, info.st_gid,
    ) != (owner, group):
        raise ChoiceError("handoff_owner_required")
    bits = stat.S_IMODE(info.st_mode)
    if (mode is None and bits & 0o022) or (mode is not None and bits != mode):
        raise ChoiceError("handoff_permissions")


def check_topology(handoff: Path, shared: Path, ops: int, group: int) -> None:
    _root_owned(handoff.parent)
    _handoff_owned(handoff, ops, group)
    _handoff_owned(shared, ops, group, _SHARED_MODE)


def provision_shared(shared: Path, group: int) -> None:
    """Run as ops: create the sticky, group-writable directory once; never repair it."""
    try:
        shared.mkdir(mode=0o770)
    except FileExistsError:
        return
    os.chown(shared, -1, group)
    shared.chmod(_SHARED_MODE)


def provision_mailbox(mailbox: Mailbox) -> None:
    """Run as the mailbox's own account: create it once, then require the exact shape."""
    try:
        mailbox.path.mkdir(mode=0o750)
    except FileExistsError:
        pass
    else:
        os.chown(mailbox.path, -1, mailbox.gid)
        mailbox.path.chmod(0o750)
    mailbox.check()


def accounts(*, provision: bool = False) -> Accounts:
    """No home-config fallback may choose whose receipt or memory is trusted."""
    _root_owned(SYSTEM_NODE_CONFIG_PATH.parent)
    _root_owned(SYSTEM_NODE_CONFIG_PATH)
    config = load_node_config(SYSTEM_NODE_CONFIG_PATH)
    agent = pwd.getpwnam(config.agent_account)
    retired = peer_retired()
    peer = None if retired else pwd.getpwnam(config.peer_account)
    ops = pwd.getpwnam(config.ops_account).pw_uid
    group = grp.getgrnam(config.service_group).gr_gid
    if agent.pw_uid == 0 or (peer is not None and (
        agent.pw_uid == peer.pw_uid or peer.pw_uid == 0
    )):
        raise ChoiceError("distinct_service_accounts_required")
    for account in (agent,) if peer is None else (agent, peer):
        if group not in os.getgrouplist(account.pw_name, account.pw_gid):
            raise ChoiceError("service_group_membership_required")
    result = Accounts(Mailbox(MAILBOX_ROOT / "agent", agent.pw_uid, group),
                      None if peer is None else Mailbox(MAILBOX_ROOT / "peer", peer.pw_uid, group))
    if provision:
        uid = os.geteuid()
        if uid == ops:
            _handoff_owned(HANDOFF_ROOT, ops, group)
            provision_shared(MAILBOX_ROOT, group)
            check_topology(HANDOFF_ROOT, MAILBOX_ROOT, ops, group)
            return result
        check_topology(HANDOFF_ROOT, MAILBOX_ROOT, ops, group)
        own = {box.uid: box for box in (result.agent, result.peer) if box is not None}.get(uid)
        if own is None:
            raise ChoiceError("provision_account")
        provision_mailbox(own)
        return result
    check_topology(HANDOFF_ROOT, MAILBOX_ROOT, ops, group)
    result.agent.check()
    if result.peer is not None:
        result.peer.check()
    return result


def main() -> int:
    try:
        if sys.argv[1:] != ["--provision"]:
            raise ChoiceError("usage: python3 -m automation.memory_relocate.choice_accounts --provision")
        _ = accounts(provision=True)
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print(f"MEMORY-CHOICES-PROVISION-REFUSED type={type(error).__name__}", file=sys.stderr)
        return 2
    print("MEMORY-CHOICES-PROVISIONED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
