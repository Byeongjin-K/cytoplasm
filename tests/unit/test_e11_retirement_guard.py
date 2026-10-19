"""Completeness guard for the E11 drive-archive retirement.

drive-archive mirrored git-tracked developer docs (.omo/plans, notepads,
docs/features·qa·patch) to Drive — pure redundancy with GitHub, so the owner
retired it. This guard is the RED→GREEN gate for the removal: it fails while any
drive-archive code path survives and passes once the subsystem is gone. The
shared ``DriveClient`` remains, while the former vendored output publishers were
subsequently retired in favor of ``automation.drive_outputs``.

Scope is code paths only (automation/, skills/, configs/, tests/). Docs keep
legitimate historical mentions (past-incident citations, discord-arch history);
their freshness is enforced by the removal task list, not by this scanner.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SELF = Path(__file__).resolve()
_CODE_ROOTS = ("automation", "skills", "configs", "tests")
_FORBIDDEN = ("drive_archive", "DRIVE_ARCHIVE", "Flow.DRIVE")


def _tracked_code_files() -> list[Path]:
    found: list[Path] = []
    for root in _CODE_ROOTS:
        for path in (_REPO / root).rglob("*"):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            if path.resolve() == _SELF:
                continue
            found.append(path)
    return found


def test_drive_archive_package_is_gone() -> None:
    assert not (_REPO / "automation" / "drive_archive").exists()


def test_drive_archive_test_files_are_gone() -> None:
    stray = sorted(
        p.relative_to(_REPO).as_posix()
        for p in (_REPO / "tests").rglob("*drive_archive*")
        if p.resolve() != _SELF
    )
    assert stray == []


def test_no_forbidden_drive_archive_tokens_in_code() -> None:
    offenders: list[str] = []
    for path in _tracked_code_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for token in _FORBIDDEN:
            if token in text:
                offenders.append(f"{path.relative_to(_REPO).as_posix()}: {token}")
    assert offenders == []


def test_approval_kind_has_no_drive_archive() -> None:
    from automation.interop.approval_surface import ApprovalKind

    assert not hasattr(ApprovalKind, "DRIVE_ARCHIVE")
    assert "drive-archive" not in {kind.value for kind in ApprovalKind}


def test_audit_flow_has_no_drive() -> None:
    from automation.interop.approval_surface_audit import Flow

    assert not hasattr(Flow, "DRIVE")


def test_denylist_has_no_drive_archive_rule() -> None:
    # The entire Hermes denylist is retired, including the old archive rule.
    assert not (_REPO / "configs" / "external-effect-tools.yaml").exists()


# --- survival: the systems that MUST keep working -------------------------


def test_shared_drive_client_survives_relocated() -> None:
    assert (_REPO / "automation" / "drive_client.py").exists()
    spec = importlib.util.find_spec("automation.drive_client")
    assert spec is not None


def test_drive_outputs_facade_survives_vendored_publisher_retirement() -> None:
    assert (_REPO / "automation" / "drive_outputs.py").exists()
    assert not list((_REPO / "skills").glob("**/scripts/drive_publish.py"))


def test_doctype_save_strictly_delegates_drive_upload_to_the_facade() -> None:
    text = (_REPO / "skills" / "doctype" / "scripts" / "doctype_save.py").read_text(
        encoding="utf-8"
    )
    assert "from automation.drive_outputs import publish" in text
    assert 'publish("doctype", artifact.stem, [(artifact, artifact.stem)])' in text
    assert 'DocumentSaveError("drive", str(error))' in text


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
