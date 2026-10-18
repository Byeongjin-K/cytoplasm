"""Owner-command Drive saves reuse the approved executor and verified publishing facade."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from automation import drive_outputs
from automation.drive_client import DriveClient, DriveClientError
from automation.effect_request import cli
from automation.effect_request.model import EffectRequestStore
from tests.unit.test_drive_outputs import FakeGws


@pytest.fixture
def drive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[FakeGws, DriveClient, Path]:
    fake = FakeGws()
    client = DriveClient("fake-gws", tmp_path / "folders.json", runner=fake)
    source = tmp_path / "result.csv"
    source.write_bytes(b"a,b\n")
    monkeypatch.setenv("EFFECT_REQUEST_ROOT", str(tmp_path / "state"))
    monkeypatch.setattr(drive_outputs, "client_from_environment", lambda: client)

    def no_card(*args, **kwargs) -> None:  # noqa: ANN002, ANN003
        pytest.fail("save-now must not create a Discord runtime or approval card")

    monkeypatch.setattr(cli, "discord_runtime", no_card)
    monkeypatch.setattr(cli, "request_approval", no_card)
    return fake, client, source


def _argv(source: Path, summary: str = "조회 결과 저장") -> list[str]:
    return [
        "save-now", "--file", str(source), "--summary", summary,
        "--title", "결과", "--project", "과제X", "--kind", "doctype", "--on", "2026-10-18",
        "--origin-channel-id", "100", "--origin-message-id", "200",
    ]


def test_save_now_publishes_frozen_bytes_and_prints_marker_after_sha_verification(
    drive: tuple[FakeGws, DriveClient, Path], monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake, client, source = drive

    def client_after_snapshot() -> DriveClient:
        source.write_bytes(b"changed after snapshot\n")
        return client

    monkeypatch.setattr(drive_outputs, "client_from_environment", client_after_snapshot)

    code = cli.main(_argv(source))

    assert code == 0
    record, = EffectRequestStore().all()
    assert record.status == "done" and record.message_id is None and record.attempts == 1
    assert record.origin_channel_id == "100" and record.origin_message_id == "200"
    assert record.receipt == {
        "links": [f"https://drive.google.test/{next(iter(fake.file_bytes))}"],
        "action": "created", "sha256": hashlib.sha256(b"a,b\n").hexdigest(),
        "owner_only": True, "method": "owner_command",
        "origin_channel_id": "100", "origin_message_id": "200",
    }
    assert list(fake.file_bytes.values()) == [b"a,b\n"]
    assert Path(str(record.payload["frozen_path"])).read_bytes() == b"a,b\n"
    assert any(call[2:4] == ["permissions", "list"] for call in fake.calls)
    assert any(call[2:4] == ["files", "get"] and "-o" in call for call in fake.calls)
    assert capsys.readouterr().out == f"OWNER-COMMAND-WRITTEN drive-save {record.receipt['links'][0]}\n"


@pytest.mark.parametrize("failure", ["publish", "sha", "sharing"])
def test_save_now_failure_has_no_success_marker_or_success_receipt(
    drive: tuple[FakeGws, DriveClient, Path], monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], failure: str,
) -> None:
    fake, _, source = drive
    match failure:
        case "publish":
            def fail_upload(*args, **kwargs) -> None:  # noqa: ANN002, ANN003
                raise DriveClientError("upload failed")

            monkeypatch.setattr(DriveClient, "upsert_file", fail_upload)
        case "sha":
            def corrupt_download(self: DriveClient, file_id: str, dest: Path, directory: Path) -> None:
                dest.write_bytes(b"corrupt remote bytes")

            monkeypatch.setattr(DriveClient, "_fetch_media", corrupt_download)
        case "sharing":
            fake.permissions.append({"id": "public", "type": "anyone", "role": "reader"})

    code = cli.main(_argv(source))

    assert code != 0
    assert not any(line.startswith("OWNER-COMMAND-WRITTEN ") for line in capsys.readouterr().out.splitlines())
    record, = EffectRequestStore().all()
    assert record.status == "failed" and record.receipt is None and record.last_error


def test_save_now_refuses_owner_only_requests_before_freezing_or_publishing(
    drive: tuple[FakeGws, DriveClient, Path], capsys: pytest.CaptureFixture[str],
) -> None:
    fake, _, source = drive

    code = cli.main(_argv(source, summary="비밀번호 변경"))

    output = capsys.readouterr()
    assert code == cli.EXIT_REFUSED
    assert "owner-only:credential" in output.err
    assert not output.out and not fake.calls
    assert not EffectRequestStore().all()
