"""Every gated external effect has a whole approval path: card producer, ✅ watcher, test.

2026-10-07: an Obsidian note save was gated at push time but had no card producer and
no watcher, so the agent improvised and asked for ✅ on a chat message that nothing
read. The gate alone does not make an effect approvable — something must post the card
and something must act on its ✅. This ledger is the build-time answer to "where is the
card and who consumes it" for every denylist rule and every approval producer:

- a new rule in ``configs/external-effect-tools.yaml`` fails until it names its producers
  (or an exemption with a reason);
- a new producer in ``approval_conformance_inventory.APPROVAL_PRODUCERS`` fails until it
  names the watcher that executes after ✅ (a declared cron, a systemd unit in the tree,
  or a ``manual:`` reason) and a test file that exercises it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from approval_conformance_inventory import APPROVAL_PRODUCERS

_REPO: Final = Path(__file__).resolve().parents[2]
_RULES: Final = _REPO / "configs" / "external-effect-tools.yaml"

#: denylist rule id -> producers whose cards approve it, or an exemption reason.
GATED_RULES: Final[dict[str, tuple[str, ...] | str]] = {
    "gws_gmail_send": ("skills/budget/scripts/budget_approval.py::request_approval",),
    "gws_calendar_mutation": (
        "skills/calendar/scripts/calendar_approval.py::request_confirmation",
        "skills/coordination/scripts/coordination_approval.py::request_confirmation",
    ),
    "gws_tasks_mutation": ("skills/todo/scripts/todo_cli.py::_cmd_request",),
    "mailon_send": ("skills/mail/scripts/triage_approval.py::request_approval",),
    "obsidian_write_note_push": (
        "automation/obsidian_write/note_request_cli.py::cmd_request",
        "automation/plaud_sync/approval_gate.py::request_approval",
        "automation/memory_relocate/approval_gate.py::request_approval",
    ),
    "patent_draft_drive_upload": ("skills/patent-prep/scripts/patent_export.py::prepare_export",),
    "gws_drive_permission_grant": ("skills/mail/scripts/triage_approval.py::request_approval",),
    "generic_external_effect_tool": ("automation/effect_request/cli.py::cmd_request",),
    "generic_external_post_command": ("automation/effect_request/cli.py::cmd_request",),
}

#: approval producer -> the thing that acts on the owner's ✅, and a test that covers it.
PRODUCER_WATCHERS: Final[dict[str, tuple[str, str]]] = {
    "automation/skill_gate.py::cmd_request": (
        "systemd:automation/systemd/autophagy-supply-chain-watch.service", "tests/unit/test_supply_chain_watch.py"),
    "automation/skill_gate_publish.py::cmd_publish_request": (
        "systemd:automation/systemd/autophagy-supply-chain-watch.service", "tests/unit/test_skill_gate_publish.py"),
    "automation/managed_skills/submission_cli.py::submit": (
        "manual:a submission is a review request; the group admin runs publish_cli --submission-* explicitly "
        "and that publish has its own approval", "tests/unit/test_personal_submission_approval.py"),
    "automation/repair/repair_ops_posting.py::PostingOwnerApproval.permits": (
        "systemd:automation/repair/systemd/autophagy-repair-approval-watch.service",
        "tests/unit/test_repair_approval_watch.py"),
    "skills/wiki/scripts/wiki_gate.py::post_confirm_message": (
        "cron:wiki-confirm-watch", "tests/unit/test_wiki_confirm_summary.py"),
    "skills/calendar/scripts/calendar_approval.py::request_confirmation": (
        "cron:calendar-confirm-watch", "tests/unit/test_calendar_confirm_reactions.py"),
    "skills/coordination/scripts/coordination_approval.py::request_confirmation": (
        "cron:coordination-confirm-watch", "tests/unit/test_coordination_confirm_reactions.py"),
    "skills/mail/scripts/triage_approval.py::request_approval": (
        "cron:mail-triage-watch", "tests/unit/test_mail_triage.py"),
    "skills/budget/scripts/budget_approval.py::request_approval": (
        "cron:budget-watch", "tests/unit/test_budget_watch_wrapper.py"),
    "skills/patent-prep/scripts/patent_export.py::prepare_export": (
        "manual:patent export is owner-run by design (DM-only, the owner executes export-execute after ✅); "
        "the reaction watcher only records the decision", "tests/unit/test_patent_export_watch.py"),
    "automation/obsidian_write/gate_binding.py::request_approval": (
        "manual:delegation boundary with no card of its own; the three obsidian producers above own the "
        "cards and their watchers", "tests/unit/test_obsidian_write_gate.py"),
    "automation/obsidian_write/note_request_cli.py::cmd_request": (
        "cron:obsidian-note-watch", "tests/unit/test_obsidian_note_request.py"),
    "automation/project_board/cli.py::post_card": (
        "cron:project-board-watch", "tests/unit/test_project_board_approval.py"),
    "automation/memory_relocate/approval_gate.py::request_approval": (
        "cron:memory-relocate-watch", "tests/unit/test_memory_relocate_approval_gate.py"),
    "automation/plaud_sync/approval_gate.py::request_approval": (
        "cron:plaud-sync-watch", "tests/unit/test_plaud_sync_watch_step.py"),
    "skills/todo/scripts/todo_cli.py::_cmd_request": (
        "cron:todo-confirm-watch", "tests/unit/test_todo_watch.py"),
    "automation/effect_request/cli.py::cmd_request": (
        "cron:effect-request-watch", "tests/unit/test_effect_request.py"),
    "automation/release_approval.py::cmd_request": (
        "systemd:automation/systemd/user/autophagy-release-complete.timer", "tests/unit/test_release_complete.py"),
}


def _rule_ids() -> set[str]:
    return set(re.findall(r"^\s*-\s*id:\s*(\S+)\s*$", _RULES.read_text(encoding="utf-8"), re.MULTILINE))


def _declared_crons() -> set[str]:
    names: set[str] = set()
    for manifest in (*_REPO.glob("automation/**/deploy-manifest.txt"), *_REPO.glob("skills/*/deploy-manifest.txt")):
        for line in manifest.read_text(encoding="utf-8").splitlines():
            fields = line.split("|")
            if len(fields) == 4 and fields[3].startswith("v2:cron"):
                names.add(fields[2])
    return names


def test_every_denylist_rule_names_its_approval_path() -> None:
    rules = _rule_ids()
    assert rules, "external-effect-tools.yaml yielded no rule ids"
    missing = sorted(rules - GATED_RULES.keys())
    stale = sorted(GATED_RULES.keys() - rules)
    assert not missing, (
        f"gated rules without an approval path: {missing} — name the card producer(s) in GATED_RULES "
        "(or an exemption reason). A gate with no card and no watcher leaves the agent to improvise."
    )
    assert not stale, f"GATED_RULES names rules that no longer exist: {stale}"
    unknown = sorted(
        producer for value in GATED_RULES.values() if isinstance(value, tuple)
        for producer in value if producer not in APPROVAL_PRODUCERS
    )
    assert not unknown, f"GATED_RULES names producers absent from APPROVAL_PRODUCERS: {unknown}"


def test_every_approval_producer_has_a_watcher_and_a_test() -> None:
    missing = sorted(APPROVAL_PRODUCERS.keys() - PRODUCER_WATCHERS.keys())
    stale = sorted(PRODUCER_WATCHERS.keys() - APPROVAL_PRODUCERS.keys())
    assert not missing, (
        f"approval producers without a ✅ watcher: {missing} — declare the cron/systemd unit that executes "
        "after the owner's ✅ (or a manual: reason) and a covering test in PRODUCER_WATCHERS."
    )
    assert not stale, f"PRODUCER_WATCHERS names producers that no longer exist: {stale}"
    crons = _declared_crons()
    failures: list[str] = []
    for producer, (watcher, test) in sorted(PRODUCER_WATCHERS.items()):
        kind, _, target = watcher.partition(":")
        if kind == "cron" and target not in crons:
            failures.append(f"{producer}: cron {target!r} is not declared in any deploy-manifest.txt")
        elif kind == "systemd" and not (_REPO / target).exists():
            failures.append(f"{producer}: systemd unit {target} is missing")
        elif kind == "manual" and len(target.strip()) < 20:
            failures.append(f"{producer}: a manual watcher needs a real reason")
        elif kind not in {"cron", "systemd", "manual"}:
            failures.append(f"{producer}: unknown watcher kind {kind!r}")
        if not (_REPO / test).is_file():
            failures.append(f"{producer}: covering test {test} is missing")
    assert not failures, "\n".join(failures)
