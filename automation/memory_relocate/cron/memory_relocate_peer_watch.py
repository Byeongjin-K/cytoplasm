"""No-agent local executor of the agent's frozen memory-choice decisions."""
from __future__ import annotations

import os
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

from automation.memory_relocate.choice_accounts import accounts  # noqa: E402
from automation.memory_relocate.choice_delete import NativeDelete  # noqa: E402
from automation.memory_relocate.choice_peer import PeerExecutor  # noqa: E402
from automation.memory_relocate.choice_relay import AgentDecision  # noqa: E402
from automation.memory_relocate.choice_request import parse_request  # noqa: E402
from automation.memory_relocate.choice_snapshot import SnapshotStore  # noqa: E402
from automation.memory_relocate.choices import ChoiceError  # noqa: E402
from automation.memory_relocate.cron.memory_relocate_watch import (  # noqa: E402
    load_env_secrets, acquire_single_instance_lock,
)


def run_once(now: datetime) -> int:
    participants = accounts()
    if os.getuid() != participants.peer.uid:
        raise ChoiceError("peer_account_required")
    root = Path.home() / ".hermes" / "memory-relocate-peer"
    reader = AgentDecision(participants.agent)
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
            executor.run(request, reader, withdrawn=withdrawn)
        except (OSError, ValueError, KeyError, RuntimeError) as error:
            failures += 1
            reason = str(error) if isinstance(error, ChoiceError) else type(error).__name__
            print(f"MEMORY-CHOICES-PEER-BLOCKED request={name[:12]} reason={reason}", file=sys.stderr)
    return 1 if failures else 0


def main() -> int:
    try:
        load_env_secrets(Path.home() / ".env.secrets")  # Account-local runtime path settings.
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
