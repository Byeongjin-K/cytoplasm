"""참고자료 본문 추출 — 남의 형식을 만나도 raise 하지 않고 사유를 돌려준다."""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

import automation.document_text as document_text
from automation.document_text import MAX_DOCUMENT_BYTES, OK, extract_document

REPO = Path(__file__).resolve().parents[2]
_FIXTURE_PDF = REPO / "skills" / "meeting" / "scripts" / "make_fixture_pdf.py"

_PPTX_SLIDE = (
    '<?xml version="1.0"?>'
    '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
    ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
    "<p:cSld><p:spTree>{body}</p:spTree></p:cSld></p:sld>"
)
_DOCX_BODY = (
    '<?xml version="1.0"?>'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<w:body>{body}</w:body></w:document>"
)
_HWPX_SECTION = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<hs:sec xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph"'
    ' xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section">{body}</hs:sec>'
)
_SHARED_STRINGS = (
    '<?xml version="1.0"?>'
    '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    "{items}</sst>"
)
_SHEET = (
    '<?xml version="1.0"?>'
    '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    "<sheetData>{rows}</sheetData></worksheet>"
)


def _pptx(path: Path, slides: list[list[str]]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        for index, runs in enumerate(slides, start=1):
            body = "".join(f"<a:t>{run}</a:t>" for run in runs)
            archive.writestr(f"ppt/slides/slide{index}.xml", _PPTX_SLIDE.format(body=body))
    return path


def _docx(path: Path, runs: list[str]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        body = "".join(f"<w:p><w:r><w:t>{run}</w:t></w:r></w:p>" for run in runs)
        archive.writestr("word/document.xml", _DOCX_BODY.format(body=body))
    return path


def _hwpx(path: Path, sections: dict[int, list[str]]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for index, texts in sections.items():
            body = "".join(f"<hp:p><hp:run><hp:t>{text}</hp:t></hp:run></hp:p>" for text in texts)
            archive.writestr(
                f"Contents/section{index}.xml",
                _HWPX_SECTION.format(body=body),
            )
    return path


def _xlsx(path: Path, shared: list[list[str]], sheets: list[str]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        items = "".join(
            f"<si>{''.join(f'<r><t>{run}</t></r>' for run in runs)}</si>"
            for runs in shared
        )
        archive.writestr("xl/sharedStrings.xml", _SHARED_STRINGS.format(items=items))
        for index, rows in enumerate(sheets, start=1):
            archive.writestr(
                f"xl/worksheets/sheet{index}.xml",
                _SHEET.format(rows=rows),
            )
    return path


def test_markdown_reference_reads_as_one_section(tmp_path: Path) -> None:
    path = tmp_path / "기준.md"
    path.write_text("굴착 오차는 10 mm 이하.", encoding="utf-8")

    extracted = extract_document(path)

    assert extracted.status == OK
    assert extracted.sections == 1
    assert "10 mm" in extracted.text


def test_pptx_reference_reads_every_slide(tmp_path: Path) -> None:
    path = _pptx(tmp_path / "발표.pptx", [["과제 개요"], ["센서 캘리브레이션"]])

    extracted = extract_document(path)

    assert extracted.status == OK
    assert extracted.sections == 2
    assert "센서 캘리브레이션" in extracted.text


def test_units_keep_an_empty_pptx_slide(tmp_path: Path) -> None:
    path = _pptx(tmp_path / "빈슬라이드.pptx", [["처음"], [], ["끝"]])

    extracted = extract_document(path)

    assert extracted.units == ("처음", "", "끝")
    assert extracted.sections == 2
    assert extracted.text == "처음\n\n끝"


def test_docx_reference_reads_the_document_body(tmp_path: Path) -> None:
    path = _docx(tmp_path / "보고서.docx", ["굴착 오차 관리", "10 mm 이하"])

    extracted = extract_document(path)

    assert extracted.status == OK
    assert "굴착 오차 관리" in extracted.text
    assert "10 mm 이하" in extracted.text


@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="pdftotext 없음")
def test_pdf_reference_reads_its_text_layer(tmp_path: Path) -> None:
    path = tmp_path / "보고서.pdf"
    subprocess.run([sys.executable, str(_FIXTURE_PDF), str(path), "--text"], check=True)

    extracted = extract_document(path)

    assert extracted.status == OK
    assert extracted.text.strip()


def test_hwpx_reads_each_section(tmp_path: Path) -> None:
    path = _hwpx(tmp_path / "설계.hwpx", {0: ["굴착 계획"], 1: ["10 mm 이하"]})

    extracted = extract_document(path)

    assert extracted.status == OK
    assert extracted.units == ("굴착 계획", "10 mm 이하")
    assert extracted.sections == 2


def test_hwpx_sections_are_sorted_numerically(tmp_path: Path) -> None:
    path = _hwpx(tmp_path / "순서.hwpx", {10: ["열 번째"], 2: ["두 번째"]})

    extracted = extract_document(path)

    assert extracted.units == ("두 번째", "열 번째")


def test_hwpx_without_text_is_refused(tmp_path: Path) -> None:
    path = _hwpx(tmp_path / "빈문서.hwpx", {0: [], 1: []})

    extracted = extract_document(path)

    assert extracted.status == "읽지 못함: 텍스트 레이어가 없습니다(스캔본이거나 빈 자료)"


def test_xlsx_joins_shared_text_and_number_in_one_row(tmp_path: Path) -> None:
    row = '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1"><v>10</v></c></row>'
    path = _xlsx(tmp_path / "수치.xlsx", [["굴착 오차"]], [row])

    extracted = extract_document(path)

    assert extracted.status == OK
    assert "굴착 오차,10" in extracted.text


def test_xlsx_keeps_each_sheet_as_a_unit(tmp_path: Path) -> None:
    sheets = [
        '<row r="1"><c r="A1" t="inlineStr"><is><t>첫 시트</t></is></c></row>',
        '<row r="1"><c r="A1"><v>20</v></c></row>',
    ]
    path = _xlsx(tmp_path / "두시트.xlsx", [], sheets)

    extracted = extract_document(path)

    assert extracted.units == ("첫 시트", "20")
    assert extracted.sections == 2


def test_xlsx_joins_rich_text_runs_in_one_shared_string(tmp_path: Path) -> None:
    row = '<row r="1"><c r="A1" t="s"><v>0</v></c></row>'
    path = _xlsx(tmp_path / "서식.xlsx", [["굴착 ", "오차"]], [row])

    extracted = extract_document(path)

    assert extracted.units == ("굴착 오차",)


def test_xlsx_skips_a_malformed_shared_string_index(tmp_path: Path) -> None:
    row = (
        '<row r="1"><c r="A1" t="s"><v>bad</v></c>'
        '<c r="B1" t="inlineStr"><is><t>유효</t></is></c></row>'
    )
    path = _xlsx(tmp_path / "잘못된인덱스.xlsx", [["미사용"]], [row])

    extracted = extract_document(path)

    assert extracted.status == OK
    assert extracted.units == ("유효",)


def test_old_hwp_has_an_actionable_refusal(tmp_path: Path) -> None:
    path = tmp_path / "설계도.hwp"
    path.write_bytes(b"\x00\x01\x02")

    extracted = extract_document(path)

    assert extracted.status == "읽지 못함: 구형 hwp 는 hwpx 나 pdf 로 저장해 주세요"


def test_unsupported_format_names_what_is_supported(tmp_path: Path) -> None:
    path = tmp_path / "발표.key"
    path.write_bytes(b"\x00\x01\x02")

    extracted = extract_document(path)

    assert extracted.status == (
        "읽지 못함: 지원 형식은 pdf·pptx·docx·hwpx·xlsx·md·txt·csv 입니다"
    )
    assert extracted.text == ""


def test_published_document_formats_are_exact() -> None:
    assert document_text.SUPPORTED_SUFFIXES == frozenset(
        {".md", ".markdown", ".txt", ".csv", ".pdf", ".pptx", ".docx", ".hwpx", ".xlsx"}
    )
    assert document_text.GOOGLE_EXPORTS == {
        "application/vnd.google-apps.document": ("text/plain", ".txt"),
        "application/vnd.google-apps.presentation": ("text/plain", ".txt"),
        "application/vnd.google-apps.spreadsheet": ("text/csv", ".csv"),
    }


def test_corrupt_archive_is_reported_not_raised(tmp_path: Path) -> None:
    path = tmp_path / "깨진.pptx"
    path.write_bytes(b"not a zip at all")

    extracted = extract_document(path)

    assert extracted.status == "읽지 못함: 파일을 여는 데 실패했습니다"


def test_missing_file_is_reported_not_raised(tmp_path: Path) -> None:
    assert extract_document(tmp_path / "없다.md").status == "읽지 못함: 파일을 찾을 수 없습니다"


def test_oversized_file_is_refused_before_reading(tmp_path: Path) -> None:
    path = tmp_path / "큰파일.txt"
    path.write_bytes(b"x" * 64)

    extracted = extract_document(path, max_bytes=32)

    assert extracted.status.startswith("읽지 못함:")
    assert extracted.text == ""


def test_scanned_pdf_without_a_text_layer_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "스캔본.txt"
    path.write_text("   \n  \n", encoding="utf-8")

    extracted = extract_document(path)

    assert extracted.status == "읽지 못함: 텍스트 레이어가 없습니다(스캔본이거나 빈 자료)"


def test_default_size_ceiling_matches_the_meeting_input_ceiling() -> None:
    assert MAX_DOCUMENT_BYTES == 25 * 1024 * 1024


def _fake_ocr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, exit_code: int = 0) -> Path:
    calls = tmp_path / "ocr-calls.txt"
    script = tmp_path / "fake_ocr.py"
    script.write_text(
        "import pathlib, sys\n"
        f"pathlib.Path({str(calls)!r}).open('a').write(sys.argv[1] + '\\n')\n"
        "print('| 항목 | 값 |\\n|---|---|\\n| 굴착 오차 | 10 mm |')\n"
        f"sys.exit({exit_code})\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(document_text.OCR_COMMAND_ENV, f"{sys.executable} {script}")
    return calls


def test_ocr_is_off_without_a_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(document_text.OCR_COMMAND_ENV, raising=False)
    path = tmp_path / "설계도.hwp"
    path.write_bytes(b"\x00\x01\x02")

    assert document_text.readable_suffixes() == document_text.SUPPORTED_SUFFIXES
    assert extract_document(path).status == "읽지 못함: 구형 hwp 는 hwpx 나 pdf 로 저장해 주세요"


def test_pdf_without_a_text_layer_is_read_by_ocr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake_ocr(tmp_path, monkeypatch)
    path = tmp_path / "스캔본.pdf"
    path.write_bytes(b"%PDF-1.4\n%%EOF\n")

    extracted = extract_document(path)

    assert extracted.status == OK
    assert "| 굴착 오차 | 10 mm |" in extracted.text
    assert calls.read_text(encoding="utf-8").splitlines() == [str(path)]


@pytest.mark.parametrize("name", ["설계도.hwp", "현장사진.png", "도면.JPG"])
def test_hwp_and_images_go_to_ocr_when_it_is_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    _ = _fake_ocr(tmp_path, monkeypatch)
    path = tmp_path / name
    path.write_bytes(b"\x00\x01\x02")

    extracted = extract_document(path)

    assert extracted.status == OK
    assert "10 mm" in extracted.text


@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="pdftotext 없음")
def test_pdf_with_a_text_layer_does_not_call_ocr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake_ocr(tmp_path, monkeypatch)
    path = tmp_path / "보고서.pdf"
    subprocess.run([sys.executable, str(_FIXTURE_PDF), str(path), "--text"], check=True)

    extracted = extract_document(path)

    assert extracted.status == OK
    assert not calls.exists()


def test_failed_ocr_is_reported_not_raised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _ = _fake_ocr(tmp_path, monkeypatch, exit_code=4)
    path = tmp_path / "설계도.hwp"
    path.write_bytes(b"\x00\x01\x02")

    extracted = extract_document(path)

    assert extracted.status == "읽지 못함: 글자 읽기(OCR)로도 본문을 얻지 못했습니다"
    assert extracted.text == ""


def test_missing_ocr_command_is_reported_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(document_text.OCR_COMMAND_ENV, str(tmp_path / "없는-명령"))
    path = tmp_path / "현장사진.png"
    path.write_bytes(b"\x00\x01\x02")

    assert extract_document(path).status == "읽지 못함: 글자 읽기(OCR)로도 본문을 얻지 못했습니다"
