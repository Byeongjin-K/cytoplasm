"""Conformance pin for the per-request approval thread (AGENTS.md 「요청별 승인 스레드 규칙」).

Owner decision 2026-09-01: every owner-only approval producer resolves its
binding with ``request=RequestThread(...)`` so the approval, its reminders and
the result notice complete in ONE thread per request, and it persists that
thread as ``approval_thread_id`` so ``origin_notice`` can find it without
guessing. A producer that keeps calling ``resolve_new_binding`` without a spec
silently falls back to the legacy per-kind thread — the exact split this rule
removes — so prose is not enough: this test fails the build for it.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Final

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "tests" / "unit"))

from approval_conformance_ast import _deployed_sources  # noqa: E402

_RESOLVER: Final = "resolve_new_binding"
_THREAD_FIELD: Final = "approval_thread_id"
_REUSE_MARKERS: Final = ("reuse_request_thread(", "reusable_binding(")
_DEFINITION_MODULE: Final = "automation/interop/approval_surface.py"

_KIND_THREAD_EXEMPT: Final[dict[str, str]] = {
    "automation/skill_gate_surface.py::SupplyChainSurface.new": (
        "공급망 승인(skill-deploy·attest·publish·submit·managed-activate·release)은 "
        "SKILL_APPROVALS 표면 — 2차 주체인 peer 봇이 같은 채널을 봐야 하므로 요청별 "
        "스레드 대상이 아니다 (2026-09-01 결정, 범위 밖)."
    ),
}


def _called_name(call: ast.Call) -> str | None:
    match call.func:
        case ast.Name(id=name):
            return name
        case ast.Attribute(attr=name):
            return name
        case _:
            return None


def _resolver_sites(tree: ast.Module) -> dict[str, ast.Call]:
    sites: dict[str, ast.Call] = {}

    def visit(node: ast.AST, scope: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                visit(child, (*scope, child.name))
                continue
            if isinstance(child, ast.Call) and _called_name(child) == _RESOLVER:
                sites[".".join(scope) or "<module>"] = child
            visit(child, scope)

    visit(tree, ())
    return sites


def _passes_request(call: ast.Call) -> bool:
    return any(keyword.arg == "request" for keyword in call.keywords)


def _sites() -> dict[str, ast.Call]:
    found: dict[str, ast.Call] = {}
    for path in _deployed_sources():
        relative = str(path.relative_to(_REPO))
        if relative == _DEFINITION_MODULE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        for qualname, call in _resolver_sites(tree).items():
            found[f"{relative}::{qualname}"] = call
    return found


def _package_mentions(surface: str, needles: tuple[str, ...]) -> bool:
    module = Path(surface.split("::")[0])
    return any(
        needle in path.read_text(encoding="utf-8", errors="ignore")
        for path in (_REPO / module.parent).glob("*.py")
        for needle in needles
    )


def test_every_owner_approval_producer_resolves_a_request_thread() -> None:
    failures = [
        f"{surface}: resolve_new_binding 에 request= 가 없다 — 승인이 kind 스레드로 "
        "떨어져 결과 통지와 갈라진다 (AGENTS.md 「요청별 승인 스레드 규칙」)"
        for surface, call in sorted(_sites().items())
        if surface not in _KIND_THREAD_EXEMPT and not _passes_request(call)
    ]
    assert not failures, "\n".join(failures)


def test_request_thread_producers_persist_the_thread_id() -> None:
    failures = [
        f"{surface}: 같은 패키지 어디에도 `{_THREAD_FIELD}` 가 없다 — origin_notice 가 "
        "승인 스레드를 찾을 수 없어 결과가 다른 스레드로 간다"
        for surface, call in sorted(_sites().items())
        if surface not in _KIND_THREAD_EXEMPT
        and _passes_request(call)
        and not _package_mentions(surface, (_THREAD_FIELD,))
    ]
    assert not failures, "\n".join(failures)


def test_request_thread_producers_reuse_the_live_thread() -> None:
    failures = [
        f"{surface}: 같은 키의 살아 있는 요청이 연 스레드를 재사용하지 않는다 — 재요청·대체마다 "
        "빈 스레드가 남는다 (approval_surface.reuse_request_thread 또는 저장 바인딩 재사용)"
        for surface, call in sorted(_sites().items())
        if surface not in _KIND_THREAD_EXEMPT
        and _passes_request(call)
        and not _package_mentions(surface, _REUSE_MARKERS)
    ]
    assert not failures, "\n".join(failures)


def test_kind_thread_exemptions_are_not_stale() -> None:
    sites = _sites()
    stale = sorted(surface for surface in _KIND_THREAD_EXEMPT if surface not in sites)
    adopted_anyway = sorted(
        surface for surface in _KIND_THREAD_EXEMPT
        if surface in sites and _passes_request(sites[surface])
    )
    assert not stale, f"exempt call sites no longer exist: {stale}"
    assert not adopted_anyway, f"exempt sites already pass request= — drop the exemption: {adopted_anyway}"
    assert all(reason.strip() for reason in _KIND_THREAD_EXEMPT.values())


def test_inventory_covers_the_owner_only_producers() -> None:
    # Given: the deployed call sites minus the supply-chain exemption
    owner_only = sorted(surface for surface in _sites() if surface not in _KIND_THREAD_EXEMPT)
    # Then: every owner-only producer known on 2026-09-01 is still in the inventory
    modules = {surface.split("::")[0] for surface in owner_only}
    assert {
        "skills/mail/scripts/triage_binding.py",
        "skills/calendar/scripts/calendar_binding.py",
        "skills/coordination/scripts/coordination_binding.py",
        "skills/todo/scripts/todo_approval_runtime.py",
        "skills/wiki/scripts/wiki_binding.py",
        "skills/patent-prep/scripts/patent_export_binding.py",
        "automation/repair/repair_ops_binding.py",
    } <= modules, modules
