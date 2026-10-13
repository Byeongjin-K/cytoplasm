"""Finalized meeting minutes on Drive as analysis input — read only.

The canonical minutes live in the Drive outputs tree (회의록/<과제>/<연도>/*.md, written by the
meeting skill). This module lists them without creating any folder, downloads one into a
temporary directory, and keeps only the finalized view (the part above the appendix, so the
transcript is never analysed). That view is cached under the board root so the dashboard can
open the cited minutes without calling Drive again.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final, Protocol

from .models import ProjectBoardError
from .sources import SourceDocument, read_meeting

FOLDER_MIME: Final = "application/vnd.google-apps.folder"
DRIVE_PREFIX: Final = "gd-"
_DATE: Final = re.compile(r"\d{4}-\d{2}-\d{2}")
_ID: Final = re.compile(r"^[A-Za-z0-9_-]{8,}$")
_YEAR: Final = re.compile(r"^\d{4}$")


class Drive(Protocol):
    def find_folder_path(self, parts: tuple[str, ...]) -> str | None: ...
    def list_children(self, folder_id: str) -> list[dict[str, object]]: ...
    def download_file(self, file_id: str, dest: Path, *, export_as: str = "") -> str: ...


@dataclass(frozen=True, slots=True)
class MinutesFile:
    file_id: str
    name: str
    project: str
    year: str
    modified: str

    @property
    def source_id(self) -> str:
        return f"{DRIVE_PREFIX}{self.file_id}"


def _nfc(value: object) -> str:
    return unicodedata.normalize("NFC", str(value or ""))


def _folders(children: list[dict[str, object]]) -> list[dict[str, object]]:
    return [child for child in children if child.get("mimeType") == FOLDER_MIME]


def scan(drive: Drive, parts: tuple[str, ...]) -> tuple[MinutesFile, ...]:
    """Every minutes file under ``parts`` (the meeting category), oldest first. Never creates a folder."""
    root = drive.find_folder_path(parts)
    if root is None:
        return ()
    found: list[MinutesFile] = []
    years: list[tuple[str, dict[str, object]]] = []
    for folder in _folders(drive.list_children(root)):
        if _YEAR.match(_nfc(folder.get("name"))):
            years.append(("", folder))
            continue
        years += [(_nfc(folder.get("name")), year) for year in _folders(drive.list_children(str(folder.get("id", ""))))]
    for project_name, year in years:
        for child in drive.list_children(str(year.get("id", ""))):
            name = _nfc(child.get("name"))
            file_id = str(child.get("id", ""))
            if child.get("mimeType") == FOLDER_MIME or not name.endswith(".md") or not _ID.match(file_id):
                continue
            found.append(MinutesFile(
                file_id, name, project_name, _nfc(year.get("name")),
                str(child.get("modifiedTime") or child.get("createdTime") or ""),
            ))
    return tuple(sorted(found, key=lambda f: (f.modified, f.name)))


def cache_path(cache: Path, source_id: str) -> Path | None:
    if not source_id.startswith(DRIVE_PREFIX) or not _ID.match(source_id[len(DRIVE_PREFIX):]):
        return None
    return cache / f"{source_id}.md"


def fetch(drive: Drive, minutes: MinutesFile, cache: Path) -> SourceDocument:
    """Download one minutes file, keep only its finalized view, and cache that view (0600)."""
    with tempfile.TemporaryDirectory(prefix="pb-minutes-") as tmp:
        local = Path(tmp) / "minutes.md"
        drive.download_file(minutes.file_id, local)
        document = read_meeting(local)
    stamp = _DATE.search(minutes.name)
    ref = replace(
        document.ref, source_id=minutes.source_id,
        subject=document.ref.subject if document.ref.subject != "minutes" else minutes.name[:-3],
        date=stamp.group(0) if stamp else minutes.modified[:10],
    )
    target = cache_path(cache, minutes.source_id)
    if target is None:
        raise ProjectBoardError(f"unexpected Drive file id: {minutes.file_id}")
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(document.text)
    return SourceDocument(ref, document.text)


def read_seen(path: Path) -> frozenset[str] | None:
    """File ids already looked at, or ``None`` before the first tick (which only records them)."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        raise ProjectBoardError(f"minutes watermark unreadable: {path.name}") from error
    seen = raw.get("seen") if isinstance(raw, dict) else None
    if not isinstance(seen, list):
        raise ProjectBoardError(f"minutes watermark malformed: {path.name}")
    return frozenset(str(item) for item in seen)


def write_seen(path: Path, seen: frozenset[str]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump({"seen": sorted(seen)}, handle)
    temporary.replace(path)
