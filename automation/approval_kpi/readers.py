"""One reader per ledger format whose shape is certain from the producing source.

Two formats are read today:

* the skill-gate approval log — newline-delimited JSON appended by
  ``automation/skill_gate.py`` (``APPROVAL_LOG``, line 52; record built at line 198)
  and by the mail Gmail gate through ``gmail_approval_gate.approval_record``;
* the posting journal — ``<slug>.posting.json`` reservations written by
  ``PostingJournal.reserve`` in ``automation/interop/approval_lease.py``.

A record that this module cannot interpret with certainty is skipped and counted by
reason instead of being guessed at. Readers only ever read.
"""
from __future__ import annotations

import json
from collections.abc import Iterator, MutableMapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from automation.approval_kpi.model import ApprovalEvent

#: ``automation/supply_chain_remind.py`` restores a request's post time from the Discord
#: message id this way (line 91); the approval log stores no separate request time.
_DISCORD_EPOCH_MS: Final = 1_420_070_400_000

#: ``action`` values whose kind is stated by the producing source. ``skill.deploy`` is
#: written by ``automation/skill_gate.py`` line 198; ``external_effect.approval`` by
#: ``skills/mail/scripts/gmail_approval_gate.py`` ``approval_record``.
_ACTION_KINDS: Final = {
    "skill.deploy": "skill-deploy",
    "external_effect.approval": "external-effect",
}

#: ``ApprovalKind`` values (automation/interop/approval_surface.py lines 36-51). A
#: posting-journal key whose prefix is not one of these has an undeterminable kind.
_KNOWN_KINDS: Final = frozenset({
    "mail-reply", "mail-compose", "budget-mail", "patent-export", "repair", "calendar",
    "coordination", "wiki", "skill-deploy", "skill-attest", "skill-publish",
    "skill-submit", "managed-activate", "release", "obsidian-write", "todo",
    "project-board",
})

_INJECTED_MARKERS: Final = ("e2e", "inject")


def _count(skips: MutableMapping[str, int] | None, reason: str) -> None:
    if skips is not None:
        skips[reason] = skips.get(reason, 0) + 1


def _moment(raw: object) -> datetime | None:
    """Parse an ISO-8601 instant; a naive one is read as UTC, as every writer emits UTC."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _requested_at(message_id: object) -> datetime | None:
    """The approval message's post time — the moment the owner was actually asked."""
    if not isinstance(message_id, str) or not message_id.isascii() or not message_id.isdigit():
        return None
    snowflake = int(message_id)
    if snowflake.bit_length() > 64:
        return None
    return datetime.fromtimestamp(
        ((snowflake >> 22) + _DISCORD_EPOCH_MS) / 1000, tz=UTC
    )


def read_skill_gate_log(
    path: Path, skips: MutableMapping[str, int] | None = None
) -> Iterator[ApprovalEvent]:
    """Yield one event per interpretable line of an ``approvals.jsonl`` ledger."""
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        return
    except OSError:
        _count(skips, "unreadable")
        return
    for line in raw.splitlines():
        if not line.strip():
            continue
        event = _skill_gate_event(line, skips)
        if event is not None:
            yield event


def _skill_gate_event(
    line: str, skips: MutableMapping[str, int] | None
) -> ApprovalEvent | None:
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        _count(skips, "malformed")
        return None
    if not isinstance(record, dict):
        _count(skips, "malformed")
        return None
    kind = _ACTION_KINDS.get(str(record.get("action", "")))
    if kind is None:
        _count(skips, "unknown-action")
        return None
    approval = record.get("approval")
    if not isinstance(approval, dict):
        _count(skips, "malformed")
        return None
    method = str(approval.get("method", ""))
    if any(marker in method for marker in _INJECTED_MARKERS):
        _count(skips, "e2e-injected")
        return None
    created_at = _requested_at(approval.get("message_id"))
    if created_at is None:
        _count(skips, "no-request-time")
        return None
    decided_at = _moment(record.get("timestamp"))
    if decided_at is None:
        _count(skips, "malformed-timestamp")
        return None
    result = record.get("result")
    decision = str(result.get("status", "")) if isinstance(result, dict) else ""
    return ApprovalEvent(
        kind=kind,
        surface=str(approval.get("channel", "")) or "unknown",
        created_at=created_at,
        decided_at=decided_at,
        decision=decision or "unknown",
        manual_reaction=method == "manual_reaction",
        request_key=str(record.get("target_id", "")) or "unknown",
    )


def read_posting_journal(
    path: Path, skips: MutableMapping[str, int] | None = None
) -> Iterator[ApprovalEvent]:
    """Yield one undecided event per ``*.posting.json`` reservation in ``path``."""
    if not path.is_dir():
        return
    for reservation in sorted(path.glob("*.posting.json")):
        event = _journal_event(reservation, skips)
        if event is not None:
            yield event


def _journal_event(
    path: Path, skips: MutableMapping[str, int] | None
) -> ApprovalEvent | None:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _count(skips, "malformed")
        return None
    if not isinstance(record, dict):
        _count(skips, "malformed")
        return None
    key = str(record.get("key", ""))
    kind = key.split(":", 1)[0]
    if kind not in _KNOWN_KINDS:
        _count(skips, "unknown-kind")
        return None
    created_at = _moment(record.get("at"))
    if created_at is None:
        _count(skips, "no-request-time")
        return None
    return ApprovalEvent(
        kind=kind,
        surface="unknown",
        created_at=created_at,
        decided_at=None,
        decision="pending",
        manual_reaction=False,
        request_key=key,
    )


def read_root(
    root: Path, skips: MutableMapping[str, int] | None = None
) -> Iterator[ApprovalEvent]:
    """Yield every interpretable event under ``root``; a missing root yields nothing."""
    if not root.is_dir():
        return
    for log in sorted(root.rglob("approvals.jsonl")):
        yield from read_skill_gate_log(log, skips)
    journals = sorted({path.parent for path in root.rglob("*.posting.json")})
    for journal in journals:
        yield from read_posting_journal(journal, skips)
