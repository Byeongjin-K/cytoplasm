"""실제 발행 파사드와 가짜 gws로 보관·재개·동시성을 검증한다."""
from __future__ import annotations

import json
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TypeAlias

import pytest

from automation import drive_outputs
from automation.drive_client import DriveClient
from automation.entity_preflight.contracts import JsonValue
from automation.plaud_sync import audio_archive
from automation.plaud_sync.audio import AudioSource
from automation.plaud_sync.audio_manifest import ManifestEntry, audio_digest, manifest_path, record_manifest, rows
from automation.plaud_sync.transcribe import process
from automation.plaud_sync.transcribe_live import LiveEffects
from tests.unit.test_drive_outputs import FakeGws
from tests.unit.test_plaud_sync_transcribe import FakeEffects, _RECORD, _SOURCE, _fail, _ok


class ArchiveGws(FakeGws):
    def __init__(self) -> None:
        super().__init__()
        self.interrupt = False
        self.corrupt = False
        self.uploaded = threading.Event()
        self.release: threading.Event | None = None

    def __call__(self, argv: list[str]) -> dict[str, JsonValue]:
        result = super().__call__(argv)
        if argv[2] == "+upload":
            self.uploaded.set()
            if self.release is not None:
                assert self.release.wait(5), "업로드 해제 신호 없음"
            if self.interrupt:
                raise KeyboardInterrupt
        if argv[2:4] == ["files", "get"]:
            params = json.loads(argv[argv.index("--params") + 1])
            if params.get("fields") == "webViewLink":
                return {"webViewLink": f"https://drive.google.com/file/d/{params['fileId']}/view"}
            if params.get("alt") == "media" and self.corrupt:
                Path(argv[argv.index("-o") + 1]).write_bytes(b"wrong bytes")
        return result

    @property
    def uploads(self) -> int:
        return sum(call[2] == "+upload" for call in self.calls)

    @property
    def mutations(self) -> int:
        return sum(call[2] == "+upload" or "--upload" in call for call in self.calls)


ArchiveSetup: TypeAlias = tuple[ArchiveGws, DriveClient, dict[str, str], Path]


@pytest.fixture
def setup_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ArchiveSetup:
    monkeypatch.setenv("DRIVE_PUBLISH_ENABLED", "1")
    monkeypatch.setenv("DRIVE_OUTPUTS_ROOT", "autophagy")
    fake = ArchiveGws()
    client = DriveClient("fake-gws", tmp_path / "folders.json", runner=fake)
    monkeypatch.setattr(drive_outputs, "client_from_environment", lambda: client)
    env = {"HOME": str(tmp_path), "DRIVE_PUBLISH_ENABLED": "1"}
    audio = tmp_path / "$(touch INJECTION);sample.m4a"
    audio.write_bytes(b"original Plaud container bytes")
    return fake, client, env, audio


def archive(setup: ArchiveSetup, *, expected_sha256: str | None = None) -> bool:
    _, client, env, audio = setup
    return audio_archive.archive_audio(audio, _SOURCE, "sample", env, client=client, expected_sha256=expected_sha256)


def test_happy_and_idempotence_use_original_bytes_and_one_manifest_row(setup_archive: ArchiveSetup) -> None:
    fake, _, env, audio = setup_archive
    before = audio.read_bytes()
    assert archive(setup_archive)
    mutation_count = fake.mutations
    assert archive(setup_archive)
    assert fake.mutations == mutation_count == 1
    assert len(fake.files) == 1
    assert tuple(fake.file_bytes.values()) == (before,)
    parent = "root"
    for name in ("autophagy", "녹음원본", "lifelog", "2026"):
        parent = fake.folders[name, parent]
    assert next(iter(fake.files))[1] == parent
    assert rows(manifest_path(env)) == [{
        "recording_id": audio_digest(audio)[:8], "domain": "lifelog", "audio_sha256": audio_digest(audio),
        "drive_file_id": next(iter(fake.file_bytes)), "duration_ms": _SOURCE.duration_ms,
        "transcript_stem": "sample", "created_at": _SOURCE.created_at,
    }]
    upload = next(call for call in fake.calls if call[2] == "+upload")
    assert upload[3] == str(audio)
    assert not Path("INJECTION").exists()
    assert manifest_path(env).stat().st_mode & 0o777 == 0o600
    assert audio_archive.may_discard(audio, env)


@pytest.mark.parametrize("malformed", ["empty", "sha"])
def test_malformed_input_never_uploads(setup_archive: ArchiveSetup, malformed: str, capsys: pytest.CaptureFixture[str]) -> None:
    fake, _, env, audio = setup_archive
    if malformed == "empty":
        audio.write_bytes(b"")
    assert not archive(setup_archive, expected_sha256="a" * 64)
    assert fake.calls == []
    assert not manifest_path(env).exists()
    assert capsys.readouterr().err.count("AUDIO-ARCHIVE-FAIL") == 1


def test_readback_mismatch_is_failure_despite_successful_upload(setup_archive: ArchiveSetup, capsys: pytest.CaptureFixture[str]) -> None:
    fake, _, env, audio = setup_archive
    fake.corrupt = True
    assert not archive(setup_archive)
    assert fake.uploads == 1
    assert not manifest_path(env).exists()
    LiveEffects(audio.parent, audio.parent / "watch.lock", env).discard_audio(audio)
    assert audio.exists()
    assert capsys.readouterr().err.count("AUDIO-ARCHIVE-FAIL") == 1
    fake.corrupt = False
    assert archive(setup_archive)
    assert fake.uploads == len(fake.files) == 1


def test_cancel_after_upload_resumes_without_duplicate(setup_archive: ArchiveSetup) -> None:
    fake, _, env, audio = setup_archive
    fake.interrupt = True
    with pytest.raises(KeyboardInterrupt):
        archive(setup_archive)
    assert audio.exists()
    assert not manifest_path(env).exists()
    fake.interrupt = False
    assert archive(setup_archive)
    assert fake.uploads == len(fake.files) == 1
    assert len(rows(manifest_path(env))) == 1


def test_two_ticks_serialize_upload_and_skip_second(setup_archive: ArchiveSetup) -> None:
    fake, _, env, _ = setup_archive
    fake.release = threading.Event()
    second_started = threading.Event()

    def second() -> bool:
        second_started.set()
        return archive(setup_archive)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(archive, setup_archive)
        try:
            assert fake.uploaded.wait(5)
            other = pool.submit(second)
            assert second_started.wait(5)
        finally:
            fake.release.set()
        assert first.result(timeout=5)
        assert other.result(timeout=5)
    assert fake.mutations == fake.uploads == len(fake.files) == 1
    assert len(rows(manifest_path(env))) == 1


@pytest.mark.parametrize("kind", ["directory", "worktree", "symlink"])
def test_manifest_checkout_refused_before_side_effects(setup_archive: ArchiveSetup, tmp_path: Path, kind: str,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    fake, client, env, audio = setup_archive
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    if kind == "directory":
        (checkout / ".git").mkdir()
    else:
        (checkout / ".git").write_text("gitdir: /unused", encoding="utf-8")
    root = checkout / "state"
    if kind == "symlink":
        link = tmp_path / "link"
        link.symlink_to(checkout, target_is_directory=True)
        root = link / "state"
    env["STT_EVAL_ROOT"] = str(root)
    assert not audio_archive.archive_audio(audio, _SOURCE, "sample", env, client=client)
    assert not root.exists()
    assert not fake.calls
    assert "STT-EVAL-ROOT-REFUSED" in capsys.readouterr().err


def test_meeting_records_existing_drive_identity_without_upload(setup_archive: ArchiveSetup) -> None:
    fake, _, env, audio = setup_archive
    entry = ManifestEntry(audio_digest(audio), "existing-drive-file", 1234, "meeting-sample",
                          "2026-09-01T00:00:00+00:00", domain="meeting")
    assert record_manifest(entry, env)
    assert not record_manifest(entry, env)
    assert rows(manifest_path(env)) == [entry.row()]
    assert fake.calls == []


@pytest.mark.parametrize("code", [0, 4])
@pytest.mark.parametrize("publication", ["ok", "exception", "disabled"])
def test_process_live_archive_preserves_transcription_result(setup_archive: ArchiveSetup, monkeypatch: pytest.MonkeyPatch,
                                                           capsys: pytest.CaptureFixture[str], code: int,
                                                           publication: str) -> None:
    fake, _, env, audio = setup_archive
    if publication == "disabled":
        env.pop("DRIVE_PUBLISH_ENABLED")
    elif publication == "exception":
        def fail(*args: object, **kwargs: object) -> None:
            raise OSError("synthetic failure")
        monkeypatch.setattr(drive_outputs, "publish_best_effort", fail)
    live = LiveEffects(audio.parent, audio.parent / "watch.lock", env)

    class Integrated(FakeEffects):
        def download(self, source: AudioSource) -> Path:
            return audio

        def archive_audio(self, path: Path, source: AudioSource, stem: str) -> None:
            live.archive_audio(path, source, stem)

        def discard_audio(self, path: Path) -> None:
            live.discard_audio(path)

    effects = Integrated(results=[_ok() if code == 0 else _fail(code)])
    assert process(_RECORD, effects=effects, max_attempts=2) == ("planned" if code == 0 else "retry")
    assert effects.commits[0][1].status == ("planned" if code == 0 else "transcribing")
    assert audio.exists() is (code != 0 or publication == "exception")
    assert len(rows(manifest_path(env))) == int(publication == "ok")
    assert fake.uploads == int(publication == "ok")
    assert capsys.readouterr().err.count("AUDIO-ARCHIVE-FAIL") == int(publication == "exception")


def manual_qa() -> None:
    """네트워크 없이 실제 발행·삭제 표면을 실행하고 집계만 출력한다."""
    with tempfile.TemporaryDirectory(prefix="plaud-archive-qa-") as directory, pytest.MonkeyPatch.context() as patch:
        root = Path(directory)
        patch.setenv("DRIVE_PUBLISH_ENABLED", "1")
        patch.setenv("DRIVE_OUTPUTS_ROOT", "autophagy")
        fake = ArchiveGws()
        client = DriveClient("fake-gws", root / "folders.json", runner=fake)
        patch.setattr(drive_outputs, "client_from_environment", lambda: client)
        env = {"HOME": str(root), "DRIVE_PUBLISH_ENABLED": "1"}
        audio = root / "sample.opus"
        audio.write_bytes(b"synthetic original container")
        setup = fake, client, env, audio
        assert archive(setup)
        parent = "root"
        parts = ("autophagy", "녹음원본", "lifelog", "2026")
        for name in parts:
            parent = fake.folders[name, parent]
        assert next(iter(fake.files))[1] == parent
        print(f"happy path={'/'.join(parts)}/ manifest_rows={len(rows(manifest_path(env)))}")
        count = fake.mutations
        assert archive(setup)
        assert fake.mutations == count
        print(f"idempotence attempts=2 upload_calls={fake.uploads} copies={len(fake.files)} duplicates=0")
        live = LiveEffects(root, root / "watch.lock", env)
        live.discard_audio(audio)
        assert not audio.exists()
        audio.write_bytes(b"synthetic failing original")
        fake.corrupt = True
        assert not archive(setup)
        live.discard_audio(audio)
        assert audio.exists()
        print("failure readback=failed deleted=false original_retained=true")
    assert not root.exists()
    print("cleanup temporary_runtime_removed=true network_calls=0")


if __name__ == "__main__":
    manual_qa()
