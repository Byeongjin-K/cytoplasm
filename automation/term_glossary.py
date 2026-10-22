"""문서 종류별로 **중첩된** 교정 참고 문서 — 어느 층을 어떤 순서로 읽는가.

    autophagy/용어집.csv                      모든 산출물에 걸리는 이름 (가장 약함)
    autophagy/<문서 종류>/용어집.csv          그 종류의 문서에만 (회의록·전사본·라이프로그 …)
    autophagy/<문서 종류>/<과제>/용어집.csv   그 과제의 그 문서에만 (가장 강함)

깊은 층이 이긴다. 한 번 적은 이름이 모든 문서에 걸리고, 안쪽 폴더는 **그 이름 하나만** 덮어쓴다
— 문서 종류마다 어휘가 다르기 때문이다(회의록은 기관명, 라이프로그는 사람·장소).

Drive 조회는 다른 모든 Drive 접촉과 같은 옵트인(`DRIVE_PUBLISH_ENABLED=1`)을 받고, 노드 캐시가
옵트아웃 경로를 받쳐 준다 — plaud 는 Drive 발행을 끄고 돌기 때문에 캐시가 없으면 한 낱말도
고치지 못한다. 캐시는 언제나 사본이지 정본이 아니다.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

from automation import term_correction

#: 앞이 정본이고 뒤는 이름을 바꾸기 전에 소유자가 이미 써 둔 파일이다.
FILES: Final = ("용어집.csv", "용어집.txt")
FILE_ENV: Final = "TERM_GLOSSARY_FILE"
CACHE_ENV: Final = "TERM_GLOSSARY_CACHE"
DEFAULT_CACHE: Final = "~/.hermes/term-glossary"
DIR_MODE: Final = 0o700
FILE_MODE: Final = 0o600
#: Drive 산출물이 아니어서 taxonomy 에 없는 문서 종류. 노트는 Obsidian 으로 가지만 교정
#: 참고 문서는 소유자가 한자리에서 관리해야 하므로 같은 나무에 자리를 준다.
EXTRA_FOLDERS: Final[Mapping[str, str]] = {"lifelog": "라이프로그"}
#: Kinds YooDori may add a row to from the owner's correction (owner 2026-10-22 Q359).
WRITABLE_KINDS: Final = ("lifelog", "meeting")
WORD_LIMIT: Final = 40


class TermGlossaryError(Exception):
    """모르는 문서 종류 — 폴더 이름을 지어내면 소유자가 못 찾는 자리에 참고 문서가 생긴다."""


def _taxonomy():
    from automation import drive_taxonomy  # noqa: PLC0415 - lazy: 카테고리 등록부는 저기가 정본

    return drive_taxonomy


def folder_for(kind: str) -> str:
    """그 문서 종류의 폴더 이름 — 카테고리 등록부가 정본이고 여기는 그 밖만 채운다."""
    taxonomy = _taxonomy()
    found = taxonomy.CATEGORIES.get(kind)
    if found is not None:
        return unicodedata.normalize("NFC", found.folder)
    extra = EXTRA_FOLDERS.get(kind)
    if extra is None:
        raise TermGlossaryError(f"unknown document kind: {kind!r}")
    return extra


def layers(kind: str, project: str = "") -> tuple[tuple[str, ...], ...]:
    """뿌리부터 안쪽까지, 바깥이 먼저 — 읽는 순서가 곧 우선순위다."""
    parts = [_taxonomy().outputs_root(), folder_for(kind)]
    named = unicodedata.normalize("NFC", project).strip()
    if named:
        if "/" in named or "\\" in named:
            raise TermGlossaryError(f"invalid project segment: {project!r}")
        parts.append(named)
    return tuple(tuple(parts[:depth]) for depth in range(1, len(parts) + 1))


def cache_path(kind: str, project: str = "", env: Mapping[str, str] | None = None) -> Path:
    """노드 캐시가 사는 자리 — 체크아웃 밖이고, 종류·과제마다 파일이 따로다."""
    source = os.environ if env is None else env
    root = Path(source.get(CACHE_ENV) or DEFAULT_CACHE).expanduser()
    named = unicodedata.normalize("NFC", project).strip()
    return root / (f"{kind}--{named}.csv" if named else f"{kind}.csv")


def _read_file(path: Path) -> tuple[tuple[str, str], ...]:
    try:
        return term_correction.parse_glossary(path.read_text(encoding="utf-8"))
    except OSError:
        return ()


def _write_cache(path: Path, pairs: Sequence[tuple[str, str]]) -> None:
    try:
        path.parent.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
        path.write_text(term_correction.format_glossary(pairs), encoding="utf-8")
        path.chmod(FILE_MODE)
    except OSError as failure:
        print(f"GLOSSARY-CACHE-FAIL {type(failure).__name__}", file=sys.stderr)


def _layer_text(drive: object, parts: tuple[str, ...]) -> str | None:
    """이 폴더에 놓인 참고 문서 — 폴더나 파일이 없으면 None.

    `find_folder_path` 이고 절대 `ensure_folder_path` 가 아니다: 참고 문서를 찾는 일이
    그 폴더를 만들어서는 안 되고, 중첩 조회는 그런 폴더를 세 번 들여다본다.
    """
    folder = drive.find_folder_path(parts)
    if folder is None:
        return None
    named = {
        str(child.get("name", "")): str(child.get("id", "")) for child in drive.list_children(folder)
    }
    for name in FILES:
        if name not in named:
            continue
        with tempfile.TemporaryDirectory(prefix="term-glossary-") as tmp:
            dest = Path(tmp) / name
            drive.download_file(named[name], dest)
            return dest.read_text(encoding="utf-8")
    return None


def _fetch(
    kind: str, project: str, *, client: object | None, env: Mapping[str, str]
) -> dict[str, str] | None:
    """층을 바깥부터 병합한다 — Drive 를 아예 보지 않았으면 None.

    한 층이 실패하면 답 전체를 None 으로 내린다: 부분 병합은 안쪽 덮어쓰기를 조용히 흘려
    바깥 값으로 이름을 고쳐 버린다.
    """
    if client is None and env.get("DRIVE_PUBLISH_ENABLED") != "1":
        return None
    merged: dict[str, str] = {}
    try:
        drive = client if client is not None else _client()
        for parts in layers(kind, project):
            text = _layer_text(drive, parts)
            if text is not None:
                merged.update(dict(term_correction.parse_glossary(text)))
    except TermGlossaryError:
        raise
    except Exception as failure:  # noqa: BLE001 - 참고 문서가 없다고 문서 생성이 멈추지 않는다
        print(f"GLOSSARY-FETCH-FAIL kind={kind} {type(failure).__name__}", file=sys.stderr)
        return None
    return merged


def _client():
    from automation import drive_outputs  # noqa: PLC0415 - lazy: Drive 접촉은 옵트인 뒤에만

    return drive_outputs.client_from_environment()


def glossary_for(
    kind: str,
    project: str = "",
    *,
    client: object | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[tuple[str, str], ...]:
    """그 문서를 만들 때 쓸 교정 참고 문서 — 명시 파일 > Drive 층 > 노드 캐시.

    Drive 가 아무것도 없다고 답한 것도 답이다. 그때는 캐시를 비운다 — 낡은 사본을 정본으로
    올리는 것이 은퇴한 이름이 돌아오는 방식이다.
    """
    source = os.environ if env is None else env
    explicit = source.get(FILE_ENV)
    if explicit:
        return _read_file(Path(explicit).expanduser())
    path = cache_path(kind, project, source)
    fetched = _fetch(kind, project, client=client, env=source)
    if fetched is None:
        return _read_file(path)
    if not fetched:
        print(f"GLOSSARY-DRIVE-ABSENT kind={kind}", file=sys.stderr)
        path.unlink(missing_ok=True)
        return ()
    pairs = tuple(fetched.items())
    _write_cache(path, pairs)
    return pairs


def _checked_word(word: str) -> str:
    cleaned = unicodedata.normalize("NFC", word).strip()
    if (not cleaned or len(cleaned) > WORD_LIMIT
            or any(char in cleaned for char in ",=\n\r\t\"#")):
        raise TermGlossaryError("a glossary word is one line of at most 40 characters without , = \" #")
    return cleaned


def add_pair(
    kind: str,
    wrong: str,
    right: str,
    *,
    project: str = "",
    client: object | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    """One owner correction into the kind's own layer (Drive when publishing is on) and the node cache.

    The owner's newest word wins: a wrong spelling already in the layer gets the new right
    spelling. Returns ``added``, ``updated`` or ``unchanged``.
    """
    if kind not in WRITABLE_KINDS:
        raise TermGlossaryError(f"glossary rows are added only for: {', '.join(WRITABLE_KINDS)}")
    wrong, right = _checked_word(wrong), _checked_word(right)
    if wrong == right:
        raise TermGlossaryError("the wrong and right spellings are the same")
    source = os.environ if env is None else env
    drive = client
    if drive is None and source.get("DRIVE_PUBLISH_ENABLED") == "1":
        drive = _client()
    parts = layers(kind, project)[-1]
    cache = cache_path(kind, project, source)
    if drive is not None:
        text = _layer_text(drive, parts)
        rows = list(term_correction.parse_glossary(text)) if text is not None else []
    else:
        rows = list(_read_file(cache))
    current = dict(rows)
    if current.get(wrong) == right:
        return "unchanged"
    action = "updated" if wrong in current else "added"
    rows = [(w, right if w == wrong else r) for w, r in rows] if wrong in current else [*rows, (wrong, right)]
    if drive is not None:
        from automation import drive_outputs  # noqa: PLC0415 - lazy: Drive 접촉은 옵트인 뒤에만

        with tempfile.TemporaryDirectory(prefix="term-glossary-") as tmp:
            local = Path(tmp) / FILES[0]
            local.write_text(term_correction.format_glossary(rows), encoding="utf-8")
            _ = drive_outputs.publish_state_file(parts, FILES[0], local, client=drive)
        merged = dict(_read_file(cache))
        merged[wrong] = right
        _write_cache(cache, tuple(merged.items()))
    else:
        _write_cache(cache, tuple(rows))
    return action


def main(argv: Sequence[str] | None = None) -> int:
    """`add <kind> <wrong> <right>` for YooDori's glossary-add; `list` for glossary-list."""
    parser = argparse.ArgumentParser(prog="term_glossary")
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add")
    add.add_argument("--kind", choices=WRITABLE_KINDS, default="lifelog")
    add.add_argument("wrong")
    add.add_argument("right")
    show = sub.add_parser("list")
    show.add_argument("--kind", choices=WRITABLE_KINDS, default="lifelog")
    args = parser.parse_args(argv)
    try:
        if args.command == "add":
            action = add_pair(args.kind, args.wrong, args.right)
            print(f"용어집({folder_for(args.kind)})에 {args.wrong} → {args.right} "
                  f"{ {'added': '추가함', 'updated': '고침', 'unchanged': '이미 있음'}[action] }")
            return 0
        pairs = [(w, r) for w, r in glossary_for(args.kind) if w != r]
        print(f"용어집({folder_for(args.kind)}) 교정 {len(pairs)}줄")
        for wrong, right in pairs:
            print(f"- {wrong} → {right}")
        return 0
    except TermGlossaryError as error:
        print(f"용어집 쓰기 거절: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
