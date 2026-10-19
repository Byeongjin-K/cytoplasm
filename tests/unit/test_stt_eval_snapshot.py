"""평가 스냅샷 경계와 기존 전사 출력의 호환성을 검증한다."""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import shutil
import stat
import subprocess
import sys
import wave
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from automation.stt_eval import model
from automation.stt_eval.snapshot import move_snapshot
import unit_node

SCRIPTS = Path(__file__).resolve().parents[2] / "skills/speechtotext/scripts"
sys.path.insert(0, str(SCRIPTS))
if TYPE_CHECKING:
    from skills.speechtotext.scripts import (
        speechtotext_cli, speechtotext_drive_watch, stt_audio, stt_client, stt_diarize,
        stt_eval_build, stt_eval_record, stt_local, stt_local_config, stt_sentence, stt_transcript,
    )
else:
    stt_client = importlib.import_module("stt_client")
    stt_transcript = importlib.import_module("stt_transcript")
    stt_eval_record = importlib.import_module("stt_eval_record")
    stt_eval_build = importlib.import_module("stt_eval_build")
    stt_local_config = importlib.import_module("stt_local_config")
    stt_sentence = importlib.import_module("stt_sentence")
    stt_diarize = importlib.import_module("stt_diarize")
    stt_local = importlib.import_module("stt_local")
    stt_audio = importlib.import_module("stt_audio")
    speechtotext_cli = importlib.import_module("speechtotext_cli")
    speechtotext_drive_watch = importlib.import_module("speechtotext_drive_watch")
NOW = datetime(2026, 9, 7, tzinfo=UTC)


def test_legacy_transcript_is_private_and_rewrites_one_file(tmp_path: Path) -> None:
    transcription = stt_client.Transcription("fixture", "fixture", "api")
    first = stt_transcript.write_transcript(tmp_path, label="fixture", source_name="fixture.wav", transcription=transcription, now=NOW)
    original = first.read_bytes()
    second = stt_transcript.write_transcript(tmp_path, label="fixture", source_name="fixture.wav", transcription=transcription, now=NOW)
    assert first == second
    assert second.read_bytes() == original
    assert list(tmp_path.iterdir()) == [first]
    assert stat.S_IMODE(first.stat().st_mode) == 0o600
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700


def _record() -> stt_eval_record.EvalSnapshot:
    return stt_eval_record.EvalSnapshot(
        recording_id="fixture", audio_sha256="a" * 64, duration_ms=1000,
        text="fixture", config_sha256="b" * 64,
        words=(stt_eval_record.SnapshotWord(
            "w0", 0, 7, 0, 500, stt_eval_record.SnapshotTag("SPEAKER", ("화자1",)), "token",
        ),),
        turns=(stt_eval_record.SnapshotTurn(0, 500, "화자1"),),
        gaps=(stt_eval_record.SnapshotGap(500, 1000, "quarantined"),), status="partial",
    )


def _toolchain(tmp_path: Path) -> stt_local_config.LocalToolchain:
    binary, weights = tmp_path / "whisper", tmp_path / "model"
    _ = binary.write_bytes(b"fixture-binary")
    _ = weights.write_bytes(b"fixture-model")
    return stt_local_config.LocalToolchain(
        binary, weights, binary, None, 1, "ko", 60.0, False, "", 0.5, "0", 900000, 15000,
    )


def test_dump_load_matches_model_and_is_private(tmp_path: Path) -> None:
    target = tmp_path / "fixture.eval.json"
    _ = target.write_text("old")
    target.chmod(0o644)
    stt_eval_record.dump_record(_record(), target)
    assert model.load_record(target) == model.EvalRecord(
        "fixture", "a" * 64, 1000, "fixture",
        words=(model.EvalWord("w0", 0, 7, 0, 500, model.SpeakerTag("SPEAKER", ("화자1",)), "token"),),
        turns=(model.EvalTurn(0, 500, "화자1"),),
        gaps=(model.EvalGap(500, 1000, "quarantined"),),
        status="partial", config_sha256="b" * 64,
    )
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert list(tmp_path.iterdir()) == [target]


def test_config_is_deterministic_and_not_asr_fingerprint(tmp_path: Path) -> None:
    toolchain = _toolchain(tmp_path)
    env = {"SPEECHTOTEXT_DIARIZE_THRESHOLD": "1.35", "SPEECHTOTEXT_ALIGN_BACKEND": "none"}
    digest = stt_eval_record.config_fingerprint(env, toolchain)
    assert digest == stt_eval_record.config_fingerprint(dict(reversed(list(env.items()))), toolchain)
    assert len(digest) == 64
    assert digest != stt_local.asr_fingerprint(toolchain, prompt="", env=env)
    assert digest != stt_eval_record.config_fingerprint({**env, "SPEECHTOTEXT_DIARIZE_THRESHOLD": "1.4"}, toolchain)


def test_config_survives_a_path_probe_that_cannot_read_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """설정 값 중에는 경로가 아닌 것이 있고(`sherpa`), 그 탐침은 던지면 안 된다.

    `Path.is_file()` 은 EACCES 를 삼키지 않는다 — CPython `_IGNORED_ERRNOS` 에 ENOENT·
    ENOTDIR·EBADF·ELOOP 만 있다. 그래서 읽을 수 없는 cwd 에서 `sherpa` 를 상대 경로로
    stat 하면 예외가 올라가고, 부르는 쪽(`stt_eval_build.snapshot_for`)이 OSError 를
    fail-soft 로 삼켜 **평가 스냅샷이 통째로 조용히 사라진다**(2026-09-18 노드 실측:
    `PermissionError: [Errno 13] Permission denied: 'sherpa'`). 같은 함정이 2026-08-26
    `approval_reminder_config` 수리의 원인이었다.
    """
    toolchain = _toolchain(tmp_path)
    real_is_file = Path.is_file

    def refuse(self: Path) -> bool:
        if not self.is_absolute():
            raise PermissionError(13, "Permission denied", str(self))
        return real_is_file(self)

    monkeypatch.setattr(Path, "is_file", refuse)

    digest = stt_eval_record.config_fingerprint(
        {"SPEECHTOTEXT_DIARIZE_BACKEND": "sherpa"}, toolchain
    )

    assert len(digest) == 64


@pytest.mark.parametrize("key,value", [
    ("SPEECHTOTEXT_DIARIZE_BACKEND", "pyannote"),
    ("SPEECHTOTEXT_DIARIZE_MIN_SPEECH", "0.6"),
    ("SPEECHTOTEXT_DIARIZE_MIN_SILENCE", "0.9"),
    ("SPEECHTOTEXT_ALIGN_BACKEND", "whisperx"),
    ("SPEECHTOTEXT_WHISPER_DTW", "large.v3"),
    ("SPEECHTOTEXT_PROMPT", "fixture-vocabulary"),
])
def test_config_tracks_non_asr_and_decode_settings(tmp_path: Path, key: str, value: str) -> None:
    toolchain = _toolchain(tmp_path)
    assert stt_eval_record.config_fingerprint({}, toolchain) != stt_eval_record.config_fingerprint({key: value}, toolchain)


@pytest.mark.parametrize("field,value", [
    ("language", "en"), ("prompt", "fixture"), ("decode_flags", ("-bs", "5")),
    ("window_ms", 600000), ("overlap_ms", 10000), ("max_context", "1"),
])
def test_config_tracks_resolved_toolchain(tmp_path: Path, field: str, value: object) -> None:
    toolchain = _toolchain(tmp_path)
    assert stt_eval_record.config_fingerprint({}, toolchain) != stt_eval_record.config_fingerprint({}, replace(toolchain, **{field: value}))


@pytest.mark.parametrize("field", ["binary", "model"])
def test_config_hashes_file_content_not_filename(tmp_path: Path, field: str) -> None:
    toolchain = _toolchain(tmp_path)
    before = stt_eval_record.config_fingerprint({}, toolchain)
    path = toolchain.binary if field == "binary" else toolchain.model
    _ = path.write_bytes(b"changed-fixture")
    assert before != stt_eval_record.config_fingerprint({}, toolchain)


def test_builder_preserves_spans_tags_turns_and_excludes_untrusted_text(tmp_path: Path) -> None:
    words = (
        stt_sentence.TimedWord(" fix", 0, 100),
        stt_sentence.TimedWord("ture", 100, 200),
        stt_sentence.TimedWord(" OMIT", 200, 300, "repeat"),
        stt_sentence.TimedWord(" unknown", -1, -1, timing_source="segment"),
    )
    record = stt_eval_build.build_record(
        audio_sha256="a" * 64, duration_ms=1000, config_sha256="b" * 64,
        words=words, turns=(stt_diarize.Turn(0, 200, 7), stt_diarize.Turn(50, 200, 9)),
        gaps=(stt_eval_record.SnapshotGap(200, 300, "quarantined"),),
    )
    target = tmp_path / "fixture.eval.json"
    stt_eval_record.dump_record(record, target)
    loaded = model.load_record(target)
    assert loaded.text == "fixture unknown"
    assert loaded.status == "partial"
    assert [(turn.start_ms, turn.end_ms) for turn in loaded.turns] == [(0, 200), (50, 200)]
    assert loaded.words[0].tag == model.SpeakerTag("OVERLAP", ("화자1", "화자2"))
    assert loaded.words[-1].tag == model.SpeakerTag("UNKNOWN")
    assert loaded.words[-1].start_ms is None
    assert loaded.words[-1].timing_source == "missing"
    assert loaded.text[loaded.words[0].char_start:loaded.words[0].char_end].strip() == "fix"


def test_transcript_writes_snapshot_beside_markdown(tmp_path: Path) -> None:
    transcription = stt_eval_build.SnapshotTranscription("fixture", "local:fixture", "local", eval_record=_record())
    target = stt_transcript.write_transcript(tmp_path, label="fixture", source_name="fixture.wav", transcription=transcription, now=NOW)
    snapshot = target.with_suffix(".eval.json")
    assert snapshot.is_file()
    assert model.load_record(snapshot).status == "partial"
    assert stat.S_IMODE(snapshot.stat().st_mode) == 0o600


def test_snapshot_write_failure_does_not_stop_transcript(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def refuse(*_args: object) -> None:
        raise OSError("fixture failure")

    transcription = stt_eval_build.SnapshotTranscription("fixture", "local:fixture", "local", eval_record=_record())
    target = stt_transcript.write_transcript(tmp_path, label="fixture", source_name="fixture.wav", transcription=transcription, now=NOW)
    assert target.with_suffix(".eval.json").is_file()
    monkeypatch.setattr(stt_eval_record, "dump_record", refuse)
    target = stt_transcript.write_transcript(tmp_path, label="fixture", source_name="fixture.wav", transcription=transcription, now=NOW)
    assert target.is_file()
    assert not target.with_suffix(".eval.json").exists()
    assert "STT-EVAL-SNAPSHOT-FAIL" in capsys.readouterr().err


def test_move_snapshot_to_config_audio_key(tmp_path: Path) -> None:
    transcript = tmp_path / "fixture.md"
    _ = transcript.write_text("fixture")
    source = transcript.with_suffix(".eval.json")
    stt_eval_record.dump_record(_record(), source)
    root = tmp_path / "evaluation"
    target = move_snapshot(transcript, {"STT_EVAL_ROOT": str(root)})
    assert target is not None
    assert target == root / "hyp" / ("b" * 64) / (("a" * 64) + ".json")
    assert model.load_record(target).text == "fixture"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
    assert not source.exists()
    assert transcript.read_text() == "fixture"


def test_move_refuses_checkout_and_keeps_transcript(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    transcript = tmp_path / "fixture.md"
    _ = transcript.write_text("fixture")
    source = transcript.with_suffix(".eval.json")
    stt_eval_record.dump_record(_record(), source)
    assert move_snapshot(transcript, {"STT_EVAL_ROOT": str(SCRIPTS.parents[2])}) is None
    assert capsys.readouterr().err.splitlines() == ["STT-EVAL-ROOT-REFUSED"]
    assert transcript.is_file() and source.is_file()


def test_deployed_scripts_import_without_automation(tmp_path: Path) -> None:
    release = tmp_path / "releases" / "speechtotext" / "fixture" / "scripts"
    _ = shutil.copytree(SCRIPTS, release, ignore=shutil.ignore_patterns("__pycache__"))
    code = "import importlib.util; assert importlib.util.find_spec('automation') is None; import stt_transcript, stt_eval_record, stt_eval_build; print('SNAPSHOT-IMPORT-OK')"
    completed = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env={"PYTHONPATH": str(release)}, capture_output=True, text=True, timeout=30, check=False)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "SNAPSHOT-IMPORT-OK"


def test_dump_failure_preserves_previous_file_and_cleans_temporary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "fixture.eval.json"
    stt_eval_record.dump_record(_record(), target)
    original = target.read_bytes()

    def refuse(_source: Path, _target: Path) -> Path:
        raise OSError("fixture rename failure")

    monkeypatch.setattr(Path, "replace", refuse)
    with pytest.raises(OSError):
        stt_eval_record.dump_record(replace(_record(), text="changed"), target)
    assert target.read_bytes() == original
    assert list(tmp_path.iterdir()) == [target]


def test_move_failure_preserves_source_and_cleans_temporary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    transcript = tmp_path / "fixture.md"
    source = transcript.with_suffix(".eval.json")
    stt_eval_record.dump_record(_record(), source)
    root = tmp_path / "evaluation"

    def refuse(_source: Path, _target: Path) -> Path:
        raise OSError("fixture rename failure")

    monkeypatch.setattr(Path, "replace", refuse)
    assert move_snapshot(transcript, {"STT_EVAL_ROOT": str(root)}) is None
    assert source.is_file()
    assert not list(root.rglob("*.json"))
    assert not list(root.rglob(".snapshot-*"))
    assert capsys.readouterr().err.strip() == "STT-EVAL-SNAPSHOT-FAIL reason=OSError"


def test_pipeline_version_and_backend_model_bytes_invalidate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    toolchain = _toolchain(tmp_path)
    embedding = tmp_path / "embedding"
    _ = embedding.write_bytes(b"fixture-model")
    env = {"SPEECHTOTEXT_DIARIZE_EMBEDDING": str(embedding)}
    original = stt_eval_record.config_fingerprint(env, toolchain)
    _ = embedding.write_bytes(b"changed-fixture")
    changed = stt_eval_record.config_fingerprint(env, toolchain)
    assert original != changed
    monkeypatch.setattr(stt_eval_record, "PIPELINE_VERSION", "fixture-next-version")
    assert changed != stt_eval_record.config_fingerprint(env, toolchain)


def _executable(path: Path, body: str) -> Path:
    _ = path.write_text(f"#!{sys.executable}\n" + body, encoding="utf-8")
    path.chmod(0o700)
    return path


def local_fixture(root: Path) -> tuple[Path, dict[str, str]]:
    """실제 subprocess로 호출되는 오프라인 도구와 40초 합성 WAV만 만든다."""
    root.mkdir(parents=True, exist_ok=True)
    audio = root / "fixture.wav"
    with wave.open(str(audio), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\0\0" * 16000 * 40)
    binary = _executable(root / "whisper", (
        "import json, os, sys\nfrom pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "start = int(args[args.index('-ot') + 1]) if '-ot' in args else 0\n"
        "if start and os.environ.get('FIXTURE_QUARANTINE') == '1': sys.exit(1)\n"
        "row = {'text': ' fixture', 'offsets': {'from': start, 'to': start + 1000}, "
        "'tokens': [{'text': ' fixture', 'offsets': {'from': start, 'to': start + 1000}}]}\n"
        "Path(args[args.index('-of') + 1] + '.json').write_text(json.dumps({'transcription': [row]}))\n"
    ))
    ffmpeg = _executable(root / "ffmpeg", "import shutil, sys\nshutil.copyfile(sys.argv[sys.argv.index('-i') + 1], sys.argv[-1])\n")
    weights = root / "model.bin"
    _ = weights.write_bytes(b"fixture-model")
    meeting = _executable(root / "meeting", "import os, sys\nsys.exit(int(os.environ.get('FIXTURE_MEETING_EXIT', '0')))\n")
    env = {
        **unit_node.env(),
        "HOME": str(root), "PATH": "/usr/bin:/bin",
        "AUTOPHAGY_RUNTIME_ROOT": str(SCRIPTS.parents[2]),
        "SPEECHTOTEXT_CLI": str(SCRIPTS / "speechtotext_cli.py"),
        "SPEECHTOTEXT_BACKEND": "local", "SPEECHTOTEXT_ALLOW_INCOMPLETE": "1",
        "SPEECHTOTEXT_WHISPER_BIN": str(binary), "SPEECHTOTEXT_WHISPER_MODEL": str(weights),
        "SPEECHTOTEXT_FFMPEG_BIN": str(ffmpeg), "SPEECHTOTEXT_FFPROBE_BIN": str(root / "absent"),
        "SPEECHTOTEXT_WINDOW_CACHE": str(root / "cache"),
        "SPEECHTOTEXT_TRANSCRIPT_DIR": str(root / "transcripts"),
        "SPEECHTOTEXT_GLOSSARY": str(root / "absent.csv"), "SPEECHTOTEXT_PROMPT": "fixture",
        "SPEECHTOTEXT_MEETING_CLI": str(meeting), "DRIVE_PUBLISH_ENABLED": "0",
        "STT_EVAL_ROOT": str(root / "evaluation"),
    }
    return audio, env


def test_characterize_real_local_cli_exit_and_markdown(tmp_path: Path) -> None:
    audio, env = local_fixture(tmp_path)
    completed = subprocess.run([sys.executable, env["SPEECHTOTEXT_CLI"], "transcribe", "--file", str(audio), "--label", "fixture"], env=env, capture_output=True, text=True, check=False, timeout=30)
    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout.splitlines()[-1])
    transcript = Path(summary["transcript_path"])
    assert transcript.is_file()
    assert stat.S_IMODE(transcript.stat().st_mode) == 0o600
    assert summary["meeting_exit"] is None
    assert "fixture" in transcript.read_text()


def test_characterize_plaud_real_cli_result(tmp_path: Path) -> None:
    from automation.plaud_sync.transcribe_live_effects import LiveEffects

    audio, env = local_fixture(tmp_path)
    effects = LiveEffects(tmp_path / "plaud", tmp_path / "plaud.lock", env)
    result = effects.transcribe(audio, "fixture")
    assert result.returncode == 0
    assert result.transcript_path is not None and result.transcript_path.is_file()
    assert result.model == "local:model"


@pytest.mark.parametrize("code", [0, 7])
def test_characterize_watch_child_exit_and_summary(tmp_path: Path, code: int) -> None:
    audio, env = local_fixture(tmp_path)
    env["FIXTURE_MEETING_EXIT"] = "0" if code == 0 else "6"
    argv = [sys.executable, env["SPEECHTOTEXT_CLI"], "ingest", "--file", str(audio), "--label", "fixture"]
    result = speechtotext_drive_watch._default_runner(argv, env)
    assert result == code
    assert list((tmp_path / "transcripts").glob("*.md"))


@pytest.mark.parametrize("partial", [False, True])
def test_real_local_cli_produces_loadable_snapshot(tmp_path: Path, partial: bool) -> None:
    audio, env = local_fixture(tmp_path)
    if partial:
        env.update(FIXTURE_QUARANTINE="1", SPEECHTOTEXT_WINDOW_MS="30000", SPEECHTOTEXT_WINDOW_OVERLAP_MS="0")
    completed = subprocess.run([sys.executable, env["SPEECHTOTEXT_CLI"], "transcribe", "--file", str(audio), "--label", "fixture"], env=env, capture_output=True, text=True, check=False, timeout=30)
    assert completed.returncode == 0, completed.stderr
    transcript = Path(json.loads(completed.stdout.splitlines()[-1])["transcript_path"])
    sidecar = transcript.with_suffix(".eval.json")
    assert sidecar.is_file()
    loaded = model.load_record(sidecar)
    assert loaded.audio_sha256 == hashlib.sha256(audio.read_bytes()).hexdigest()
    assert loaded.duration_ms == 40000
    assert loaded.text == "fixture"
    assert loaded.status == ("partial" if partial else "ok")
    assert loaded.words[0].timing_source == "token"
    assert loaded.words[0].start_ms == 0 and loaded.words[0].end_ms == 1000
    assert loaded.gaps == ((model.EvalGap(30000, 40000, "quarantined"),) if partial else ())
    assert stat.S_IMODE(sidecar.stat().st_mode) == 0o600


def test_local_producer_uses_effective_prompt_and_diarizer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    audio, env = local_fixture(tmp_path)
    env["SPEECHTOTEXT_PROMPT"] = "ambient"
    monkeypatch.setattr(os, "environ", env)
    toolchain = stt_local.resolve_toolchain(env)
    assert toolchain is not None
    diarizer = stt_diarize.DiarizeToolchain(toolchain.binary, toolchain.model, toolchain.model, 1.4, 1, 60.0, 5)
    observed: list[dict[str, str]] = []
    original = stt_eval_record.config_fingerprint

    def fingerprint(environment: dict[str, str], tools: stt_local_config.LocalToolchain) -> str:
        observed.append(dict(environment))
        return original(environment, tools)

    monkeypatch.setattr(stt_eval_record, "config_fingerprint", fingerprint)
    monkeypatch.setattr(stt_diarize, "diarize", lambda *_args, **_kwargs: (stt_diarize.Turn(0, 1000, 7),))
    result = stt_local.transcribe(stt_audio.check_audio(audio), toolchain, prompt="explicit", diarizer=diarizer, num_speakers=3)
    assert isinstance(result, stt_eval_build.SnapshotTranscription)
    assert result.eval_record is not None
    assert observed[0]["SPEECHTOTEXT_PROMPT"] == "explicit"
    assert observed[0]["SPEECHTOTEXT_DIARIZE_SPEAKERS"] == "3"
    assert observed[0]["SPEECHTOTEXT_DIARIZE_THRESHOLD"] == "1.4"
    assert result.eval_record.config_sha256 == original(observed[0], toolchain)
    assert result.eval_record.turns == (stt_eval_record.SnapshotTurn(0, 1000, "화자1"),)
    assert result.eval_record.words[0].tag == stt_eval_record.SnapshotTag("SPEAKER", ("화자1",))


@pytest.mark.parametrize("stage", ["prepare", "write"])
def test_real_transcription_snapshot_failure_is_soft(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], stage: str) -> None:
    audio, env = local_fixture(tmp_path)
    monkeypatch.setattr(os, "environ", env)

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise OSError("fixture disk failure")

    monkeypatch.setattr(stt_eval_record, "config_fingerprint" if stage == "prepare" else "dump_record", refuse)
    code = speechtotext_cli.main(["transcribe", "--file", str(audio), "--label", "fixture"])
    captured = capsys.readouterr()
    assert code == 0
    transcript = Path(json.loads(captured.out.splitlines()[-1])["transcript_path"])
    assert transcript.is_file() and not transcript.with_suffix(".eval.json").exists()
    assert captured.err.splitlines().count("STT-EVAL-SNAPSHOT-FAIL reason=OSError") == 1


@pytest.mark.parametrize("refused", [False, True])
def test_plaud_moves_real_producer_snapshot_without_changing_result(tmp_path: Path, capsys: pytest.CaptureFixture[str], refused: bool) -> None:
    from automation.plaud_sync.transcribe_live_effects import LiveEffects

    audio, env = local_fixture(tmp_path)
    if refused:
        env["STT_EVAL_ROOT"] = str(SCRIPTS.parents[2] / ".omo")
    result = LiveEffects(tmp_path / "plaud", tmp_path / "plaud.lock", env).transcribe(audio, "fixture")
    assert result.returncode == 0 and result.transcript_path is not None
    assert result.transcript_path.is_file()
    sidecar = result.transcript_path.with_suffix(".eval.json")
    if refused:
        assert sidecar.is_file()
        assert capsys.readouterr().err.splitlines().count("STT-EVAL-ROOT-REFUSED") == 1
    else:
        target, = (tmp_path / "evaluation" / "hyp").glob("*/*.json")
        assert model.load_record(target).audio_sha256 == hashlib.sha256(audio.read_bytes()).hexdigest()
        assert not sidecar.exists()


class SnapshotDrive:
    """네트워크 없이 실제 워처 download→ingest 경계를 통과한다."""
    def __init__(self, audio: Path) -> None:
        self.audio: Path = audio
        self.downloaded: Path | None = None

    def ensure_folder_path(self, parts: tuple[str, ...]) -> str:
        assert parts == ("fixture",)
        return "fixture-folder"

    def list_children(self, folder_id: str) -> list[dict[str, str]]:
        assert folder_id == "fixture-folder"
        return [{"id": "fixture-audio", "name": "fixture.wav"}]

    def verify_owner_only(self, file_id: str) -> None:
        assert file_id == "fixture-audio"

    def download_file(self, file_id: str, dest: Path) -> str:
        assert file_id == "fixture-audio"
        _ = shutil.copyfile(self.audio, dest)
        self.downloaded = dest
        return hashlib.sha256(dest.read_bytes()).hexdigest()


@pytest.mark.parametrize("code,refused", [(0, False), (7, False), (0, True)])
def test_watch_moves_real_snapshot_and_preserves_ingest_decision(tmp_path: Path, capsys: pytest.CaptureFixture[str], code: int, refused: bool) -> None:
    audio, env = local_fixture(tmp_path)
    env.update(SPEECHTOTEXT_DRIVE_FOLDER="fixture", FIXTURE_MEETING_EXIT="0" if code == 0 else "6")
    if refused:
        env["STT_EVAL_ROOT"] = str(SCRIPTS.parents[2] / ".omo")
    drive = SnapshotDrive(audio)
    summary = speechtotext_drive_watch.run_once(client=drive, env=env, runner=speechtotext_drive_watch._default_runner, now=NOW)
    assert summary["ingested"] == int(code == 0)
    assert summary["failed"] == int(code != 0)
    assert drive.downloaded is not None and not drive.downloaded.parent.exists()
    transcript, = (tmp_path / "transcripts").glob("*.md")
    assert transcript.is_file()
    if refused:
        assert transcript.with_suffix(".eval.json").is_file()
        assert capsys.readouterr().err.splitlines().count("STT-EVAL-ROOT-REFUSED") == 1
    else:
        target, = (tmp_path / "evaluation" / "hyp").glob("*/*.json")
        assert model.load_record(target).audio_sha256 == hashlib.sha256(audio.read_bytes()).hexdigest()
        assert not transcript.with_suffix(".eval.json").exists()


def test_plaud_forwards_child_snapshot_failure_once(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from automation.plaud_sync.transcribe_live_effects import LiveEffects

    audio, env = local_fixture(tmp_path)
    wrapper = _executable(tmp_path / "fault-cli", (
        f"import sys\nsys.path.insert(0, {str(SCRIPTS)!r})\n"
        "import stt_eval_record, speechtotext_cli\n"
        "def refuse(*args, **kwargs): raise OSError('fixture disk failure')\n"
        "stt_eval_record.dump_record = refuse\n"
        "sys.exit(speechtotext_cli.main())\n"
    ))
    env["SPEECHTOTEXT_CLI"] = str(wrapper)
    result = LiveEffects(tmp_path / "plaud", tmp_path / "plaud.lock", env).transcribe(audio, "fixture")
    assert result.returncode == 0 and result.transcript_path is not None
    assert result.transcript_path.is_file() and not result.transcript_path.with_suffix(".eval.json").exists()
    assert capsys.readouterr().err.splitlines().count("STT-EVAL-SNAPSHOT-FAIL reason=OSError") == 1


@pytest.mark.parametrize("caller", ["plaud", "watch"])
def test_relocation_io_failure_preserves_real_transcript(tmp_path: Path, capsys: pytest.CaptureFixture[str], caller: str) -> None:
    from automation.plaud_sync.transcribe_live_effects import LiveEffects

    audio, env = local_fixture(tmp_path)
    _ = Path(env["STT_EVAL_ROOT"]).write_text("fixture file blocks directory")
    if caller == "plaud":
        result = LiveEffects(tmp_path / "plaud", tmp_path / "plaud.lock", env).transcribe(audio, "fixture")
        assert result.returncode == 0 and result.transcript_path is not None
        transcript = result.transcript_path
    else:
        env["SPEECHTOTEXT_DRIVE_FOLDER"] = "fixture"
        summary = speechtotext_drive_watch.run_once(client=SnapshotDrive(audio), env=env, runner=speechtotext_drive_watch._default_runner, now=NOW)
        assert summary["ingested"] == 1
        transcript, = (tmp_path / "transcripts").glob("*.md")
    assert transcript.is_file() and transcript.with_suffix(".eval.json").is_file()
    errors = [line for line in capsys.readouterr().err.splitlines() if line.startswith("STT-EVAL-SNAPSHOT-FAIL")]
    assert len(errors) == 1
