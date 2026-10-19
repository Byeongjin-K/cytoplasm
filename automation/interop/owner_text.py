"""형 채널 글에 기계 출력이 섞였는지 판정하는 단일 규칙 (형 지시 2026-10-20 00:10 KST).

형이 #yoodori 에서 본 회의록 야간 작업 글은 맨 위에 자식 CLI 의 결과 JSON 이 그대로 붙어
있었다. 형 채널에 나가는 자동 글은 짧은 한국어 한두 줄 + 링크 + 형 할 일이고, 기계 출력
(JSON 덩어리, 종료코드, ref·SHA, provider, 경로, 대문자 표식 코드, UTC 타임스탬프)은 형 글에서
빠져 내부 로그나 #dori-pair 로 간다. 이 모듈이 그 판정의 유일한 정의다 — 발신 출구
(`cron_timer_run.deliver`, `owner_notice.notify_owner`)가 이것을 거쳐 형 몫만 보낸다.

판정은 줄 단위다. URL 은 링크로 남겨야 하므로 판정 전에 지운다(쿼리의 `a=b` 를 기계 출력으로
읽지 않는다). 메일 제목처럼 형이 읽어야 할 사람 말은 대문자·하이픈만으로 걸리지 않게, 표식
코드는 실패·차단 같은 상태 접미사를 가진 것만 잡는다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Final

_URL: Final = re.compile(r"<?https?://\S+>?")
#: (이름, 패턴) — 이름은 시험과 내부 로그가 읽는다. 형 글에는 나가지 않는다.
RULES: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("exit-code", re.compile(r"\b(?:exit|rc|returncode)\b\s*[=:]?\s*-?\d+|종료코드", re.IGNORECASE)),
    ("key-value", re.compile(r"(?<![\w/.-])[a-z][a-z0-9_]+=[^\s,]+")),
    ("status-code", re.compile(
        r"\b[A-Z][A-Z0-9]+(?:[-_][A-Z0-9]+)*[-_](?:FAIL(?:ED|URE)?|BLOCK(?:ED)?|SKIP(?:PED)?|ERROR|DRIFT"
        r"|WARN(?:ING)?|UNAVAILABLE|UNCONFIGURED|MISSING|REFUSED|REJECT(?:ED)?|GOVERNED|TIMEOUT"
        r"|DELIVERED|NOT[-_]SUBMITTED)\b")),
    ("git-ref", re.compile(r"\b(?:HEAD|ref|sha|commit)\b[\s\"':=(`]*[0-9a-f]{7,64}\b|`[0-9a-f]{12,64}`")),
    ("provider", re.compile(r"\b(?:openai-codex|codex_called|served_provider|served_model|provider)\b")),
    ("path", re.compile(r"(?<![\w.])(?:~|/(?:home|srv|etc|tmp|var|opt|usr|run))/[\w.@+-]")),
    ("internal-file", re.compile(r"\b[\w.-]+\.(?:py|jsonl?|log|db|sh|ya?ml|toml|lock)\b")),
    ("utc-timestamp", re.compile(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")),
)


@dataclass(frozen=True, slots=True)
class OwnerView:
    """한 덩어리 출력을 형 몫과 나머지로 나눈 결과."""

    owner: str
    #: 형 글에서 뺀 줄 중 결과 데이터(JSON) — 내부 로그에만 남긴다.
    data: tuple[str, ...]
    #: 형 글에서 뺀 줄 중 진단·경고 — 페어 채널로 보낸다. 숨겨서 없애지 않는다.
    diagnostics: tuple[str, ...]
    #: JSON 줄만 뺀 원문(순서 그대로) — 페어 채널이 없는 설치의 기존 목적지용.
    without_data: str


def _is_json(line: str) -> bool:
    if not line.startswith(("{", "[")):
        return False
    if line.startswith(('{"', "[{")):
        return True
    try:
        _ = json.loads(line)
    except ValueError:
        return False
    return True


def findings(line: str) -> tuple[str, ...]:
    """한 줄이 걸리는 규칙 이름들. 빈 튜플이면 형에게 보여도 되는 사람 말이다."""
    stripped = line.strip()
    if _is_json(stripped):
        return ("json",)
    text = _URL.sub("", stripped)
    return tuple(name for name, pattern in RULES if pattern.search(text))


def split(text: str) -> OwnerView:
    """형 몫 줄과 뺀 줄을 가른다. 줄 순서는 그대로 둔다."""
    owner: list[str] = []
    data: list[str] = []
    diagnostics: list[str] = []
    kept: list[str] = []
    for line in text.strip().splitlines():
        found = findings(line)
        if found == ("json",):
            data.append(line)
            continue
        kept.append(line)
        (diagnostics if found else owner).append(line)
    return OwnerView("\n".join(owner).strip(), tuple(data), tuple(diagnostics), "\n".join(kept).strip())
