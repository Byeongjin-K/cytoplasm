"""Root-owned topology for the two unprivileged memory executors."""
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

MAILBOX_ROOT: Final = Path("/var/lib/autophagy/memory-choices")


@dataclass(frozen=True, slots=True)
class Accounts:
    agent: Mailbox
    peer: Mailbox


def _root_owned(path: Path) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ChoiceError("root_owned_path_required")


def accounts(*, provision: bool = False) -> Accounts:
    """No home-config fallback may choose whose receipt or memory is trusted."""
    _root_owned(SYSTEM_NODE_CONFIG_PATH.parent)
    _root_owned(SYSTEM_NODE_CONFIG_PATH)
    config = load_node_config(SYSTEM_NODE_CONFIG_PATH)
    agent = pwd.getpwnam(config.agent_account)
    peer = pwd.getpwnam(config.peer_account)
    group = grp.getgrnam(config.service_group).gr_gid
    if agent.pw_uid == peer.pw_uid or 0 in (agent.pw_uid, peer.pw_uid):
        raise ChoiceError("distinct_service_accounts_required")
    for account in (agent, peer):
        if group not in os.getgrouplist(account.pw_name, account.pw_gid):
            raise ChoiceError("service_group_membership_required")
    result = Accounts(Mailbox(MAILBOX_ROOT / "agent", agent.pw_uid, group),
                      Mailbox(MAILBOX_ROOT / "peer", peer.pw_uid, group))
    if provision:
        if os.geteuid() != 0:
            raise ChoiceError("provision_requires_root")
        for directory in (MAILBOX_ROOT.parent, MAILBOX_ROOT):
            directory.mkdir(mode=0o755, exist_ok=True)
            _root_owned(directory)
        for mailbox in (result.agent, result.peer):
            try:
                mailbox.path.mkdir(mode=0o750)
            except FileExistsError:
                mailbox.check()
            else:
                os.chown(mailbox.path, mailbox.uid, mailbox.gid)
                mailbox.path.chmod(0o750)
    _root_owned(MAILBOX_ROOT.parent)
    _root_owned(MAILBOX_ROOT)
    result.agent.check()
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
