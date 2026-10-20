"""Composite action hash binding one recording to its exact note payload.

노트 본문을 얼리는 일(`finalize`)도 여기 산다. 본문과 그 sha, 그리고 sha 를 안는 action
hash 는 **한 호출 안에서** 만들어져야 서로 어긋나지 않는다 — 카드와 push 가 묶는 것이 바로
그 sha 이므로, 본문이 한 번 언 뒤에는 교정도 재렌더도 존재할 수 없다.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import tzinfo
from pathlib import PurePosixPath
from typing import Final

from automation import term_correction

from .lifelog_model import ExtractionOutcome, LifelogRecording
from .model import PlaudSyncRecord
from .note import corrected_lifelog_note
from .transcript_corrector import Corrector

PLAUD_SYNC_HASH_VERSION: Final = "plaud-sync-v1"


@dataclass(frozen=True, slots=True)
class PlaudHashFields:
    recording_id: str
    note_relpath: str
    note_title: str
    body_sha256: str


def plaud_action_hash(fields: PlaudHashFields) -> str:
    encoded = json.dumps(
        {
            "body_sha256": fields.body_sha256,
            "destination_kind": "obsidian",
            "note_relpath": fields.note_relpath,
            "note_title": fields.note_title,
            "recording_id": fields.recording_id,
            "version": PLAUD_SYNC_HASH_VERSION,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def finalize(
    record: PlaudSyncRecord,
    recording: LifelogRecording,
    *,
    extraction: ExtractionOutcome,
    tz: tzinfo,
    glossary: term_correction.Glossary = (),
    transcript_corrector: Corrector | None = None,
) -> tuple[PlaudSyncRecord, str, tuple[term_correction.Correction, ...]]:
    """언 기록 · 그 본문 · 그 노트를 만들며 고친 어절.

    교정 참고 문서를 여기서 받는 이유는 하나다: 고칠 수 있는 마지막 순간이 본문을 얼리기
    직전이기 때문이다. 무엇이 바뀌었는지는 돌려주기만 한다 — 로그는 참고 문서를 읽어 온
    효과 경계의 일이다.
    """
    # 이름은 제목이 확정되는 지금 짓는다(발견 때는 로컬 전사 녹음에 제목이 없다). 단 vault 에 이미
    # 쓰인 노트(remote_ref)는 경로를 고정해 재처리가 같은 파일을 덮어쓰게 한다 — 옛 노트를 고아로
    # 남기지 않는다(소유자 결정 2026-09-15).
    pinned = PurePosixPath(record.note_relpath) if record.remote_ref is not None else None
    note = corrected_lifelog_note(
        recording, extraction=extraction, tz=tz, glossary=glossary, relpath=pinned,
        transcript_corrector=transcript_corrector,
    )
    plan = note.plan
    relpath = plan.relpath.as_posix()
    body_sha256 = hashlib.sha256(plan.body.encode("utf-8")).hexdigest()
    promoted = replace(
        record,
        status="planned",
        note_relpath=relpath,
        note_title=plan.title,
        body_sha256=body_sha256,
        action_hash=plaud_action_hash(
            PlaudHashFields(
                recording_id=record.recording_id,
                note_relpath=relpath,
                note_title=plan.title,
                body_sha256=body_sha256,
            )
        ),
        last_block_reason=None,
        next_transcribe_at=None,
    )
    return promoted, plan.body, note.corrections
