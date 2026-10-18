"""Pure auto-write kind registry and release-card binding; no delivery dependencies."""
from __future__ import annotations

import hashlib
from typing import Final

KINDS: Final = {"plaud": "Plaud 노트"}
SURFACE_PREFIX: Final = "auto-write:"
CARD_LINES: Final = {
    "plaud": "Plaud 노트 자동 쓰기: 녹음 노트를 카드 없이 옵시디언에 씀(쓴 건 매일 21:30 요약)",
}


def surface(kind: str) -> tuple[str, str]:
    """Bind this producer's exact `on` policy into the release approval hash."""
    if kind not in KINDS:
        raise ValueError("unknown auto-write kind")
    return SURFACE_PREFIX + kind, hashlib.sha256(b"on").hexdigest()
