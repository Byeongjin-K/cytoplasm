"""회의록이 소유자 참고자료를 근거로 쓰는 경계 — 게이트 합산은 fail-closed, 조회는 fail-soft."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "skills" / "meeting"
FIXTURES = SKILL / "fixtures"
sys.path.insert(0, str(SKILL / "scripts"))

import meeting_cli  # noqa: E402
import meeting_llm  # noqa: E402
import meeting_reference  # noqa: E402
import meeting_slides  # noqa: E402


def _offline_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEETING_NOTES_DIR", str(tmp_path / "notes"))
    monkeypatch.setenv("MEETING_PROMPT_FILE", str(REPO / "prompts/meeting-extraction-v4.md"))
    monkeypatch.setenv("MEETING_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MEETING_PLAN_DIR", str(tmp_path / "plan"))
    monkeypatch.setenv("MEETING_CONFIG", str(tmp_path / "absent.json"))


def _ingest(capsys: pytest.CaptureFixture[str], *extra: str) -> dict[str, object]:
    code = meeting_cli.main(
        [
            "ingest",
            "--file", str(FIXTURES / "meeting-clean.md"),
            "--recorded-response", str(FIXTURES / "recorded-clean.json"),
            "--offline", "--notify-channel", "TEST", *extra,
        ]
    )
    assert code == 0
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def _deck(text: str, name: str = "굴착 오차 관리기준.pdf") -> meeting_slides.Deck:
    return meeting_slides.Deck(name=name, text=text, slide_count=2, status="ok")


def _shelf(monkeypatch: pytest.MonkeyPatch, *decks: meeting_slides.Deck) -> list[str]:
    asked: list[str] = []

    def _collect(query: str, **_: Any) -> tuple[meeting_slides.Deck, ...]:
        asked.append(query)
        return decks

    monkeypatch.setattr(meeting_reference, "collect", _collect)
    return asked


def test_frequent_transcript_words_reach_the_query() -> None:
    result = meeting_reference.query("주간회의", "해양과제", "궤도보정 궤도보정 궤도보정 센서")

    assert "궤도보정" in result.split()


def test_spoken_fillers_do_not_reach_the_query() -> None:
    text = " ".join(["그리고"] * 20 + ["그래서"] * 10 + ["궤도보정"] * 3)

    result = meeting_reference.query("주간회의", "해양과제", text)

    assert "궤도보정" in result.split()
    assert "그리고" not in result.split()
    assert "그래서" not in result.split()


def test_query_is_deterministic_and_sorts_frequency_ties() -> None:
    text = "제타 제타 알파 알파"

    first = meeting_reference.query("주간회의", "해양과제", text)
    second = meeting_reference.query("주간회의", "해양과제", text)

    assert first == second
    assert first == "주간회의 해양과제 알파 제타"


def test_query_limits_transcript_terms() -> None:
    text = " ".join(f"낱말{index}" for index in range(20))

    result = meeting_reference.query("주간회의", "해양과제", text)

    assert len(result.split()[2:]) <= meeting_reference.MAX_QUERY_TERMS


def test_empty_transcript_query_is_only_label_and_project() -> None:
    assert meeting_reference.query("주간회의", "해양과제", "  \n\t") == "주간회의 해양과제"


def test_transcript_word_reaches_the_shelf_through_ingest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _offline_env(tmp_path, monkeypatch)
    asked = _shelf(monkeypatch)

    _ingest(capsys)

    assert asked and "센서" in asked[0].split()


def test_reference_is_named_in_the_note_apart_from_the_deck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _offline_env(tmp_path, monkeypatch)
    _shelf(monkeypatch, _deck("굴착 오차는 10 mm 이하로 관리한다"))
    deck = tmp_path / "kickoff.md"
    deck.write_text("# 과제 개요", encoding="utf-8")

    _ingest(capsys, "--slides", str(deck))
    note = next((tmp_path / "notes").glob("*.md")).read_text(encoding="utf-8")

    assert "| 발표자료 | kickoff.md (1쪽) |" in note
    assert "| 참고자료 | 굴착 오차 관리기준.pdf (2쪽) |" in note


def test_reference_text_reaches_the_extraction_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _offline_env(tmp_path, monkeypatch)
    _shelf(monkeypatch, _deck("굴착 오차는 10 mm 이하로 관리한다"))
    seen: dict[str, str] = {}
    original = meeting_llm.extract

    def _spy(text: str, **kwargs: Any) -> Any:
        seen["slides"] = str(kwargs.get("slides", ""))
        return original(text, **kwargs)

    monkeypatch.setattr(meeting_llm, "extract", _spy)

    _ingest(capsys)

    assert "굴착 오차는 10 mm 이하로 관리한다" in seen["slides"]
    assert "참고자료" in seen["slides"]


def test_the_shelf_is_asked_with_the_meeting_label_and_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _offline_env(tmp_path, monkeypatch)
    asked = _shelf(monkeypatch)

    _ingest(capsys, "--label", "킥오프", "--project", "해양고신뢰성")

    assert asked and "킥오프" in asked[0] and "해양고신뢰성" in asked[0]


def test_a_failing_shelf_never_blocks_the_minutes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _offline_env(tmp_path, monkeypatch)

    def _explode(query: str, **_: Any) -> tuple[meeting_slides.Deck, ...]:
        raise RuntimeError("Drive 가 응답하지 않음")

    monkeypatch.setattr(meeting_reference, "_scan", _explode)

    result = _ingest(capsys)

    assert result["exit"] == 0


def test_collect_turns_reference_documents_into_decks(monkeypatch: pytest.MonkeyPatch) -> None:
    from automation import drive_reference

    scan = drive_reference.ReferenceScan(
        status=drive_reference.OK,
        root="KIMM",
        scanned=2,
        documents=(
            drive_reference.ReferenceDocument(
                file=drive_reference.ReferenceFile(
                    file_id="f1",
                    name="관리기준.pdf",
                    path="KIMM/2026/관리기준.pdf",
                    mime_type="application/pdf",
                    modified="2026-08-01T09:00:00Z",
                ),
                text="굴착 오차는 10 mm 이하.",
                status=drive_reference.OK,
                sections=3,
                score=5,
            ),
        ),
    )
    monkeypatch.setattr(meeting_reference, "_scan", lambda query, limit: scan)

    decks = meeting_reference.collect("굴착 오차")

    assert [deck.name for deck in decks] == ["관리기준.pdf"]
    assert decks[0].text == "굴착 오차는 10 mm 이하."
    assert decks[0].slide_count == 3
