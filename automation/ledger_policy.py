"""Pure release-card binding, also staged by the skill supply-chain gate."""
from __future__ import annotations

import hashlib
from typing import Final

SURFACE: Final = "ledger:on"
CARD_LINE: Final = "할 일 장부: 수리 티켓·회의록 액션·제안서 카드를 Hermes 보드에서 새 장부로(카드 그대로 옮김), 파일 삭제 후 재배포로 Hermes 보드 복귀"


def surface() -> tuple[str, str]:
    return SURFACE, hashlib.sha256(b"on").hexdigest()
