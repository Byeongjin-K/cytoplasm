"""Promotion and fallback assembly moved from ``transcribe`` for the F2 250-LOC ceiling.

The pure-step facade keeps its original import surface while this module owns local
transcript conversion and cloud-fallback promotion as one cohesive assembly seam.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Final

from .audio import AudioSource
from .binding import finalize
from .lifelog_model import LifelogExtractError
from .model import PlaudSyncRecord
from .note import LifelogRecording, split_lifelog_body
from .transcribe_model import ENVIRONMENT_EXIT_CODES, LocalTranscript, Outcome, TranscribeEffects, TranscribeError
from .transcribe_policy import RetryPolicy
from .transcribe_text import recording_from_source as _recording
from .transcribe_text import split_transcript, transcript_stem
from .transcript_corrector import CorrectionEffects, Corrector, TranscriptContext

REASON_LIMIT: Final = 200
NO_SUMMARY: Final = "- (요약 없음)"
NO_TRANSCRIPT: Final = "- (전사 없음)"


def promote(
    record: PlaudSyncRecord, recording: LifelogRecording, effects: TranscribeEffects,
    *, transcript: LocalTranscript | None = None,
) -> tuple[PlaudSyncRecord, str]:
    """Finalize the record with fields extracted from this transcription."""
    corrector: Corrector | None = None
    if isinstance(effects, CorrectionEffects):
        command = effects.transcript_corrector

        def correct_note(markdown: str, context: TranscriptContext) -> str:
            if transcript is None:
                return command(markdown, context)
            corrected = command(transcript.markdown, context)
            legend, text = split_transcript(corrected)
            return "\n\n".join(part for part in (legend, text) if part)

        corrector = correct_note
    promoted, body, corrections = finalize(
        record,
        recording,
        extraction=effects.extract(recording),
        tz=effects.tz,
        glossary=effects.glossary(),
        transcript_corrector=corrector,
    )
    effects.record_corrections(recording, corrections)
    return promoted, body


def local_transcript(
    source: AudioSource, record: PlaudSyncRecord, effects: TranscribeEffects
) -> tuple[LocalTranscript, Path]:
    audio = effects.download(source)
    try:
        result = effects.transcribe(audio, transcript_stem(record))
        reason = f"rc={result.returncode} {result.detail}".strip()
        if result.returncode in ENVIRONMENT_EXIT_CODES:
            raise TranscribeError(reason, counted=False)
        if result.returncode != 0 or result.transcript_path is None:
            raise TranscribeError(reason, counted=True)
        markdown = effects.read_transcript(result.transcript_path)
        legend, body = split_transcript(markdown)
        if not body:
            raise TranscribeError("전사본 본문이 비어 있다", counted=True)
        return LocalTranscript(markdown, legend, body, result.model), audio
    finally:
        effects.archive_audio(audio, source, transcript_stem(record))


def block(
    record: PlaudSyncRecord, effects: TranscribeEffects, reason: str, *, attempts: int | None = None
) -> Outcome:
    blocked = replace(
        record,
        last_block_reason=reason[:REASON_LIMIT],
        transcribe_attempts=record.transcribe_attempts if attempts is None else attempts,
    )
    return "retry" if effects.commit(record, blocked, None) else "stale"


def fallback(
    record: PlaudSyncRecord,
    source: AudioSource,
    draft: str,
    effects: TranscribeEffects,
    *,
    attempts: int,
    reason: str,
    policy: RetryPolicy,
) -> Outcome:
    draft_summary, _ = split_lifelog_body(draft)
    try:
        summary = effects.fetch_summary(record.recording_id) or (
            "" if draft_summary == NO_SUMMARY else draft_summary
        )
        cloud = effects.fetch_transcript(record.recording_id)
    except TranscribeError as error:
        return block(record, effects, error.reason, attempts=attempts)
    missing = [
        name
        for name, content, placeholder in (
            ("요약", summary, NO_SUMMARY),
            ("전사", cloud.text, NO_TRANSCRIPT),
        )
        if not content.strip() or content == placeholder
    ]
    if len(missing) == 2:
        if attempts >= policy.give_up:
            return abandon(record, effects, attempts)
        blocked = replace(
            record,
            transcribe_attempts=attempts,
            last_block_reason="클라우드 폴백 보류: 요약과 전사가 없다",
            next_transcribe_at=policy.next_at(attempts),
        )
        return "retry" if effects.commit(record, blocked, None) else "stale"
    recording = _recording(
        source,
        draft,
        summary=summary,
        transcript_text=cloud.text,
        transcript_source=f"{cloud.source_label}(로컬 전사 {attempts}회 실패: {reason[:80]})",
    )
    try:
        promoted, body = promote(replace(record, transcribe_attempts=attempts), recording, effects)
    except LifelogExtractError as error:
        return block(record, effects, f"추출: {error}", attempts=attempts)
    return "fallback" if effects.commit(record, promoted, body) else "stale"


def abandon(record: PlaudSyncRecord, effects: TranscribeEffects, attempts: int) -> Outcome:
    """Mark a record terminal when both cloud sources remain unavailable."""
    after = replace(
        record,
        status="abandoned",
        transcribe_attempts=attempts,
        next_transcribe_at=None,
        last_block_reason=f"로컬 전사 {attempts}회 실패·클라우드 요약/전사 없음 — 폐기",
    )
    return "abandoned" if effects.commit(record, after, None) else "stale"
