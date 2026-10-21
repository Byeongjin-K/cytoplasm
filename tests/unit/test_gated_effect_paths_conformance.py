"""Outbound cards have producers, watchers and tests; personal writes need no card.

The Hermes denylist is retired. New owner-internal calendar/document/task/Obsidian
writes use dori-gate, from an owner Discord command. Keep the legacy stored-card
inventory audited without making those internal flows mandatory card paths.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from approval_conformance_inventory import APPROVAL_PRODUCERS

_REPO: Final = Path(__file__).resolve().parents[2]
#: Outbound effect -> repository producers, or the runtime boundary owning it.
GATED_RULES: Final[dict[str, tuple[str, ...] | str]] = {
    "gws_gmail_send": "dori-gate owns direct Gmail sends; the budget skill, its only repository producer, was retired 2026-10-22",
    "gws_calendar_external_invite": (
        "skills/calendar/scripts/calendar_approval.py::request_confirmation",
        "skills/coordination/scripts/coordination_approval.py::request_confirmation",
    ),
    "mailon_send": ("skills/mail/scripts/triage_approval.py::request_approval",),
    "gws_drive_permission_grant": ("skills/mail/scripts/triage_approval.py::request_approval",),
    "generic_external_effect_tool": ("automation/effect_request/cli.py::cmd_request",),
    "generic_external_post_command": ("automation/effect_request/cli.py::cmd_request",),
    "non_owner_or_public_write": "dori-gate owns ownership/public-scope classification and outbound cards",
    "external_url_image": "dori-gate owns external image fetch classification and outbound cards",
    "sheet_write": "dori-gate owns sheet write classification and outbound cards",
    "hourly_deletes_over_ten": "dori-gate owns the rolling delete count and outbound cards",
}

# Existing adapters remain for historical records, not for new internal requests.
LEGACY_INTERNAL_PRODUCERS: Final[dict[str, str]] = {
    "skills/wiki/scripts/wiki_gate.py::post_confirm_message": "retired wiki skill; no new watcher deployment",
    "automation/obsidian_write/gate_binding.py::request_approval": "legacy note-card delegation adapter",
    "automation/obsidian_write/note_request_cli.py::cmd_request": "legacy stored note-card compatibility",
    "automation/plaud_sync/approval_gate.py::request_approval": "legacy stored lifelog-card compatibility",
    "skills/patent-prep/scripts/patent_export.py::prepare_export": "legacy owner-internal draft backup card",
    "automation/project_board/cli.py::post_card": "legacy owner-internal board proposal card",
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
    "skills/calendar/scripts/calendar_approval.py::request_confirmation": (
        "cron:calendar-confirm-watch", "tests/unit/test_calendar_confirm_reactions.py"),
    "skills/coordination/scripts/coordination_approval.py::request_confirmation": (
        "cron:coordination-confirm-watch", "tests/unit/test_coordination_confirm_reactions.py"),
    "skills/mail/scripts/triage_approval.py::request_approval": (
        "cron:mail-triage-watch", "tests/unit/test_mail_triage.py"),
    "automation/effect_request/cli.py::cmd_request": (
        "cron:effect-request-watch", "tests/unit/test_effect_request.py"),
    "automation/release_approval.py::cmd_request": (
        "systemd:automation/systemd/user/autophagy-release-complete.timer", "tests/unit/test_release_complete.py"),
}


def _declared_crons() -> set[str]:
    names: set[str] = set()
    for manifest in (*_REPO.glob("automation/**/deploy-manifest.txt"), *_REPO.glob("skills/*/deploy-manifest.txt")):
        for line in manifest.read_text(encoding="utf-8").splitlines():
            fields = line.split("|")
            if (len(fields) == 4 and fields[3].startswith("v2:cron")
                    and ";policy=retired" not in fields[3]):
                names.add(fields[2])
    return names


def test_owner_internal_writes_when_registered_do_not_require_card_producers() -> None:
    # Given the approval-path ledger used by the build.
    internal = ("skills/todo/", "skills/wiki/", "automation/obsidian_write/")
    # When collecting mandatory producers for gated outbound effects.
    producers = tuple(producer for value in GATED_RULES.values()
                      if isinstance(value, tuple) for producer in value)
    # Then personal writes cannot become mandatory card producers again.
    assert not tuple(producer for producer in producers if producer.startswith(internal))
    assert "gws_calendar_mutation" not in GATED_RULES


def test_every_outbound_rule_names_its_approval_path() -> None:
    assert GATED_RULES
    unknown = sorted(
        producer for value in GATED_RULES.values() if isinstance(value, tuple)
        for producer in value if producer not in APPROVAL_PRODUCERS
    )
    assert not unknown, f"GATED_RULES names producers absent from APPROVAL_PRODUCERS: {unknown}"
    assert all(len(value.strip()) >= 20 for value in GATED_RULES.values() if isinstance(value, str))


def test_every_approval_producer_has_a_watcher_and_a_test() -> None:
    missing = sorted(APPROVAL_PRODUCERS.keys() - PRODUCER_WATCHERS.keys() - LEGACY_INTERNAL_PRODUCERS.keys())
    stale = sorted(PRODUCER_WATCHERS.keys() - APPROVAL_PRODUCERS.keys())
    assert not missing, (
        f"approval producers without a ✅ watcher: {missing} — declare the cron/systemd unit that executes "
        "after the owner's ✅ (or a manual: reason) and a covering test in PRODUCER_WATCHERS."
    )
    assert not stale, f"PRODUCER_WATCHERS names producers that no longer exist: {stale}"
    assert not LEGACY_INTERNAL_PRODUCERS.keys() - APPROVAL_PRODUCERS.keys()
    assert not LEGACY_INTERNAL_PRODUCERS.keys() & PRODUCER_WATCHERS.keys()
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
