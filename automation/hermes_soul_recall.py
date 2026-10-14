"""Agent `~/.hermes/SOUL.md` 에 회상 지시 한 줄이 있는지 확인하고 없으면 덧붙인다.

소유자 결정(Q138 ②, 2026-10-14): 사람 정보·처리 규칙을 USER.md 밖으로 옮기면 그 사실은 매 세션
프롬프트에 실리지 않는다. 그래서 에이전트 정체성 파일에 「사람 이름·과거 사실은 recall로 찾아라」 를 둔다.
SOUL.md 는 소유자가 손으로 고칠 수 있는 파일이라 통째로 배포하지 않고(다른 줄을 지운다) **그 한 줄만**
보장한다. 파일이 없으면 만들지 않는다 — Hermes 는 SOUL.md 가 없을 때 기본 정체성을 쓰므로, 한 줄짜리
파일을 만들면 정체성 전체가 그 한 줄로 바뀐다.

Hermes 는 세션 프롬프트를 만들 때 SOUL.md 를 읽는다(설치본 `agent/system_prompt.py`) — 다음 세션부터
보이고 게이트웨이 재시동은 필요 없다. 선언 `v2:config;algorithm=hermes-soul-recall-v1`, 실행은
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

ALGORITHM: Final = "hermes-soul-recall-v1"
MARKER: Final = "HERMES-SOUL-RECALL"
LINE: Final = "사람 이름·과거 사실은 recall로 찾아라."


def present(text: str) -> bool:
    return any(line.strip() == LINE for line in text.splitlines())


def identity(has_line: bool) -> str:
    return f"line={'present' if has_line else 'absent'}"


def wanted_identity() -> str:
    return identity(True)


def with_line(text: str) -> str:
    if present(text):
        return text
    body = text.rstrip("\n")
    return f"{body}\n\n{LINE}\n" if body else f"{LINE}\n"


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
