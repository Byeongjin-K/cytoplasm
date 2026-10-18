"""Owner Q241/Q243 (2026-10-18): daily omo-memory copy and mirror reads through the YooDori gate.

A new file because test_yoodori_gate.py pins the original gate argv byte for byte.
"""
from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import pytest

from automation import auto_write
from automation.yoodori_gate import yoodori_read_gate as gate

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(gate, "HOME", tmp_path)
    monkeypatch.setattr(gate, "STAGING", tmp_path / "staging")
    monkeypatch.setattr(gate, "MIRROR", tmp_path / "mirror")
    monkeypatch.setattr(gate, "_log", lambda *a: None)
    return tmp_path


def test_memory_copy_is_a_policy_write_to_one_fixed_note(home: Path) -> None:
    job = gate.memory_copy_job(home / "staging/memory-copy.md")
    assert (job.kind, job.marker, job.write, job.needs_origin) == ("yoodori-memory", gate.WRITTEN, True, False)
    assert job.argv[job.argv.index("--relpath") + 1] == gate.MEMORY_COPY_RELPATH
    script = ("import sys; sys.path.insert(0, sys.argv[1]); "
              "from automation.obsidian_write import note_request_cli as m; m.build_parser().parse_args(sys.argv[2:])")
    parsed = subprocess.run([sys.executable, "-c", script, str(REPO), *job.argv[3:]],
                            capture_output=True, text=True, check=False)
    assert parsed.returncode == 0, parsed.stderr


def test_memory_copy_stages_stdin_and_skips_the_origin_check(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: the switch is on and the owner-origin verifier must never be reached.
    seen: list[gate.Job] = []
    monkeypatch.setattr(gate, "run_job", lambda job: seen.append(job) or 0)
    monkeypatch.setattr(gate.sys, "stdin", io.TextIOWrapper(io.BytesIO("# 기억\n- 형은 회의록 PDF 기본".encode())))
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "memory-copy")
    # When
    assert gate.main() == 0
    # Then: the body was staged and the fixed-note job was built.
    staged = home / "staging/memory-copy.md"
    assert staged.read_text(encoding="utf-8").startswith("# 기억")
    assert seen and seen[0].argv[seen[0].argv.index("--body-file") + 1] == str(staged)


def test_memory_copy_refused_when_its_switch_is_off(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auto_write, "state_root", lambda home=None: Path(gate.HOME) / ".hermes/auto-write")
    with pytest.raises(gate.Refused, match="auto-write-yoodori-memory"):
        gate.run_job(gate.memory_copy_job(home / "x.md"))


def test_note_get_reads_only_inside_the_mirror(home: Path) -> None:
    note = gate.MIRROR / "000_PARA/Area/People/홍길동.md"
    note.parent.mkdir(parents=True)
    note.write_text("# 홍길동", encoding="utf-8")
    name, argv, _ = gate.build(["note-get", "000_PARA/Area/People/홍길동.md"])
    assert name == "note-get" and argv == ["/usr/bin/cat", "--", str(note.resolve())]
    for bad in ("../../.env.secrets", "000_PARA/../x.md", "000_PARA/none.md", "notes/x.md"):
        with pytest.raises(gate.Refused):
            gate.build(["note-get", bad])
    (gate.MIRROR / "000_PARA/link.md").symlink_to(note)
    with pytest.raises(gate.Refused):
        gate.build(["note-get", "000_PARA/link.md"])
