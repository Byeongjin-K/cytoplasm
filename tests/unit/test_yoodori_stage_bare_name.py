"""A staged file can be named by its STAGED name alone, still only inside the staging folder.

2026-10-19: YooDori staged a file, then `mail-compose --attachment <name>` was refused for every
form it tried, because only an absolute path was accepted and the STAGED line printed no path.
New file so the forced-command cases in test_yoodori_gate.py keep their recorded shape.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from automation.yoodori_gate import yoodori_read_gate as gate
from tests.unit.test_yoodori_gate import ORIGIN
from tests.unit.test_yoodori_gate import staged as staged  # noqa: F401


def test_a_bare_staged_name_becomes_its_staging_path(staged: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gate, "_owner_id", lambda: "123456789")
    _, argv, _ = gate.build(["mail-compose", "--to", "reader@example.invalid", "--subject", "안내",
                             "--body", "본문", "--attachment", "note.md", *ORIGIN])
    assert argv[argv.index("--attachment") + 1] == str(staged)
    _, argv, _ = gate.build(["drive-save", "--file", "note.md", "--summary", "파일 저장", *ORIGIN])
    assert argv[argv.index("--file") + 1] == str(staged)


@pytest.mark.parametrize("name", ["alias", "absent", ".hidden", "stage/note.md", "../note.md"])
def test_a_bare_name_still_refuses_links_missing_files_and_paths(staged: Path, name: str) -> None:
    (gate.STAGING / "alias").symlink_to(staged)
    with pytest.raises(gate.Refused):
        gate.build(["drive-save", "--file", name, "--summary", "파일 저장", *ORIGIN])
