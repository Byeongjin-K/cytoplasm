"""mirror와 Drive의 소유자 편집을 읽기만 한다. 원본·동결본은 수정하지 않는다."""
from __future__ import annotations

import json
import re
import tempfile
import unicodedata
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast

from automation.drive_client import DriveClient
from automation.drive_outputs import client_from_environment
from automation.drive_taxonomy import category_parts
from automation.entity_preflight.contracts import JsonValue
from automation.plaud_sync.store import load_note_body, load_state
# mirror 경로 해석의 단일 정의가 이 사설 함수뿐이라 사본을 만들지 않고 재사용한다.
from automation.rag_ingest.config import ConfigError, _parse_obsidian

from automation.stt_eval.cron.capture_text import Surface
from skills.speechtotext.scripts import stt_transcript

_FOLDER_MIME = "application/vnd.google-apps.folder"


class ReadDrive(Protocol):
    """폴더 생성·발행 API를 노출하지 않는 수집 경계."""
    def find_folder_path(self, parts: tuple[str, ...]) -> str | None: ...
    def list_children(self, folder_id: str) -> list[dict[str, JsonValue]]: ...
    def download_file(self, file_id: str, dest: Path, *, export_as: str = "") -> str: ...


@dataclass(frozen=True, slots=True)
class Document:
    surface: Surface
    stem: str
    current: str
    frozen: str | None
    stamp: str


def mirror_dir(home: Path) -> Path | None:
    """RAG와 같은 obsidian 설정 파서를 재사용하되 동기화·타 소스 로드는 하지 않는다."""
    path = home / ".hermes/rag-ingest/config.json"
    if not path.exists():
        return None
    raw = cast(object, json.loads(path.read_text(encoding="utf-8")))
    if not isinstance(raw, dict):
        raise ConfigError("config root must be an object")
    config = _parse_obsidian(cast(dict[str, object], raw))
    return config.mirror_dir if config is not None else None


def lifelog_documents(home: Path) -> Iterator[Document]:
    mirror = mirror_dir(home)
    if mirror is None or not mirror.is_dir():
        print("STT-EVAL-CAPTURE-SKIP reason=mirror-missing")
        return
    state_dir = home / ".hermes/plaud-sync"
    state = load_state(state_dir / "state.json")
    records = {unicodedata.normalize("NFC", record.note_relpath): record for record in state.records.values()}
    for path in sorted((mirror / "000_PARA/Area/Lifelog").rglob("*.md")):
        _ = path.resolve().relative_to(mirror.resolve())
        key = unicodedata.normalize("NFC", path.relative_to(mirror).as_posix())
        record = records.get(key)
        frozen = load_note_body(state_dir, record.recording_id) if record is not None else None
        yield Document("lifelog", unicodedata.normalize("NFC", path.stem), path.read_text(encoding="utf-8"), frozen,
                       datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat())


def default_drive() -> DriveClient:
    """래퍼가 secrets를 주입한 자식 환경에서 공용 DriveClient를 생성한다."""
    return client_from_environment()


def meeting_documents(home: Path, env: Mapping[str, str], drive: ReadDrive) -> Iterator[Document]:
    """pending_transcripts의 과제/연도 순회만 재사용한다. 회의록 유무는 필터가 아니다."""
    root = drive.find_folder_path(category_parts("transcript"))
    if root is None:
        return
    local_root = Path(env.get("SPEECHTOTEXT_TRANSCRIPT_DIR", str(home / ".hermes/speechtotext/transcripts"))).expanduser()
    for project in drive.list_children(root):
        if project.get("mimeType") != _FOLDER_MIME:
            continue
        for year in drive.list_children(str(project["id"])):
            if year.get("mimeType") != _FOLDER_MIME:
                continue
            for child in drive.list_children(str(year["id"])):
                name = unicodedata.normalize("NFC", str(child.get("name", "")))
                if not name.endswith(".md"):
                    continue
                if Path(name).name != name or "\\" in name:
                    raise ValueError("capture unsafe Drive filename")
                # 회의 원장의 정본 키는 Drive 워처가 전달한 오디오 label이다.
                # Drive는 title을 보존하므로 발행 날짜만 한 번 벗기고, 동결본은
                # CLI의 공용 파일명 규칙으로 찾는다. lifelog의 stem은 건드리지 않는다.
                stem = Path(name).stem
                dated = re.fullmatch(r"(\d{4}-\d{2}-\d{2})_(.+)", stem)
                local_name = name
                if dated is not None:
                    stem = dated[2]
                    local_name = stt_transcript.transcript_name(stem, datetime.fromisoformat(dated[1]))
                local = local_root / local_name
                _ = local.resolve().relative_to(local_root.resolve())
                frozen = local.read_text(encoding="utf-8") if local.exists() else None
                with tempfile.TemporaryDirectory(prefix="stt-eval-capture-") as temporary:
                    downloaded = Path(temporary) / "transcript.md"
                    _ = drive.download_file(str(child["id"]), downloaded)
                    current = downloaded.read_text(encoding="utf-8")
                stamp = str(child.get("modifiedTime") or datetime.now(UTC).isoformat())
                yield Document("meeting", stem, current, frozen, stamp)
