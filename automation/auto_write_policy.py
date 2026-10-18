"""Pure auto-write kind registry and release-card binding; no delivery dependencies."""
from __future__ import annotations

import hashlib
from typing import Final

KINDS: Final = {
    "plaud": "Plaud 노트", "yoodori": "유돌 요청", "calendar": "형 일정", "todo": "할 일",
    "obsidian": "옵시디언 노트", "drive-save": "Drive 저장", "project-board": "프로젝트 보드",
    "yoodori-memory": "유돌 기억 사본",
}
SURFACE_PREFIX: Final = "auto-write:"
CARD_LINES: Final = {
    "plaud": "Plaud 노트 자동 쓰기: 녹음 노트를 카드 없이 옵시디언에 씀(쓴 건 매일 21:30 요약)",
    # Owner 2026-10-18 (Q233; patent export 18:29 KST "5번도 카드 없이 해").
    "yoodori": "유돌 요청 쓰기: 형 디스코드 글로 시킨 위키·문서·전사·회의록·특허 준비·반출을 카드 없이 "
                "(형 글인지 노드가 확인, 쓴 건 근거 링크와 매일 21:30 요약)",
    # Q233 (2026-10-18 17:40 KST "6개 다 카드 없이 바로 + 하루 요약에 남기기"), one switch each.
    "calendar": "형 일정 자동 쓰기: 형 글로 시킨 형 혼자 일정을 카드 없이(바깥 참석자가 있으면 카드 그대로)",
    "todo": "할 일 자동 쓰기: 형 글로 시킨 할 일을 카드 없이 Google Tasks 에 등록",
    "obsidian": "옵시디언 노트 자동 쓰기: 형 글로 시킨 노트 저장을 카드 없이",
    "drive-save": "Drive 저장 자동 쓰기: 형 글로 시킨 파일을 카드 없이 형 Drive 비공개로 저장",
    "project-board": "프로젝트 보드 자동 반영: 노드가 만든 보드 제안을 카드 없이 반영(쓴 건 매일 21:30 요약)",
    # Owner Q241 (2026-10-18 20:55 KST): omo memory is canonical, one automatic Obsidian copy a day.
    "yoodori-memory": "유돌 기억 사본: 유돌 omo 기억을 하루 한 번 옵시디언 노트 한 개로 덮어씀(recall 에 잡힘)",
}


def surface(kind: str) -> tuple[str, str]:
    """Bind this producer's exact `on` policy into the release approval hash."""
    if kind not in KINDS:
        raise ValueError("unknown auto-write kind")
    return SURFACE_PREFIX + kind, hashlib.sha256(b"on").hexdigest()
