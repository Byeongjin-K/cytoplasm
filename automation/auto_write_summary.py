"""21:30 KST summary; late writes carry forward and failed sends do not advance."""
from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from automation import auto_write, owner_notice
from automation.credential_rotation.files import atomic_write


def summarize(
    now: datetime, *, root: Path | None = None, send: Callable[[str], bool] | None = None,
) -> bool:
    root = root or auto_write.state_root()
    local = now.astimezone(auto_write.KST)
    if (local.hour, local.minute) < (21, 30):
        return False
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Serialize send + watermark against both other summaries and ledger appends.
    with (root / "ledger.lock").open("a", encoding="utf-8") as lock:
        os.fchmod(lock.fileno(), 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = root / "summary.json"
        state = json.loads(path.read_text()) if path.exists() else {"days": [], "rows": 0}
        day = local.date().isoformat()
        if day in state["days"]:
            return False
        rows = auto_write._rows(root)
        pending = rows[state["rows"]:]
        if not pending:
            return False
        content = auto_write.summary_text(pending, day=day)
        if send is None:
            sent = owner_notice.notify_owner(content)
        else:
            sent = send(content)
        if not sent:
            return False
        atomic_write(path, json.dumps({"days": [*state["days"], day], "rows": len(rows)}).encode())
        return True
