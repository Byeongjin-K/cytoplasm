"""파일 바이트 → 순서가 있는 단위별 본문. 실패는 예외가 아니라 상태로 돌아온다.

참고자료는 소유자가 모아 둔 남의 형식이다 — 스캔본 pdf, 낯선 확장자, 열리지 않는 zip 이
섞여 있고, 그 중 하나를 못 읽었다고 회의록 작성이 멈추면 안 된다. 그래서 이 함수는
`raise` 하지 않고 `Extracted.status` 로 사유를 돌려준다.

`skills/meeting/scripts/meeting_slides.py` 는 같은 계약을 발표자료(`Deck`) 용어로 이미
구현해 두었다. 그쪽은 마운트된 스킬이라 `automation` 을 import 하지 않는 자기완결 모듈이며,
둘의 수렴은 그 제약을 푸는 별도 작업이다(docs/follow-ups.md).
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from xml.etree import ElementTree

MAX_DOCUMENT_BYTES: Final = 25 * 1024 * 1024
PDF_TIMEOUT_SECONDS: Final = 120
OK: Final = "ok"
OCR_COMMAND_ENV: Final = "DOCUMENT_OCR_CMD"
OCR_TIMEOUT_ENV: Final = "DOCUMENT_OCR_TIMEOUT_SECONDS"
OCR_TIMEOUT_SECONDS: Final = 1800
OCR_SUFFIXES: Final = frozenset({".pdf", ".hwp", ".png", ".jpg", ".jpeg"})

SUPPORTED_SUFFIXES: Final = frozenset(
    {".md", ".markdown", ".txt", ".csv", ".pdf", ".pptx", ".docx", ".hwpx", ".xlsx"}
)
GOOGLE_EXPORTS: Final = {
    "application/vnd.google-apps.document": ("text/plain", ".txt"),
    "application/vnd.google-apps.presentation": ("text/plain", ".txt"),
    "application/vnd.google-apps.spreadsheet": ("text/csv", ".csv"),
}

_TEXT_SUFFIXES: Final = frozenset({".md", ".markdown", ".txt", ".csv"})
_SLIDE_ENTRY: Final = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
_HWPX_SECTION_ENTRY: Final = re.compile(r"^Contents/section(\d+)\.xml$")
_SHEET_ENTRY: Final = re.compile(r"^xl/worksheets/sheet(\d+)\.xml$")
_DOCTYPE: Final = re.compile(rb"<!(?:DOCTYPE|ENTITY)", re.IGNORECASE)
SUPPORTED_REASON: Final = "지원 형식은 pdf·pptx·docx·hwpx·xlsx·md·txt·csv 입니다"
HWP_REASON: Final = "구형 hwp 는 hwpx 나 pdf 로 저장해 주세요"
OCR_FAILED_REASON: Final = "글자 읽기(OCR)로도 본문을 얻지 못했습니다"


@dataclass(frozen=True, slots=True)
class Extracted:
    units: tuple[str, ...]
    status: str

    @property
    def text(self) -> str:
        return "\n\n".join(unit for unit in self.units if unit.strip())

    @property
    def sections(self) -> int:
        return sum(bool(unit.strip()) for unit in self.units)


def _refused(reason: str) -> Extracted:
    return Extracted(units=(), status=f"읽지 못함: {reason}")


def oversize_reason(max_bytes: int) -> str:
    return f"{max_bytes // (1024 * 1024)}MiB 를 넘습니다"


def _xml_root(payload: bytes) -> ElementTree.Element | None:
    if _DOCTYPE.search(payload):
        return None
    return ElementTree.fromstring(payload.decode("utf-8", errors="replace"))


def _xml_runs(payload: bytes, tag: str) -> list[str]:
    root = _xml_root(payload)
    if root is None:
        return []
    return [
        node.text.strip()
        for node in root.iter()
        if node.tag.endswith(f"}}{tag}") and node.text and node.text.strip()
    ]


def _from_pptx(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        entries = sorted(
            (int(match.group(1)), name)
            for name, match in ((name, _SLIDE_ENTRY.match(name)) for name in archive.namelist())
            if match is not None
        )
        return ["\n".join(_xml_runs(archive.read(name), "t")) for _, name in entries]


def _from_docx(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        if "word/document.xml" not in archive.namelist():
            return []
        return ["\n".join(_xml_runs(archive.read("word/document.xml"), "t"))]


def _from_hwpx(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        entries = sorted(
            (int(match.group(1)), name)
            for name, match in (
                (name, _HWPX_SECTION_ENTRY.match(name)) for name in archive.namelist()
            )
            if match is not None
        )
        return ["\n".join(_xml_runs(archive.read(name), "t")) for _, name in entries]


def _shared_strings(payload: bytes) -> list[str]:
    root = _xml_root(payload)
    if root is None:
        return []
    return [
        "".join(node.text or "" for node in item.iter() if node.tag.endswith("}t"))
        for item in root.iter()
        if item.tag.endswith("}si")
    ]


def _cell_value(cell: ElementTree.Element, shared: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(
            node.text or "" for node in cell.iter() if node.tag.endswith("}t")
        ).strip()
    value = next(
        (node.text or "" for node in cell.iter() if node.tag.endswith("}v")),
        "",
    ).strip()
    if cell_type != "s":
        return value
    try:
        index = int(value)
    except ValueError:
        return ""
    return shared[index].strip() if 0 <= index < len(shared) else ""


def _sheet_text(payload: bytes, shared: list[str]) -> str:
    root = _xml_root(payload)
    if root is None:
        return ""
    rows: list[str] = []
    for row in (node for node in root.iter() if node.tag.endswith("}row")):
        values = [
            value
            for cell in row
            if cell.tag.endswith("}c") and (value := _cell_value(cell, shared))
        ]
        rows.append(",".join(values))
    return "\n".join(rows)


def _from_xlsx(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        shared = (
            _shared_strings(archive.read("xl/sharedStrings.xml"))
            if "xl/sharedStrings.xml" in names
            else []
        )
        entries = sorted(
            (int(match.group(1)), name)
            for name, match in ((name, _SHEET_ENTRY.match(name)) for name in names)
            if match is not None
        )
        return [_sheet_text(archive.read(name), shared) for _, name in entries]


def _from_pdf(path: Path) -> list[str]:
    completed = subprocess.run(  # noqa: S603
        ["pdftotext", "-enc", "UTF-8", "-layout", str(path), "-"],
        capture_output=True,
        timeout=PDF_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        return []
    pages = completed.stdout.decode("utf-8", errors="replace").split("\x0c")
    if pages and not pages[-1].strip():
        _ = pages.pop()
    return [page.strip() for page in pages]


def ocr_command() -> tuple[str, ...]:
    """`DOCUMENT_OCR_CMD` 가 비어 있으면 글자 읽기는 꺼져 있다(기본).

    값은 `<명령> [인자...]` 이고 끝에 파일 경로를 붙여 부르며, 명령은 markdown 을 stdout 에 낸다.
    """
    configured = os.environ.get(OCR_COMMAND_ENV, "").strip()
    return tuple(shlex.split(configured)) if configured else ()


def readable_suffixes() -> frozenset[str]:
    return SUPPORTED_SUFFIXES | OCR_SUFFIXES if ocr_command() else SUPPORTED_SUFFIXES


def _ocr_timeout() -> int:
    configured = os.environ.get(OCR_TIMEOUT_ENV, "").strip()
    return int(configured) if configured.isdigit() and int(configured) > 0 else OCR_TIMEOUT_SECONDS


def _from_ocr(path: Path, command: tuple[str, ...]) -> list[str]:
    try:
        completed = subprocess.run(  # noqa: S603
            [*command, str(path)],
            capture_output=True,
            timeout=_ocr_timeout(),
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0:
        return []
    text = completed.stdout.decode("utf-8", errors="replace").strip()
    return [text] if text else []


def extract_document(path: Path, *, max_bytes: int = MAX_DOCUMENT_BYTES) -> Extracted:
    ocr = ocr_command()
    tried_ocr = False
    try:
        if not path.is_file():
            return _refused("파일을 찾을 수 없습니다")
        if path.stat().st_size > max_bytes:
            return _refused(oversize_reason(max_bytes))
        suffix = path.suffix.lower()
        if suffix in _TEXT_SUFFIXES:
            sections = [path.read_text(encoding="utf-8", errors="replace").strip()]
        elif suffix == ".pptx":
            sections = _from_pptx(path)
        elif suffix == ".docx":
            sections = _from_docx(path)
        elif suffix == ".hwpx":
            sections = _from_hwpx(path)
        elif suffix == ".xlsx":
            sections = _from_xlsx(path)
        elif suffix == ".pdf":
            try:
                sections = _from_pdf(path)
            except OSError:
                if not ocr:
                    raise
                sections = []
            if ocr and not any(section.strip() for section in sections):
                tried_ocr = True
                sections = _from_ocr(path, ocr)
        elif ocr and suffix in OCR_SUFFIXES:
            tried_ocr = True
            sections = _from_ocr(path, ocr)
        elif suffix == ".hwp":
            return _refused(HWP_REASON)
        else:
            return _refused(SUPPORTED_REASON)
    except (
        OSError,
        ValueError,
        zipfile.BadZipFile,
        ElementTree.ParseError,
        subprocess.SubprocessError,
    ):
        return _refused("파일을 여는 데 실패했습니다")
    if not any(section.strip() for section in sections):
        if tried_ocr:
            return _refused(OCR_FAILED_REASON)
        return _refused("텍스트 레이어가 없습니다(스캔본이거나 빈 자료)")
    return Extracted(units=tuple(sections), status=OK)
