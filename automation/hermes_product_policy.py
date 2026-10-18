"""Pure release-card policy; staged gates do not import removal or ledger effects."""
from __future__ import annotations

import hashlib
from typing import Final

SURFACE: Final = "hermes-product:retired"
CARD_LINES: Final = (
    "Hermes 제품 지우기: state.db·sessions·memories·skills·모델 캐시·게이트웨이 상태 파일, kanban.db(장부 이관 확인 뒤) — 장부 원본만 백업",
    "~/.hermes 폴더 자체는 지금 우리 런타임이라 남김(이전 때 새 경로) · SOUL·hermes 바이너리·config·auth·hermes-agent 는 이전 단계에서",
)


def surface() -> tuple[str, str]:
    return SURFACE, hashlib.sha256(b"retired").hexdigest()
