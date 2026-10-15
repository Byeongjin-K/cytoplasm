"""Agent `~/.hermes/SOUL.md` 에 "기억 먼저 찾기" 계기 목록이 있는지 확인하고 없으면 덧붙인다.

소유자 결정(Q164, 2026-10-15): 한 줄 지시(v1)로는 언제 찾을지가 분명하지 않아, 구체적인 계기 목록과 짧은
검색어 규칙으로 바꾼다. v1 줄이 있으면 목록을 넣으면서 그 줄을 빼 두 번 보이지 않게 한다.

소유자 결정(Q138 ②, 2026-10-14): 사람 정보·처리 규칙을 USER.md 밖으로 옮기면 그 사실은 매 세션
프롬프트에 실리지 않는다. 그래서 에이전트 정체성 파일에 「사람 이름·과거 사실은 recall로 찾아라」 를 둔다.
SOUL.md 는 소유자가 손으로 고칠 수 있는 파일이라 통째로 배포하지 않고(다른 줄을 지운다) **그 한 줄만**
보장한다. 파일이 없으면 만들지 않는다 — Hermes 는 SOUL.md 가 없을 때 기본 정체성을 쓰므로, 한 줄짜리
파일을 만들면 정체성 전체가 그 한 줄로 바뀐다.

Hermes 는 세션 프롬프트를 만들 때 SOUL.md 를 읽는다(설치본 `agent/system_prompt.py`) — 다음 세션부터
보이고 게이트웨이 재시동은 필요 없다. 선언 `v2:config;algorithm=hermes-soul-recall-v2`, 실행은
`automation.hermes_memory_config` 와 같은 `--show`/`--check`/`--apply` 계약이다.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Final

ALGORITHM: Final = "hermes-soul-recall-v2"
MARKER: Final = "HERMES-SOUL-RECALL"
LEGACY_LINE: Final = "사람 이름·과거 사실은 recall로 찾아라."
BLOCK: Final = (
    "## 기억 먼저 찾기",
    "다음일 때는 다른 도구보다 먼저 search_memory(또는 recall)로 저장된 기억을 찾는다:",
    "- 사람 이름·호칭·연락처가 나오거나 누군가에게 연락할 때",
    "- 지난 일·결정·약속·보낸 메일을 말할 때",
    "- 내 노트·전에 정한 방식·선호·규칙이 필요할 때",
    "검색어는 핵심 낱말 2~4개(이름·장소·과제명)로 짧게 쓰고, 없으면 이름만으로 한 번 더 찾는다.",
)


def present(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines()]
    return any(lines[i:i + len(BLOCK)] == list(BLOCK) for i in range(len(lines)))


def identity(has_line: bool) -> str:
    return f"block={'present' if has_line else 'absent'}"


def wanted_identity() -> str:
    return identity(True)


def with_line(text: str) -> str:
    if present(text):
        return text
    kept = [line for line in text.splitlines() if line.strip() != LEGACY_LINE]
    body = "\n".join(kept).rstrip("\n")
    block = "\n".join(BLOCK)
    return f"{body}\n\n{block}\n" if body else f"{block}\n"


def _write(path: Path, text: str) -> Path:
    backup = path.with_name(f"{path.name}.bak-soul-recall-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}")
    shutil.copy2(path, backup)
    temporary = path.with_name(f".{path.name}.soul-recall")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(descriptor, text.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    return backup


def main(argv: Sequence[str] | None = None, *, home: Path | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m automation.hermes_soul_recall")
    mode = parser.add_mutually_exclusive_group(required=True)
    _ = mode.add_argument("--show", action="store_true")
    _ = mode.add_argument("--check", action="store_true")
    _ = mode.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    soul = (home or Path.home()) / ".hermes" / "SOUL.md"
    try:
        text = soul.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        print(f"{MARKER}-ERROR {soul.name}: {error.__class__.__name__}", file=sys.stderr)
        return 3
    has_line = present(text)
    if args.show:
        print(f"{MARKER} have={identity(has_line)}")
        return 0
    if args.check:
        print(f"{MARKER} {'ok' if has_line else 'drift'} have={identity(has_line)}")
        return 0 if has_line else 1
    if has_line:
        print(f"{MARKER} unchanged have={identity(True)}")
        return 0
    try:
        backup = _write(soul, with_line(text))
        after = present(soul.read_text(encoding="utf-8"))
    except OSError as error:
        print(f"{MARKER}-ERROR write: {error.__class__.__name__}", file=sys.stderr)
        return 3
    if not after:
        print(f"{MARKER}-ERROR read-back have={identity(False)}", file=sys.stderr)
        return 5
    print(f"{MARKER} applied have={identity(True)} backup={backup.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
