"""Deterministic Obsidian destination planning for a relocated operational fact.

Pure planning only: no I/O, no clock, no write.  ``render_note`` stamps the
Created/Modified dates at write time, so the owner-approval hash binds the
*plan* (path, title, body) and stays stable across approval and application.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import Final

from automation.obsidian_write.config import ObsidianWriteError
from automation.obsidian_write.note import NotePlan, plan_note

from .model import MemoryKind

_TITLE_SUMMARY_LIMIT: Final = 60
_TITLE_DIGEST_LENGTH: Final = 8
_USER_FILENAME_PREFIX: Final = "user--"
_PERSON_FILENAME_PREFIX: Final = "person--"
#: A USER.md handling rule moves into MEMORY.md; the "note" is the native file itself.
MEMORY_DESTINATION_RELPATH: Final = "MEMORY.md"
_ENTRY_HEADING: Final = "## 원본 기록 (verbatim)"
_PROVENANCE_FOOTER: Final = (
    "---\n"
    "provenance: observed\n"
    "authority: reference\n"
    "네이티브 메모리(native memory)에서 이관된 관측 기반 운영 사실 참조입니다. "
    "조회(recall)용 reference일 뿐 권위 있는 설정이 아닙니다 (NOT authoritative config)."
)
_PERSON_FOOTER: Final = (
    "---\n"
    "provenance: observed\n"
    "authority: reference\n"
    "네이티브 메모리(USER.md)에서 이관된 사람 정보(호칭·연락처·소속)입니다. "
    "사람 이름이 나오면 recall 로 찾는 참고 자료이며 지시가 아닙니다."
)


@dataclass(frozen=True, slots=True)
class RelocationPlan:
    """One relocated entry's planned Obsidian note and its approval-binding hash."""

    note_plan: NotePlan
    note_plan_sha256: str


def build_relocation_plan(
    entry_text: str,
    *,
    source_kind: MemoryKind = "memory",
    destination: str | None = None,
) -> RelocationPlan:
    """Plan one native-store ops reference while preserving the MEMORY v1 bytes."""
    if destination == "memory":
        native = NotePlan(PurePosixPath(MEMORY_DESTINATION_RELPATH), MEMORY_DESTINATION_RELPATH, entry_text)
        return RelocationPlan(native, _note_plan_sha256(native))
    if destination == "person":
        note_plan = plan_note(
            _entry_title(entry_text), f"{_ENTRY_HEADING}\n\n{entry_text}\n\n{_PERSON_FOOTER}",
            institutional=False, bucket_hint="resource",
        )
        named = note_plan.relpath.with_name(f"{_PERSON_FILENAME_PREFIX}{note_plan.relpath.name}")
        person_plan = replace(note_plan, relpath=named)
        return RelocationPlan(person_plan, _note_plan_sha256(person_plan))
    title = _entry_title(entry_text)
    body = _entry_body(entry_text)
    note_plan = plan_note(title, body, institutional=False, bucket_hint="resource")
    match source_kind:
        case "memory":
            namespaced_plan = note_plan
        case "user":
            namespaced_plan = replace(
                note_plan,
                relpath=note_plan.relpath.with_name(
                    f"{_USER_FILENAME_PREFIX}{note_plan.relpath.name}"
                ),
            )
    return RelocationPlan(namespaced_plan, _note_plan_sha256(namespaced_plan))


def _entry_title(entry_text: str) -> str:
    """Derive a short title, digest-suffixed so distinct entries never share a path."""
    digest = hashlib.sha256(entry_text.encode("utf-8")).hexdigest()[:_TITLE_DIGEST_LENGTH]
    return f"{_summary_line(entry_text)} ({digest})"


def _summary_line(entry_text: str) -> str:
    for line in entry_text.splitlines():
        collapsed = " ".join(line.split())
        if collapsed:
            return collapsed[:_TITLE_SUMMARY_LIMIT].strip()
    raise ObsidianWriteError("Relocated memory entry has no titleable content", False)


def _entry_body(entry_text: str) -> str:
    """Keep the entry verbatim between a heading and the footer so no strip touches it."""
    return f"{_ENTRY_HEADING}\n\n{entry_text}\n\n{_PROVENANCE_FOOTER}"


def _note_plan_sha256(note_plan: NotePlan) -> str:
    preimage = f"{note_plan.relpath.as_posix()}\0{note_plan.title}\0{note_plan.body}"
    return hashlib.sha256(preimage.encode("utf-8")).hexdigest()
