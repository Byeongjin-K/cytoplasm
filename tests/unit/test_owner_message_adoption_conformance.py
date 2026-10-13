"""Owner-message adoption ledger; AST only, never import deployed skills.

The pending migration ledger is empty; structural exceptions live separately in
_PERMANENT_RAW_CONTENT_EXEMPT. RETAINED: deliver/notify_owner/notify_owner_dm
keep content: str for approval_reminder._PointerSender, the cardless Obsidian
gate_binding delegation, budget_confirm.dm_owner and calendar_confirm.send_owner_dm.
The callbacks consume already-rendered bodies or legacy fallback bytes; they must
not render again. Meeting's isolated gateway plugin remains separately governed by
origin-notice _THREAD_API_ALLOWED and test_meeting_runtime_root, not this scan's
scripts roots. Retire strings only after these boundaries and capability fallbacks
are replaced, all exemptions expire, and isolated meeting delivery still passes.
An entry exempts its scope: deleting it without migration or reasoned promotion
silently removes that protection. Both staleness and nonblank reasons are required.

Notice adoption requires message= on each deliver/notify_owner/notify_owner_dm
call (including injected deliver callbacks), except capability fallbacks paired
with ANY message= call to the same resolved facade in the same innermost scope.
Bodies must have identical ast.dump output: content= for deliver; the first
positional argument, or content= if absent, for notify_owner/notify_owner_dm.
Missing bodies never pair. This is syntax pairing, not capability-branch proof.
Card adoption starts at the explicit render root paired with APPROVAL_PRODUCERS: that scope or
same-module functions it references must CALL render imported from owner_message
(or module_alias.render). References include version-dispatch tables; an unused
sibling renderer, import alone or prose does not count. Cross-module render-path
moves require updating the root here, not broadening to package text matches.
Aliases include imported names, assignments and callback defaults. Bypasses
use transport shape (hermes send argv, channel-message POST or send_owner_dm
method calls, lexical bound-method/attribute aliases and explicit positional/keyword
forwarding through same-module helpers). owner_message_sender_ast documents the
injected-sender graph and its acknowledged dynamic blind spots. Non-owner transports
have a separate reasoned, non-stale ledger. This is syntax conformance,
not whole-program dataflow or a proof that a rendered value reaches the network.
Local list/tuple argv follows the nearest preceding simple single-Name binding,
including nested blocks in textual order (flow-insensitive). RHS uses cannot see
their enclosing assignment or future bindings. Starred expansion and Name alias
resolution are bounded to depth three; HTTP path names stay same-function.
KNOWN LIMITS for argv/HTTP analysis: AugAssign, BinOp concatenation, append/extend/insert, IfExp,
comprehensions, subscripts/slices, walrus, argv from parameters, return values,
attributes or call results (including shlex.split), dynamic attribute access,
eval and exec are outside the guaranteed closure. The helper retains its legacy
append/extend, +=, concatenation and get/getenv-default recognition only.
"""
from __future__ import annotations

import ast
from collections.abc import Mapping
from typing import Final

import pytest

from tests.unit.approval_conformance_ast import (
    _called_name, _deployed_sources, _qualnames,
)
from tests.unit.approval_conformance_inventory import APPROVAL_PRODUCERS, _REPO
from tests.unit.owner_message_conformance_ast import (
    bypasses as _bypasses, renders as _renders, scoped_nodes as _scoped_nodes,
)
from tests.unit.owner_message_inventory import Scan as _Scan, scan_sources
from tests.unit.owner_message_sender_probes import batched_inventory

_LINK_DEFINITIONS: Final = frozenset({
    "automation/interop/owner_message.py", "automation/interop/approval_reminder.py",
})
_BYPASS_SENDERS: Final = frozenset({
    "skills/doctype/scripts/doctype_review.py::send_review",
    "skills/proposal/scripts/proposal_dm.py::send_review",
    "skills/procurement/scripts/procure_review.py::send_review",
})
# Adopted direct producers remain registered, NOT exempt from render enforcement.
_INJECTED_OWNER_SENDERS: Final = {
    "skills/calendar/scripts/confirm_reaction_watch.py::_notify_owner":
        "캘린더 무스레드 결과 생산자 — owner_notice 파사드에 봉투와 실제 목적지 해석을 위임",
}
# Producer order is NOT significant: keys must equal the shared inventory.
_CARD_RENDER_ROOTS: Final = {
    "automation/skill_gate.py::cmd_request": "automation/skill_gate_specs.py::DeploySpec.render",
    "automation/skill_gate_publish.py::cmd_publish_request": "automation/skill_gate_specs.py::PublishSpec.render",
    "automation/managed_skills/submission_cli.py::submit": "automation/managed_skills/submission_message.py::render_submission_message",
    "automation/repair/repair_ops_posting.py::PostingOwnerApproval.permits": "automation/repair/repair_approval_render.py::approval_request_content",
    "skills/wiki/scripts/wiki_gate.py::post_confirm_message": "skills/wiki/scripts/wiki_gate.py::confirm_text",
    "skills/calendar/scripts/calendar_approval.py::request_confirmation": "skills/calendar/scripts/calendar_card.py::render_envelope",
    "skills/coordination/scripts/coordination_approval.py::request_confirmation": "skills/coordination/scripts/coordination_lifecycle.py::render_owner_card",
    "skills/mail/scripts/triage_approval.py::request_approval": "skills/mail/scripts/triage_core.py::render_approvals_message",
    "skills/budget/scripts/budget_approval.py::request_approval": "skills/budget/scripts/budget_core.py::render_approvals_message",
    "skills/patent-prep/scripts/patent_export.py::prepare_export": "skills/patent-prep/scripts/patent_export_render.py::render_approval",
    "automation/obsidian_write/gate_binding.py::request_approval": "automation/obsidian_write/gate_binding.py::request_approval",
    "automation/obsidian_write/note_request_cli.py::cmd_request": "automation/obsidian_write/note_request_render.py::render_note_approval",
    "automation/project_board/cli.py::post_card": "automation/project_board/proposal_render.py::render_proposal_card",
    "automation/memory_relocate/approval_gate.py::request_approval": "automation/memory_relocate/render.py::render_relocation_approval",
    "automation/plaud_sync/approval_gate.py::request_approval": "automation/plaud_sync/render.py::render_plaud_approval",
    "skills/todo/scripts/todo_cli.py::_cmd_request": "skills/todo/scripts/todo_approval_render.py::render_todo_approval",
    "automation/release_approval.py::cmd_request": "automation/release_spec_message.py::render_v6",
}
# Closed pending ledger. Delete rows only WITH migration or reasoned promotion;
# stale rows silently un-guard their scope (approval inventory precedent).
_RAW_CONTENT_EXEMPT: dict[str, str] = {}

# Permanent means structural, not unchecked: every row must still be raw and live.
_PERMANENT_RAW_CONTENT_EXEMPT: dict[str, str] = {
    "automation/interop/approval_reminder.py::_PointerSender.send": "영구 최소정보 포인터 — context.deliver(channel_id, content) 계약으로 compose_reminder의 유형·경과시간·공용 정의 링크만 전송",
    "automation/obsidian_write/gate_binding.py::request_approval": "영구 위임 경계 — 주입 ApprovalGate에 lifecycle을 위임하며 자체 카드·구체 게시 어댑터가 없다; 어댑터 도입 시 재감사",
    "skills/budget/scripts/budget_confirm.py::dm_owner": "영구 문자열 수신 경계 — deliver가 목적지별 렌더한 본문 또는 import·능력 폴백의 기존 바이트를 notify_owner로 전달; 재렌더 금지",
    "skills/calendar/scripts/calendar_confirm.py::send_owner_dm": "영구 문자열 수신 경계 — deliver 폴백·워처의 렌더된 본문 또는 기존 바이트·공유 최소정보 리마인더를 수신; 목적지 선택은 호출자가 소유",
    "skills/doctype/scripts/doctype_review.py::send_review": "영구 문자열 발신 경계 — 스킬이 봉투를 렌더하고 옛 런타임·렌더 거부의 능력 폴백으로 기존 바이트를 보존한 뒤 notify_owner로 전달; message=는 폴백을 없애므로 재렌더 금지",
    "skills/procurement/scripts/procure_review.py::send_review": "영구 문자열 발신 경계 — 해석된 통지 채널 좌표로 렌더한 본문·레거시 폴백 바이트를 첨부와 함께 notify_owner로 전달; 재렌더하면 좌표와 폴백이 모두 사라진다",
    "skills/proposal/scripts/proposal_dm.py::send_review": "영구 문자열 발신 경계 — 스킬이 봉투를 렌더하고 옛 런타임·렌더 거부의 능력 폴백으로 기존 바이트를 보존한 뒤 notify_owner로 전달; message=는 폴백을 없애므로 재렌더 금지",
}

# Transport internals, peer protocols and approval posting: not notice producers.
_NOT_OWNER_FACING: dict[str, str] = {
    "automation/interop/discord_transport.py::DiscordTransport._send_chunk": "공유 전송 내부 — 봉투 채택은 호출한 발신자가 소유한다",

    "automation/owner_notice.py::_post_multipart": "파사드 첨부 전송 내부 — 목적지·본문은 notify_owner가 정하고 여기서는 multipart 인코딩만 올린다",


    "automation/interop/reaction_approval.py::DiscordTransport.post_message": "승인 라이프사이클 공용 전송 — 카드 생산자에서 렌더 경로를 검사한다",

    "automation/managed_skills/submission_transport.py::DiscordSubmissionTransport.post_submission": "승인 카드 첨부 전송 — submission 생산자의 렌더 경로가 검사 대상이다",

    "automation/peer_attest_runtime.py::DiscordRestTransport.post_reply": "피어 봇 검증 프로토콜 회신이며 소유자 결과 통지가 아니다",

    "automation/repair/repair_ops_discord.py::RepairDiscordApi.post_message": "저장된 승인 표면으로 보내는 수리 공용 전송 내부",

    "automation/repair/repair_report_send.py::_send_direct": "agents-log 봇 보고 프로토콜이며 소유자 통지 표면이 아니다",

    "automation/skill_gate_approval.py::SkillApprovalGate.post": "승인 라이프사이클 카드 게시 — skill 및 release 생산자의 렌더 경로가 검사 대상이다",
    "automation/release_request_gate.py::ReleaseRequestGate.post": "릴리스 승인 게시 트랜잭션 — 카드 봉투는 release 생산자의 render_v6 경로가 검사하고, 여기서는 실제 상세 좌표와 reply payload만 결합한다",
    "automation/release_request_gate.py::ReleaseRequestGate._post_details": "릴리스 변경 상세 전송 내부 — 저장된 패치노트와 전체 번들 목록의 기계적 분할이며 승인 카드 생산자는 render_v6 경로가 검사한다",

    "automation/supply_chain_watch_cli.py::main.deliver": "승인 리마인더의 주입 전송 — approval_reminder 발신자가 봉투를 소유한다",

    "skills/budget/scripts/budget_confirm.py::post_approval_request": "예산 승인 카드 전송 — budget 생산자의 렌더 경로가 검사 대상이다",

    "skills/calendar/scripts/calendar_confirm.py::post_confirmation_message": "일정 승인 카드 게시 — calendar 생산자의 렌더 루트로 별도 검사한다",

    "skills/calendar/scripts/calendar_confirm.py::post_message": "공유 최소정보 리마인더 전송 내부 — _PointerSender.send가 본문과 목적지를 소유한다",


    "skills/coordination/scripts/coordinate_io.py::post_message": "조율 승인 및 피어 메시지의 공용 전송 내부",

    "skills/mail/scripts/triage_confirm.py::post_approval_request": "메일 승인 카드 전송 — mail 생산자의 렌더 경로가 검사 대상이다",

    "skills/patent-prep/scripts/patent_export_gate.py::post_approval_request": "특허 승인 카드 전송 — patent 생산자의 렌더 경로가 검사 대상이다",

    "skills/todo/scripts/todo_discord.py::TodoDiscordTransport.post_message": "할 일 승인 라이프사이클 공용 전송 내부",

    "skills/wiki/scripts/wiki_approval.py::WikiApprovalGate.post": "위키 승인 라이프사이클 카드 게시 — wiki 생산자의 렌더 경로가 검사 대상이다",
}

_LINK_LITERAL_ALLOWED: dict[str, str] = {
    "skills/plaud/scripts/plaud_cli.py::_thread_url": "샌드박스 stdlib 격리로 automation import 불가 — mail_runtime 인라인 폴백과 동일 유형",
}


@pytest.fixture(scope="module")
def sources() -> Mapping[str, ast.Module]:
    return {str(path.relative_to(_REPO)): ast.parse(path.read_text(encoding="utf-8"))
            for path in _deployed_sources()}


@pytest.fixture(scope="module")
def inventory(sources: Mapping[str, ast.Module]) -> _Scan:
    # One repository graph for raw/migrated probes AND unchanged-deployment assertions.
    return batched_inventory(sources, _scan, test_senders_use_envelopes_when_discovered)


def _inventory(sources: Mapping[str, ast.Module] | _Scan) -> tuple[dict[str, list[bool]], dict[str, int], set[str]]:
    scan = _scan(sources)
    return scan.sites, scan.counts, scan.links


def _scan(sources: Mapping[str, ast.Module] | _Scan) -> _Scan:
    if isinstance(sources, _Scan):
        return sources
    return scan_sources(sources, _NOT_OWNER_FACING, _CARD_RENDER_ROOTS, _LINK_DEFINITIONS)


def test_senders_use_envelopes_when_discovered(inventory: Mapping[str, ast.Module] | _Scan) -> None:
    # Given deployed sources; when scanned; then every raw sender is accounted for.
    sites, _, _ = _inventory(inventory)
    exemptions = _RAW_CONTENT_EXEMPT.keys() | _PERMANENT_RAW_CONTENT_EXEMPT.keys()
    failures = sorted(key for key, adopted in sites.items() if not all(adopted) and key not in exemptions)
    assert not failures, "raw owner-message senders without message=/render: " + ", ".join(failures)


def test_pending_raw_migration_ledger_is_empty() -> None:
    assert not _RAW_CONTENT_EXEMPT, f"unfinished owner-message migrations: {sorted(_RAW_CONTENT_EXEMPT)}"


def test_raw_content_exemptions_are_not_stale(inventory: Mapping[str, ast.Module] | _Scan) -> None:
    # Given the ledger; when compared to live calls; then no vanished/adopted row remains.
    sites, _, _ = _inventory(inventory)
    stale = sorted(key for key in _RAW_CONTENT_EXEMPT if key not in sites or all(sites[key]))
    assert not stale, f"raw exemptions no longer needed: {stale}"


def test_permanent_raw_content_exemptions_are_not_stale(inventory: Mapping[str, ast.Module] | _Scan) -> None:
    sites, _, _ = _inventory(inventory)
    stale = sorted(key for key in _PERMANENT_RAW_CONTENT_EXEMPT if key not in sites or all(sites[key]))
    assert not stale, f"permanent raw exemptions no longer needed: {stale}"
    assert not _RAW_CONTENT_EXEMPT.keys() & _PERMANENT_RAW_CONTENT_EXEMPT.keys(), "pending and permanent overlap"


@pytest.mark.parametrize("ledger_name", ["pending", "permanent"])
@pytest.mark.parametrize("body", ["def notify(): pass", "def notify(): notify_owner(raw, message=envelope)"])
def test_raw_exemption_rejects_missing_or_adopted_sender(
    body: str, ledger_name: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = "skills/example/scripts/probe.py::notify"
    ledger, check = {
        "pending": (_RAW_CONTENT_EXEMPT, test_raw_content_exemptions_are_not_stale),
        "permanent": (_PERMANENT_RAW_CONTENT_EXEMPT, test_permanent_raw_content_exemptions_are_not_stale),
    }[ledger_name]
    monkeypatch.setitem(ledger, key, "synthetic boundary")
    with pytest.raises(AssertionError, match=key):
        check({key.split("::")[0]: ast.parse(body)})


def test_link_literals_use_single_definition_when_discovered(inventory: Mapping[str, ast.Module] | _Scan) -> None:
    # Given deployed constants; when scanned; then only the independent link ledger exempts them.
    _, _, links = _inventory(inventory)
    assert not links - _LINK_LITERAL_ALLOWED.keys(), sorted(links - _LINK_LITERAL_ALLOWED.keys())


def test_link_literal_exemptions_are_not_stale(inventory: Mapping[str, ast.Module] | _Scan) -> None:
    # Given link exemptions; when scopes are scanned; then each still owns a literal.
    _, _, links = _inventory(inventory)
    assert not _LINK_LITERAL_ALLOWED.keys() - links, sorted(_LINK_LITERAL_ALLOWED.keys() - links)


def test_transport_exemptions_are_not_stale(inventory: Mapping[str, ast.Module] | _Scan) -> None:
    # Given transport exceptions; then the shared scan retains shape and one audience.
    transports = _scan(inventory).transports
    stale = sorted(_NOT_OWNER_FACING.keys() - transports)
    overlap = sorted(_NOT_OWNER_FACING.keys() & (_RAW_CONTENT_EXEMPT.keys() | _PERMANENT_RAW_CONTENT_EXEMPT.keys()))
    assert not stale, f"transport exemptions no longer needed: {stale}"
    assert not overlap, f"conflicting transport audience: {overlap}"


def test_repository_sender_escape_fails_closed(sources: Mapping[str, ast.Module]) -> None:
    # A raised scan has no inventory to share; only this abort-path needs a second pass.
    path = "skills/synthetic_owner_guard/opaque.py"
    tree = ast.parse("def notify(client, external): external(client.send_owner_dm, 'raw')")
    with pytest.raises(AssertionError, match=f"UNRESOLVED_OWNER_SENDER {path}::notify"):
        test_senders_use_envelopes_when_discovered({**sources, path: tree})


def test_ledgers_have_reasons_when_registered() -> None:
    # Given all registries; when reading reasons; then blank reasons are rejected.
    for ledger in (_RAW_CONTENT_EXEMPT, _PERMANENT_RAW_CONTENT_EXEMPT, _LINK_LITERAL_ALLOWED,
                   _NOT_OWNER_FACING, _INJECTED_OWNER_SENDERS):
        assert all(reason.strip() for reason in ledger.values()), "empty exemption reason"


def test_inventory_is_nonempty_when_scanning_deployed_sources(sources: Mapping[str, ast.Module], inventory: _Scan) -> None:
    # Given the shared inventory; when independently scanning notices; then no category is vacuous.
    assert _CARD_RENDER_ROOTS.keys() == APPROVAL_PRODUCERS.keys()
    sites, counts, _ = _inventory(inventory)
    assert counts["cards"] == len(APPROVAL_PRODUCERS)
    print("owner-message AST counts:", dict(sorted(counts.items())))
    # Main deliberately retired the last DM-only producer; the retained facade is
    # exercised by its own tests, not by inventing a production consumer.
    assert counts["notify_owner_dm"] == 0, counts
    assert all(value for name, value in counts.items() if name != "notify_owner_dm"), counts
    assert _BYPASS_SENDERS <= sites.keys()
    assert _INJECTED_OWNER_SENDERS.keys() <= sites.keys()
    assert not _INJECTED_OWNER_SENDERS.keys() & (
        _RAW_CONTENT_EXEMPT.keys() | _PERMANENT_RAW_CONTENT_EXEMPT.keys() | _NOT_OWNER_FACING.keys()
    )
    for producer in APPROVAL_PRODUCERS:
        path, scope = producer.split("::")
        assert scope in _qualnames(sources[path]), producer


@pytest.mark.parametrize(("body", "expected"), [
    ("from automation.interop.owner_message import render as show\ndef root(): return show(msg)", True),
    ("from automation.interop import owner_message as om\ndef root(): return om.render(msg)", True),
    ("from automation.interop.owner_message import render\ndef root(): return 'render(msg)'", False),
    ("from automation.interop.owner_message import render\ndef unused(): return render(msg)\ndef root(): return raw", False),
    ("from automation.interop.owner_message import render\ndef version(): return render(msg)\ndef root(): return {2: version}[v]()", True),
])
def test_render_adoption_when_imports_and_paths_differ(body: str, expected: bool) -> None:
    # Given synthetic code; when the render path is inspected; then imports/prose alone do not adopt.
    tree = ast.parse(body)
    assert _renders(tree, "root") is expected


def test_scopes_keep_every_call_when_nested_or_mixed() -> None:
    # Given two calls in one scope and a nested def; when walked; then none overwrite another.
    tree = ast.parse("def outer():\n deliver(content=raw)\n deliver(message=msg)\n def inner(): notify_owner(raw)")
    calls = [(scope, _called_name(node)) for scope, node in _scoped_nodes(tree) if isinstance(node, ast.Call)]
    assert calls == [("outer", "deliver"), ("outer", "deliver"), ("outer.inner", "notify_owner")]


@pytest.mark.parametrize(("expression", "expected"), [
    ("subprocess.run(('hermes', 'send', '--to', target, body))", True),
    ("_api('POST', f'/channels/{channel}/messages', payload)", True),
    ("_api('GET', f'/channels/{channel}/messages')", False),
    ("subprocess.run(('hermes', 'status'))", False),
])
def test_bypass_detection_when_transport_shapes_differ(expression: str, expected: bool) -> None:
    # Given a transport expression; when matched; then only review-send shapes count.
    call = ast.parse(expression, mode="eval").body
    assert isinstance(call, ast.Call)
    assert _bypasses(call, [call]) is expected


@pytest.mark.parametrize("ledger_name", [
    "_RAW_CONTENT_EXEMPT", "_PERMANENT_RAW_CONTENT_EXEMPT", "_LINK_LITERAL_ALLOWED",
    "_NOT_OWNER_FACING", "_INJECTED_OWNER_SENDERS",
])
def test_blank_reasons_are_rejected_when_registered(ledger_name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given a malformed registry entry; when checked; then either registry rejects it.
    ledger = {"_RAW_CONTENT_EXEMPT": _RAW_CONTENT_EXEMPT, "_PERMANENT_RAW_CONTENT_EXEMPT": _PERMANENT_RAW_CONTENT_EXEMPT,
              "_LINK_LITERAL_ALLOWED": _LINK_LITERAL_ALLOWED, "_NOT_OWNER_FACING": _NOT_OWNER_FACING,
              "_INJECTED_OWNER_SENDERS": _INJECTED_OWNER_SENDERS}[ledger_name]
    monkeypatch.setitem(ledger, "skills/example/scripts/review.py::send_review", "  ")
    with pytest.raises(AssertionError, match="empty exemption reason"):
        test_ledgers_have_reasons_when_registered()
