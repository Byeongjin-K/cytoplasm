"""Node command boundary and pre-freeze integration, using only synthetic text."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest

from automation.plaud_sync.binding import PlaudHashFields, plaud_action_hash
from automation.plaud_sync.lifelog_model import LifelogExtraction, LifelogRecording
from automation.plaud_sync.model import PlaudSyncState
from automation.plaud_sync.note import split_lifelog_body
from automation.plaud_sync.sync import plan_new_records
from automation.plaud_sync.transcribe import process
from automation.plaud_sync.transcribe_live_effects import LiveEffects
from automation.plaud_sync.transcript_corrector import (
    CommandCorrector,
    CorrectedTranscript,
    Corrector,
    SETTINGS_RELPATH,
    TranscriptContext,
    preserves_structure,
)
from test_plaud_sync_transcribe import FakeEffects, _RECORD, _SOURCE, _ok

RAW: Final = "# synthetic\n\n- source: fixture\n\n---\n\n[00:00:01] speaker_1\nsynthetic typo\n"
FIXED: Final = RAW.replace("typo", "term")
CONTEXT: Final = TranscriptContext("synthetic-id", "2026-10-20_1200_synthetic")


def _settings(home: Path, payload: str) -> None:
    path = home / SETTINGS_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def _command(home: Path, program: str, *, timeout: float = 300) -> None:
    _settings(home, json.dumps({
        "argv": [sys.executable, "-c", program],
        "timeout_seconds": timeout,
    }))


class HookEffects(FakeEffects):
    @property
    def transcript_corrector(self) -> Corrector:
        return CommandCorrector(os.environ)


def test_default_off_leaves_transcript_unchanged(tmp_path, monkeypatch, capsys):
    # Given no node setting and a command seam that must not execute.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: pytest.fail("unexpected command"))
    # When the hook is consulted.
    result = CommandCorrector(os.environ)(RAW, CONTEXT)
    # Then the original bytes and silence are preserved.
    assert result.text == RAW
    assert capsys.readouterr().err == ""


def test_success_passes_stdin_env_and_uses_stdout(tmp_path, monkeypatch, capsys):
    # Given a real command that verifies its context before correcting stdin.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SYNTHETIC_INHERITED", "present")
    _command(tmp_path, (
        "import os,sys;"
        "assert os.environ['PLAUD_RECORDING_ID']=='synthetic-id';"
        "assert os.environ['PLAUD_NOTE_TITLE']=='2026-10-20_1200_synthetic';"
        "assert os.environ['SYNTHETIC_INHERITED']=='present';"
        "sys.stderr.write('synthetic-private-output');"
        "sys.stdout.write(sys.stdin.read().replace('typo','term'))"
    ))
    # When the configured command executes.
    result = CommandCorrector(os.environ)(RAW, CONTEXT)
    # Then only its valid stdout is accepted and stderr is not relayed.
    assert result.text == FIXED
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("program,reason", [
    ("import sys;sys.exit(7)", "exit:7"),
    ("print('synthetic invalid output')", "invalid-output"),
    ("import sys;sys.stdout.buffer.write(bytes([255]))", "command-error"),
])
def test_command_failure_falls_back_without_payload(tmp_path, monkeypatch, capsys, program, reason):
    # Given an unsuccessful or invalid command.
    monkeypatch.setenv("HOME", str(tmp_path))
    _command(tmp_path, program)
    # When correction is attempted.
    result = CommandCorrector(os.environ)(RAW, CONTEXT)
    # Then the raw input survives, with only a reason code.
    assert result.text == RAW
    assert f"reason={reason}" in capsys.readouterr().err


def test_timeout_falls_back(tmp_path, monkeypatch, capsys):
    # Given a child that blocks on a signal, rather than a timing-dependent sleep.
    monkeypatch.setenv("HOME", str(tmp_path))
    _command(tmp_path, "import signal;signal.pause()", timeout=0.05)
    # When its real execution deadline expires.
    result = CommandCorrector(os.environ)(RAW, CONTEXT)
    # Then correction cannot block the note and raw text remains.
    assert result.text == RAW
    assert "reason=timeout" in capsys.readouterr().err


@pytest.mark.parametrize("payload", [
    "{", "[]", "{}", '{"argv":[]}', '{"argv":[""]}',
    '{"argv":[1]}', '{"argv":["echo"],"extra":true}',
    '{"argv":["echo"],"timeout_seconds":0}',
    '{"argv":["echo"],"timeout_seconds":true}',
    '{"argv":["echo"],"timeout_seconds":NaN}',
    '{"argv":["echo"],"timeout_seconds":Infinity}',
    '{"argv":["echo"],"timeout_seconds":3601}',
])
def test_invalid_setting_falls_back(tmp_path, monkeypatch, capsys, payload):
    # Given malformed or unsupported node settings.
    monkeypatch.setenv("HOME", str(tmp_path))
    _settings(tmp_path, payload)
    # When correction is attempted.
    result = CommandCorrector(os.environ)(RAW, CONTEXT)
    # Then no note content is lost.
    assert result.text == RAW
    assert "reason=invalid-config" in capsys.readouterr().err


@pytest.mark.parametrize("candidate", [
    RAW + "\n", RAW.replace("synthetic\n", "changed\n", 1),
    RAW.replace("speaker_1", "speaker_2"), RAW.replace("\n\n---", "\n---"),
    RAW.replace("synthetic typo", ""), RAW.replace("synthetic typo", "# injected"), "",
])
def test_structure_change_is_rejected(candidate):
    # Given a transcript and output with damaged structure.
    # When the output is checked.
    accepted = preserves_structure(RAW, candidate)
    # Then it cannot replace the evidence view.
    assert accepted is False


@pytest.mark.parametrize("configured", [False, True])
def test_process_freezes_actual_body_and_preserves_raw_archive(tmp_path, monkeypatch, configured):
    # Given the real process seam, synthetic recording and optional command.
    monkeypatch.setenv("HOME", str(tmp_path))
    if configured:
        _command(tmp_path, (
            "import os,sys;"
            "assert os.environ['PLAUD_NOTE_TITLE']=='2026-09-01_1700_synthetic';"
            "sys.stdout.write(sys.stdin.read().replace('typo','term'))"
        ))
    effects = HookEffects(
        source=replace(_SOURCE, name="synthetic"),
        draft="## 요약\n\n- (요약 없음)\n\n## 전문\n\nsynthetic",
        markdown=RAW, summary="", results=[_ok()],
        extraction=LifelogExtraction(summary="synthetic raw summary"),
    )
    baseline = FakeEffects(
        source=effects.source, draft=effects.draft, markdown=RAW, summary="", results=[_ok()],
        extraction=effects.extraction,
    )
    assert process(_RECORD, effects=baseline, max_attempts=2) == "planned"
    # When the node produces its first frozen note.
    outcome = process(_RECORD, effects=effects, max_attempts=2)
    # Then the archived transcript is raw and hashes bind the actual note body.
    assert outcome == "planned"
    assert effects.stored[0][1] == RAW
    after, body = effects.commits[0][1:]
    assert body is not None
    summary, transcript = split_lifelog_body(body)
    assert summary == "synthetic raw summary"
    assert ("synthetic term" if configured else "synthetic typo") in transcript
    assert after.body_sha256 == hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert after.action_hash == plaud_action_hash(PlaudHashFields(
        after.recording_id, after.note_relpath, after.note_title, after.body_sha256,
    ))
    if not configured:
        assert effects.commits == baseline.commits
    else:
        assert after.action_hash != baseline.commits[0][1].action_hash


def test_live_effects_exposes_node_corrector(tmp_path, monkeypatch):
    # Given the production adapter and an opt-in command.
    monkeypatch.setenv("HOME", str(tmp_path))
    _command(tmp_path, "import sys;sys.stdout.write(sys.stdin.read().replace('typo','term'))")
    effects = LiveEffects(tmp_path, tmp_path / "lock", dict(os.environ))
    # When the effect used by promotion is called.
    result = effects.transcript_corrector(RAW, CONTEXT)
    # Then live wiring uses the node-local command.
    assert result.text == FIXED


@pytest.mark.parametrize("status", ["planned", "transcribing"])
def test_cloud_discovery_corrects_only_write_ready_notes(tmp_path, monkeypatch, status):
    # Given a cloud recording and a configured node command.
    monkeypatch.setenv("HOME", str(tmp_path))
    _command(tmp_path, "import sys;sys.stdout.write(sys.stdin.read().replace('typo','term'))")
    recording = LifelogRecording(
        id="synthetic-id", name="synthetic", created_at="2026-10-20T03:00:00Z",
        start_at="2026-10-20T03:00:00Z", duration_ms=60000,
        summary_markdown="synthetic summary", transcript_text=RAW,
    )
    # When discovery freezes a write-ready note or parks a local-transcription draft.
    result = plan_new_records(
        PlaudSyncState(1, None, {}), [recording], now=datetime(2026, 10, 20, tzinfo=UTC),
        policy_version=8, extractor=lambda _: LifelogExtraction(),
        initial_status=status, transcript_corrector=CommandCorrector(os.environ),
    )
    # Then only a note ready for first write carries the corrected transcript.
    body = result.bodies[recording.id]
    assert ("synthetic term" if status == "planned" else "synthetic typo") in body
    assert result.state.records[recording.id].body_sha256 == hashlib.sha256(
        body.encode("utf-8")
    ).hexdigest()


@pytest.mark.parametrize("program,timeout", [
    ("print('synthetic malformed output')", 300),
    ("import signal;signal.pause()", 0.05),
])
def test_failed_hook_still_freezes_the_raw_note(tmp_path, monkeypatch, program, timeout):
    # Given a failed correction command before local promotion.
    monkeypatch.setenv("HOME", str(tmp_path))
    _command(tmp_path, program, timeout=timeout)
    effects = HookEffects(
        source=replace(_SOURCE, name="synthetic"), markdown=RAW,
        summary="synthetic summary", results=[_ok()],
    )
    baseline = FakeEffects(
        source=effects.source, markdown=RAW, summary=effects.summary, results=[_ok()],
    )
    assert process(_RECORD, effects=baseline, max_attempts=2) == "planned"
    # When correction fails.
    result = process(_RECORD, effects=effects, max_attempts=2)
    # Then the note is not blocked and its body/hash exactly match the raw path.
    assert result == "planned"
    assert effects.commits == baseline.commits
    assert effects.stored == baseline.stored


MEETING: Final = (
    "# synthetic meeting\n\n---\n\n"
    "[00:00:01] 화자1: 홍길둥 박사님 오셨어요\n"
    "[00:00:05] 화자2: 네 시작합니다\n"
    "[00:00:09] 화자1 · 가명\n"
    "[00:00:11] 라벨 없는 발화 홍길둥\n"
)


def test_meeting_lines_accept_word_edits_but_not_label_or_time_edits():
    # Given dori meeting lines ([time] label: words) and an unlabeled timed line.
    fixed = MEETING.replace("홍길둥", "홍길동")
    # When word edits, then label, time or header edits are checked.
    # Then only the words after the frozen prefix may change.
    assert preserves_structure(MEETING, fixed) is True
    for damaged in (
        fixed.replace("화자1: 홍길동", "화자3: 홍길동"),
        fixed.replace("[00:00:05]", "[00:00:06]"),
        fixed.replace("화자1 · 가명", "화자1 · 다른이름"),
        fixed.replace("화자2: 네 시작합니다", "화자2:"),
    ):
        assert preserves_structure(MEETING, damaged) is False


def test_change_table_comes_from_bytes_and_report_evidence(tmp_path, monkeypatch):
    # Given a command that fixes one word, reports it, and silently changes another.
    monkeypatch.setenv("HOME", str(tmp_path))
    _command(tmp_path, (
        "import json,os,sys;t=sys.stdin.read();"
        "open(os.environ['PLAUD_CORRECTION_REPORT'],'w').write(json.dumps("
        "{'evidence':[{'before':'홍길둥','after':'홍길동','evidence':'기억: 합성 노트'}],'rejected':2}));"
        "sys.stdout.write(t.replace('홍길둥 박사님','홍길동 박사님').replace('시작합니다','시작함니다'))"
    ))
    # When the hook runs.
    result = CommandCorrector(os.environ)(MEETING, CONTEXT)
    # Then the unreported change is still in the table, with no claimed evidence.
    assert [(c.at, c.before, c.after, c.evidence) for c in result.changes] == [
        ("00:00:01", "홍길둥", "홍길동", "기억: 합성 노트"),
        ("00:00:05", "시작합니다", "시작함니다", ""),
    ]
    assert result.rejected == 2


@pytest.mark.parametrize("report", ["[]", "{\"evidence\":[{\"before\":1}]}", "{\"rejected\":true}"])
def test_malformed_report_falls_back_to_raw(tmp_path, monkeypatch, capsys, report):
    # Given a command whose evidence report is malformed.
    monkeypatch.setenv("HOME", str(tmp_path))
    _command(tmp_path, (
        f"import os,sys;open(os.environ['PLAUD_CORRECTION_REPORT'],'w').write({report!r});"
        "sys.stdout.write(sys.stdin.read().replace('홍길둥','홍길동'))"
    ))
    # When the hook runs.
    result = CommandCorrector(os.environ)(MEETING, CONTEXT)
    # Then the raw transcript is kept.
    assert result == CorrectedTranscript(MEETING)
    assert "reason=invalid-report" in capsys.readouterr().err


@pytest.mark.parametrize("configured", [False, True])
def test_note_carries_change_table_before_the_transcript(tmp_path, monkeypatch, configured):
    # Given the real process seam and, optionally, a command that fixes one word.
    monkeypatch.setenv("HOME", str(tmp_path))
    if configured:
        _command(tmp_path, (
            "import json,os,sys;"
            "open(os.environ['PLAUD_CORRECTION_REPORT'],'w').write(json.dumps("
            "{'evidence':[{'before':'typo','after':'term','evidence':'기억: 합성'}],'rejected':1}));"
            "sys.stdout.write(sys.stdin.read().replace('typo','term'))"
        ))
    effects = HookEffects(
        source=replace(_SOURCE, name="synthetic"), markdown=RAW,
        summary="synthetic summary", results=[_ok()],
    )
    # When the first note is frozen.
    assert process(_RECORD, effects=effects, max_attempts=2) == "planned"
    body = effects.commits[0][2]
    # Then the table sits in its own section and the summary readback is untouched.
    assert body is not None
    summary, transcript = split_lifelog_body(body)
    assert summary == "synthetic summary"
    if configured:
        assert "## 교정 기록" in body
        assert "| 00:00:01 | typo | term | 기억: 합성 |" in body
        assert "이름 가드가 막은 교정 1건" in body
        assert "synthetic term" in transcript and "교정 기록" not in transcript
    else:
        assert "## 교정 기록" not in body
