"""One outage/recovery notice per episode; retries within one worker are one tick."""
from __future__ import annotations

import fcntl
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from automation.credential_rotation.files import atomic_write


def notify(recovered: bool) -> bool:
    from automation import owner_notice
    from automation.interop.owner_message import Action, OwnerMessage, Periodic, Ref

    here = Ref(scope="none")
    text = "이 PC 대신 호출: 회복됨" if recovered else "이 PC 대신 호출: 안 닿음 — 밤 배치 작업이 보류되고 있습니다"
    now = datetime.now(timezone(timedelta(hours=9)))
    return owner_notice.notify_owner(message=OwnerMessage(
        subject_key="omo-relay", subject="배치 모델 연결", fact=text, location=here,
        owner=Action("none"), agent_next=None, recovery="not_applicable",
        detail=Periodic(now, now), render_version="owner-ko-v2",
    ))


def record_result(home: Path, success: bool) -> None:
    """Best effort only: notice/state errors cannot hide a model error or answer."""
    root = home / ".config/autophagy/omo-relay"
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(root / "availability.lock", os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = root / "availability.json"
            state = json.loads(path.read_text()) if path.exists() else {}
            now = time.time()
            # Timer/cron workers are separate processes. Multiple calls in the same
            # worker and the client's transport retry must not count as extra ticks.
            start = Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()[19]
            tick = f"{os.getpid()}:{start}"
            if success:
                if state.get("notified") and not notify(True):
                    return
                state = {}
            else:
                first = state.setdefault("first", now)
                ticks = state.setdefault("ticks", [])
                if tick not in ticks and len(ticks) < 3:
                    ticks.append(tick)
                if (len(ticks) >= 3 or now - first >= 1800) and not state.get("notified"):
                    state["notified"] = notify(False)
            atomic_write(path, json.dumps(state).encode())
    except (OSError, ValueError, TypeError, KeyError):
        print("OMO-RELAY-AVAILABILITY-FAIL", file=sys.stderr)
