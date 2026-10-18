"""Owner-command CLI uses the real writer with fake Git, never a remote push."""

from __future__ import annotations

import hashlib
from functools import partial
from pathlib import Path

import pytest

from automation.obsidian_write import note_request_cli, write_now
from automation.obsidian_write.config import ObsidianWriteConfig
from automation.obsidian_write.note_request import NoteRequestStore
from automation.obsidian_write.writer import write_note
from tests.unit.test_obsidian_write_writer import FakeGitRunner

REL_PATH = "000_PARA/Resource/Demo/note.md"


@pytest.fixture
def command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[str], ObsidianWriteConfig]:
    # Given: isolated state, body and clone; only the Git transport is fake.
    clone = tmp_path / "clone"
    (clone / ".git").mkdir(parents=True)
    key = tmp_path / "key"
    key.write_text("test key material", encoding="utf-8")
    config = ObsidianWriteConfig("git@example.invalid:vault.git", clone, key)
    body = tmp_path / "body.md"
    body.write_text("# Heading\n\nOwner body", encoding="utf-8")
    monkeypatch.setenv("OBSIDIAN_NOTE_REQUEST_ROOT", str(tmp_path / "state"))
    monkeypatch.setattr(write_now, "load_config", lambda: config)
    return [
        "write-now", "--relpath", REL_PATH, "--body-file", str(body),
        "--title", "Heading", "--origin-channel-id", "123", "--origin-message-id", "456",
    ], config


def test_success_marker_follows_verified_write_and_owner_command_audit(
    command: tuple[list[str], ObsidianWriteConfig],
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    argv, config = command
    runner = FakeGitRunner(config.clone_dir)
    monkeypatch.setattr(write_now, "write_note", partial(write_note, runner=runner))

    # When
    code = note_request_cli.main(argv)

    # Then: the marker certifies the actual rendered bytes read back by the writer.
    digest = hashlib.sha256((config.clone_dir / REL_PATH).read_bytes()).hexdigest()
    assert code == 0
    assert capsys.readouterr().out == f"OWNER-COMMAND-WRITTEN obsidian {REL_PATH} {digest[:12]}\n"
    record, = NoteRequestStore().all()
    assert (record.status, record.method, record.content_sha256) == ("written", "owner_command", digest)
    assert (record.origin_channel_id, record.origin_message_id) == ("123", "456")
    assert record.message_id is None
    assert any(call.argv[:2] == ("git", "push") for call in runner.calls)


def test_readback_mismatch_fails_without_success_marker(
    command: tuple[list[str], ObsidianWriteConfig],
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    argv, config = command
    runner = FakeGitRunner(config.clone_dir, remote_content="wrong remote bytes")
    monkeypatch.setattr(write_now, "write_note", partial(write_note, runner=runner))

    # When
    code = note_request_cli.main(argv)

    # Then
    output = capsys.readouterr()
    assert code != 0
    assert "OWNER-COMMAND-WRITTEN " not in output.out
    assert "verification did not match" in output.err
    record, = NoteRequestStore().all()
    assert record.status == "failed" and record.content_sha256 == ""


@pytest.mark.parametrize("relpath", ["000_PARA/../escape.md", "/000_PARA/note.md", "other/note.md"])
def test_unsafe_relpath_is_refused_before_git(
    command: tuple[list[str], ObsidianWriteConfig],
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], relpath: str,
) -> None:
    # Given
    argv, config = command
    argv[2] = relpath
    runner = FakeGitRunner(config.clone_dir)
    monkeypatch.setattr(write_now, "write_note", partial(write_note, runner=runner))

    # When
    code = note_request_cli.main(argv)

    # Then
    assert code != 0
    assert "OWNER-COMMAND-WRITTEN " not in capsys.readouterr().out
    assert runner.calls == []
    assert NoteRequestStore().all() == ()
