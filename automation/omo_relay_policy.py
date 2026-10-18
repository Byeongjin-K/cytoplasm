"""Pure release metadata and installed route selection, safe for staged gates."""
from __future__ import annotations

from pathlib import Path

MODE = ".hermes/omo-relay/mode"
SURFACE = "omo-relay"
CARD_LINE = "배치 모델 호출: 노드 → 이 PC omo(도구 없음), 파일 삭제 후 재배포로 Hermes 호출 복귀"


def enabled(home: Path) -> bool:
    try:
        value = (home / MODE).read_bytes()
    except FileNotFoundError:
        return False
    if value != b"on":
        raise ValueError("invalid installed omo-relay mode")
    return True
