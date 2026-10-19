"""Name-level Python 3.11 guard for gateway and cron import roots.

The gateway imports skill plugins directly and no-agent cron jobs load these
automation packages under Python 3.11. Keep this scan separate from the
FS3-pinned Plaud guard so its historical evidence remains reproducible.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

_REPO: Final = Path(__file__).resolve().parents[2]
_PACKAGES: Final = (
    _REPO / "automation" / "interop",
    _REPO / "automation" / "group_roster",
    _REPO / "automation" / "skill_generation",
    _REPO / "automation" / "selfskill_audit",
    _REPO / "automation" / "memory_curator",
    _REPO / "automation" / "rag_ingest",
    _REPO / "automation" / "research_trends",
    _REPO / "skills" / "meeting" / "plugin",
)
_MODULES: Final = (
    _REPO / "automation" / "owner_notice.py",
    _REPO / "automation" / "skill_review.py",
    _REPO / "automation" / "skill_mount.py",
    _REPO / "automation" / "scenario_runner.py",
    _REPO / "automation" / "runtime_root.py",
    _REPO / "automation" / "gateway_generation.py",
    _REPO / "automation" / "repair" / "repair_capability.py",
    _REPO / "automation" / "repair" / "repair_report_consumer.py",
    _REPO / "automation" / "repair" / "repair_report_queue.py",
    _REPO / "automation" / "repair" / "repair_report_send.py",
)
_POST_311_TYPING_NAMES: Final = frozenset(
    {
        "override",
        "TypeAliasType",
        "get_protocol_members",
        "is_protocol",
        "ReadOnly",
        "TypeIs",
        "NoDefault",
    }
)


def _scan(paths: tuple[Path, ...]) -> tuple[int, list[str]]:
    offenders: list[str] = []
    scanned = 0
    for path in paths:
        if not path.exists():
            continue
        files = (path,) if path.is_file() else tuple(path.rglob("*.py"))
        for source_path in sorted(files):
            scanned += 1
            tree = ast.parse(source_path.read_text(encoding="utf-8"), str(source_path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or node.module != "typing":
                    continue
                offenders.extend(
                    f"{source_path}:{node.lineno}: {alias.name}"
                    for alias in node.names
                    if alias.name in _POST_311_TYPING_NAMES
                )
    return scanned, offenders


def test_gateway_and_cron_imports_use_py311_typing_names() -> None:
    scanned, offenders = _scan((*_PACKAGES, *_MODULES))
    assert scanned, "the gateway and cron scan found no Python files"
    assert not offenders, (
        "gateway and cron run CPython 3.11; import these via "
        "automation.typing_compat instead of typing:\n" + "\n".join(offenders)
    )


def test_a_post_311_typing_import_is_reported(tmp_path: Path) -> None:
    source = tmp_path / "post_311.py"
    source.write_text("from typing import override\n", encoding="utf-8")

    scanned, offenders = _scan((source,))

    assert scanned == 1
    assert offenders == [f"{source}:1: override"]


def test_the_scan_is_not_empty() -> None:
    scanned, _ = _scan((*_PACKAGES, *_MODULES))

    assert scanned > 0, "the gateway and cron scan unexpectedly found no files"
