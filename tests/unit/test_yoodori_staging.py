"""The forced key can upload private files and propose an inline Obsidian note."""
from __future__ import annotations

import hashlib
import io
import os
import shlex
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from automation.yoodori_gate import yoodori_read_gate as gate
from tests.unit.test_yoodori_gate import ORIGIN, PY, b64


@pytest.fixture
def private_staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / ".local/share/yoodori-staging"
    monkeypatch.setattr(gate, "HOME", tmp_path)
    monkeypatch.setattr(gate, "STAGING", root)
    monkeypatch.setattr(gate, "_log", lambda *_: None)
    return root


def test_stage_reads_binary_stdin_without_a_child_and_builds_drive_request(
    private_staging: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: arbitrary bytes, including values rejected in the SSH command text.
    data = bytes(range(256)) + b"\x00\xff"
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "stage upload.bin")
    monkeypatch.setattr(gate.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(data)))
    def no_child(*_args: object, **_kwargs: object) -> None:
        pytest.fail("private staging spawned a process")
    monkeypatch.setattr(gate.subprocess, "run", no_child)
    # When
    assert gate.main() == 0
    # Then
    path = private_staging / "upload.bin"
    assert path.read_bytes() == data
    assert path.stat().st_mode & 0o777 == 0o600
    assert private_staging.stat().st_mode & 0o777 == 0o700
    assert list(private_staging.iterdir()) == [path]
    assert capsys.readouterr().out == (
        f"STAGED upload.bin sha256={hashlib.sha256(data).hexdigest()} bytes={len(data)} path={path}\n")
    assert gate.build(["drive-save", "--file", str(path), "--summary", "파일 저장", *ORIGIN]) == (
        "drive-save", [PY, "-m", "automation.effect_request", "request", "drive-save",
                       "--file", str(path), "--summary", "파일 저장", *ORIGIN], gate.RELEASE)


@pytest.mark.parametrize("existing", [False, True])
def test_stage_oversize_refuses_and_removes_only_the_partial_file(
    private_staging: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], existing: bool,
) -> None:
    # Given: one byte over the actual 64 MiB cap and optionally an older completed upload.
    private_staging.mkdir(parents=True)
    path = private_staging / "large.bin"
    if existing:
        path.write_bytes(b"previous")
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "stage large.bin")
    monkeypatch.setattr(gate.sys, "stdin", SimpleNamespace(
        buffer=io.BytesIO(b"x" * (64 * 1024 * 1024 + 1))))
    # When
    assert gate.main() == 2
    # Then: no ACK, no partial, and a prior completed file is not lost.
    output = capsys.readouterr()
    assert output.out == ""
    assert "YOODORI-GATE-REFUSED stage file exceeds 64 MiB" in output.err
    assert sorted(p.name for p in private_staging.iterdir()) == (["large.bin"] if existing else [])
    if existing:
        assert path.read_bytes() == b"previous"


@pytest.mark.parametrize("name", [
    ".hidden", ".", "..", "../outside", "folder/file", "two words", "x" * 121,
    "한글", "a;b", "a\nb", "a\\b", "",
])
def test_bad_stage_names_are_refused_before_reading_stdin(
    private_staging: Path, name: str,
) -> None:
    # Given / When / Then
    with pytest.raises(gate.Refused):
        gate.build(["stage", name])
    assert not private_staging.exists()


def test_stage_requires_exactly_one_name() -> None:
    # Given / When / Then
    for words in (["stage"], ["stage", "a", "b"]):
        with pytest.raises(gate.Refused):
            gate.build(words)


def test_staging_replaces_a_destination_symlink_not_its_target(
    private_staging: Path, tmp_path: Path,
) -> None:
    # Given
    private_staging.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.write_bytes(b"preserve")
    (private_staging / "file").symlink_to(outside)
    # When
    path, digest, size = gate.stage_file(private_staging, "file", io.BytesIO(b"new"))
    # Then
    assert not path.is_symlink()
    assert path.read_bytes() == b"new"
    assert (digest, size) == (hashlib.sha256(b"new").hexdigest(), 3)
    assert outside.read_bytes() == b"preserve"


def test_obsidian_b64_body_is_staged_before_only_the_existing_request_cli(
    private_staging: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    body = "# 제목\n내용; $(그대로) & <기호>"
    (gate.HOME / ".env.secrets").write_text("DISCORD_BOT_TOKEN=private-value\n")
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", shlex.join([
        "obsidian-request", "--relpath", "000_PARA/Resource/note.md",
        "--body-b64", b64(body), *ORIGIN]))
    path = private_staging / f"note-{hashlib.sha256(body.encode()).hexdigest()}.md"
    calls: list[list[str]] = []
    def run(argv: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append(argv)
        assert path.read_bytes() == body.encode()
        assert path.stat().st_mode & 0o777 == 0o600
        assert kwargs["stdin"] == subprocess.DEVNULL
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(gate.subprocess, "run", run)
    # When / Then
    assert gate.main() == 0
    assert calls == [[PY, "-m", "automation.obsidian_write", "request",
                      "--relpath", "000_PARA/Resource/note.md", "--body-file", str(path), *ORIGIN]]


@pytest.mark.parametrize("extra", [
    [], ["--body", "raw"], ["--body-b64", "!"], ["--body-b64", b64("\x00")],
    ["--body-b64", b64("x" * 8001)],
])
def test_invalid_inline_obsidian_input_does_not_stage(
    private_staging: Path, extra: list[str],
) -> None:
    # Given / When / Then
    with pytest.raises(gate.Refused):
        gate.build(["obsidian-request", "--relpath", "000_PARA/Resource/note.md", *extra, *ORIGIN])
    assert not private_staging.exists()


def test_obsidian_body_forms_are_mutually_exclusive(private_staging: Path) -> None:
    # Given
    private_staging.mkdir(parents=True)
    path = private_staging / "body.md"
    path.write_text("old")
    # When / Then
    with pytest.raises(gate.Refused):
        gate.build(["obsidian-request", "--relpath", "000_PARA/Resource/note.md",
                    "--body-file", str(path), "--body-b64", b64("new"), *ORIGIN])
    assert path.read_text() == "old"
    assert list(private_staging.iterdir()) == [path]


def test_stage_body_matches_only_option_positions(private_staging: Path) -> None:
    # Given: a title equal to the internal body marker is data, not another option.
    argv = [PY, "-m", "automation.obsidian_write", "request", "--title", "--body",
            "--body-file", "/existing/file"]
    # When / Then
    assert gate.stage_body(argv, private_staging) == argv
    assert not private_staging.exists()


def test_actual_isolated_gate_accepts_ssh_stdin(tmp_path: Path) -> None:
    # Given: exercise the sshd entry without any real SSH connection or credentials.
    data = b"staged through forced-command stdin"
    # When
    process = subprocess.run(
        [PY, "-I", gate.__file__], cwd=tmp_path, input=data, capture_output=True, check=False,
        env={**os.environ, "HOME": str(tmp_path), "SSH_ORIGINAL_COMMAND": "stage file.txt"})
    # Then
    assert process.returncode == 0, process.stderr.decode()
    path = tmp_path / ".local/share/yoodori-staging/file.txt"
    assert path.read_bytes() == data
    assert process.stdout.decode() == (
        f"STAGED file.txt sha256={hashlib.sha256(data).hexdigest()} bytes={len(data)} path={path}\n")
