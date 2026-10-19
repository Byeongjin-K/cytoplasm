"""Release-card binding for the retired spend gateway, without removal effects."""
from __future__ import annotations

import hashlib
import re
from typing import Final

SURFACE: Final = "litellm:retired"
CARD_LINES: Final = (
    "LiteLLM 지우기: 컨테이너 이미지·볼륨·유닛·설정 폴더·agent 키(설정·원장은 백업됨)",
)


def surface() -> tuple[str, str]:
    return SURFACE, hashlib.sha256(b"retired").hexdigest()


def config_without_gateway(data: bytes) -> bytes:
    """Remove only the two known YAML blocks, retaining surrounding bytes."""
    lines = data.splitlines(keepends=True)
    selected: set[int] = set()
    blocks = 0
    for heading in (b"providers:", b"custom_providers:"):
        starts = [i for i, line in enumerate(lines) if line.rstrip(b"\r\n") == heading]
        if len(starts) != 1:
            raise ValueError("GATEWAY-RETIRE-REFUSED: 제공자 설정 구조 불일치")
        start = starts[0] + 1
        # PyYAML emits both indented mappings and indentless YAML sequences.
        end = next((i for i in range(start, len(lines))
                    if lines[i].strip() and not lines[i].startswith((b" ", b"#", b"- "))), len(lines))
        prefixes = (b"  litellm:",) if heading == b"providers:" else (
            b"  - name: litellm", b"- name: litellm",
        )
        candidates = [i for i in range(start, end) if lines[i].rstrip(b"\r\n") in prefixes]
        if len(candidates) > 1:
            raise ValueError("GATEWAY-RETIRE-REFUSED: 제공자 설정 중복")
        if not candidates:
            continue  # rerun after a completed atomic edit
        index = candidates[0]
        blocks += 1
        indent = b"  " if lines[index].startswith(b"- ") else b"    "
        block_end = next((i for i in range(index + 1, end)
                          if not lines[i].startswith(indent)), end)
        body = [line.rstrip(b"\r\n") for line in lines[index + 1:block_end]]
        if (len(body) != 2 or set(row.split(b":", 1)[0] for row in body) !=
                {indent + b"base_url", indent + b"key_env"}):
            raise ValueError("GATEWAY-RETIRE-REFUSED: 제공자 설정 필드 불일치")
        values = {row.split(b":", 1)[0].strip(): row.split(b":", 1)[1].strip() for row in body}
        if values[b"base_url"].strip(b"'\"") not in (
                b"http://127.0.0.1:4000", b"http://127.0.0.1:4000/v1") or not re.fullmatch(
                    rb"LITELLM_[A-Z0-9_]+", values[b"key_env"].strip(b"'\"")):
            raise ValueError("GATEWAY-RETIRE-REFUSED: 제공자 설정 값 불일치")
        selected.update(range(index, block_end))
    if blocks == 1:
        raise ValueError("GATEWAY-RETIRE-REFUSED: 제공자 설정 한 쪽만 존재")
    result = b"".join(line for i, line in enumerate(lines) if i not in selected)
    if re.search(rb"(?i)litellm|(?:127\.0\.0\.1|localhost):4000", result):
        raise ValueError("GATEWAY-RETIRE-REFUSED: 예상 밖 제공자 참조 잔존")
    return result
