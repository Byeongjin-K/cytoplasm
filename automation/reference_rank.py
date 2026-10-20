"""참고자료 후보를 줄 세우는 순수 로직 — Drive 를 모른다.

두 가지에 답한다. **내려받기 전에** 못 읽는다고 말할 수 있는가(형식·크기), 그리고 질의에
얼마나 맞는가(이름·본문). 앞의 답이 랭킹의 첫 열쇠다 — 읽을 수 없는 파일이 이름 점수만으로
상위 자리를 차지하면 정작 읽을 수 있는 근거가 밀려나고, 실측 폴더에는 그런 파일이 64건 중
17건(설문·6.8GiB 짜리 압축본 포함)이었다.

`automation/drive_reference.py` 가 250 pure-LOC 상한에 닿아 갈라져 나왔고, 그 덕에 랭킹
규칙은 Drive 없이 시험된다.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from pathlib import PurePosixPath
from typing import Final, Protocol

from automation.document_text import (
    GOOGLE_EXPORTS,
    HWP_REASON,
    SUPPORTED_REASON,
    oversize_reason,
    readable_suffixes,
)

SNIPPET_CHARS: Final = 320
NOT_EXPORTABLE: Final = "내보낼 수 없는 Google 형식입니다"
_GOOGLE_PREFIX: Final = "application/vnd.google-apps."
_STOP_TERMS: Final = frozenset(
    {"회의", "자료", "내용", "정리", "관련", "대한", "그리고", "the", "and", "for"}
)


class Candidate(Protocol):
    name: str
    path: str
    mime_type: str
    modified: str
    size: int


def nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def terms(query: str) -> tuple[str, ...]:
    cleaned = "".join(char if char.isalnum() else " " for char in nfc(query))
    found = [word.lower() for word in cleaned.split() if len(word) > 1]
    return tuple(dict.fromkeys(word for word in found if word not in _STOP_TERMS))


def refusal(candidate: Candidate, max_bytes: int) -> str:
    """메타데이터만으로 알 수 있는 거부 사유. 읽을 수 있으면 빈 문자열."""
    native = candidate.mime_type.startswith(_GOOGLE_PREFIX)
    if native and candidate.mime_type not in GOOGLE_EXPORTS:
        return NOT_EXPORTABLE
    if not native:
        suffix = PurePosixPath(candidate.name).suffix.lower()
        readable = readable_suffixes()
        if suffix == ".hwp" and suffix not in readable:
            return HWP_REASON
        if suffix not in readable:
            return SUPPORTED_REASON
    if 0 < max_bytes < candidate.size:
        return oversize_reason(max_bytes)
    return ""


def name_score(path: str, wanted: Sequence[str]) -> int:
    haystack = path.lower()
    return sum(3 for term in wanted if term in haystack)


def text_score(text: str, wanted: Sequence[str]) -> int:
    haystack = text.lower()
    return sum(haystack.count(term) for term in wanted)


def coverage(haystack: str, wanted: Sequence[str]) -> int:
    """맞은 낱말의 **가짓수**. 한 낱말이 백 번 나오는 것보다 세 낱말이 한 번씩 나오는 쪽이 낫다.

    전사 본문에서 뽑은 질의어에는 어느 문서에나 있는 흔한 말이 섞여 들어온다. 횟수만 세면
    그런 낱말 하나로 엉뚱한 문서가 올라오지만, 가짓수는 그렇게 올라오지 않는다.
    """
    lowered = haystack.lower()
    return sum(1 for term in wanted if term in lowered)


def fetch_key(
    candidate: Candidate, wanted: Sequence[str], max_bytes: int
) -> tuple[int, int, str, str]:
    return (
        1 if refusal(candidate, max_bytes) else 0,
        -name_score(candidate.path, wanted),
        candidate.modified,
        candidate.path,
    )


def snippet(text: str, wanted: Sequence[str], width: int = SNIPPET_CHARS) -> str:
    flattened = " ".join(text.split())
    lowered = flattened.lower()
    position = min(
        (found for found in (lowered.find(term) for term in wanted) if found >= 0),
        default=-1,
    )
    if position < 0:
        return flattened[:width].strip()
    start = max(0, position - width // 3)
    body = flattened[start : start + width].strip()
    return f"…{body}" if start > 0 else body


def link_for(file_id: str) -> str:
    return f"https://drive.google.com/file/d/{file_id}/view"
