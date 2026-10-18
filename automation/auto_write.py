"""Owner-command writes: per-kind release policy and one private receipt ledger.

Q231 enables Plaud only. Q233's other producers can reuse record(kind, entry)
when they are wired in a later release; an unknown switch is never enabled.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

KINDS: Final = {"plaud": "Plaud 노트"}
SURFACE_PREFIX: Final = "auto-write:"
CARD_LINES: Final = {
    "plaud": "Plaud 노트 자동 쓰기: 녹음 노트를 카드 없이 옵시디언에 씀(쓴 건 매일 21:30 요약)",
}
KST: Final = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True, slots=True)
class Entry:
    """A verified write receipt, never a document body."""

    source_id: str
    path: str
    revision: str
    recorded_at: str
    written_at: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def state_root(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".hermes/auto-write"


def surface(kind: str) -> tuple[str, str]:
    if kind not in KINDS:
        raise ValueError("unknown auto-write kind")
    return SURFACE_PREFIX + kind, hashlib.sha256(b"on").hexdigest()


def release_kinds(home: Path) -> tuple[str, ...]:
    """Only exact `on` opts in; a flag for an unwired producer is refused."""
    directory = home / ".hermes/release-flags"
    for path in directory.glob("auto-write-*"):
        if path.name.removeprefix("auto-write-") not in KINDS:
            raise ValueError("unknown auto-write release switch")
    enabled_kinds: list[str] = []
    for kind in KINDS:
        try:
            value = (directory / f"auto-write-{kind}").read_bytes()
        except FileNotFoundError:
            continue
        if value != b"on":
            raise ValueError("AUTO-WRITE-REFUSED: expected the literal word on only")
        enabled_kinds.append(kind)
    return tuple(sorted(enabled_kinds))


def enabled(kind: str, *, home: Path | None = None) -> bool:
    """A node reads only its release-written setting, never host flags."""
    surface(kind)
    try:
        value = (state_root(home) / f"{kind}.mode").read_bytes()
    except FileNotFoundError:
        return False
    if value != b"on":
        raise ValueError("AUTO-WRITE-REFUSED: malformed node mode")
    return True


def _rows(root: Path) -> list[dict[str, str]]:
    try:
        lines = (root / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    rows = []
    for line in lines:
        row = json.loads(line)
        if (not isinstance(row, dict) or set(row) != {"kind", *Entry.__dataclass_fields__}
                or not all(isinstance(value, str) for value in row.values())
                or row["kind"] not in KINDS):
            raise ValueError("invalid auto-write ledger")
        rows.append(row)
    return rows


def record(kind: str, entry: Entry, *, root: Path | None = None) -> None:
    """Append once per kind/id/commit, under the same lock the summary reads."""
    surface(kind)
    root = root or state_root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (root / "ledger.lock").open("a", encoding="utf-8") as lock:
        os.fchmod(lock.fileno(), 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        row = {"kind": kind, **entry.as_dict()}
        if any(all(old[key] == row[key] for key in ("kind", "source_id", "revision"))
               for old in _rows(root)):
            return
        fd = os.open(root / "ledger.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as ledger:
            os.fchmod(ledger.fileno(), 0o600)
            ledger.write(json.dumps(row, ensure_ascii=False) + "\n")
            ledger.flush()
            os.fsync(ledger.fileno())


def summary_text(rows: list[dict[str, str]]) -> str:
    lines = ["## 📝 오늘 자동으로 쓴 것"]
    for kind, label in KINDS.items():
        selected = [row for row in rows if row["kind"] == kind]
        if not selected:
            continue
        lines.extend(("", f"### {label}"))
        for row in selected:
            stamp = datetime.fromisoformat(row["recorded_at"]).astimezone(KST)
            path = " ".join(row["path"].split())
            lines.append(f"- {stamp:%m-%d %H:%M} · {path}")
    return "\n".join(lines)


def summarize(
    now: datetime, *, root: Path | None = None, send: Callable[[str], bool] | None = None,
) -> bool:
    """Run the shared summary (lazy import keeps release planning stdlib-only)."""
    from automation.auto_write_summary import summarize as run

    return run(now, root=root, send=send)
