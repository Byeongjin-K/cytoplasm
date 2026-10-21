"""승인 요청을 게시하는 모든 생산자가 `APPROVAL-THREAD` 줄을 내거나, 내지 않는 사유를 적었는지 강제한다.

2026-09-23: mail compose 뒤 에이전트 답장이 "승인 스레드에 게시했습니다" 로만 끝났고 같은 날
todo 도 그랬다. 원인은 CLI 출력에 스레드 좌표가 없어 에이전트가 인용할 링크가 없었던 것이다.
새 승인 생산자가 `approval_conformance_inventory.APPROVAL_PRODUCERS` 에 오르는 순간(그 등록은
lifecycle conformance 가 강제한다) 여기서 채택/면제를 결정하지 않으면 RED 다 — 산문이 아니라
코드가 진실이다.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Final

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "tests" / "unit"))

from approval_conformance_inventory import APPROVAL_PRODUCERS  # noqa: E402

_POINTER_MODULE: Final = "automation.interop.thread_pointer"
_POINTER_FILE: Final = "automation/interop/thread_pointer.py"

#: 생산자 → 에이전트가 실행하고 게시 직후 좌표 줄을 내는 파일.
_ADOPTED: Final[dict[str, str]] = {
    "skills/mail/scripts/triage_approval.py::request_approval": "skills/mail/scripts/triage_cli.py",
    "skills/calendar/scripts/calendar_approval.py::request_confirmation":
        "skills/calendar/scripts/calendar_output.py",
    "skills/coordination/scripts/coordination_approval.py::request_confirmation":
        "skills/coordination/scripts/coordination_lifecycle.py",
    "automation/obsidian_write/note_request_cli.py::cmd_request": "automation/obsidian_write/note_request_cli.py",
    "automation/effect_request/cli.py::cmd_request": "automation/effect_request/cli.py",
    "automation/project_board/cli.py::post_card": "automation/project_board/cli.py",
}

_SUPPLY_CHAIN = "공급망 #approvals 표면 — 운영자 스크립트가 쓰고 소유자 대화 답장을 거치지 않는다"
_NO_AGENT = "no-agent 워처·운영 경로가 게시하며 에이전트 답장이 없다(부모 채널 안내 메시지가 스레드를 가리킨다)"

_EXEMPT: Final[dict[str, str]] = {
    "automation/skill_gate.py::cmd_request": _SUPPLY_CHAIN,
    "automation/skill_gate_publish.py::cmd_publish_request": _SUPPLY_CHAIN,
    "automation/managed_skills/submission_cli.py::submit": _SUPPLY_CHAIN,
    "automation/release_approval.py::cmd_request": _SUPPLY_CHAIN,
    "automation/repair/repair_ops_posting.py::PostingOwnerApproval.permits": _NO_AGENT,
    "automation/obsidian_write/gate_binding.py::request_approval": _NO_AGENT,
    "automation/plaud_sync/approval_gate.py::request_approval": _NO_AGENT,
    "skills/wiki/scripts/wiki_gate.py::post_confirm_message":
        "위키는 좌표도 소유자 DM 밖으로 싣지 않는다(소유자 메시지 계약 마스킹) + wiki_gate.py 수정 동결",
    "skills/patent-prep/scripts/patent_export.py::prepare_export":
        "특허 내보내기는 좌표를 싣지 않는다(소유자 메시지 계약 마스킹 규칙)",
}


def _tree(relative: str) -> ast.Module:
    return ast.parse((_REPO / relative).read_text(encoding="utf-8"), filename=relative)


def test_every_approval_producer_is_adopted_or_exempt_exactly_once() -> None:
    adopted, exempt, producers = set(_ADOPTED), set(_EXEMPT), set(APPROVAL_PRODUCERS)
    assert not adopted & exempt
    assert adopted | exempt == producers, (
        "새 승인 생산자는 게시 직후 APPROVAL-THREAD 줄을 내거나(_ADOPTED) 사유와 함께 _EXEMPT 에 "
        f"올라야 한다 — 미결정: {sorted(producers - adopted - exempt)}, "
        f"없는 생산자: {sorted((adopted | exempt) - producers)}"
    )


def test_adopted_files_print_the_shared_pointer_line() -> None:
    missing: list[str] = []
    for relative in sorted(set(_ADOPTED.values())):
        tree = _tree(relative)
        imports = any(
            isinstance(node, ast.ImportFrom) and node.module == _POINTER_MODULE
            and any(alias.name == "approval_thread_line" for alias in node.names)
            for node in ast.walk(tree)
        )
        calls = any(
            isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "approval_thread_line"
            for node in ast.walk(tree)
        )
        if not (imports and calls):
            missing.append(relative)
    assert not missing, f"공용 approval_thread_line 을 부르지 않는 채택 파일: {missing}"


def test_the_pointer_line_format_has_a_single_definition() -> None:
    offenders: list[str] = []
    for path in sorted((*(_REPO / "automation").rglob("*.py"), *(_REPO / "skills").rglob("*.py"))):
        relative = path.relative_to(_REPO).as_posix()
        if relative == _POINTER_FILE:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=relative)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and "APPROVAL-THREAD " in node.value:
                offenders.append(f"{relative}:{node.lineno}")
    assert not offenders, f"APPROVAL-THREAD 줄을 직접 조립하는 사본: {offenders}"
