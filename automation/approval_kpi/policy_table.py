"""Static kind-level TTL / reminder policy, derived by reading the constants.

Every row cites the ``file:line`` it was read from. A cell that the source does not
state is ``None`` with an ``UNKNOWN:<file inspected>`` citation — this table never
infers a policy from a sibling kind, from a doc, or from a plausible default.

Two cells deserve their footnote:

* ``mail-reply``'s TTL is the Gmail send gate's approval expiry stamped on the record
  (``expires_at``), not a request-expiry in the triage store, which has none.
* ``skill-deploy``'s reminder is the supply-chain tick that claims reminder slots for
  unanswered deploy approvals; the deploy gate itself schedules nothing.
* ``release``'s reminder is the same supply-chain tick for unanswered release cards.

``memory_relocate`` is intentionally absent: its approval kind comes from the payload
(``automation/memory_relocate/model.py:193``), so no static kind can be named for it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class PolicyEntry:
    """One kind's request expiry and reminder policy, with its provenance."""

    kind: str
    ttl_seconds: int | None
    ttl_source: str
    reminder: bool | None
    reminder_source: str
    note: str = ""

    @property
    def ttl_text(self) -> str:
        return "UNKNOWN" if self.ttl_seconds is None else str(self.ttl_seconds)

    @property
    def reminder_text(self) -> str:
        if self.reminder is None:
            return "UNKNOWN"
        return "yes" if self.reminder else "no"


POLICY_TABLE: Final[tuple[PolicyEntry, ...]] = (
    PolicyEntry(
        kind="todo",
        ttl_seconds=86_400,
        ttl_source="skills/todo/scripts/todo_approval_store.py:15",
        reminder=True,
        reminder_source="skills/todo/scripts/todo_confirm_reaction_watch.py:141",
    ),
    PolicyEntry(
        kind="repair",
        ttl_seconds=86_400,
        ttl_source="automation/repair/repair_ops_reaction_watch.py:40",
        reminder=True,
        reminder_source="automation/repair/repair_ops_reaction_watch.py:143",
    ),
    PolicyEntry(
        kind="calendar",
        ttl_seconds=None,
        ttl_source="UNKNOWN:skills/calendar/scripts/calendar_confirm.py",
        reminder=True,
        reminder_source="skills/calendar/scripts/confirm_reaction_watch.py:323",
    ),
    PolicyEntry(
        kind="mail-reply",
        ttl_seconds=900,
        ttl_source="skills/mail/scripts/triage_gate_gmail.py:20",
        reminder=None,
        reminder_source="UNKNOWN:skills/mail/scripts/mail_triage_watch.py",
        note="Gmail 발송 게이트가 승인 레코드에 stamp 하는 expires_at 기한",
    ),
    PolicyEntry(
        kind="mail-compose",
        ttl_seconds=None,
        ttl_source="UNKNOWN:skills/mail/scripts/triage_approval.py",
        reminder=None,
        reminder_source="UNKNOWN:skills/mail/scripts/mail_triage_watch.py",
    ),
    PolicyEntry(
        kind="skill-deploy",
        ttl_seconds=None,
        ttl_source="UNKNOWN:automation/skill_gate.py",
        reminder=True,
        reminder_source="automation/supply_chain_remind.py:176",
        note="supply-chain 틱이 미응답 deploy 승인의 reminder slot 을 claim 한다",
    ),
    PolicyEntry(
        kind="release",
        ttl_seconds=None,
        ttl_source="UNKNOWN:automation/release_approval.py",
        reminder=True,
        reminder_source="automation/supply_chain_remind.py:176",
        note="supply-chain 틱이 미응답 release 승인의 reminder slot 을 claim 한다",
    ),
    PolicyEntry(
        kind="coordination",
        ttl_seconds=None,
        ttl_source="UNKNOWN:skills/coordination/scripts/coordination_lifecycle.py",
        reminder=None,
        reminder_source="UNKNOWN:skills/coordination/scripts/coordination_lifecycle.py",
    ),
    PolicyEntry(
        kind="obsidian-write",
        ttl_seconds=None,
        ttl_source="UNKNOWN:automation/plaud_sync/sync.py",
        reminder=None,
        reminder_source="UNKNOWN:automation/plaud_sync/sync.py",
    ),
)


def unguarded_kinds() -> tuple[str, ...]:
    """Kinds with neither a TTL nor a reminder — a request there can hang forever."""
    return tuple(
        entry.kind
        for entry in POLICY_TABLE
        if entry.ttl_seconds is None and entry.reminder is not True
    )
