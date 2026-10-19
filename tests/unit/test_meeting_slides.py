"""발표자료(`--slides`) 추출과 대명사 교정 재료 주입 — 소유자 지시 2026-08-26."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "skills" / "meeting"
FIXTURES = SKILL / "fixtures"
sys.path.insert(0, str(SKILL / "scripts"))

import meeting_cli  # noqa: E402
import meeting_llm  # noqa: E402
import meeting_slides  # noqa: E402

_SLIDE_XML = (
    '<?xml version="1.0"?>'
    '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
    ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
    "<p:cSld><p:spTree>{body}</p:spTree></p:cSld></p:sld>"
)


def _pptx(path: Path, slides: list[list[str]]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        for index, runs in enumerate(slides, start=1):
            body = "".join(f"<a:t>{run}</a:t>" for run in runs)
            archive.writestr(f"ppt/slides/slide{index}.xml", _SLIDE_XML.format(body=body))
    return path


def _text_pdf(path: Path) -> Path:
    subprocess.run(
        [sys.executable, str(SKILL / "scripts" / "make_fixture_pdf.py"), str(path), "--text"],
        check=True,
    )
    return path


# --- 추출 -------------------------------------------------------------------


def test_pptx_deck_becomes_slide_numbered_text():
    import tempfile

    with tempfile.TemporaryDirectory() as work:
        path = _pptx(
            Path(work) / "kickoff.pptx",
            [["과제 개요", "AUTOPHAGY-2026"], ["센서 캘리브레이션"], ["일정"]],
        )
        deck = meeting_slides.extract_deck(path)
    assert deck.status == "ok"
    assert deck.slide_count == 3
    assert "[슬라이드 1] 과제 개요" in deck.text
    assert "AUTOPHAGY-2026" in deck.text
    assert "[슬라이드 3] 일정" in deck.text


def test_pptx_slides_are_ordered_numerically_not_lexically(tmp_path):
    path = _pptx(tmp_path / "d.pptx", [[f"장 {n}"] for n in range(1, 12)])
    deck = meeting_slides.extract_deck(path)
    order = [line.split("]")[0] for line in deck.text.splitlines() if line.startswith("[슬라이드")]
    assert order[:3] == ["[슬라이드 1", "[슬라이드 2", "[슬라이드 3"]
    assert order[-1] == "[슬라이드 11", "slide10.xml 이 slide2.xml 앞으로 정렬됐다"


@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="poppler absent")
def test_pdf_deck_splits_on_page_breaks(tmp_path):
    deck = meeting_slides.extract_deck(_text_pdf(tmp_path / "deck.pdf"))
    assert deck.status == "ok"
    assert deck.slide_count >= 1
    assert "[슬라이드 1]" in deck.text
    assert "dataset dictionary" in deck.text


def test_markdown_and_text_decks_pass_through(tmp_path):
    path = tmp_path / "notes.md"
    path.write_text("# 과제 개요\n실증 사이트는 A동이다", encoding="utf-8")
    deck = meeting_slides.extract_deck(path)
    assert deck.status == "ok" and "실증 사이트는 A동이다" in deck.text


# --- fail-soft: 발표자료가 죽어도 회의록은 만들어진다 --------------------------


def test_missing_deck_is_reported_not_raised(tmp_path):
    deck = meeting_slides.extract_deck(tmp_path / "nope.pptx")
    assert deck.status != "ok" and deck.text == ""
    assert meeting_slides.note_label(deck).startswith("nope.pptx — 읽지 못함")


def test_unsupported_suffix_is_reported_not_raised(tmp_path):
    path = tmp_path / "deck.key"
    path.write_bytes(b"\x00\x01")
    deck = meeting_slides.extract_deck(path)
    assert deck.status != "ok" and deck.text == ""


@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="poppler absent")
def test_scanned_pdf_deck_is_reported_not_raised(tmp_path):
    path = tmp_path / "scan.pdf"
    subprocess.run(
        [sys.executable, str(SKILL / "scripts" / "make_fixture_pdf.py"), str(path), "--scanned"],
        check=True,
    )
    deck = meeting_slides.extract_deck(path)
    assert deck.status != "ok" and deck.text == ""


def test_oversized_deck_is_refused_before_reading_content(tmp_path):
    path = tmp_path / "huge.md"
    with path.open("wb") as handle:
        handle.truncate(meeting_slides.MAX_DECK_BYTES + 1)
    deck = meeting_slides.extract_deck(path)
    assert deck.status != "ok" and deck.text == ""


def test_note_label_reports_page_count_for_a_usable_deck(tmp_path):
    deck = meeting_slides.extract_deck(_pptx(tmp_path / "kickoff.pptx", [["a"], ["b"]]))
    assert meeting_slides.note_label(deck) == "kickoff.pptx (2쪽)"


# --- 프롬프트 주입 -----------------------------------------------------------


def test_prompt_block_labels_the_material_and_is_empty_without_decks(tmp_path):
    assert meeting_slides.prompt_block(()) == ""
    deck = meeting_slides.extract_deck(_pptx(tmp_path / "d.pptx", [["과제 개요"]]))
    block = meeting_slides.prompt_block((deck,))
    assert "발표자료" in block and "d.pptx" in block and "[슬라이드 1] 과제 개요" in block


def test_prompt_block_drops_unreadable_decks(tmp_path):
    broken = meeting_slides.extract_deck(tmp_path / "nope.pdf")
    assert meeting_slides.prompt_block((broken,)) == ""


def test_prompt_block_truncates_and_says_so(tmp_path):
    path = tmp_path / "long.md"
    path.write_text("가" * (meeting_slides.MAX_PROMPT_CHARS + 500), encoding="utf-8")
    block = meeting_slides.prompt_block((meeting_slides.extract_deck(path),))
    assert len(block) < meeting_slides.MAX_PROMPT_CHARS + 400
    assert "이하 생략" in block


def test_build_prompt_substitutes_the_slides_placeholder():
    template = "본문:\n{{MEETING_TEXT}}\n\n{{SLIDES}}\n\n지시: {{MY_NAMES}}"
    prompt = meeting_llm.build_prompt(
        template, meeting_text="회의 본문", my_names="차", slides="발표자료 A"
    )
    assert "{{SLIDES}}" not in prompt
    assert prompt.index("발표자료 A") > prompt.index("회의 본문")
    assert prompt.index("발표자료 A") < prompt.index("지시:")


def test_build_prompt_leaves_no_placeholder_when_there_are_no_slides():
    template = "본문:\n{{MEETING_TEXT}}\n{{SLIDES}}\n지시: {{MY_NAMES}}"
    prompt = meeting_llm.build_prompt(template, meeting_text="t", my_names="차")
    assert "{{SLIDES}}" not in prompt


def test_v4_prompt_carries_the_conservative_pronoun_rules():
    for path in (
        REPO / "prompts" / "meeting-extraction-v4.md",
        SKILL / "prompts" / "meeting-extraction-v4.md",
    ):
        body = meeting_llm.load_prompt_template(path)
        assert "{{SLIDES}}" in body and "{{MEETING_TEXT}}" in body and "{{MY_NAMES}}" in body
        assert "대명사" in body and "추정" in body
        assert "```" not in body, "v1/v2 가 폐기된 이유 — 펜스/스키마 블록은 에코를 부른다"


# --- ingest: 발표자료는 노트에 표기되고 회의록 생성을 막지 않는다 ---------------


def _offline_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEETING_NOTES_DIR", str(tmp_path / "notes"))
    monkeypatch.setenv("MEETING_PROMPT_FILE", str(REPO / "prompts/meeting-extraction-v4.md"))
    monkeypatch.setenv("MEETING_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MEETING_PLAN_DIR", str(tmp_path / "plan"))
    monkeypatch.setenv("MEETING_CONFIG", str(tmp_path / "absent.json"))


def _ingest(tmp_path, capsys, *extra: str) -> dict[str, object]:
    rc = meeting_cli.main(
        [
            "ingest",
            "--file", str(FIXTURES / "meeting-clean.md"),
            "--recorded-response", str(FIXTURES / "recorded-clean.json"),
            "--offline", "--notify-channel", "TEST", *extra,
        ]
    )
    assert rc == 0
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_deck_is_labeled_in_the_note(
    tmp_path, monkeypatch, capsys
):
    _offline_env(tmp_path, monkeypatch)
    deck = tmp_path / "kickoff.md"
    deck.write_text("# 과제 개요\n실증 사이트는 A동", encoding="utf-8")
    result = _ingest(tmp_path, capsys, "--slides", str(deck))
    assert result["slides"] == ["kickoff.md (1쪽)"]
    note = next((tmp_path / "notes").glob("*.md")).read_text(encoding="utf-8")
    assert "| 발표자료 | kickoff.md (1쪽) |" in note


def test_unreadable_deck_never_blocks_the_ingest(tmp_path, monkeypatch, capsys):
    _offline_env(tmp_path, monkeypatch)
    result = _ingest(tmp_path, capsys, "--slides", str(tmp_path / "gone.pdf"))
    assert result["exit"] == 0
    note = next((tmp_path / "notes").glob("*.md")).read_text(encoding="utf-8")
    assert "읽지 못함" in note


def test_ingest_without_slides_is_unchanged(tmp_path, monkeypatch, capsys):
    _offline_env(tmp_path, monkeypatch)
    result = _ingest(tmp_path, capsys)
    assert result["slides"] == []
    note = next((tmp_path / "notes").glob("*.md")).read_text(encoding="utf-8")
    assert "발표자료" not in note
