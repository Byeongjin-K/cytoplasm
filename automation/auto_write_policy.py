"""Pure auto-write kind registry and release-card binding; no delivery dependencies."""
from __future__ import annotations

import hashlib
from typing import Final

KINDS: Final = {"plaud": "Plaud 노트", "yoodori": "유돌 요청"}
SURFACE_PREFIX: Final = "auto-write:"
CARD_LINES: Final = {
    "plaud": "Plaud 노트 자동 쓰기: 녹음 노트를 카드 없이 옵시디언에 씀(쓴 건 매일 21:30 요약)",
    # Owner 2026-10-18 (Q233; patent export 18:29 KST "5번도 카드 없이 해").
    "yoodori": "유돌 요청 쓰기: 형 디스코드 글로 시킨 위키·문서·전사·회의록·특허 준비·반출을 카드 없이 "
                "(형 글인지 노드가 확인, 쓴 건 근거 링크와 매일 21:30 요약)",
}


def surface(kind: str) -> tuple[str, str]:
    """Bind this producer's exact `on` policy into the release approval hash."""
    if kind not in KINDS:
        raise ValueError("unknown auto-write kind")
    return SURFACE_PREFIX + kind, hashlib.sha256(b"on").hexdigest()
