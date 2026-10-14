"""Hermes 내장 메모리 상한(MEMORY.md·USER.md 문자 수)의 단일 출처와 계정 config.yaml 수렴.

소유자 결정 2026-10-14(Q138, 「①②③ 다 (추천: ① 오늘…)」): USER.md 3,000자 · MEMORY.md 3,300자,
agent·peer 둘 다. Hermes 기본값(1,375·2,200)은 USER.md 를 98% 까지 채웠고 새 사실을 받을
자리가 없었다. 값은 여기 `LIMITS` 한 곳에만 있다 — 큐레이터의 `CAPS`(`memory_curator/model.py`)가
이것을 읽고, 릴리스는 선언 `v2:config;algorithm=hermes-memory-limits-v1` 로 각 계정의
`~/.hermes/config.yaml` 이 이 값을 싣는지 관측해 다르면 배포기가 고친다(손 편집이 아니라).

이 모듈은 노드 계정 안에서 실행된다(`python3 -m automation.hermes_memory_config`, 릴리스 트리가
PYTHONPATH):

    --show    지금 값을 한 줄로 낸다          HERMES-MEMORY-LIMITS have=<identity>
    --check   LIMITS 와 같으면 0, 다르면 1
    --apply   다르면 백업 뒤 두 키만 원자적으로 고친다(나머지 바이트는 그대로)

`memory:` 블록이 없으면(Hermes 기본값을 쓰는 계정) 두 키만 든 블록을 끝에 붙인다 — Hermes 는
설정을 기본값 위에 깊은 병합하므로 다른 메모리 키는 기본값 그대로다. 모양을 해석할 수 없으면
(같은 키 두 번, 한 줄 매핑, 정수가 아닌 값) 아무것도 쓰지 않는다.

상한을 **내리는** 변경은 그 파일이 이미 새 상한보다 길면 거부한다(rc 7) — 업스트림 결함
NousResearch/hermes-agent#133028(열림): 파일이 상한보다 큰 상태에서는 정리·삭제 쓰기까지 거부돼
메모리가 갇힌다. 내리려면 파일을 먼저 줄인다.

게이트웨이는 설정을 시작할 때 읽는다 — 선언이 `activation=gateway` 라 릴리스가 agent+peer 를
함께 재시동한다(「게이트웨이 재시동 규칙」).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

ALGORITHM: Final = "hermes-memory-limits-v1"
SECTION: Final = "memory"
#: 키 → 상한(문자 수). 순서가 identity 의 순서다.
LIMITS: Final[dict[str, int]] = {"memory_char_limit": 3300, "user_char_limit": 3000}
#: Hermes 가 키가 없을 때 쓰는 값(설치본 hermes_cli/config_defaults.py).
HERMES_DEFAULTS: Final[dict[str, int]] = {"memory_char_limit": 2200, "user_char_limit": 1375}
#: 상한이 지키는 파일(`~/.hermes/memories/`).
FILES: Final[dict[str, str]] = {"memory_char_limit": "MEMORY.md", "user_char_limit": "USER.md"}
MARKER: Final = "HERMES-MEMORY-LIMITS"

_TOP_KEY: Final = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):(.*)$")
_CHILD: Final = re.compile(r"^([ \t]+)([A-Za-z_][A-Za-z0-9_]*):[ \t]*(.*?)[ \t]*$")
_INT: Final = re.compile(r"^[0-9]+$")


class ConfigShapeError(ValueError):
    """config.yaml 의 memory 블록을 안전하게 해석할 수 없다 — 쓰지 않는다."""


def _value(raw: str) -> str:
    text = raw.split(" #", 1)[0].strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1]
    return text


def _section(lines: list[str]) -> tuple[int, int] | None:
    """`memory:` 블록의 [머리 줄, 끝) — 없으면 None. 끝은 마지막 들여쓴 줄 다음이다."""
    heads = [i for i, line in enumerate(lines) if (m := _TOP_KEY.match(line)) and m[1] == SECTION]
    if not heads:
        return None
    if len(heads) > 1:
        raise ConfigShapeError("memory: appears more than once at top level")
    head = heads[0]
    rest = _TOP_KEY.match(lines[head])
    if rest is not None and _value(rest[2]):
        raise ConfigShapeError("memory: is not a block mapping")
    end = head + 1
    last = head + 1
    while end < len(lines):
        line = lines[end]
        if line.strip() and not line[:1].isspace() and not line.startswith("#"):
            break
        end += 1
        if line.strip() and not line.lstrip().startswith("#"):
            last = end
    return head, last


def _children(lines: list[str], span: tuple[int, int]) -> tuple[str, dict[str, int]]:
    indent = ""
    found: dict[str, int] = {}
    for index in range(span[0] + 1, span[1]):
        match = _CHILD.match(lines[index])
        if match is None:
            continue
        if not indent:
            indent = match[1]
        if match[1] != indent:
            continue
        if match[2] in found:
            raise ConfigShapeError(f"memory.{match[2]} appears twice")
        found[match[2]] = index
    return indent or "  ", found


def read_limits(text: str) -> dict[str, int | None]:
    """설정에 적힌 값(없으면 None — Hermes 기본값을 쓴다는 뜻)."""
    lines = text.splitlines()
    span = _section(lines)
    values: dict[str, int | None] = dict.fromkeys(LIMITS)
    if span is None:
        return values
    _, found = _children(lines, span)
    for key in LIMITS:
        if key in found:
            match = _CHILD.match(lines[found[key]])
            raw = _value(match[3]) if match else ""
            if not _INT.fullmatch(raw):
                raise ConfigShapeError(f"memory.{key} is not an integer")
            values[key] = int(raw)
    return values


def effective(values: Mapping[str, int | None]) -> dict[str, int]:
    return {key: HERMES_DEFAULTS[key] if values.get(key) is None else int(values[key] or 0) for key in LIMITS}


def identity(values: Mapping[str, int | None]) -> str:
    return ",".join(f"{key}={'-' if values.get(key) is None else values[key]}" for key in LIMITS)


def wanted_identity() -> str:
    return identity(LIMITS)


def digest(values: Mapping[str, int | None]) -> str:
    return hashlib.sha256(identity(values).encode()).hexdigest()[:16]


def apply_limits(text: str, wanted: Mapping[str, int] | None = None) -> str:
    """두 키만 바꾼 새 본문. 이미 같으면 입력을 그대로 돌려준다."""
    wanted = LIMITS if wanted is None else wanted
    if all(read_limits(text).get(key) == value for key, value in wanted.items()):
        return text
    lines = text.splitlines()
    span = _section(lines)
    if span is None:
        body = text if not text or text.endswith("\n") else text + "\n"
        block = [f"{SECTION}:", *(f"  {key}: {value}" for key, value in wanted.items())]
        return body + ("\n" if body.strip() else "") + "\n".join(block) + "\n"
    indent, found = _children(lines, span)
    missing: list[str] = []
    for key, value in wanted.items():
        if key in found:
            lines[found[key]] = f"{indent}{key}: {value}"
        else:
            missing.append(f"{indent}{key}: {value}")
    lines[span[1]:span[1]] = missing
    return "\n".join(lines) + "\n"


def lowering_blocked(values: Mapping[str, int | None], memories: Path,
                     wanted: Mapping[str, int] | None = None) -> list[str]:
    """상한을 내리는데 그 파일이 새 상한보다 긴 키들(#133028)."""
    wanted = LIMITS if wanted is None else wanted
    current = effective(values)
    blocked: list[str] = []
    for key, value in wanted.items():
        if value >= current[key]:
            continue
        try:
            size = len((memories / FILES[key]).read_text(encoding="utf-8").strip())
        except FileNotFoundError:
            continue
        if size > value:
            blocked.append(f"{FILES[key]} {size}>{value}")
    return blocked


def _write(path: Path, text: str) -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    backup = path.with_name(f"{path.name}.bak-memory-limits-{stamp}")
    shutil.copy2(path, backup)
    temporary = path.with_name(f".{path.name}.memory-limits")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(descriptor, text.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    return backup


def main(argv: Sequence[str] | None = None, *, home: Path | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m automation.hermes_memory_config")
    mode = parser.add_mutually_exclusive_group(required=True)
    _ = mode.add_argument("--show", action="store_true")
    _ = mode.add_argument("--check", action="store_true")
    _ = mode.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    root = (home or Path.home()) / ".hermes"
    config = root / "config.yaml"
    try:
        text = config.read_text(encoding="utf-8")
        values = read_limits(text)
    except (OSError, UnicodeDecodeError, ConfigShapeError) as error:
        print(f"{MARKER}-ERROR {config.name}: {error.__class__.__name__}: {str(error)[:120]}", file=sys.stderr)
        return 3
    if args.show:
        print(f"{MARKER} have={identity(values)}")
        return 0
    in_sync = all(values.get(key) == value for key, value in LIMITS.items())
    if args.check:
        print(f"{MARKER} {'ok' if in_sync else 'drift'} have={identity(values)} want={identity(LIMITS)}")
        return 0 if in_sync else 1
    if in_sync:
        print(f"{MARKER} unchanged have={identity(values)}")
        return 0
    blocked = lowering_blocked(values, root / "memories")
    if blocked:
        print(f"{MARKER}-REFUSED lowering below current size ({'; '.join(blocked)}) — "
              "shrink the file first (upstream hermes-agent#133028)", file=sys.stderr)
        return 7
    try:
        backup = _write(config, apply_limits(text))
        after = read_limits(config.read_text(encoding="utf-8"))
    except (OSError, ConfigShapeError) as error:
        print(f"{MARKER}-ERROR write: {error.__class__.__name__}: {str(error)[:120]}", file=sys.stderr)
        return 3
    if any(after.get(key) != value for key, value in LIMITS.items()):
        print(f"{MARKER}-ERROR read-back have={identity(after)}", file=sys.stderr)
        return 5
    print(f"{MARKER} applied have={identity(after)} backup={backup.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
