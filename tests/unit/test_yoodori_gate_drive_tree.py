"""The YooDori gate passes a folder-tree save as one card request (2026-11-23).

``drive-save --manifest <staged json> --folder <parent id>`` carries a whole folder tree in
a handful of arguments, so the 64-argument card limit no longer splits a job into many
cards, and the node's cardless drive-save switch never applies to a tree.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from automation.yoodori_gate import yoodori_read_gate as gate
from tests.unit.test_yoodori_gate import ORIGIN, PY

FOLDER = "1AbCdEfGhIjKlMnOp"


@pytest.fixture
def staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / ".local/share/yoodori-staging"
    root.mkdir(parents=True)
    (root / "manifest.json").write_text('{"version": 1, "files": []}', encoding="utf-8")
    (root / "a.las").write_bytes(b"a\n")
    monkeypatch.setattr(gate, "HOME", tmp_path)
    monkeypatch.setattr(gate, "STAGING", root)
    monkeypatch.setattr(gate, "_log", lambda *_: None)
    return root


def test_a_staged_manifest_and_its_parent_folder_become_one_request(staging: Path) -> None:
    name, argv, cwd = gate.build(["drive-save", "--manifest", "manifest.json", "--folder", FOLDER,
                                  "--summary", "측량 자료", *ORIGIN])
    assert (name, cwd) == ("drive-save", gate.RELEASE)
    assert argv == [PY, "-m", "automation.effect_request", "request", "drive-save",
                    "--manifest", str(staging / "manifest.json"), "--folder", FOLDER,
                    "--summary", "측량 자료", *ORIGIN]


@pytest.mark.parametrize("extra, reason", [
    (["--manifest", "manifest.json"], "--folder"),
    (["--manifest", "manifest.json", "--folder", FOLDER, "--file", "a.las"], "exactly one"),
    (["--manifest", "manifest.json", "--folder", FOLDER, "--title", "t"], "no --title"),
    (["--manifest", "manifest.json", "--folder", FOLDER, "--kind", "doctype"], "no --title"),
    (["--manifest", "/etc/passwd", "--folder", FOLDER], "staged regular file"),
    (["--folder", FOLDER], "exactly one"),
])
def test_a_tree_request_outside_its_shape_is_refused(staging: Path, extra: list[str], reason: str) -> None:
    with pytest.raises(gate.Refused, match=reason):
        gate.build(["drive-save", *extra, "--summary", "s", *ORIGIN])


def test_a_tree_stays_a_card_even_when_the_cardless_switch_is_on(
    staging: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ran: list[list[str]] = []
    jobs: list[str] = []
    monkeypatch.setattr(gate, "_switched", lambda kind: True)
    monkeypatch.setattr(gate, "card_env", lambda *args: {})
    monkeypatch.setattr(gate, "run_job", lambda job: jobs.append(job.argv[3]) or 0)
    monkeypatch.setattr(gate.subprocess, "run",
                        lambda argv, **kwargs: ran.append(argv) or subprocess.CompletedProcess(argv, 0))
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", " ".join(
        ["drive-save", "--manifest", "manifest.json", "--folder", FOLDER, "--summary", "tree", *ORIGIN]))
    assert gate.main() == 0
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", " ".join(
        ["drive-save", "--file", "a.las", "--summary", "one", *ORIGIN]))
    assert gate.main() == 0
    assert [argv[3:5] for argv in ran] == [["request", "drive-save"]] and jobs == ["save-now"]


def test_drive_save_card_keeps_an_overwrite_on_its_own_card_with_the_switch_on(
    staging: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ran: list[list[str]] = []
    monkeypatch.setattr(gate, "_switched", lambda kind: True)
    monkeypatch.setattr(gate, "card_env", lambda *args: {})
    monkeypatch.setattr(gate, "run_job", lambda job: pytest.fail("an overwrite went cardless"))
    monkeypatch.setattr(gate.subprocess, "run",
                        lambda argv, **kwargs: ran.append(argv) or subprocess.CompletedProcess(argv, 0))
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", " ".join(
        ["drive-save", "--card", "--folder", FOLDER, "--file", "a.las", "--summary", "overwrite", *ORIGIN]))
    assert gate.main() == 0
    assert [argv[3:5] for argv in ran] == [["request", "drive-save"]] and "--card" not in ran[0]
