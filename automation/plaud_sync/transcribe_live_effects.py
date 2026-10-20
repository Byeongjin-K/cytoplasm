"""Live transcription effects moved from ``transcribe_live`` for the F2 250-LOC ceiling.

The runnable facade retains compatibility seams; this module owns the concrete MCP,
filesystem, subprocess, and locking implementation of ``LiveEffects``.
"""

from __future__ import annotations

import fcntl
import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import tzinfo
from pathlib import Path
from types import TracebackType
from typing import Final, TextIO, cast

from automation import term_correction
from automation.skill_mount import skill_scripts
from automation.stt_eval.snapshot import move_snapshot

from .audio import (
    DEFAULT_MAX_AUDIO_BYTES,
    AudioError,
    AudioSource,
    AudioTooLargeError,
    Opener,
    download,
    open_url,
    parse_source,
)
from .audio_archive import archive_audio, may_discard
from .fetch import CloudTranscript, fetch_summary, fetch_transcript
from .lifelog_extract_live import build_extractor
from .lifelog_fields import note_timezone
from .lifelog_model import ExtractionOutcome, LifelogRecording
from .mcp_client import PlaudMcpClient, PlaudMcpError, text_content
from .mcp_transport import JsonValue
from .model import PlaudSyncRecord, PlaudSyncState
from .store import load_note_body, load_state, save_note_body, save_state, save_transcript
from .terms import glossary as lifelog_glossary
from .terms import record as record_note_corrections
from .transcribe import CliResult, TranscribeError
from .transcript_corrector import CommandCorrector, Corrector

CLI_ENV: Final = "SPEECHTOTEXT_CLI"
SCRIPTS_ENV: Final = "SPEECHTOTEXT_SCRIPTS"
DEFAULT_CLI_TIMEOUT: Final = 21600.0
_CHILD_OVERRIDES: Final = {
    "SPEECHTOTEXT_BACKEND": "local",
    "SPEECHTOTEXT_ALLOW_INCOMPLETE": "1",
    "DRIVE_PUBLISH_ENABLED": "0",
}
_MCP_ERRORS: Final = (PlaudMcpError, OSError, ValueError)
_DETAIL_LIMIT: Final = 160
_REPO_ROOT: Final = Path(__file__).resolve().parents[2]


def _env_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else default


def _env_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name, "").strip()
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def cli_path(env: Mapping[str, str]) -> Path:
    override = env.get(CLI_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return skill_scripts("speechtotext", env_var=SCRIPTS_ENV, env=env) / "speechtotext_cli.py"


class _BlockingLock:
    __slots__: tuple[str, ...] = ("_handle", "_path")
    _path: Path
    _handle: TextIO | None

    def __init__(self, path: Path) -> None:
        self._path = path
        self._handle = None

    def __enter__(self) -> None:
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        handle = self._path.open("a", encoding="utf-8")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        self._handle = handle

    def __exit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> bool:
        if self._handle is not None:
            self._handle.close()
            self._handle = None
        return False


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _cli_result(completed: subprocess.CompletedProcess[str]) -> CliResult:
    if completed.returncode != 0:
        detail = _last_line(completed.stdout) or _last_line(completed.stderr)
        return CliResult(completed.returncode, None, "", detail[:_DETAIL_LIMIT])
    try:
        summary: JsonValue = json.loads(_last_line(completed.stdout) or "null")
    except ValueError:
        summary = None
    if not isinstance(summary, dict) or not isinstance(summary.get("transcript_path"), str):
        return CliResult(1, None, "", "CLI 요약 JSON 에 transcript_path 가 없다")
    model = summary.get("model")
    return CliResult(0, Path(cast(str, summary["transcript_path"])), model if isinstance(model, str) else "", "")


@dataclass(frozen=True, slots=True)
class LiveEffects:
    """Concrete side effects for the pure local-transcription step."""

    state_dir: Path
    lock_path: Path
    env: Mapping[str, str]
    opener: Opener = open_url

    @property
    def state_path(self) -> Path:
        return self.state_dir / "state.json"

    @property
    def tz(self) -> tzinfo:
        return note_timezone(self.env)[0]

    @property
    def transcript_corrector(self) -> Corrector:
        return CommandCorrector(self.env)

    def _mcp_client(self) -> PlaudMcpClient:
        return PlaudMcpClient()

    def _load_state(self) -> PlaudSyncState:
        return load_state(self.state_path)

    def _save_state(self, state: PlaudSyncState) -> None:
        save_state(self.state_path, state)

    def extract(self, recording: LifelogRecording) -> ExtractionOutcome:
        return build_extractor(self.env, repo_root=_REPO_ROOT)(recording)

    def glossary(self) -> term_correction.Glossary:
        return lifelog_glossary(self.env)

    def record_corrections(
        self, recording: LifelogRecording, corrections: Sequence[term_correction.Correction]
    ) -> None:
        _ = record_note_corrections(corrections, label=recording.name or recording.id, env=self.env)

    def draft_body(self, recording_id: str) -> str | None:
        return load_note_body(self.state_dir, recording_id)

    def fetch_source(self, recording_id: str) -> AudioSource:
        try:
            with self._mcp_client() as client:
                text = text_content(client.call_tool("get_file", {"file_id": recording_id}))
            return parse_source(text, recording_id)
        except (*_MCP_ERRORS, AudioError) as error:
            raise TranscribeError(f"get_file: {type(error).__name__}: {error}", counted=False) from error

    def fetch_summary(self, recording_id: str) -> str:
        try:
            with self._mcp_client() as client:
                return fetch_summary(client, recording_id)
        except _MCP_ERRORS as error:
            raise TranscribeError(f"get_note: {type(error).__name__}", counted=False) from error

    def fetch_transcript(self, recording_id: str) -> CloudTranscript:
        try:
            with self._mcp_client() as client:
                return fetch_transcript(client, recording_id)
        except _MCP_ERRORS as error:
            raise TranscribeError(f"get_transcript: {type(error).__name__}", counted=False) from error

    def download(self, source: AudioSource) -> Path:
        dest = self.state_dir / "audio" / f"{source.recording_id}{source.suffix}"
        cap = _env_int(self.env, "PLAUD_SYNC_MAX_AUDIO_BYTES", DEFAULT_MAX_AUDIO_BYTES)
        try:
            return download(source, dest, max_bytes=cap, opener=self.opener)
        except AudioTooLargeError as error:
            raise TranscribeError(str(error), counted=True) from error
        except AudioError as error:
            raise TranscribeError(str(error), counted=False) from error

    def transcribe(self, audio: Path, label: str) -> CliResult:
        cli = cli_path(self.env)
        if not cli.is_file():
            raise TranscribeError(f"speechtotext CLI 미마운트: {cli}", counted=False)
        work = self.state_dir / "transcripts" / ".work"
        child = {**self.env, **_CHILD_OVERRIDES, "SPEECHTOTEXT_TRANSCRIPT_DIR": str(work)}
        argv = [sys.executable, str(cli), "transcribe", "--file", str(audio), "--label", label]
        try:
            completed = subprocess.run(  # noqa: S603 - resolved interpreter and governed CLI
                argv, env=child, capture_output=True, text=True, check=False,
                timeout=_env_float(self.env, "PLAUD_SYNC_TRANSCRIBE_TIMEOUT", DEFAULT_CLI_TIMEOUT),
            )
        except subprocess.TimeoutExpired as error:
            raise TranscribeError("로컬 전사가 시간 제한을 넘겼다", counted=True) from error
        except OSError as error:
            raise TranscribeError(f"CLI 실행 실패: {type(error).__name__}", counted=False) from error
        for line in completed.stderr.splitlines():
            if line.startswith("STT-EVAL-"):
                print(line, file=sys.stderr)
        result = _cli_result(completed)
        if result.transcript_path is not None:
            _ = move_snapshot(result.transcript_path, self.env)
        return result

    def read_transcript(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except OSError as error:
            raise TranscribeError(f"전사본을 읽지 못했다: {type(error).__name__}", counted=False) from error

    def store_transcript(self, stem: str, markdown: str) -> Path:
        return save_transcript(self.state_dir, stem, markdown)

    def commit(self, before: PlaudSyncRecord, after: PlaudSyncRecord, body: str | None) -> bool:
        with _BlockingLock(self.lock_path):
            state = self._load_state()
            current = state.records.get(before.recording_id)
            if current != before or before.status != "transcribing":
                return False
            if body is not None:
                save_note_body(self.state_dir, after.recording_id, body)
            records = dict(state.records)
            records[after.recording_id] = after
            self._save_state(PlaudSyncState(state.version, state.last_poll_at, records))
        return True

    def archive_audio(self, path: Path, source: AudioSource, stem: str) -> None:
        _ = archive_audio(path, source, stem, self.env)

    def discard_audio(self, path: Path) -> None:
        if not may_discard(path, self.env):
            return
        try:
            path.unlink(missing_ok=True)
        except OSError:
            return
