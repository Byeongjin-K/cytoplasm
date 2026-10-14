"""Contract for the memory-curator-v1 report shape."""

from __future__ import annotations

import json
from pathlib import Path

from automation.memory_curator.apply import apply_curation
from automation.memory_curator.report import SCHEMA, build_report


def test_report_schema_and_fields(tmp_path: Path) -> None:
    (tmp_path / "USER.md").write_text(
        "이름은 <owner-name>\n§\n앞으로 배려를 원칙으로 한다\n§\n이름은 <owner-name>",
        encoding="utf-8",
    )
    report = build_report(apply_curation(tmp_path, "user", dry_run=False))
    assert report["schema"] == SCHEMA
    assert report["kind"] == "user"
    assert report["char_cap"] == 3000
    assert report["changed"] is True
    assert isinstance(report["freed_chars"], int) and report["freed_chars"] > 0
    assert report["backup"] is not None
    assert any("원칙" in c for c in report["promotion_candidates"])  # type: ignore[union-attr]
    assert 0.0 <= float(report["fill_ratio"]) <= 1.0  # type: ignore[arg-type]


def test_report_dry_run_has_no_backup(tmp_path: Path) -> None:
    (tmp_path / "MEMORY.md").write_text("A\n§\nA", encoding="utf-8")
    report = build_report(apply_curation(tmp_path, "memory", dry_run=True))
    assert report["changed"] is True
    assert report["backup"] is None
    assert report["promotion_candidates"] == []


def test_report_is_json_serializable(tmp_path: Path) -> None:
    (tmp_path / "MEMORY.md").write_text("fact one\n§\nfact two", encoding="utf-8")
    report = build_report(apply_curation(tmp_path, "memory", dry_run=True))
    # must not raise
    _ = json.dumps(report, ensure_ascii=False)
