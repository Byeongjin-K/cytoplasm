#!/usr/bin/env python3
"""plaud skill CLI — read-only status of the Plaud lifelog sync watcher.

``status`` reads the watcher's frozen state (``~/.hermes/plaud-sync/state.json``) and
reports the per-status counts, the last Plaud poll, and the recordings whose approval
card is still waiting for the owner's ✅. It talks to neither Plaud nor Discord and
writes nothing — the owner asked for a phrase ("plaud 상태") instead of a path to
remember, and this is the whole of what that phrase does.

stdlib only on purpose: the deploy sandbox runs it under a disposable HOME with no
repository on ``sys.path``, and a status reporter that needs the runtime to be healthy
cannot report that the runtime is broken.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Final

STATUSES: Final = ("transcribing", "planned", "posted", "approved", "written", "abandoned")
DEFAULT_STATE: Final = Path.home() / ".hermes" / "plaud-sync" / "state.json"
KST: Final = timezone(timedelta(hours=9))
MARKER: Final = "PLAUD-STATUS"


class StatusError(ValueError):
    """The state file exists but is not a plaud-sync state this reader understands."""


@dataclass(frozen=True, slots=True)
class Pending:
    recording_id: str
    thread_id: str
    note_name: str
    recorded_at: str
    guild_id: str = ""
    thread_url: str | None = None


@dataclass(frozen=True, slots=True)
class Transcribing:
    recording_id: str
    attempts: int
    reason: str
    last_recheck_error: str = ""


@dataclass(frozen=True, slots=True)
class Transcript:
    recording_id: str
    status: str
    path: Path


@dataclass(frozen=True, slots=True)
class StatusSummary:
    last_poll_at: str | None
    counts: tuple[tuple[str, int], ...]
    pending: tuple[Pending, ...]
    approved: tuple[tuple[str, str], ...]
    total: int
    transcribing: tuple[Transcribing, ...] = ()
    transcripts_dir: Path | None = None
    transcripts: tuple[Transcript, ...] = ()


def _text(raw: dict[str, object], key: str, fallback: str = "") -> str:
    value = raw.get(key)
    return value if isinstance(value, str) and value else fallback


def _attempts(raw: dict[str, object]) -> int:
    value = raw.get("transcribe_attempts", 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _transcript(raw: dict[str, object], transcripts_dir: Path) -> Path | None:
    stem = Path(_text(raw, "note_relpath")).stem
    candidate = transcripts_dir / f"{stem}.md"
    return candidate if stem and candidate.is_file() else None


def _thread_url(guild_id: str, thread_id: str) -> str | None:
    """샌드박스용 인라인 예외 — 공유 discord_link와의 동등성은 테스트로 고정한다."""
    if not all(value.isascii() and value.isdecimal() and value.strip("0")
               for value in (guild_id, thread_id)):
        return None
    return f"https://discord.com/channels/{guild_id}/{thread_id}"


def summarize(payload: object, transcripts_dir: Path | None = None) -> StatusSummary:
    """Fold the raw state into counts and the owner-facing pending list (fail-closed)."""
    if not isinstance(payload, dict):
        raise StatusError("state root is not an object")
    records = payload.get("records")
    if not isinstance(records, dict):
        raise StatusError("records is not an object")
    last_poll_at = payload.get("last_poll_at")
    if last_poll_at is not None and not isinstance(last_poll_at, str):
        raise StatusError("last_poll_at is not a string")
    counts = dict.fromkeys(STATUSES, 0)
    pending: list[Pending] = []
    approved: list[tuple[str, str]] = []
    transcribing: list[Transcribing] = []
    transcripts: list[Transcript] = []
    for key, raw in records.items():
        if not isinstance(raw, dict):
            raise StatusError(f"record {key} is not an object")
        status = raw.get("status")
        if not isinstance(status, str) or status not in counts:
            raise StatusError(f"record {key} has an unknown status: {status!r}")
        counts[status] += 1
        if status == "posted":
            guild_id = _text(raw, "approval_guild_id")
            pending.append(
                Pending(
                    recording_id=_text(raw, "recording_id", str(key)),
                    thread_id=_text(raw, "approval_thread_id", _text(raw, "channel_id", "?")),
                    note_name=Path(_text(raw, "note_relpath")).name,
                    recorded_at=_text(raw, "recorded_at"),
                    guild_id=guild_id,
                    thread_url=_thread_url(guild_id, _text(raw, "approval_thread_id")),
                )
            )
        elif status == "approved":
            approved.append((_text(raw, "recording_id", str(key)), _text(raw, "last_block_reason")))
        elif status == "transcribing":
            transcribing.append(
                Transcribing(
                    _text(raw, "recording_id", str(key)), _attempts(raw), _text(raw, "last_block_reason"),
                    last_recheck_error=_text(raw, "last_recheck_error"),
                )
            )
        if transcripts_dir is not None and (path := _transcript(raw, transcripts_dir)) is not None:
            transcripts.append(Transcript(_text(raw, "recording_id", str(key)), status, path))
    return StatusSummary(
        last_poll_at,
        tuple(counts.items()),
        tuple(pending),
        tuple(approved),
        len(records),
        tuple(transcribing),
        transcripts_dir,
        tuple(transcripts),
    )


def _kst(iso: str) -> str:
    try:
        moment = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return "시각 해석 불가"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(KST).strftime("%Y-%m-%d %H:%M KST")


def render(summary: StatusSummary) -> str:
    poll = summary.last_poll_at
    lines = [
        f"{MARKER} state=present",
        f"- 마지막 Plaud 폴: {poll} ({_kst(poll)})" if poll else "- 마지막 Plaud 폴: 아직 없음",
        f"- 레코드 {summary.total}건: " + " · ".join(f"{k} {v}" for k, v in summary.counts),
        f"- 승인 대기(posted) {len(summary.pending)}건:",
    ]
    lines.extend(
        # This standalone sandbox CLI has no interop runtime; only human text is masked.
        f"  - {p.recording_id} · 스레드 "
        + (f"[승인 스레드](<{p.thread_url}>)" if p.thread_url else p.thread_id)
        + f" · {p.note_name}"
        for p in summary.pending
    )
    if summary.approved:
        lines.append(f"- 저장 대기(approved) {len(summary.approved)}건:")
        lines.extend(
            f"  - {rid} · 사유 {reason or '없음(다음 틱에 저장)'}" for rid, reason in summary.approved
        )
    if summary.transcribing:
        lines.append(f"- 전사 대기(transcribing) {len(summary.transcribing)}건:")
        lines.extend(
            f"  - {t.recording_id} · 시도 {t.attempts} · 사유 {t.reason or '없음(다음 틱에 전사)'}"
            + (f" · 마지막 클라우드 재확인 오류 {t.last_recheck_error}" if t.last_recheck_error else "")
            for t in summary.transcribing
        )
    if summary.transcripts:
        lines.append(f"- 로컬 전사본 {len(summary.transcripts)}건 ({summary.transcripts_dir}):")
        lines.extend(f"  - {t.recording_id} · {t.path.name}" for t in summary.transcripts)
    return "\n".join(lines)


def _payload(summary: StatusSummary) -> dict[str, object]:
    return {
        "state": "present",
        "last_poll_at": summary.last_poll_at,
        "total": summary.total,
        "counts": dict(summary.counts),
        "approved": [{"recording_id": r, "reason": why} for r, why in summary.approved],
        "transcribing": [
            {"recording_id": t.recording_id, "attempts": t.attempts, "reason": t.reason,
             **({"last_recheck_error": t.last_recheck_error} if t.last_recheck_error else {})}
            for t in summary.transcribing
        ],
        "transcripts_dir": str(summary.transcripts_dir) if summary.transcripts_dir else None,
        "transcripts": [
            {"recording_id": t.recording_id, "status": t.status, "path": str(t.path)}
            for t in summary.transcripts
        ],
        "pending": [
            {
                "recording_id": p.recording_id,
                "thread_id": p.thread_id,
                "thread_url": p.thread_url,
                "note_name": p.note_name,
                "recorded_at": p.recorded_at,
            }
            for p in summary.pending
        ],
    }


def load(path: Path) -> StatusSummary | None:
    """``None`` when the watcher has not run yet; ``StatusError`` when the file is unreadable."""
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise StatusError(f"{path.name}: {error}") from error
    return summarize(payload, path.parent / "transcripts")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="plaud_cli.py", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status", help="plaud-sync 워처 상태(읽기 전용)")
    _ = status.add_argument("--state", type=Path, default=DEFAULT_STATE)
    _ = status.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    try:
        summary = load(args.state)
    except StatusError as error:
        print(f"{MARKER} state=unreadable reason={error}", file=sys.stderr)
        return 2
    if summary is None:
        print(json.dumps({"state": "absent"}) if args.as_json else f"{MARKER} state=absent")
        return 0
    print(json.dumps(_payload(summary), ensure_ascii=False) if args.as_json else render(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
