"""``automation.plaud_sync.transcribe_live`` — locks, the CLI subprocess and the cron seam.

Separate from the pure step tests: everything here touches a real file lock, a real
subprocess or the cron wrapper. The lock discipline is the point — the watch lock is
released while the node transcribes (≈0.4× real time) and re-taken only for the one
state save, and the whisper toolchain is shared with the speechtotext Drive watcher
through ``automation.pipeline_lock``.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import select
import stat
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest

from automation import pipeline_lock
from automation.plaud_sync import transcribe_live, transcribe_live_effects
from automation.plaud_sync.audio import AudioSource
from automation.plaud_sync.model import PlaudSyncRecord, PlaudSyncState
from automation.plaud_sync.store import load_note_body, load_state, save_note_body, save_state
from automation.plaud_sync.transcribe import CliResult, TranscribeError
from automation.plaud_sync.transcribe_live import (
    LiveEffects,
    StepSummary,
    cli_path,
    run_transcribe_step,
)

_REPO: Final = Path(__file__).resolve().parents[2]
_WATCH: Final = _REPO / "automation" / "plaud_sync" / "cron" / "plaud_sync_watch.py"
_NOW: Final = datetime(2026, 9, 4, 4, 0, 0, tzinfo=UTC)

_RECORD: Final = PlaudSyncRecord(
    version=1,
    recording_id="rec-001",
    recorded_at="2026-09-01T08:00:00",
    note_relpath="000_PARA/Area/Lifelog/2026/2026-09-01-standup--08008c284627.md",
    note_title="standup (2026-09-01)",
    body_sha256="a" * 64,
    action_hash=f"sha256:{'b' * 64}",
    status="transcribing",
    kind="obsidian-write",
    surface="agent-chat-thread",
    channel_id="",
    policy_version=8,
    message_id=None,
    created_at="2026-09-01T09:00:00",
    approved_at=None,
    written_at=None,
    remote_ref=None,
    note_content_sha256=None,
    last_block_reason=None,
)
_SOURCE: Final = AudioSource(
    recording_id="rec-001",
    name="standup",
    created_at="2026-09-01T08:05:00",
    start_at="2026-09-01T08:00:00",
    duration_ms=60000,
    url="https://bucket.invalid/files/rec-001.mp3?X-Amz-Signature=abc",
    suffix=".mp3",
)
_FAKE_CLI: Final = """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
Path(os.environ["FAKE_CLI_LOG"]).write_text(json.dumps({
    "argv": sys.argv[1:],
    "backend": os.environ.get("SPEECHTOTEXT_BACKEND"),
    "drive": os.environ.get("DRIVE_PUBLISH_ENABLED"),
    "incomplete": os.environ.get("SPEECHTOTEXT_ALLOW_INCOMPLETE"),
    "transcript_dir": os.environ.get("SPEECHTOTEXT_TRANSCRIPT_DIR"),
}), encoding="utf-8")
code = int(os.environ.get("FAKE_CLI_EXIT", "0"))
if code:
    print("로컬 전사 도구를 찾지 못했습니다: whisper.cpp 바이너리와 ggml 모델을 설치하고")
    sys.exit(code)
out = Path(os.environ["SPEECHTOTEXT_TRANSCRIPT_DIR"])
out.mkdir(parents=True, exist_ok=True)
label = sys.argv[sys.argv.index("--label") + 1]
target = out / f"2026-09-04_{label}.md"
target.write_text("# x 전사본\\n\\n- 화자: 화자1=미상\\n\\n---\\n\\n[00:00:01] 화자1\\n안녕하세요.\\n", encoding="utf-8")
print("SPEECHTOTEXT-NOTE: some diagnostic line first")
print(json.dumps({"transcript_path": str(target), "model": "local:test-model", "chars": 5}))
"""


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    # 교정 참고 문서 캐시와 감사 로그는 체크아웃 밖(노드 HOME)에 산다 — 테스트가 실제 노드의
    # 용어집을 읽거나 그 로그에 적지 않도록 tmp 로 묶는다.
    return {
        "HOME": str(tmp_path),
        "PATH": "/usr/bin:/bin",
        "TERM_GLOSSARY_CACHE": str(tmp_path / "term-glossary"),
        "TERM_CORRECTION_LOG": str(tmp_path / "term-glossary" / "corrections.jsonl"),
        **extra,
    }


def _state_dir(tmp_path: Path, *records: PlaudSyncRecord) -> Path:
    state_dir = tmp_path / "plaud-sync"
    save_state(
        state_dir / "state.json",
        PlaudSyncState(1, None, {record.recording_id: record for record in records}),
    )
    return state_dir


def _effects(tmp_path: Path, **extra: str) -> LiveEffects:
    state_dir = tmp_path / "plaud-sync"
    return LiveEffects(state_dir=state_dir, lock_path=state_dir / "watch.lock", env=_env(tmp_path, **extra))


def _flock_is_free(path: Path) -> bool:
    with path.open("a", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return True


def test_run_transcribe_step_when_pipeline_lock_is_held_then_yields_busy_and_touches_nothing(
    tmp_path: Path,
) -> None:
    env = _env(tmp_path)
    state_dir = _state_dir(tmp_path, _RECORD)
    before = (state_dir / "state.json").read_bytes()
    lock = pipeline_lock.lock_path(env)
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("w", encoding="utf-8") as holder:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        summary = run_transcribe_step(state_dir=state_dir, lock_path=state_dir / "watch.lock", env=env)

    assert summary == StepSummary(line="plaud-sync: transcribe busy (pipeline lock held)", promoted=0)
    assert (state_dir / "state.json").read_bytes() == before
    assert not (state_dir / "audio").exists()


def test_run_transcribe_step_when_kill_switch_is_off_then_does_nothing_and_takes_no_lock(
    tmp_path: Path,
) -> None:
    env = _env(tmp_path, PLAUD_SYNC_TRANSCRIBE="0")
    state_dir = _state_dir(tmp_path, _RECORD)
    assert run_transcribe_step(state_dir=state_dir, lock_path=state_dir / "watch.lock", env=env) is None
    assert not pipeline_lock.lock_path(env).exists()


def test_run_transcribe_step_when_nothing_is_transcribing_then_returns_none(tmp_path: Path) -> None:
    env = _env(tmp_path)
    state_dir = _state_dir(tmp_path, replace(_RECORD, status="planned"))
    assert run_transcribe_step(state_dir=state_dir, lock_path=state_dir / "watch.lock", env=env) is None


def test_commit_holds_the_watch_lock_exclusively_while_it_verifies_and_saves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = _state_dir(tmp_path, _RECORD)
    effects = _effects(tmp_path)
    inside = threading.Event()
    release = threading.Event()
    real_load = transcribe_live.load_state

    def gated_load(path: Path) -> PlaudSyncState:
        inside.set()
        assert release.wait(5), "test never released the critical section"
        return real_load(path)

    monkeypatch.setattr(transcribe_live, "load_state", gated_load)
    after = replace(_RECORD, status="planned", body_sha256="c" * 64, action_hash=f"sha256:{'d' * 64}")
    results: list[bool] = []
    worker = threading.Thread(target=lambda: results.append(effects.commit(_RECORD, after, "## 요약\n\nbody")))
    worker.start()
    try:
        assert inside.wait(5), "commit never reached its critical section"
        assert not _flock_is_free(effects.lock_path), "commit must hold watch.lock while it saves"
    finally:
        release.set()
        worker.join(5)
    assert results == [True]
    assert _flock_is_free(effects.lock_path)
    assert load_state(state_dir / "state.json").records["rec-001"] == after
    assert load_note_body(state_dir, "rec-001") == "## 요약\n\nbody"


def test_commit_when_record_changed_underneath_then_refuses_and_writes_nothing(tmp_path: Path) -> None:
    moved_on = replace(_RECORD, status="planned")
    state_dir = _state_dir(tmp_path, moved_on)
    save_note_body(state_dir, "rec-001", "frozen draft")
    effects = _effects(tmp_path)
    before = (state_dir / "state.json").read_bytes()

    assert effects.commit(_RECORD, replace(_RECORD, status="planned", body_sha256="c" * 64), "new") is False

    assert (state_dir / "state.json").read_bytes() == before
    assert load_note_body(state_dir, "rec-001") == "frozen draft"


def test_transcribe_effect_runs_the_cli_with_local_backend_and_no_drive_publication(
    tmp_path: Path,
) -> None:
    fake = tmp_path / "fake_cli.py"
    fake.write_text(_FAKE_CLI, encoding="utf-8")
    log = tmp_path / "cli.json"
    effects = _effects(tmp_path, SPEECHTOTEXT_CLI=str(fake), FAKE_CLI_LOG=str(log), DRIVE_PUBLISH_ENABLED="1")
    audio = tmp_path / "rec-001.mp3"
    audio.write_bytes(b"\x00")

    result = effects.transcribe(audio, "2026-09-01-standup--08008c284627")

    work = tmp_path / "plaud-sync" / "transcripts" / ".work"
    assert result == CliResult(0, work / "2026-09-04_2026-09-01-standup--08008c284627.md", "local:test-model", "")
    seen = json.loads(log.read_text(encoding="utf-8"))
    assert seen == {
        "argv": ["transcribe", "--file", str(audio), "--label", "2026-09-01-standup--08008c284627"],
        "backend": "local",
        "drive": "0",
        "incomplete": "1",
        "transcript_dir": str(work),
    }
    assert effects.read_transcript(result.transcript_path or Path()).startswith("# x 전사본")


def test_transcribe_effect_when_cli_exits_nonzero_then_reports_code_and_its_notice(tmp_path: Path) -> None:
    fake = tmp_path / "fake_cli.py"
    fake.write_text(_FAKE_CLI, encoding="utf-8")
    effects = _effects(tmp_path, SPEECHTOTEXT_CLI=str(fake), FAKE_CLI_LOG=str(tmp_path / "l.json"), FAKE_CLI_EXIT="4")
    result = effects.transcribe(tmp_path / "rec-001.mp3", "x")
    assert (result.returncode, result.transcript_path, result.model) == (4, None, "")
    assert result.detail.startswith("로컬 전사 도구를 찾지 못했습니다")


def test_transcribe_effect_when_cli_is_not_mounted_then_environment_failure(tmp_path: Path) -> None:
    effects = _effects(tmp_path, SPEECHTOTEXT_CLI=str(tmp_path / "missing_cli.py"))
    with pytest.raises(TranscribeError) as caught:
        effects.transcribe(tmp_path / "rec-001.mp3", "x")
    assert caught.value.counted is False
    assert "미마운트" in caught.value.reason



_SLOW_CLI: Final = """#!/usr/bin/env python3
import os, subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
Path(os.environ["FAKE_CLI_CHILD"]).write_text(str(child.pid), encoding="utf-8")
time.sleep(60)
"""


def _exits_within(pid: int, seconds: float) -> bool:
    """True once the process has terminated: a pidfd turns readable on exit (no polling)."""
    try:
        handle = os.pidfd_open(pid)
    except ProcessLookupError:
        return True
    try:
        readable, _, _ = select.select([handle], [], [], seconds)
        return bool(readable)
    finally:
        os.close(handle)


def test_transcribe_effect_stops_inside_the_jobs_own_deadline_and_counts_the_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 2026-11-20: a 23-hour recording ran under the cron job's 3600 s ceiling while the CLI limit was
    # 21600 s, so the job was killed first, the attempt was never counted and the record kept the
    # head of the queue. The CLI must now end before the job does, and its whole process group with it.
    fake = tmp_path / "slow_cli.py"
    fake.write_text(_SLOW_CLI, encoding="utf-8")
    child = tmp_path / "child.pid"
    monkeypatch.setattr(transcribe_live_effects, "MIN_CLI_SECONDS", 1.0)
    deadline = time.monotonic() + transcribe_live_effects.JOB_RESERVE_SECONDS + 2
    effects = replace(
        _effects(tmp_path, SPEECHTOTEXT_CLI=str(fake), FAKE_CLI_CHILD=str(child)), deadline=deadline
    )

    with pytest.raises(TranscribeError) as caught:
        effects.transcribe(tmp_path / "rec-001.mp3", "x")

    assert caught.value.counted is True
    assert time.monotonic() < deadline - transcribe_live_effects.JOB_RESERVE_SECONDS + 10
    assert _exits_within(int(child.read_text(encoding="utf-8")), 5.0)


def test_transcribe_effect_when_the_job_has_no_time_left_then_waits_without_counting(tmp_path: Path) -> None:
    fake = tmp_path / "fake_cli.py"
    fake.write_text(_FAKE_CLI, encoding="utf-8")
    log = tmp_path / "cli.json"
    effects = replace(
        _effects(tmp_path, SPEECHTOTEXT_CLI=str(fake), FAKE_CLI_LOG=str(log)),
        deadline=time.monotonic() + transcribe_live_effects.JOB_RESERVE_SECONDS,
    )

    with pytest.raises(TranscribeError) as caught:
        effects.transcribe(tmp_path / "rec-001.mp3", "x")

    assert caught.value.counted is False
    assert not log.exists()


def test_transcribe_effect_reuses_a_finished_transcript_until_the_record_leaves_transcribing(
    tmp_path: Path,
) -> None:
    # 2026-11-20: a 23-hour transcript finished, the field extraction after it timed out, and the next
    # tick decoded the whole recording again. A finished transcript is now reused until the commit.
    fake = tmp_path / "fake_cli.py"
    fake.write_text(_FAKE_CLI, encoding="utf-8")
    log = tmp_path / "cli.json"
    state_dir = _state_dir(tmp_path, _RECORD)
    effects = _effects(tmp_path, SPEECHTOTEXT_CLI=str(fake), FAKE_CLI_LOG=str(log))
    label = "2026-09-01-standup--08008c284627"
    audio = tmp_path / "rec-001.mp3"

    first = effects.transcribe(audio, label)
    log.unlink()
    again = effects.transcribe(audio, label)

    assert again == first and not log.exists(), "a finished transcript must not run the CLI again"
    assert effects.commit(_RECORD, replace(_RECORD, status="planned"), None)
    assert (state_dir / "transcripts" / ".work").is_dir() and first.transcript_path is not None
    assert first.transcript_path.is_file(), "releasing the pointer never removes the transcript"
    _ = effects.transcribe(audio, label)
    assert log.exists(), "after the record left transcribing a new attempt transcribes afresh"


def test_cli_path_prefers_the_override_then_the_governed_live_mount(tmp_path: Path) -> None:
    assert cli_path({"SPEECHTOTEXT_CLI": "/opt/x/speechtotext_cli.py"}) == Path("/opt/x/speechtotext_cli.py")
    assert cli_path({"AUTOPHAGY_SKILL_LIVE_ROOT": str(tmp_path)}) == (
        tmp_path / "speechtotext" / "scripts" / "speechtotext_cli.py"
    )
    assert cli_path({}) == Path("/srv/autophagy-skills/live/speechtotext/scripts/speechtotext_cli.py")


def test_download_effect_classifies_the_cap_as_the_recording_and_the_network_as_the_node(
    tmp_path: Path,
) -> None:
    class _Response:
        headers: dict[str, str] = {"Content-Length": "999"}

        def read(self, size: int) -> bytes:
            return b""

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *_exception: object) -> bool:
            return False

    capped = replace(_effects(tmp_path, PLAUD_SYNC_MAX_AUDIO_BYTES="10"), opener=lambda url, timeout: _Response())
    with pytest.raises(TranscribeError) as too_large:
        capped.download(_SOURCE)
    assert too_large.value.counted is True

    def unreachable(url: str, timeout: float) -> _Response:
        raise OSError("name resolution failed")

    offline = replace(_effects(tmp_path), opener=unreachable)
    with pytest.raises(TranscribeError) as network:
        offline.download(_SOURCE)
    assert network.value.counted is False
    assert not (tmp_path / "plaud-sync" / "audio" / "rec-001.mp3").exists()


def test_store_transcript_writes_owner_only_under_the_state_transcripts_dir(tmp_path: Path) -> None:
    effects = _effects(tmp_path)
    path = effects.store_transcript("2026-09-01-standup--08008c284627", "# x 전사본\n")
    assert path == tmp_path / "plaud-sync" / "transcripts" / "2026-09-01-standup--08008c284627.md"
    assert path.read_text(encoding="utf-8") == "# x 전사본\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_body_digest_of_a_committed_record_matches_the_saved_note(tmp_path: Path) -> None:
    state_dir = _state_dir(tmp_path, _RECORD)
    effects = _effects(tmp_path)
    body = "## 요약\n\n- 새 요약\n\n## 전문\n\n[00:00:01] 화자1\n안녕하세요.\n\n---\n\n출처: PLAUD 녹음 rec-001"
    after = replace(_RECORD, status="planned", body_sha256=hashlib.sha256(body.encode("utf-8")).hexdigest())
    assert effects.commit(_RECORD, after, body) is True
    saved = load_note_body(state_dir, "rec-001")
    assert saved is not None
    assert hashlib.sha256(saved.encode("utf-8")).hexdigest() == after.body_sha256
