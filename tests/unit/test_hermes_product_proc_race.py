"""A process that disappears while the Hermes-product check reads /proc is not a refusal.

2026-10-19 the v2.6.3 rehearsal was refused once with 사용 경로 판독 불가 at the hermes-product
step; the same check against the node's release passed, and a rerun passed. A process of the
same account exiting mid-scan made reading its fd table fail. A process that is still there and
denies access remains a refusal. New file so the recorded product cases keep their shape.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from automation import hermes_product_live as live


def _proc(tmp_path: Path, pid: str) -> Path:
    entry = tmp_path / "proc" / pid
    (entry / "fd").mkdir(parents=True)
    (entry / "cwd").symlink_to(tmp_path)
    return entry


def test_a_process_that_vanishes_during_the_fd_read_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _proc(tmp_path, "4242")
    real = Path.iterdir

    def iterdir(self: Path):
        if self == entry / "fd":
            raise ProcessLookupError(3, "No such process")
        return real(self)

    monkeypatch.setattr(Path, "iterdir", iterdir)
    assert live.process_paths(tmp_path / "proc") == ()


def test_a_permission_denial_from_a_process_already_gone_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _proc(tmp_path, "4243")
    real = Path.iterdir

    def iterdir(self: Path):
        if self == entry / "fd":
            (entry / "cwd").unlink()
            (entry / "fd").rmdir()
            entry.rmdir()  # the process exits while we read it
            raise PermissionError(13, "Permission denied")
        return real(self)

    monkeypatch.setattr(Path, "iterdir", iterdir)
    monkeypatch.setattr(live, "_opaque_helper", lambda entry: False)
    assert live.process_paths(tmp_path / "proc") == ()


def test_a_live_process_that_denies_access_still_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _proc(tmp_path, "4244")
    real = Path.iterdir

    def iterdir(self: Path):
        if self == entry / "fd":
            raise PermissionError(13, "Permission denied")
        return real(self)

    monkeypatch.setattr(Path, "iterdir", iterdir)
    monkeypatch.setattr(live, "_opaque_helper", lambda entry: False)
    with pytest.raises(PermissionError):
        live.process_paths(tmp_path / "proc")
    assert os.getuid() >= 0
