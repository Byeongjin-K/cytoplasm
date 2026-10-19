"""Approval-surface INVENTORY — who may post an owner approval, and the ledger of
every deliberate exception (AS-1.3, split out under AS-1.11).

Helper module, not a test module: the name carries no ``test_`` prefix so pytest
does not collect it. It holds data only — no AST walk, no assertion — and is
imported by ``approval_conformance_ast`` and by the conformance tests.

Every entry below is an audit record of a decision an owner signed off on. The
Korean reason strings are the evidence; retiring one is a task of its own, never
a cleanup.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Final

_REPO: Final = Path(__file__).resolve().parents[2]
_RULE: Final = "AGENTS.md 「승인 메시지 단일성 규칙」"

# Public producer surfaces. Gate adapters below own post/commit; their lifecycle host owns
# the façade call where the existing module boundary separates those responsibilities.
APPROVAL_PRODUCERS: Final[Mapping[str, str]] = {
    "automation/skill_gate.py::cmd_request": "automation/skill_gate_approval.py",
    "automation/skill_gate_publish.py::cmd_publish_request": "automation/skill_gate_approval.py",
    "automation/managed_skills/submission_cli.py::submit": "automation/managed_skills/submission_approval.py",
    "automation/repair/repair_ops_posting.py::PostingOwnerApproval.permits": "automation/repair/repair_ops_approval_gate.py",
    "skills/wiki/scripts/wiki_gate.py::post_confirm_message": "skills/wiki/scripts/wiki_approval.py",
    "skills/calendar/scripts/calendar_approval.py::request_confirmation": "skills/calendar/scripts/calendar_approval.py",
    "skills/coordination/scripts/coordination_approval.py::request_confirmation": "skills/coordination/scripts/coordination_approval.py",
    "skills/mail/scripts/triage_approval.py::request_approval": "skills/mail/scripts/triage_approval_gate.py",
    "skills/budget/scripts/budget_approval.py::request_approval": "skills/budget/scripts/budget_approval.py",
    "skills/patent-prep/scripts/patent_export.py::prepare_export": "skills/patent-prep/scripts/patent_export_approval.py",
    "automation/obsidian_write/gate_binding.py::request_approval": "automation/obsidian_write/gate_binding.py",
    "automation/obsidian_write/note_request_cli.py::cmd_request": "automation/obsidian_write/note_request_gate.py",
    "automation/plaud_sync/approval_gate.py::request_approval": "automation/plaud_sync/approval_gate.py",
    "skills/todo/scripts/todo_cli.py::_cmd_request": "skills/todo/scripts/todo_approval.py",
    "automation/release_approval.py::cmd_request": "automation/skill_gate_approval.py",
    "automation/project_board/cli.py::post_card": "automation/project_board/proposal_gate.py",
    "automation/effect_request/cli.py::cmd_request": "automation/effect_request/gate.py",
}

_LIFECYCLE_HOSTS: Final[Mapping[str, str]] = {
    "automation/skill_gate_approval.py": "automation/skill_gate_request.py",
    "automation/managed_skills/submission_approval.py": "automation/managed_skills/submission_approval.py",
    "automation/repair/repair_ops_approval_gate.py": "automation/repair/repair_ops_posting.py",
    "skills/wiki/scripts/wiki_approval.py": "skills/wiki/scripts/wiki_gate.py",
    "skills/calendar/scripts/calendar_approval.py": "skills/calendar/scripts/calendar_approval.py",
    "skills/coordination/scripts/coordination_approval.py": "skills/coordination/scripts/coordination_approval.py",
    "skills/mail/scripts/triage_approval_gate.py": "skills/mail/scripts/triage_approval.py",
    "skills/budget/scripts/budget_approval.py": "skills/budget/scripts/budget_approval.py",
    "skills/patent-prep/scripts/patent_export_approval.py": "skills/patent-prep/scripts/patent_export.py",
    "automation/obsidian_write/gate_binding.py": "automation/obsidian_write/gate_binding.py",
    "automation/obsidian_write/note_request_gate.py": "automation/obsidian_write/note_request_gate.py",
    "automation/plaud_sync/approval_gate.py": "automation/plaud_sync/approval_gate.py",
    "skills/todo/scripts/todo_approval.py": "skills/todo/scripts/todo_approval.py",
    "automation/project_board/proposal_gate.py": "automation/project_board/proposal_gate.py",
    "automation/effect_request/gate.py": "automation/effect_request/gate.py",
}

_ADAPTER_POSTERS: Final[frozenset[str]] = frozenset({
    "automation/repair/repair_ops_approval_gate.py::RepairApprovalGate.post",
    "automation/skill_gate_approval.py::SkillApprovalGate.post",
    "automation/managed_skills/submission_approval.py::PersonalSubmissionGate.post",
    "skills/wiki/scripts/wiki_approval.py::WikiApprovalGate.post",
    "skills/calendar/scripts/calendar_approval.py::CalendarApprovalGate.post",
    "skills/coordination/scripts/coordination_approval.py::CoordinationApprovalGate.post",
    "skills/mail/scripts/triage_approval_gate.py::MailApprovalGate.post",
    "skills/budget/scripts/budget_approval.py::BudgetApprovalGate.post",
    "skills/patent-prep/scripts/patent_export_approval.py::PatentApprovalGate.post",
    "automation/obsidian_write/note_request_gate.py::NoteApprovalGate.post",
    "automation/plaud_sync/approval_gate.py::PlaudApprovalGate.post",
    "skills/todo/scripts/todo_approval.py::TodoApprovalGate.post",
    "automation/project_board/proposal_gate.py::ProposalApprovalGate.post",
    "automation/effect_request/gate.py::EffectApprovalGate.post",
})

_POSTING_PRIMITIVE_IMPLEMENTATIONS: Final[frozenset[str]] = frozenset({
    "automation/repair/repair_ops_discord.py::RepairDiscordApi.post_approval",
    "skills/budget/scripts/budget_confirm.py::post_approval_request",
    "skills/calendar/scripts/calendar_confirm.py::post_confirmation_message",
    "skills/coordination/scripts/coordinate_io.py::post_message",
    "skills/mail/scripts/triage_confirm.py::post_approval_request",
    "skills/patent-prep/scripts/patent_export_gate.py::post_approval_request",
})

_EXEMPT: Final[Mapping[str, str]] = {
    "automation/selfskill_audit/report.py::send_report": "자체 스킬 감사 알림이며 승인 포인터를 생성하지 않는다.",
    "skills/mail/scripts/triage_cli.py::_remind_pending": "기존 승인 원문을 가리키는 무반응 최소정보 리마인더이며 새 승인 포인터를 저장하지 않는다.",
    "automation/repair/repair_ops_reaction_watch.py::RepairApprovalWatcher._process.deliver": "기존 승인 원문을 가리키는 무반응 최소정보 리마인더이며 새 승인 포인터를 저장하지 않는다.",
    "skills/budget/scripts/budget_confirm.py::notify_result": "발송/취소 결과 안내(원 채널 스레드, 소유자 DM 폴백)이며 승인 요청 메시지를 저장하지 않는다.",
    "skills/calendar/scripts/calendar_confirm.py::send_owner_dm": "확정 결과 알림 DM이며 pending 승인 메시지를 만들지 않는다.",
    "skills/calendar/scripts/calendar_confirm.py::post_message": "기존 승인 원문을 가리키는 무반응 최소정보 리마인더이며 새 승인 포인터를 저장하지 않는다.",
    "skills/calendar/scripts/confirm_reaction_watch.py::DiscordApi.post_message": "기존 승인 원문을 가리키는 무반응 최소정보 리마인더이며 새 승인 포인터를 저장하지 않는다.",
    "skills/calendar/scripts/confirm_reaction_watch.py::DiscordApi.post_reminder_message": "같은 무반응 최소정보 리마인더를 게시하고 그 메시지 id를 돌려준다(다이제스트 스레드에서 다음 리마인더가 편집해 붙는다) — 승인 포인터를 저장하지 않는다.",
    "skills/calendar/scripts/confirm_reaction_watch.py::_deliver_reminder": "리마인더 전달 경로 선택(다이제스트 스레드면 한 메시지로 모으기, 아니면 기존 게시) — 승인 요청을 게시하지 않는다.",
    "skills/calendar/scripts/confirm_reaction_watch.py::_process_entries": "기존 승인 원문을 가리키는 무반응 최소정보 리마인더이며 새 승인 포인터를 저장하지 않는다.",
    "skills/coordination/scripts/confirm_reaction_watch.py::DiscordApi.send_owner_dm": "워처의 처리 결과 알림이며 승인 요청을 게시하지 않는다.",
    "skills/coordination/scripts/coordination_lifecycle.py::finish": "피어 조율 완료 통지이며 승인 메시지 포인터를 만들지 않는다.",
    "skills/coordination/scripts/coordination_lifecycle.py::send_owner_dm": "조율 완료 알림 DM이며 승인 게이트가 아니다.",
    "skills/mail/scripts/triage_confirm.py::dm_owner": "승인 결과 안내 DM이며 draft message_id를 쓰지 않는다.",
    "skills/mail/scripts/triage_confirm.py::post_in": "다이제스트 메일 카드를 머리글 스레드에 올린다 — 승인 카드가 아니며 draft message_id를 쓰지 않는다.",
    "skills/mail/scripts/triage_confirm.py::notify_result": "발송/취소 결과 안내(원 채널 스레드, 소유자 DM 폴백)이며 승인 요청 메시지를 저장하지 않는다.",
    "skills/mail/scripts/triage_confirm.py::_CardReply.send": "다이제스트 스레드 초안의 결과 안내를 그 승인 카드 아래 답글로 올린다 — 승인 카드가 아니며 draft message_id를 쓰지 않는다.",
    "automation/interop/origin_notice.py::resolve_thread_id": "결과 통지용 원 채널 스레드 해석·생성 공유 구현이며 승인 메시지를 게시하지 않는다.",
    "automation/interop/origin_notice.py::deliver": "결과 통지 공유 배달기(원 채널 스레드, 호출자 폴백)이며 승인 요청 메시지를 저장하지 않는다.",
    "skills/patent-prep/scripts/patent_export_gate.py::dm_owner": "승인 결과 안내 DM이며 manifest message_id를 쓰지 않는다.",
}

_POST_NAMES: Final[frozenset[str]] = frozenset({
    "post",
    "send",
    "dm",
    "post_message",
    "post_approval",
    "post_approval_request",
    "post_confirmation_message",
})

# ── AS-1.3 · approval-surface conformance ──────────────────────────────────
# Producer surface → the `ApprovalKind` VALUE that flow posts under. Membership
# is checked against the enum itself, never against a duplicated member list.
APPROVAL_KINDS: Final[Mapping[str, str]] = {
    "automation/skill_gate.py::cmd_request": "skill-deploy",
    "automation/skill_gate_publish.py::cmd_publish_request": "skill-publish",
    "automation/managed_skills/submission_cli.py::submit": "skill-submit",
    "automation/repair/repair_ops_posting.py::PostingOwnerApproval.permits": "repair",
    "skills/wiki/scripts/wiki_gate.py::post_confirm_message": "wiki",
    "skills/calendar/scripts/calendar_approval.py::request_confirmation": "calendar",
    "skills/coordination/scripts/coordination_approval.py::request_confirmation": "coordination",
    "skills/mail/scripts/triage_approval.py::request_approval": "mail-reply",
    "skills/budget/scripts/budget_approval.py::request_approval": "budget-mail",
    "skills/patent-prep/scripts/patent_export.py::prepare_export": "patent-export",
    "automation/obsidian_write/gate_binding.py::request_approval": "obsidian-write",
    "automation/obsidian_write/note_request_cli.py::cmd_request": "obsidian-write",
    "automation/plaud_sync/approval_gate.py::request_approval": "obsidian-write",
    "skills/todo/scripts/todo_cli.py::_cmd_request": "todo",
    "automation/release_approval.py::cmd_request": "release",
    "automation/project_board/cli.py::post_card": "project-board",
    "automation/effect_request/cli.py::cmd_request": "external-effect",
}

# Commit surface (the ONLY writer of a flow's message_id) → the module that
# constructs that flow's pending record. That record must carry the binding.
_RECORD_WRITERS: Final[Mapping[str, str]] = {
    "automation/repair/repair_ops_approval_gate.py::RepairApprovalGate.commit": "automation/repair/repair_ops_pending.py",
    "automation/skill_gate_approval.py::SkillApprovalGate.commit": "automation/skill_gate_specs.py",
    "automation/managed_skills/submission_approval.py::PersonalSubmissionGate.commit": "automation/managed_skills/submission_approval.py",
    "skills/wiki/scripts/wiki_approval.py::WikiApprovalGate.commit": "skills/wiki/scripts/wiki_approval.py",
    "skills/calendar/scripts/calendar_approval.py::CalendarApprovalGate.commit": "skills/calendar/scripts/calendar_pending.py",
    "skills/coordination/scripts/coordination_approval.py::CoordinationApprovalGate.commit": "skills/coordination/scripts/coordination_pending.py",
    "skills/mail/scripts/triage_approval_gate.py::MailApprovalGate.commit": "skills/mail/scripts/triage_gate.py",
    "skills/budget/scripts/budget_approval.py::BudgetApprovalGate.commit": "skills/budget/scripts/budget_gate.py",
    "skills/patent-prep/scripts/patent_export_approval.py::PatentApprovalGate.commit": "skills/patent-prep/scripts/patent_export_manifest.py",
    "automation/obsidian_write/note_request_gate.py::NoteApprovalGate.commit": "automation/obsidian_write/note_request.py",
    "automation/plaud_sync/approval_gate.py::PlaudApprovalGate.commit": "automation/plaud_sync/model.py",
    "skills/todo/scripts/todo_approval.py::TodoApprovalGate.commit": "skills/todo/scripts/todo_approval_store.py",
    "automation/project_board/proposal_gate.py::ProposalApprovalGate.commit": "automation/project_board/proposals.py",
}

# The migration ledger. Key = the `+`-joined path(s) one R1 flow owns; value =
# the flow label, the Korean reason and the task that deletes the entry. Each
# Wave-3 task's RED step is deleting its own line; AS-1.11 asserts it is empty.
_PENDING_MIGRATION: Final[Mapping[str, str]] = {
    # Empty, and kept that way by test_pending_migration_entries_are_not_stale: an
    # entry here exempts its file from the surface-naming guard, so one that outlives
    # its reason silently un-guards the file it was written about. The last entry
    # (budget_core.py, spent once AS-2.2 neutralised the outbound wording) was exactly
    # that case — see docs/qa/AS-3/as-3-3-spent-ledger-entry-red.txt.
}

# `/users/@me/channels` call sites that are NOT approval surfaces (SI-2 scope
# limit). Notice senders resolve targets through `automation/owner_notice.py`,
# so this intentionally remains empty.
_NON_APPROVAL_DM_SENDERS: Final[Mapping[str, str]] = {}

# Text that legitimately tells the owner WHERE to look (digest footer, CLI help).
# Text that legitimately tells the owner WHERE to look, or reports on a surface
# to ops. Everything else must go through `approval_surface.reaction_instruction`.
_SURFACE_NAMING_ALLOWED: Final[Mapping[str, str]] = {
    "automation/release_spec_message.py::approval_surface_lines":
        "소유자가 승인하는 선택적 도리 전환의 영향 채널을 명시한 고정 카드 문구다. "
        + "승인 목적지를 선택하거나 반응을 안내하지 않으며, 전환 표면이 없으면 기존 카드 바이트 그대로다.",
    "automation/doctor/capabilities.py::<module>":
        "연결·승인 점검기의 「사람이 승인·연결할 곳」 안내 — 어느 채널 id 를 적어야 하는지가 이 도구의 출력 "
        + "그 자체다. 승인을 게시하지 않고(읽기 전용) 표면을 해석하지도 않는다.",
    "automation/doctor/capabilities.py::_by_design":
        "설치 점검 결과 중 #approvals id 부재를 고장에서 빼는 판정 — 표면 해석이 아니라 그 결과 이름을 읽는다.",
    "automation/doctor/guides.py::<module>":
        "연결·승인 점검기의 조치 절차 문구 — 어느 채널을 만들고 config 에 적을지 알려 준다. 표면을 해석하지 않는다.",
    "automation/hermes_compat/owner-dm-drain-check.py::<module>":
        "owner DM 백로그 드레인 점검 도구 — 점검 대상이 표면 그 자체다.",
    "automation/hermes_compat/patch_busy_fifo.py::<module>":
        "게이트웨이 패치 설명문 — 소유자에게 어느 표면이 영향받는지 알린다.",
    "automation/hermes_compat/patch_discord_receipts.py::<module>":
        "게이트웨이 패치 설명문 — 소유자에게 어느 표면이 영향받는지 알린다.",
    "automation/install/discord_check.py::<module>":
        "설치 전 Discord 전제 점검기 — 어느 서버의 어느 채널이 필요한지가 이 도구의 출력 그 자체다. "
        + "승인을 게시하지 않고(전부 GET) 표면을 해석하지도 않는다.",
    "automation/install/discord_check.py::evaluate_permissions":
        "봇이 어느 서버에도 초대되지 않았을 때의 조치 안내문 — 승인 표면 해석이 아니다.",
    "automation/research_trends/research_trends.py::_send_dm":
        "연구동향 리포트 DM 본문이며 승인 표면과 무관하다.",
    "skills/doctype/scripts/doctype_review.py::send_review":
        "문서종 검토 알림 — 소유자에게 어디서 볼지 알려주는 안내문이다.",
    "skills/proposal/scripts/proposal_dm.py::send_review":
        "제안서 검토 알림 — 소유자에게 어디서 볼지 알려주는 안내문이다.",
}

_APPROVAL_DIRECTORY: Final = "automation/interop/approval_directory.py"
_APPROVAL_POLICY: Final = "automation/interop/approval_surface.py"
_LEGACY_MIGRATOR: Final = f"{_APPROVAL_POLICY}::legacy_binding"
# The SSOT pair: the directory resolves surfaces and the policy module owns the
# ONE formatter allowed to name one. Naming a surface is their job, by design.
_SURFACE_SSOT: Final[tuple[str, ...]] = (_APPROVAL_DIRECTORY, _APPROVAL_POLICY)
_DM_OPEN_PATH: Final = "/users/@me/channels"

_BANNED_RESOLVER_NAMES: Final[frozenset[str]] = frozenset({
    "approvals_channel_id",
    "_approvals_channel_id",
    "owner_dm_channel_id",
    "owner_dm_channel",
    "confirm_via_dm_scan",
})

_BANNED_RESOLVER_LITERALS: Final[frozenset[str]] = frozenset({
    "personal_approvals_channel_id",
    _DM_OPEN_PATH,
})

_BANNED_ENV_OVERRIDE: Final = re.compile(r"^[A-Z_]*APPROVALS_CHANNEL_ID$")

_SURFACE_LITERALS: Final[frozenset[str]] = frozenset({
    "#approvals",
    "#agent-chat",
    "owner DM",
    "승인 DM",
    "개인 서버",
})

_BINDING_FIELDS: Final[frozenset[str]] = frozenset({"kind", "surface", "channel_id", "policy_version"})

# Historical evidence must never gate the build; a guard that fails on history
# is a guard someone deletes.
_HISTORY_ROOTS: Final[tuple[str, ...]] = ("docs/qa/", "docs/patch/", ".omo/", "tests/")
