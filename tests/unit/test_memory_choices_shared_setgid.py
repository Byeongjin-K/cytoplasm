"""The shared memory-choices folder keeps setgid even when ops is not in the service group.

PR #104 gap 2 (2026-10-19): the kernel clears S_ISGID when a non-member chmods, so the folder
stayed 1770 and the deployer refused for good. The fake chmod below does what that kernel does.
New file so the recorded choice-account cases keep their shape.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from automation.memory_relocate import choice_accounts as accounts


@pytest.fixture
def parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "handoff"
    folder.mkdir()
    folder.chmod(0o2750)  # the setgid hand-off parent
    real = Path.chmod

    def non_member_chmod(self: Path, mode: int, **kwargs: object) -> None:
        real(self, mode & ~stat.S_ISGID, **kwargs)

    monkeypatch.setattr(Path, "chmod", non_member_chmod)
    return folder


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_a_new_folder_keeps_setgid_through_inheritance(parent: Path) -> None:
    shared = parent / "memory-choices"
    accounts.provision_shared(shared, os.getgid())
    assert _mode(shared) == 0o3770


def test_an_empty_folder_left_without_setgid_is_made_again(parent: Path) -> None:
    shared = parent / "memory-choices"
    shared.mkdir()
    os.chmod(shared, 0o1770)
    accounts.provision_shared(shared, os.getgid())
    assert _mode(shared) == 0o3770


def test_a_folder_with_contents_is_never_touched(parent: Path) -> None:
    shared = parent / "memory-choices"
    shared.mkdir()
    os.chmod(shared, 0o1770)
    (shared / "agent").mkdir()
    accounts.provision_shared(shared, os.getgid())
    assert _mode(shared) == 0o1770 and (shared / "agent").is_dir()
