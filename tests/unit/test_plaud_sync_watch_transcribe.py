from __future__ import annotations

import json
import importlib.util
import sys
import time
from types import ModuleType
from pathlib import Path
import pytest
from automation.cron_timer import SCRIPT_TIMEOUT
from automation.plaud_sync import transcribe_live
from automation.plaud_sync.model import PlaudSyncState
from automation.plaud_sync.note import LifelogRecording, split_lifelog_body
from automation.plaud_sync.store import load_note_body
from automation.plaud_sync.transcribe_live import StepSummary
from automation.plaud_sync.watch_step import ResolveResult
from automation.term_correction import FUZZY, Correction
from tests.unit.test_plaud_sync_transcribe_live import _state_dir, _RECORD, _NOW, _flock_is_free, _effects, _REPO, _WATCH


def _load_watch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ModuleType:
    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(_REPO))
    monkeypatch.setenv("HOME", str(tmp_path))
    spec = importlib.util.spec_from_file_location("plaud_sync_watch_transcribe_test", _WATCH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    state_dir = tmp_path / "plaud-sync"
    monkeypatch.setattr(module, "STATE_DIR", state_dir)
    monkeypatch.setattr(module, "STATE_PATH", state_dir / "state.json")
    monkeypatch.setattr(module, "LOCK_PATH", state_dir / "watch.lock")
    monkeypatch.setattr(module, "_load_env_secrets", lambda *_args: None)
    monkeypatch.setenv("TERM_GLOSSARY_CACHE", str(tmp_path / "term-glossary"))
    monkeypatch.setenv("TERM_CORRECTION_LOG", str(tmp_path / "term-glossary" / "corrections.jsonl"))
    return module


def test_cron_main_releases_the_watch_lock_before_transcribing_and_posts_right_after(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    watch = _load_watch(monkeypatch, tmp_path)
    _state_dir(tmp_path, _RECORD)
    observed: list[bool] = []
    deadlines: list[float | None] = []

    def fake_step(*, state_dir: Path, lock_path: Path, env: object, deadline: float | None) -> StepSummary:
        observed.append(_flock_is_free(lock_path))
        deadlines.append(deadline)
        return StepSummary(line="plaud-sync: transcribed=1 fallback=0 retry=0 stale=0", promoted=1)

    monkeypatch.setattr(transcribe_live, "run_transcribe_step", fake_step)
    monkeypatch.setattr(watch, "_run", lambda argv: ["plaud-sync: posted=0 written=0 abandoned=0"])
    monkeypatch.setattr(
        watch,
        "run_once",
        lambda now: ResolveResult(PlaudSyncState(1, None, {}), ("rec-001",), (), ()),
    )

    assert watch.main([]) == 0

    assert observed == [True], "the transcribe step must run with watch.lock released"
    # The step gets the cron ceiling as a deadline, so its CLI ends before the job is killed.
    assert deadlines[0] is not None and 0 < deadlines[0] - time.monotonic() <= SCRIPT_TIMEOUT
    out = capsys.readouterr().out.splitlines()
    assert out == [
        "plaud-sync: posted=0 written=0 abandoned=0",
        "plaud-sync: transcribed=1 fallback=0 retry=0 stale=0",
        "plaud-sync: posted=1 written=0 abandoned=0",
    ]


def test_cron_discover_freezes_new_recordings_as_transcribing_unless_the_switch_is_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    watch = _load_watch(monkeypatch, tmp_path)
    recording = LifelogRecording(
        id="rec-new",
        name="walk",
        created_at="2026-09-04T01:05:00",
        start_at="2026-09-04T01:00:00",
        duration_ms=5000,
        summary_markdown="- 요약",
        transcript_text="클라우드",
    )

    class _Client:
        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *_exception: object) -> bool:
            return False

    import automation.plaud_sync.fetch as fetch_module
    import automation.plaud_sync.mcp_client as mcp_module

    monkeypatch.setattr(fetch_module, "fetch_recordings", lambda client, *, date_from: (recording,))
    monkeypatch.setattr(mcp_module, "PlaudMcpClient", _Client)

    state = watch._discover(PlaudSyncState(1, None, {}), _NOW)
    assert state.records["rec-new"].status == "transcribing"
    assert load_note_body(tmp_path / "plaud-sync", "rec-new") is not None

    monkeypatch.setenv("PLAUD_SYNC_TRANSCRIBE", "0")
    assert watch._discover(PlaudSyncState(1, None, {}), _NOW).records["rec-new"].status == "planned"


def test_live_effects_read_the_lifelog_glossary_and_log_what_they_corrected(tmp_path: Path) -> None:
    """참고 문서 조회와 감사 로그가 전사 스텝의 효과 경계에 붙어 있다 — 순수 단계는 짝만 받는다."""
    glossary = tmp_path / "용어집.csv"
    glossary.write_text("한전기술\n", encoding="utf-8")
    log = tmp_path / "corrections.jsonl"
    effects = _effects(tmp_path, TERM_GLOSSARY_FILE=str(glossary), TERM_CORRECTION_LOG=str(log))
    recording = LifelogRecording(
        id="rec-001",
        name="산책",
        created_at="2026-09-04T01:05:00",
        start_at="2026-09-04T01:00:00",
        duration_ms=1000,
        summary_markdown="",
        transcript_text="",
    )

    assert effects.glossary() == (("한전기술", "한전기술"),)
    effects.record_corrections(
        recording,
        (Correction(before="항정기술", after="한전기술", term="한전기술", kind=FUZZY),),
    )

    entry = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert (entry["document"], entry["stage"], entry["label"]) == ("lifelog", "note", "산책")
    assert (entry["before"], entry["after"], entry["kind"]) == ("항정기술", "한전기술", "fuzzy")


def test_cron_discover_corrects_the_note_it_freezes_and_logs_only_the_owner_facing_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """발견 경로도 교정한다 — 전사 스위치가 꺼져 있으면 여기서 언 노트가 소유자에게 간다.

    전사가 켜져 있을 때 얼리는 것은 초안이고 transcribe.finalize 가 다시 언다. 그 초안까지
    로그에 남기면 같은 녹음이 두 번 적혀 오탐을 되짚기 어려워지므로 로그는 한 번만 남긴다.
    """
    watch = _load_watch(monkeypatch, tmp_path)
    glossary = tmp_path / "용어집.csv"
    glossary.write_text("한전기술\n", encoding="utf-8")
    log = tmp_path / "corrections.jsonl"
    monkeypatch.setenv("TERM_GLOSSARY_FILE", str(glossary))
    monkeypatch.setenv("TERM_CORRECTION_LOG", str(log))
    monkeypatch.setenv("PLAUD_SYNC_TRANSCRIBE", "0")
    recording = LifelogRecording(
        id="rec-new",
        name="산책",
        created_at="2026-09-04T01:05:00",
        start_at="2026-09-04T01:00:00",
        duration_ms=5000,
        summary_markdown="항정기술과 회의했다.",
        transcript_text="[00:00 · 화자1] 항정기술과 회의했다.",
    )

    class _Client:
        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *_exception: object) -> bool:
            return False

    import automation.plaud_sync.fetch as fetch_module
    import automation.plaud_sync.mcp_client as mcp_module

    monkeypatch.setattr(fetch_module, "fetch_recordings", lambda client, *, date_from: (recording,))
    monkeypatch.setattr(mcp_module, "PlaudMcpClient", _Client)

    state = watch._discover(PlaudSyncState(1, None, {}), _NOW)

    assert state.records["rec-new"].status == "planned"
    body = load_note_body(tmp_path / "plaud-sync", "rec-new")
    assert body is not None
    summary, transcript = split_lifelog_body(body)
    assert "한전기술과 회의했다." in summary
    assert "항정기술과 회의했다." in transcript
    entry = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert (entry["document"], entry["stage"], entry["label"]) == ("lifelog", "note", "산책")
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1

    monkeypatch.delenv("PLAUD_SYNC_TRANSCRIBE")
    draft = watch._discover(PlaudSyncState(1, None, {}), _NOW)

    assert draft.records["rec-new"].status == "transcribing"
    drafted = load_note_body(tmp_path / "plaud-sync", "rec-new")
    assert drafted is not None and "한전기술과 회의했다." in split_lifelog_body(drafted)[0]
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1, "초안은 로그를 두 번 만들지 않는다"


