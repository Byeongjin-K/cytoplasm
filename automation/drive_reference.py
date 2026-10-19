"""소유자의 참고자료 폴더(기본 `내 드라이브/KIMM`)에서 근거를 찾는 읽기 전용 조회.

회의록을 쓸 때 용어와 사실을 바로잡을 근거는 소유자가 모아 둔 자료 안에 있다. 이 모듈은
그 폴더를 뒤져 질의에 맞는 문서와 그 문서의 근거 구절을 돌려준다.

계약 넷이 이 모듈의 존재 이유다.

- **읽기 전용**: 이 트리는 우리 산출물 루트가 아니라 소유자의 보관함이다. 폴더를 만들지
  않고(`find_folder_path`, `ensure_folder_path` 금지), 공유 폴더 캐시도 건드리지 않는다.
- **fail-closed**: 옵트인(`DRIVE_PUBLISH_ENABLED=1`)이 없거나 루트를 못 찾으면 아무것도
  하지 않고 사유를 돌려준다. 추측해서 다른 폴더를 뒤지지 않는다.
- **fail-soft**: 조회 실패는 예외가 아니라 상태다. 참고자료를 못 읽었다고 회의록 작성이
  멈추면 안 된다 — 그때는 근거 없이 진행할 뿐이다.
- **내려받기 전에 거른다**: 형식과 크기는 메타데이터만으로 판정된다. 실측 폴더 64건 중
  17건이 25MiB 를 넘었고 그중 하나는 6.8GiB 였다 — 받아 본 뒤에 못 읽는다고 말하면 대역폭도
  자리도 잃는다. 그 판정은 `reference_rank.refusal` 이 단독으로 소유한다.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from automation import reference_rank
from automation.document_text import GOOGLE_EXPORTS, Extracted, extract_document
from automation.interop.approval_types import JsonValue

ROOT_ENV: Final = "DRIVE_REFERENCE_ROOT"
DEFAULT_ROOT: Final = "KIMM"
ENABLE_ENV: Final = "DRIVE_PUBLISH_ENABLED"

OK: Final = "ok"
DISABLED: Final = "REFERENCE-DISABLED"
ROOT_MISSING: Final = "REFERENCE-ROOT-MISSING"
FAILED: Final = "REFERENCE-FAIL"

MAX_DEPTH: Final = 4
MAX_FOLDERS: Final = 60
MAX_FILES: Final = 400
MAX_FETCH: Final = 3
MAX_REFERENCE_BYTES: Final = 64 * 1024 * 1024

_FOLDER_MIME: Final = "application/vnd.google-apps.folder"


class ReferenceDrive(Protocol):
    def find_folder_path(self, parts: tuple[str, ...]) -> str | None: ...
    def list_children(self, folder_id: str) -> list[dict[str, JsonValue]]: ...
    def download_file(self, file_id: str, dest: Path, *, export_as: str = "") -> str: ...


@dataclass(frozen=True, slots=True)
class ReferenceFile:
    file_id: str
    name: str
    path: str
    mime_type: str
    modified: str
    size: int = 0


@dataclass(frozen=True, slots=True)
class ReferenceHit:
    name: str
    path: str
    file_id: str
    link: str
    snippet: str
    score: int
    status: str


@dataclass(frozen=True, slots=True)
class ReferenceDocument:
    file: ReferenceFile
    text: str
    status: str
    sections: int
    score: int
    coverage: int = 0


@dataclass(frozen=True, slots=True)
class ReferenceScan:
    status: str
    root: str
    scanned: int
    documents: tuple[ReferenceDocument, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReferenceResult:
    status: str
    root: str
    scanned: int
    hits: tuple[ReferenceHit, ...] = ()
    notes: tuple[str, ...] = ()


def root_parts(env: Mapping[str, str]) -> tuple[str, ...]:
    configured = (env.get(ROOT_ENV) or DEFAULT_ROOT).strip()
    return tuple(
        reference_rank.nfc(part.strip()) for part in configured.split("/") if part.strip()
    )


def enabled(env: Mapping[str, str]) -> bool:
    return env.get(ENABLE_ENV) == "1"


def _default_client(env: Mapping[str, str]) -> ReferenceDrive:
    from automation.drive_outputs import client_from_environment

    return client_from_environment()


def _size_of(child: Mapping[str, JsonValue]) -> int:
    raw = str(child.get("size", "") or "")
    return int(raw) if raw.isdigit() else 0


def walk(
    drive: ReferenceDrive,
    folder_id: str,
    root: str,
    *,
    max_depth: int = MAX_DEPTH,
    max_folders: int = MAX_FOLDERS,
    max_files: int = MAX_FILES,
) -> tuple[tuple[ReferenceFile, ...], tuple[str, ...]]:
    found: list[ReferenceFile] = []
    notes: list[str] = []
    queue: list[tuple[str, str, int]] = [(folder_id, root, 1)]
    visited = 0
    while queue:
        current, path, depth = queue.pop(0)
        visited += 1
        if visited > max_folders:
            notes.append(f"폴더 {max_folders}개까지만 훑었습니다")
            break
        for child in sorted(drive.list_children(current), key=lambda row: str(row.get("name", ""))):
            name = reference_rank.nfc(str(child.get("name", "")))
            child_id = str(child.get("id", ""))
            if not name or not child_id:
                continue
            if str(child.get("mimeType", "")) == _FOLDER_MIME:
                if depth < max_depth:
                    queue.append((child_id, f"{path}/{name}", depth + 1))
                else:
                    notes.append(f"{max_depth}단계보다 깊은 폴더는 보지 않았습니다")
                continue
            if len(found) >= max_files:
                notes.append(f"파일 {max_files}개까지만 훑었습니다")
                return tuple(found), tuple(dict.fromkeys(notes))
            found.append(
                ReferenceFile(
                    file_id=child_id,
                    name=name,
                    path=f"{path}/{name}",
                    mime_type=str(child.get("mimeType", "")),
                    modified=str(child.get("modifiedTime", "")),
                    size=_size_of(child),
                )
            )
    return tuple(found), tuple(dict.fromkeys(notes))


def _download(drive: ReferenceDrive, item: ReferenceFile, work: Path) -> Extracted:
    export = GOOGLE_EXPORTS.get(item.mime_type)
    suffix = export[1] if export is not None else Path(item.name).suffix.lower()
    local = work / f"{item.file_id}{suffix}"
    drive.download_file(item.file_id, local, export_as=export[0] if export is not None else "")
    return extract_document(local, max_bytes=MAX_REFERENCE_BYTES)


def _read(drive: ReferenceDrive, item: ReferenceFile, work: Path) -> Extracted:
    refused = reference_rank.refusal(item, MAX_REFERENCE_BYTES)
    if refused:
        return Extracted(units=(), status=f"읽지 못함: {refused}")
    try:
        return _download(drive, item, work)
    except Exception as failure:  # noqa: BLE001 - 한 파일이 조회 전체를 무너뜨리면 안 된다
        return Extracted(units=(), status=f"읽지 못함: {type(failure).__name__}")


def _document(item: ReferenceFile, read: Extracted, wanted: Sequence[str]) -> ReferenceDocument:
    return ReferenceDocument(
        file=item,
        text=read.text,
        status=read.status,
        sections=read.sections,
        score=reference_rank.name_score(item.path, wanted)
        + reference_rank.text_score(read.text, wanted),
        coverage=reference_rank.coverage(f"{item.path}\n{read.text}", wanted),
    )


def _hit(document: ReferenceDocument, wanted: Sequence[str]) -> ReferenceHit:
    return ReferenceHit(
        name=document.file.name,
        path=document.file.path,
        file_id=document.file.file_id,
        link=reference_rank.link_for(document.file.file_id),
        snippet=reference_rank.snippet(document.text, wanted) if document.status == OK else "",
        score=document.score,
        status=document.status,
    )


def _refused(status: str, root: str, note: str) -> ReferenceScan:
    return ReferenceScan(status=status, root=root, scanned=0, notes=(note,))


def collect(
    query: str,
    *,
    client: ReferenceDrive | None = None,
    env: Mapping[str, str] | None = None,
    limit: int = MAX_FETCH,
) -> ReferenceScan:
    environment = os.environ if env is None else env
    parts = root_parts(environment)
    root = "/".join(parts)
    if client is None and not enabled(environment):
        return _refused(DISABLED, root, f"Drive 조회 옵트인이 꺼져 있습니다({ENABLE_ENV}=1 필요)")
    wanted = reference_rank.terms(query)
    if not wanted:
        return _refused(OK, root, "찾을 낱말이 없습니다")
    try:
        drive = client if client is not None else _default_client(environment)
        folder = drive.find_folder_path(parts)
        if folder is None:
            return _refused(ROOT_MISSING, root, f"참고자료 폴더를 찾지 못했습니다: {root}")
        files, notes = walk(drive, folder, root)
        ranked = sorted(
            files,
            key=lambda item: reference_rank.fetch_key(item, wanted, MAX_REFERENCE_BYTES),
        )
        found: list[ReferenceDocument] = []
        with tempfile.TemporaryDirectory(prefix="drive-reference-") as work:
            for item in ranked[: max(0, limit)]:
                found.append(_document(item, _read(drive, item, Path(work)), wanted))
    except Exception as failure:  # noqa: BLE001 - 참고자료 실패가 회의록을 멈추면 안 된다
        return _refused(f"{FAILED}: {type(failure).__name__}", root, "참고자료 조회에 실패했습니다")
    found.sort(key=lambda document: (-document.coverage, -document.score, document.file.path))
    return ReferenceScan(
        status=OK,
        root=root,
        scanned=len(files),
        documents=tuple(item for item in found if item.score > 0 or item.status != OK),
        notes=notes,
    )


def search(
    query: str,
    *,
    client: ReferenceDrive | None = None,
    env: Mapping[str, str] | None = None,
    limit: int = MAX_FETCH,
) -> ReferenceResult:
    scan = collect(query, client=client, env=env, limit=limit)
    wanted = reference_rank.terms(query)
    return ReferenceResult(
        status=scan.status,
        root=scan.root,
        scanned=scan.scanned,
        hits=tuple(_hit(document, wanted) for document in scan.documents),
        notes=scan.notes,
    )
