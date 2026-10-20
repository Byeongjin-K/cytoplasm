"""Discovery planning — freeze new recordings into approval-ready records.

v2 (B안, 2026-09-04): each new recording goes through the injected extractor before
its note is frozen. A permanent skip (no LLM configured) freezes a
note that says so; a transient failure (LifelogExtractError) defers the recording to
the next poll instead of freezing a degraded note forever — same principle as the
empty-summary skip.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, tzinfo
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Final

from automation import term_correction

from .binding import PlaudHashFields, plaud_action_hash
from .duration import DEFAULT_MIN_DURATION_MS
from .lifelog_fields import DEFAULT_TIMEZONE
from .lifelog_model import ExtractionOutcome, ExtractionSkipped, Extractor, LifelogExtractError
from .model import PlaudStatus, PlaudSyncRecord, PlaudSyncState, canonical_recording_id
from .note import LifelogRecording, PlaudNoteError, corrected_lifelog_note, recording_stamp
from .transcript_corrector import Corrector

APPROVAL_KIND: Final = "obsidian-write"
APPROVAL_SURFACE: Final = "agent-chat-thread"
#: 한눈에 줄에 적히는 초안 사유 — transcribe.finalize 가 로컬 전사로 진짜 추출을 한다.
DRAFT_EXTRACTION_REASON: Final = "로컬 전사 뒤 추출"


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    state: PlaudSyncState
    bodies: Mapping[str, str]
    planned: tuple[str, ...]
    skipped: tuple[str, ...]
    skipped_durations: tuple[tuple[str, int], ...] = ()
    #: Recordings whose field extraction failed this poll — not frozen, retried next poll.
    deferred: tuple[str, ...] = ()
    #: (녹음 이름, 그 노트에서 고친 어절들). 감사 로그는 효과 경계가 남긴다.
    corrections: tuple[tuple[str, tuple[term_correction.Correction, ...]], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "bodies", MappingProxyType(dict(self.bodies)))

    @property
    def skipped_lines(self) -> tuple[str, ...]:
        durations = dict(self.skipped_durations)
        return tuple(
            f"plaud-sync: recording_id={key} outcome=skipped duration_ms={durations[key]} reason=최소 길이 미만"
            if key in durations else f"plaud-sync: unplannable recording skipped: {key}"
            for key in self.skipped
        )


def poll_due(state: PlaudSyncState, now: datetime, interval_seconds: int) -> bool:
    if state.last_poll_at is None:
        return True
    try:
        last_poll = datetime.fromisoformat(state.last_poll_at)
    except ValueError:
        return True
    if last_poll.tzinfo is None:
        return True
    return (now - last_poll).total_seconds() >= interval_seconds


def plan_new_records(
    state: PlaudSyncState,
    recordings: Iterable[LifelogRecording],
    *,
    now: datetime,
    policy_version: int,
    extractor: Extractor,
    tz: tzinfo = DEFAULT_TIMEZONE,
    initial_status: PlaudStatus = "planned",
    glossary: term_correction.Glossary = (),
    min_duration_ms: int = DEFAULT_MIN_DURATION_MS,
    transcript_corrector: Corrector | None = None,
) -> DiscoveryResult:
    records = dict(state.records)
    known_ids = {
        canonical_recording_id(alias)
        for key, record in records.items()
        for alias in (key, record.recording_id, *record.aliases)
    }
    bodies: dict[str, str] = {}
    planned: list[str] = []
    skipped: list[str] = []
    skipped_durations: list[tuple[str, int]] = []
    deferred: list[str] = []
    corrections: list[tuple[str, tuple[term_correction.Correction, ...]]] = []

    for recording in recordings:
        if canonical_recording_id(recording.id) in known_ids:
            continue
        if recording.duration_ms < min_duration_ms:
            skipped.append(recording.id)
            skipped_durations.append((recording.id, recording.duration_ms))
            continue
        if initial_status != "transcribing" and not (
            recording.summary_markdown.strip() or recording.transcript_text.strip()
        ):
            # Plaud has not produced anything yet (transcription pending or failed):
            # freezing an empty note would post an approval card for nothing and
            # pin the recording forever, so leave it for a later poll instead.
            # When the node transcribes locally the cloud text is only a draft — an
            # empty one is exactly the recording that path exists for (2026-09-05 실측:
            # a 64-minute recording Plaud never processed was skipped at every poll).
            skipped.append(recording.id)
            continue
        try:
            _ = recording_stamp(recording, tz)
        except PlaudNoteError:
            skipped.append(recording.id)
            continue
        if initial_status == "transcribing":
            # The draft only parks the record until the local transcript is in;
            # transcribe.finalize runs the extractor on that transcript (the better input).
            extraction: ExtractionOutcome = ExtractionSkipped(DRAFT_EXTRACTION_REASON)
        else:
            try:
                extraction = extractor(recording)
            except LifelogExtractError:
                deferred.append(recording.id)
                continue
        taken = frozenset(PurePosixPath(existing.note_relpath) for existing in records.values())
        note = corrected_lifelog_note(
            recording, extraction=extraction, tz=tz, glossary=glossary, taken=taken,
            transcript_corrector=transcript_corrector if initial_status == "planned" else None,
        )
        plan = note.plan
        if note.corrections:
            corrections.append((recording.name or recording.id, note.corrections))
        note_relpath = plan.relpath.as_posix()
        body_sha256 = hashlib.sha256(plan.body.encode("utf-8")).hexdigest()
        records[recording.id] = PlaudSyncRecord(
            version=1,
            recording_id=recording.id,
            recorded_at=recording.start_at or recording.created_at,
            note_relpath=note_relpath,
            note_title=plan.title,
            body_sha256=body_sha256,
            action_hash=plaud_action_hash(
                PlaudHashFields(
                    recording_id=recording.id,
                    note_relpath=note_relpath,
                    note_title=plan.title,
                    body_sha256=body_sha256,
                )
            ),
            status=initial_status,
            kind=APPROVAL_KIND,
            surface=APPROVAL_SURFACE,
            channel_id="",
            policy_version=policy_version,
            message_id=None,
            created_at=now.isoformat(),
            approved_at=None,
            written_at=None,
            remote_ref=None,
            note_content_sha256=None,
            last_block_reason=None,
        )
        bodies[recording.id] = plan.body
        planned.append(recording.id)
        known_ids.add(canonical_recording_id(recording.id))

    return DiscoveryResult(
        state=PlaudSyncState(state.version, now.isoformat(), records),
        bodies=bodies,
        planned=tuple(planned),
        skipped=tuple(skipped),
        skipped_durations=tuple(skipped_durations),
        deferred=tuple(deferred),
        corrections=tuple(corrections),
    )
