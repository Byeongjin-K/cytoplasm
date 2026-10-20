"""PLAUD lifelog note planning and rendering — v2 layout (B안, 2026-09-04).

frontmatter → (# 제목 은 obsidian_write.render_note 가 올린다) → ## 한눈에 → ## 요약 →
## 결정 · 할 일(있을 때만) → ## 전문(접힌 callout) → --- 출처. The body starts with the
YAML block on purpose: the body is what the owner's ✅ binds (body_sha256 → action_hash),
so the frontmatter is approved with everything else.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, replace
from datetime import datetime, tzinfo
from pathlib import PurePosixPath
from typing import assert_never

from automation import term_correction
from automation.obsidian_write.note import NotePlan
from .lifelog_fields import (
    DECISIONS_HEADING,
    DEFAULT_TIMEZONE,
    GLANCE_HEADING,
    NO_SUMMARY,
    SOURCE_RULE,
    SUMMARY_HEADING,
    TAG_ROOT,
    TRANSCRIPT_HEADING,
    decisions_block,
    frontmatter,
    glance_lines,
    lifelog_sections,
    render_duration,
    strip_unresolvable_images,
    topic_tags,
    transcript_block,
    unquote_transcript,
)
from .lifelog_model import ExtractionOutcome, ExtractionSkipped, LifelogExtraction, LifelogRecording
from .note_paths import PlaudNoteError, note_destination, note_title, recording_stamp
from .transcript_corrector import Corrector, TranscriptContext

__all__ = [
    "SOURCE_RULE",
    "SUMMARY_HEADING",
    "TRANSCRIPT_HEADING",
    "CorrectedNote",
    "LifelogRecording",
    "PlaudNoteError",
    "corrected_lifelog_note",
    "note_title",
    "plan_lifelog_note",
    "recording_stamp",
    "render_lifelog_body",
    "split_lifelog_body",
]

@dataclass(frozen=True, slots=True)
class CorrectedNote:
    """언 노트와 그때 바뀐 낱말들 — 감사 로그는 참고 문서를 읽어 온 쪽이 남긴다."""

    plan: NotePlan
    corrections: tuple[term_correction.Correction, ...] = ()


def plan_lifelog_note(
    recording: LifelogRecording,
    *,
    extraction: ExtractionOutcome,
    tz: tzinfo = DEFAULT_TIMEZONE,
    glossary: term_correction.Glossary = (),
    taken: Collection[PurePosixPath] = frozenset(),
    relpath: PurePosixPath | None = None,
) -> NotePlan:
    """Build the deterministic PARA destination and content for one recording."""
    return corrected_lifelog_note(
        recording, extraction=extraction, tz=tz, glossary=glossary, taken=taken, relpath=relpath
    ).plan


def corrected_lifelog_note(
    recording: LifelogRecording,
    *,
    extraction: ExtractionOutcome,
    tz: tzinfo = DEFAULT_TIMEZONE,
    glossary: term_correction.Glossary = (),
    taken: Collection[PurePosixPath] = frozenset(),
    relpath: PurePosixPath | None = None,
    transcript_corrector: Corrector | None = None,
) -> CorrectedNote:
    """계획한 노트와 그 노트를 만들며 고친 낱말들.

    교정은 노트를 **얼리는 이 자리**에서 끝난다 — 승인 카드와 push 가 언 본문의 sha 를 묶으므로
    나중 교정은 존재할 수 없다. 무엇이 바뀌었는지는 돌려만 주고, 로그로 남기는 것은 참고 문서를
    읽어 온 효과 경계의 일이다(순수 함수는 파일을 쓰지 않는다).

    ``relpath`` 를 주면 그 경로가 목적지이고 제목은 그 stem 이다 — 이미 vault 에 쓰인 노트의
    재처리는 이름을 새로 짓지 않고 같은 파일을 덮어쓴다.
    """
    stamp = recording_stamp(recording, tz)
    title_corrections: tuple[term_correction.Correction, ...] = ()
    destination = relpath
    if destination is None:
        destination, title_corrections = note_destination(
            recording, stamp, glossary, generated=_generated_title(extraction), taken=taken
        )
    if transcript_corrector is not None:
        recording = replace(
            recording,
            transcript_text=transcript_corrector(
                recording.transcript_text, TranscriptContext(recording.id, destination.stem)
            ),
        )
    body, corrections = _render(recording, extraction, stamp, glossary, title=destination.stem)
    return CorrectedNote(
        plan=NotePlan(relpath=destination, title=destination.stem, body=body),
        corrections=(*title_corrections, *corrections),
    )


def render_lifelog_body(
    recording: LifelogRecording,
    *,
    extraction: ExtractionOutcome,
    tz: tzinfo = DEFAULT_TIMEZONE,
    glossary: term_correction.Glossary = (),
) -> str:
    """Render the v2 Markdown body (frontmatter first) for one recording."""
    return corrected_lifelog_note(recording, extraction=extraction, tz=tz, glossary=glossary).plan.body


def _corrected_fields(
    extraction: ExtractionOutcome, glossary: term_correction.Glossary
) -> tuple[ExtractionOutcome, tuple[term_correction.Correction, ...]]:
    """용어집은 사람·장소·결정·할 일에만 적용한다 — 기본 '## 전문' 은 원문이다.

    시각(`at`)과 기한(`due`)은 낱말이 아니라 일정이라 손대지 않고, 생략 사유는 우리가 쓴
    문장이라 교정할 것이 없다. 노드가 명시한 transcript_corrector만 동결 전에 전문을 바꾼다.
    """
    if not isinstance(extraction, LifelogExtraction):
        return extraction, ()
    collected: list[term_correction.Correction] = []

    def fixed(text: str) -> str:
        repaired, corrections = term_correction.apply(text, glossary)
        collected.extend(corrections)
        return repaired

    corrected = replace(
        extraction,
        people=tuple(fixed(person) for person in extraction.people),
        places=tuple(fixed(place) for place in extraction.places),
        decisions=tuple(replace(item, text=fixed(item.text)) for item in extraction.decisions),
        todos=tuple(
            replace(todo, text=fixed(todo.text), owner=fixed(todo.owner))
            for todo in extraction.todos
        ),
    )
    return corrected, tuple(collected)


def _render(
    recording: LifelogRecording,
    extraction: ExtractionOutcome,
    stamp: datetime,
    glossary: term_correction.Glossary,
    *,
    title: str,
) -> tuple[str, tuple[term_correction.Correction, ...]]:
    summary_source = recording.summary_markdown
    if not summary_source.strip():
        match extraction:
            case LifelogExtraction(summary=generated):
                summary_source = generated
            case ExtractionSkipped():
                pass
            case unreachable:
                assert_never(unreachable)
    summary, corrections = term_correction.apply(
        strip_unresolvable_images(summary_source), glossary
    )
    extraction, field_corrections = _corrected_fields(extraction, glossary)
    topics = topic_tags(summary)
    parts = [
        frontmatter(
            tags=(TAG_ROOT, *topics),
            title=title,
            source=f"PLAUD 녹음 {recording.id}",
            stamp=stamp,
        ),
        GLANCE_HEADING,
        "\n".join(glance_lines(recording, extraction, stamp=stamp, summary=summary)),
        SUMMARY_HEADING,
        summary or NO_SUMMARY,
    ]
    decisions = decisions_block(extraction)
    if decisions:
        parts += [DECISIONS_HEADING, decisions]
    source_timestamp = (
        stamp.isoformat(timespec="seconds")
        if "로컬 전사" in recording.transcript_source
        else recording.start_at or recording.created_at
    )
    source_line = (
        f"출처: PLAUD 녹음 {recording.id} · {source_timestamp} · {render_duration(recording.duration_ms)}"
    )
    if recording.transcript_source:
        source_line += f" · 전사: {recording.transcript_source}"
    parts += [TRANSCRIPT_HEADING, transcript_block(recording.transcript_text), SOURCE_RULE, source_line]
    return "\n\n".join(parts), (*corrections, *field_corrections)


def _generated_title(extraction: ExtractionOutcome) -> str:
    """생략된 추출에는 제목이 없다 — 그때는 Plaud 이름이 그대로 이름이다."""
    return extraction.title if isinstance(extraction, LifelogExtraction) else ""


def split_lifelog_body(body: str) -> tuple[str, str]:
    """(summary, transcript) read back from a rendered body — v1 and v2 alike.

    Splits on the note's own heading lines only; a Plaud summary carries its own
    '## ' sub-headings and '------------' rules (2026-09-02 실측). The transcript comes
    back without the v2 callout prefix so a round trip returns the recording's text.
    """
    sections = lifelog_sections(body)
    return sections.get(SUMMARY_HEADING, ""), unquote_transcript(sections.get(TRANSCRIPT_HEADING, ""))
