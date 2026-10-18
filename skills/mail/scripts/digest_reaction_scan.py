"""Bounded rotating reaction scan; shares the existing per-card/action SQLite claim."""
from __future__ import annotations

import fcntl
import json
from datetime import UTC, datetime
from typing import Final
from urllib.error import HTTPError

import digest_reaction_actions as actions
import triage_confirm
import triage_gate

CARD_LIMIT: Final = 20


def run(*, now: datetime | None = None) -> int:
    """At most 20 card reads (plus up to two reactor lists each); 429 pauses later scans."""
    current = now or datetime.now(UTC)
    db = triage_gate.db_path()
    db.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    state_path = db.with_suffix(".reaction-scan.json")
    with db.with_suffix(".reaction-scan.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            state = {"cursor": 0, "after": 0}
        if current.timestamp() < state["after"]:
            return 0
        _, cards = actions._recent_cards(current)  # noqa: SLF001 — same three-day window
        if not cards:
            return 0
        start = state["cursor"] % len(cards)
        owner = triage_confirm.owner_id()
        ran = 0
        for offset in range(min(CARD_LIMIT, len(cards))):
            index = (start + offset) % len(cards)
            try:
                ran += actions._act(cards[index], owner, db)  # noqa: SLF001 — one claim implementation
            except HTTPError as error:
                if error.code != 429:
                    raise
                retry_now = now or datetime.now(UTC)
                state["after"] = retry_now.timestamp() + max(
                    20, triage_confirm._retry_after(error),  # noqa: SLF001
                )
                state["cursor"] = index
                break
            state["cursor"] = (index + 1) % len(cards)
        triage_gate.write_json(state_path, state)
        return ran
