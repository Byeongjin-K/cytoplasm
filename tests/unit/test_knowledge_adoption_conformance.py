"""Knowledge Adoption Contract v1 boundary guards."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

_REPO: Final = Path(__file__).resolve().parents[2]
KNOWLEDGE_ADOPTERS: Final[dict[str, str]] = {
    "mail": "skills/mail/scripts/mail_knowledge.py",
    "meeting": "skills/meeting/scripts/meeting_knowledge.py",
    "proposal": "skills/proposal/scripts/proposal_knowledge.py",
    "report": "skills/report/scripts/report_knowledge.py",
    "wiki": "skills/wiki/scripts/wiki_knowledge.py",
}
_EXEMPT: Final[dict[str, str]] = {
    "recall": "phase-1 conversation front and legacy recall-v1 compatibility caller",
    "wiki": "adopted facade consultation plus bounded self-store CLI reads for query, backlinks, cleanup, and tag vocabulary",
    "twin_distill": "decision-twin write-side producer; it consumes approved recall files",
}
_DIRECT = frozenset({"search_memory", "query_notes", "consult"})
_BANNED_IMPORTS = ("mcp_client", "wiki_store", "qdrant")


def _python_files(root: Path) -> tuple[Path, ...]:
    return tuple(sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts))


def _calls(path: Path) -> tuple[tuple[str, int], ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    calls: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        name = function.id if isinstance(function, ast.Name) else function.attr if isinstance(function, ast.Attribute) else ""
        if name in _DIRECT:
            calls.append((name, node.lineno))
    return tuple(calls)


def test_adopters_import_only_the_knowledge_facade() -> None:
    failures: list[str] = []
    for adopter, relative in KNOWLEDGE_ADOPTERS.items():
        path = _REPO / relative
        if not path.is_file():
            failures.append(f"{adopter}: adapter missing: {relative}")
            continue
        source = path.read_text(encoding="utf-8")
        if "automation.knowledge.facade" not in source:
            failures.append(f"{adopter}: facade import missing")
        if any(name in source for name in _BANNED_IMPORTS):
            failures.append(f"{adopter}: direct store import")
    assert not failures, "knowledge adoption failures:\n" + "\n".join(failures)


def test_skills_have_no_uninventoried_direct_store_reads() -> None:
    failures: list[str] = []
    for path in _python_files(_REPO / "skills"):
        skill = path.relative_to(_REPO / "skills").parts[0]
        if skill in _EXEMPT or skill in KNOWLEDGE_ADOPTERS:
            continue
        failures.extend(f"{path.relative_to(_REPO)}:{line}: direct {name}()" for name, line in _calls(path))
    assert not failures, "knowledge adoption failures:\n" + "\n".join(failures)


def test_citation_literals_are_created_only_by_render_citations() -> None:
    failures: list[str] = []
    for path in _python_files(_REPO / "skills"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value
                if any(f"[E{number}]" in value for number in range(1, 21)):
                    failures.append(f"{path.relative_to(_REPO)}:{node.lineno}: citation literal")
    assert not failures, "knowledge adoption failures:\n" + "\n".join(failures)


def test_wiki_adoption_keeps_only_its_bounded_self_store_exception() -> None:
    assert set(KNOWLEDGE_ADOPTERS).intersection(_EXEMPT) == {"wiki"}
    assert "self-store CLI reads" in _EXEMPT["wiki"]
    targets = {
        "wiki_cli.py": {"cmd_consult"},
        "wiki_evidence.py": {"collect", "command_consult"},
    }
    for filename, functions in targets.items():
        path = _REPO / "skills" / "wiki" / "scripts" / filename
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in functions:
                direct = [name for name, _ in _calls_in_node(node)]
                assert not set(direct).intersection(_DIRECT), f"{filename}:{node.name}: {direct}"


def _calls_in_node(node: ast.AST) -> tuple[tuple[str, int], ...]:
    calls: list[tuple[str, int]] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        function = child.func
        name = function.id if isinstance(function, ast.Name) else function.attr if isinstance(function, ast.Attribute) else ""
        if name in _DIRECT:
            calls.append((name, child.lineno))
    return tuple(calls)


def test_knowledge_exemptions_are_not_stale() -> None:
    assert all(reason.strip() for reason in _EXEMPT.values())
    missing = [skill for skill in _EXEMPT if not ((_REPO / "skills" / skill).is_dir() or (_REPO / "automation" / skill).is_dir())]
    assert not missing, f"stale knowledge exemptions: {missing}"
