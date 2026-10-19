"""화자 라벨 → 실제 이름 매핑 계약 (전사본 화자분리 → 회의록).

전사본이 `[00:03:12] 화자1 · 김민수` 같은 블록 헤더를 달고 오면 LLM 은 그 라벨이 누구인지
회의록에 적힌 근거(자기소개·호명·소개)만으로 답한다. 여기서 고정하는 것은 그 답이
스키마 → CLI JSON → 회의록 머리말까지 형태를 잃지 않고 흐른다는 것이다.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "skills" / "meeting"
FIXTURES = SKILL / "fixtures"
sys.path.insert(0, str(SKILL / "scripts"))

import meeting_cli  # noqa: E402
import meeting_llm  # noqa: E402
import meeting_minutes  # noqa: E402

NOW = datetime(2026, 9, 1, 10, 0, 0, tzinfo=ZoneInfo("Asia/Seoul"))
_V6 = SKILL / "prompts" / "meeting-extraction-v6.md"


# --- 스키마: 관대하게 받고 망가진 항목만 버린다 --------------------------------


def test_speakers_parse_into_label_name_basis():
    extraction = meeting_llm.parse_extraction(
        json.dumps(
            {
                "speakers": [
                    {"label": "화자1", "name": "김민수", "basis": "저는 김민수입니다"},
                    {"label": "화자2", "name": None, "basis": ""},
                ]
            },
            ensure_ascii=False,
        )
    )
    assert extraction.speakers == (
        meeting_llm.SpeakerRef("화자1", "김민수", "저는 김민수입니다"),
        meeting_llm.SpeakerRef("화자2", None, ""),
    )


def test_malformed_speaker_entries_are_dropped_not_fatal():
    extraction = meeting_llm.parse_extraction(
        json.dumps(
            {
                "speakers": [
                    "화자1",
                    {"name": "이름만"},
                    {"label": "speaker_00", "name": "김민수"},
                    {"label": "화자9", "name": "  이영희  ", "basis": 7},
                    {"label": "화자10", "name": "   "},
                ],
                "decisions": ["결정"],
            },
            ensure_ascii=False,
        )
    )
    assert extraction.speakers == (
        meeting_llm.SpeakerRef("화자9", "이영희", "7"),
        meeting_llm.SpeakerRef("화자10", None, ""),
    )
    assert extraction.decisions[0].text == "결정"


def test_absent_speakers_key_stays_empty():
    extraction = meeting_llm.parse_extraction('{"todos": [{"title": "t", "basis": "b"}]}')
    assert extraction.speakers == ()


def test_citation_transform_keeps_the_label_and_cleans_the_prose():
    extraction = meeting_llm.Extraction(
        speakers=(meeting_llm.SpeakerRef("화자1", "김민수", "저는 김민수입니다"),)
    )
    mapped = meeting_llm.map_extraction(extraction, lambda text: f"<{text}>")
    assert mapped.speakers == (
        meeting_llm.SpeakerRef("화자1", "<김민수>", "<저는 김민수입니다>"),
    )


# --- 회의록 머리말 -------------------------------------------------------------


def _render(extraction: meeting_llm.Extraction) -> str:
    return meeting_minutes.render(
        label="주간 연구 미팅",
        kind="md",
        extraction=extraction,
        original_text="[00:03:12] 화자1 · 김민수\n저는 김민수입니다.",
        ref="a1b2c3d4",
        now=NOW,
    )


def test_minutes_header_lists_speakers_with_unknown_names_marked():
    extraction = meeting_llm.Extraction(
        meeting=meeting_llm.MeetingHeader(attendees=("김민수",)),
        speakers=(
            meeting_llm.SpeakerRef("화자1", "김민수", "저는 김민수입니다"),
            meeting_llm.SpeakerRef("화자2", None, ""),
        ),
    )
    head = _render(extraction).split(meeting_minutes.APPENDIX_HEADING)[0]
    assert "- 화자: 화자1=김민수 · 화자2=미상" in head
    # 참석 줄 옆자리 — 머리말 안이지 본문 섹션이 아니다.
    assert head.index("| 참석 |") < head.index("- 화자:") < head.index("## ")


def test_minutes_header_omits_the_speaker_line_when_there_are_none():
    document = _render(meeting_llm.Extraction(decisions=(meeting_llm.Decision("d", "b"),)))
    assert "- 화자:" not in document


# --- CLI: 마지막 stdout 한 줄 ---------------------------------------------------


def test_ingest_prints_speakers_in_the_final_json_line(tmp_path, monkeypatch, capsys):
    recorded = tmp_path / "recorded.json"
    recorded.write_text(
        json.dumps(
            {
                "meeting": {"title": "주간 연구 미팅", "date": "2026-09-01", "attendees": ["김민수"]},
                "decisions": [{"text": "중간보고서는 9월 1일 제출", "basis": "제출일 확정"}],
                "todos": [{"title": "데이터셋 정리", "deadline": "2026-09-10", "basis": "차 담당"}],
                "speakers": [
                    {"label": "화자1", "name": "김민수", "basis": "저는 김민수입니다"},
                    {"label": "화자2", "name": None, "basis": "근거 없음"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    for name, value in (
        ("MEETING_NOTES_DIR", tmp_path / "notes"),
        ("MEETING_PROMPT_FILE", SKILL / "prompts/meeting-extraction-v6.md"),
        ("MEETING_LOG_DIR", tmp_path / "logs"),
        ("MEETING_PLAN_DIR", tmp_path / "plan"),
        ("MEETING_CONFIG", tmp_path / "absent.json"),
    ):
        monkeypatch.setenv(name, str(value))

    rc = meeting_cli.main(
        [
            "ingest",
            "--file", str(FIXTURES / "meeting-clean.md"),
            "--recorded-response", str(recorded),
            "--offline",
        ]
    )

    assert rc == 0
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["speakers"] == [
        {"label": "화자1", "name": "김민수", "basis": "저는 김민수입니다"},
        {"label": "화자2", "name": None, "basis": "근거 없음"},
    ]


def test_ingest_prints_an_empty_speaker_list_when_the_transcript_has_none(
    tmp_path, monkeypatch, capsys
):
    for name, value in (
        ("MEETING_NOTES_DIR", tmp_path / "notes"),
        ("MEETING_PROMPT_FILE", SKILL / "prompts/meeting-extraction-v6.md"),
        ("MEETING_LOG_DIR", tmp_path / "logs"),
        ("MEETING_PLAN_DIR", tmp_path / "plan"),
        ("MEETING_CONFIG", tmp_path / "absent.json"),
    ):
        monkeypatch.setenv(name, str(value))

    rc = meeting_cli.main(
        [
            "ingest",
            "--file", str(FIXTURES / "meeting-clean.md"),
            "--recorded-response", str(FIXTURES / "recorded-clean.json"),
            "--offline",
        ]
    )

    assert rc == 0
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["speakers"] == []


# --- 프롬프트 v6 ---------------------------------------------------------------


def test_v6_prompt_asks_for_speakers_and_keeps_the_v5_material():
    body = meeting_llm.load_prompt_template(_V6)
    assert _V6.read_text(encoding="utf-8").splitlines().count("<<<PROMPT>>>") == 1
    for placeholder in ("{{MEETING_TEXT}}", "{{MY_NAMES}}", "{{SLIDES}}", "{{OPEN_ACTIONS}}"):
        assert placeholder in body
    speakers_line = next(
        line for line in body.splitlines() if line.startswith("speakers:")
    )
    assert "화자1" in speakers_line and "label/name/basis" in speakers_line
    assert "null" in speakers_line and "빈 배열" in speakers_line
    assert "resolved_actions:" in body  # v5 의 지시는 그대로 살아 있다
    assert "```" not in body  # v3 의 교훈: 펜스 금지


def test_v6_prompt_mirrors_v5_layout_byte_for_byte():
    """v5 가 저장소 루트에 사본을 두지 않았으면 v6 도 두지 않는다 — 사본은 동기화 부채다."""
    root_v5 = REPO / "prompts" / "meeting-extraction-v5.md"
    root_v6 = REPO / "prompts" / "meeting-extraction-v6.md"
    assert root_v6.exists() == root_v5.exists()
    if root_v6.exists():
        assert root_v6.read_bytes() == _V6.read_bytes()


def test_cli_default_prompt_points_at_v6():
    source = (SKILL / "scripts" / "meeting_cli.py").read_text(encoding="utf-8")
    assert "meeting-extraction-v6.md" in source
    assert "meeting-extraction-v5.md" not in source
