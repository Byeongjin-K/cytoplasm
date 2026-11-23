"""The staged manifest of a folder-tree Drive save: one job, one card (owner 2026-11-23).

On 2026-11-23 YooDori uploaded 76 survey files in several subfolders file by file through
the egress gate; past the per-message write limit every file and folder raised its own
card (about 40, retries included), only 21 files landed, and files under a folder still
waiting for approval failed with 404. The owner: "드라이브 업로드 하는거, 건별로 파일들 묶어서
승인 받도록 설정해줘". ``drive-save --manifest <staged json> --folder <parent id>`` reads the
whole job from one manifest, so the card lists every path once and one ✅ saves it all.

Manifest (UTF-8 JSON, at most 1 MiB)::

    {"version": 1, "files": [
        {"path": "현장측량/1일차/scan-01.las", "staged": "survey-001.las",
         "sha256": "<64 hex>", "size": 123456}, ...]}

``path`` is where the file lands below the parent folder (``/`` between folders, NFC);
``staged`` is a plain file name in the manifest's own folder (the gate's staging folder),
never a path; ``sha256``/``size`` are what the sender staged, and a file whose bytes differ
refuses the whole request before anything is stored.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .model import EffectRequestError, canonical, file_sha256

#: Files one tree card may carry; a bigger job is split into several requests.
MAX_TREE_FILES: Final = 500
#: Folders between the parent and a file at most.
MAX_TREE_DEPTH: Final = 8
MAX_MANIFEST_BYTES: Final = 1024 * 1024
_NAME_LIMIT: Final = 120
_SHA256: Final = re.compile(r"[0-9a-f]{64}")
_FORBIDDEN: Final = frozenset("\\")
_ENTRY_KEYS: Final = frozenset({"path", "staged", "sha256", "size"})


@dataclass(frozen=True, slots=True)
class TreeEntry:
    path: str
    source: Path
    sha256: str
    size: int

    @property
    def folder(self) -> str:
        return self.path.rpartition("/")[0]

    @property
    def name(self) -> str:
        return self.path.rpartition("/")[2]


def _component(raw: str, path: str) -> str:
    if (not raw or raw != raw.strip() or raw in {".", ".."} or raw.startswith(".")
            or len(raw) > _NAME_LIMIT or any(c in _FORBIDDEN for c in raw)
            or any(unicodedata.category(c) in {"Cc", "Cf", "Cs", "Co", "Zl", "Zp"} for c in raw)):
        raise EffectRequestError(f"매니페스트 경로가 올바르지 않다: {path[:200]}")
    stem = Path(raw).stem or raw
    if raw.startswith("-") or stem != stem.strip():
        # A separate overwrite card carries the name as --title: a leading "-" reads as an option
        # and edge spaces are trimmed, so such a name could not keep its tree name there.
        raise EffectRequestError(f"이름이 -로 시작하거나 확장자 앞에 앞뒤 공백이 있다: {path[:200]}")
    return raw


def tree_path(raw: object) -> str:
    """A relative Drive path below the parent: NFC, ``/`` between names, no dot or empty part."""
    if not isinstance(raw, str):
        raise EffectRequestError("매니페스트 path 는 문자열이다")
    path = unicodedata.normalize("NFC", raw)
    parts = path.split("/")
    if len(parts) > MAX_TREE_DEPTH + 1:
        raise EffectRequestError(f"폴더가 {MAX_TREE_DEPTH}단보다 깊다: {path[:200]}")
    return "/".join(_component(part, path) for part in parts)


def _staged(raw: object, root: Path) -> Path:
    if (not isinstance(raw, str) or not raw or "/" in raw or "\\" in raw or raw.startswith(".")
            or len(raw) > _NAME_LIMIT):
        raise EffectRequestError("매니페스트 staged 는 매니페스트와 같은 폴더의 파일 이름이다(경로 금지)")
    source = root / raw
    if source.is_symlink() or not source.is_file():
        raise EffectRequestError(f"매니페스트의 파일이 없다: {raw}")
    return source


def read_manifest(manifest: Path) -> tuple[TreeEntry, ...]:
    """Every listed file, sorted by path, its staged bytes checked against the declared sha256 and size."""
    try:
        if manifest.is_symlink() or manifest.stat().st_size > MAX_MANIFEST_BYTES:
            raise EffectRequestError("매니페스트는 1 MiB 이하의 일반 파일이다")
        body = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EffectRequestError(f"매니페스트를 읽을 수 없다: {type(error).__name__}") from error
    if not isinstance(body, dict) or set(body) - {"version", "files"} or body.get("version", 1) != 1:
        raise EffectRequestError("매니페스트는 {\"version\": 1, \"files\": [...]} 형식이다")
    rows = body.get("files")
    if not isinstance(rows, list) or not rows:
        raise EffectRequestError("매니페스트에 파일이 없다")
    if len(rows) > MAX_TREE_FILES:
        raise EffectRequestError(
            f"파일 {len(rows)}개 — 카드 한 장에 최대 {MAX_TREE_FILES}개, 나눠서 요청한다(카드는 올리지 않았다)")
    entries: dict[str, TreeEntry] = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != _ENTRY_KEYS:
            raise EffectRequestError("매니페스트 항목은 path·staged·sha256·size 네 개다")
        path = tree_path(row["path"])
        sha256, size = row["sha256"], row["size"]
        if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
            raise EffectRequestError(f"sha256 은 소문자 16진 64자다: {path}")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise EffectRequestError(f"size 는 0 이상의 정수다: {path}")
        if path in entries:
            raise EffectRequestError(f"같은 경로가 두 번 있다: {path}")
        source = _staged(row["staged"], manifest.parent)
        if source.suffix != Path(path).suffix:
            # A separate overwrite card names the file title + staged suffix, so both must agree.
            raise EffectRequestError(f"staged 파일의 확장자는 path 와 같아야 한다: {path}")
        entries[path] = TreeEntry(path, source, sha256, size)
    folders = folders_of(entries.values())
    clash = sorted(path for path in entries if path in folders)
    if clash:
        raise EffectRequestError(f"파일과 폴더 이름이 겹친다: {clash[0]}")
    ordered = tuple(entries[path] for path in sorted(entries))
    for entry in ordered:
        if file_sha256(entry.source) != (entry.sha256, entry.size):
            raise EffectRequestError(
                f"sha256 이 매니페스트와 다르다: {entry.path} — 아무것도 올리지 않았고 카드도 올리지 않았다")
    return ordered


def folders_of(entries: object) -> tuple[str, ...]:
    """Every folder the entries need, parents before children."""
    found: set[str] = set()
    for entry in entries:  # type: ignore[attr-defined]
        parts = entry.path.split("/")[:-1]
        found.update("/".join(parts[: depth + 1]) for depth in range(len(parts)))
    return tuple(sorted(found, key=lambda folder: (folder.count("/"), folder)))


def manifest_digest(entries: tuple[TreeEntry, ...]) -> str:
    """One sha256 over every (path, sha256, size): the card shows it, the request key uses it."""
    body = canonical([[entry.path, entry.sha256, entry.size] for entry in entries])
    return hashlib.sha256(body.encode("utf-8")).hexdigest()
