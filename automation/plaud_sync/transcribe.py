"""Local transcription stage — ``transcribing`` → ``planned`` with the node's own transcript.

Discovery freezes a recording with Plaud's cloud draft but marks it ``transcribing``
(2026-09-04, owner request): the approval card must quote the transcript the NODE
produced — whisper.cpp + sherpa speaker diarization through the governed speechtotext
CLI — not Plaud's. This module is the pure step: which records to work on, how a CLI
result becomes note text, and what a failure means. Every effect (MCP, S3,
subprocess, locks, state writes) is injected; ``transcribe_live`` binds them.

Two failure classes, on purpose. An *environment* failure (no toolchain, governed
refusal, MCP or network error) retries every tick without counting — the owner fixes
the node and the recording waits visibly in ``plaud 상태``. A *recording* failure (the
CLI refused or timed out on this audio, the download broke the cap) counts toward
``max_attempts``; at the cap the record is promoted with the cloud transcript and the
note's source line says so, because a lifelog note stuck forever helps nobody.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime

from .duration import DEFAULT_MIN_DURATION_MS, abandon_short
from .lifelog_model import LifelogExtractError
from .model import PlaudSyncRecord, PlaudSyncState
from .transcribe_policy import DEFAULT_GIVE_UP, RetryPolicy, utc_now
from .transcribe_promote import (
    REASON_LIMIT,
    NO_SUMMARY as _NO_SUMMARY,
    NO_TRANSCRIPT as _NO_TRANSCRIPT,
    abandon as abandon,
    block as _block,
    fallback as _fallback,
    local_transcript as _local_transcript,
    promote as _promote,
)
from .transcribe_queue import candidates as candidates
from .transcribe_text import (
    SPEAKER_LEGEND_PREFIX as SPEAKER_LEGEND_PREFIX,
    recording_from_source as _recording,
    split_transcript as split_transcript,
    transcript_stem as transcript_stem,
)
from .transcribe_model import (
    DEFAULT_MAX_ATTEMPTS as DEFAULT_MAX_ATTEMPTS,
    CliResult as CliResult,
    Outcome,
    TranscribeEffects as TranscribeEffects,
    TranscribeError as TranscribeError,
)


def recheck(record: PlaudSyncRecord, effects: TranscribeEffects, policy: RetryPolicy) -> Outcome | None:
    """Cloud-only pass; None means a local slot may be assigned, never an attempt."""
    try:
        summary = effects.fetch_summary(record.recording_id)
        cloud = effects.fetch_transcript(record.recording_id)
        source = effects.fetch_source(record.recording_id)
    except TranscribeError as error:
        if not error.counted:
            after = replace(record, last_recheck_error=error.reason[:REASON_LIMIT])
            return "retry" if effects.commit(record, after, None) else "stale"
        return _block(record, effects, error.reason)
    if source.duration_ms < policy.min_duration_ms:
        return abandon_short(record, effects, source.duration_ms)
    if (summary.strip() and summary.strip() != _NO_SUMMARY) or (cloud.text.strip() and cloud.text.strip() != _NO_TRANSCRIPT):
        draft = effects.draft_body(record.recording_id)
        if draft is None:
            return _block(record, effects, "동결 본문이 없다 — notes/<id>.md 를 확인한다")
        return _fallback(record, source, draft, effects, attempts=record.transcribe_attempts,
                         reason=record.last_block_reason or "", policy=policy)
    if record.transcribe_attempts >= policy.give_up:
        return abandon(record, effects, record.transcribe_attempts)
    return "waiting" if policy.waiting(record) else None


def process(
    record: PlaudSyncRecord, *, effects: TranscribeEffects, max_attempts: int,
    give_up: int = DEFAULT_GIVE_UP, now: Callable[[], datetime] = utc_now,
    min_duration_ms: int = DEFAULT_MIN_DURATION_MS,
) -> Outcome:
    """Keep the existing max_attempts API; clock and total cap are independent test/runtime knobs."""
    policy = RetryPolicy(max_attempts, give_up, now, min_duration_ms)
    if record.next_transcribe_at is not None or record.transcribe_attempts >= give_up:
        outcome = recheck(record, effects, policy)
        if outcome is not None:
            return outcome
    draft = effects.draft_body(record.recording_id)
    if draft is None:
        return _block(record, effects, "동결 본문이 없다 — notes/<id>.md 를 확인한다")
    try:
        source = effects.fetch_source(record.recording_id)
    except TranscribeError as error:
        return _block(record, effects, error.reason)
    if source.duration_ms < min_duration_ms:
        return abandon_short(record, effects, source.duration_ms)
    try:
        transcript, audio = _local_transcript(source, record, effects)
    except TranscribeError as error:
        if not error.counted:
            return _block(record, effects, error.reason)
        attempts = record.transcribe_attempts + 1
        if attempts < min(max_attempts, give_up):
            return _block(record, effects, error.reason, attempts=attempts)
        return _fallback(record, source, draft, effects, attempts=attempts, reason=error.reason, policy=policy)
    recording = _recording(
        source,
        draft,
        summary=effects.fetch_summary(record.recording_id),
        transcript_text=transcript.note_text,
        transcript_source=transcript.source_label,
    )
    try:
        promoted, body = _promote(record, recording, effects, transcript=transcript)
    except LifelogExtractError as error:
        return _block(record, effects, f"추출: {error}")
    _ = effects.store_transcript(transcript_stem(promoted), transcript.markdown)
    if not effects.commit(record, promoted, body):
        return "stale"
    effects.discard_audio(audio)
    return "planned"


def run_step(
    state: PlaudSyncState, *, effects: TranscribeEffects, limit: int,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS, give_up: int = DEFAULT_GIVE_UP,
    now: Callable[[], datetime] = utc_now,
    min_duration_ms: int = DEFAULT_MIN_DURATION_MS,
) -> tuple[tuple[str, Outcome], ...]:
    """Preserve the public tick knobs; all cloud checks finish before any local work."""
    policy = RetryPolicy(max_attempts, give_up, now, min_duration_ms)
    outcomes: list[tuple[str, Outcome]] = []
    eligible: list[PlaudSyncRecord] = []
    for record in candidates(state, limit=len(state.records)):
        if record.next_transcribe_at is not None or record.transcribe_attempts >= give_up:
            outcome = recheck(record, effects, policy)
            if outcome is not None:
                outcomes.append((record.recording_id, outcome))
                continue
        eligible.append(record)
    for record in eligible[:max(limit, 0)]:
        outcome = process(record, effects=effects, max_attempts=max_attempts,
                          give_up=give_up, now=now, min_duration_ms=min_duration_ms)
        outcomes.append((record.recording_id, outcome))
    return tuple(outcomes)
