"""No-agent executor of known memory-choice requests, using only the peer's credentials."""
from __future__ import annotations

import os
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path


def _runtime_root() -> Path:
    override = os.environ.get("AUTOPHAGY_REPO_ROOT", "").strip()
    if override:
        return Path(override).expanduser()
    current = Path("/srv/autophagy-agent-current")
    return current if current.exists() else Path("/srv/autophagy-agents")


_REPO_ROOT = _runtime_root()
if (_REPO_ROOT / "automation").is_dir() and str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from automation.interop.approval_directory import DiscordChannelDirectory  # noqa: E402
from automation.interop.reaction_approval import DiscordTransport  # noqa: E402
from automation.memory_relocate.choice_accounts import accounts  # noqa: E402
from automation.memory_relocate.choice_delete import NativeDelete  # noqa: E402
from automation.memory_relocate.choice_notice import notify_peer  # noqa: E402
from automation.memory_relocate.choice_peer import PeerExecutor  # noqa: E402
from automation.memory_relocate.choice_request import parse_request  # noqa: E402
from automation.memory_relocate.choice_snapshot import SnapshotStore  # noqa: E402
from automation.memory_relocate.choice_verify import Verifier  # noqa: E402
from automation.memory_relocate.choices import ChoiceError  # noqa: E402
from automation.memory_relocate.cron.memory_relocate_watch import (  # noqa: E402
    load_env_secrets, owner_id, acquire_single_instance_lock,
)


def run_once(now: datetime) -> int:
    participants = accounts()
    if os.getuid() != participants.peer.uid:
        raise ChoiceError("peer_account_required")
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not token:
        raise ChoiceError("peer_token_missing")
    owner = owner_id()
    root = Path.home() / ".hermes" / "memory-relocate-peer"
    # A private verifier config avoids enabling the peer gateway's free-response
    # behavior when its ordinary interop config has no owner-chat channel.
    config = root / "approval-config.json"
    info = config.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise ChoiceError("peer_approval_config_permissions")
    os.environ["INTEROP_CONFIG"] = str(config)
    transport = DiscordTransport(token, owner)
    directory = DiscordChannelDirectory(token, owner, transport.api, root / "directory.json")
    verifier = Verifier(transport, directory)
    executor = PeerExecutor(
        SnapshotStore(root / "decisions", now),
        NativeDelete(Path.home() / ".hermes" / "memories", root / "deletes", now),
        participants.peer,
    )
    failures = 0
    for name in participants.agent.names():
        if len(name) != 69:
            continue  # Denial markers are not requests and never produce approvals.
        try:
            document = participants.agent.read(name)
            if document is None:
                continue
            request = parse_request(document)
            if name != f"{request.key}.json":
                raise ChoiceError("request_filename")
            withdrawn = participants.agent.read(f"{request.key}.cancel.json") is not None
            snapshot = executor.run(request, verifier, withdrawn=withdrawn)
            if snapshot is not None:
                executor.notify(snapshot, lambda saved: notify_peer(saved, transport))
        except (OSError, ValueError, KeyError, RuntimeError) as error:
            failures += 1
            reason = str(error) if isinstance(error, ChoiceError) else type(error).__name__
            print(f"MEMORY-CHOICES-PEER-BLOCKED request={name[:12]} reason={reason}", file=sys.stderr)
    return 1 if failures else 0


def main() -> int:
    try:
        load_env_secrets(Path.home() / ".env.secrets")  # the peer account's own secrets file
        lock = acquire_single_instance_lock(Path.home() / ".hermes/memory-relocate-peer/watch.lock")
        if lock is None:
            return 0
        with lock:
            return run_once(datetime.now(UTC))
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print(f"MEMORY-CHOICES-PEER-BLOCKED type={type(error).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
