"""Fold filtering is independent of the separately mounted speechtotext skill."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Final

import pytest

REPO: Final = Path(__file__).resolve().parents[2]
SKILL: Final = REPO / "skills/meeting"
sys.path.insert(0, str(SKILL / "scripts"))

import meeting_cli  # noqa: E402
import meeting_extract  # noqa: E402
import meeting_llm  # noqa: E402

FOLD: Final = "<details><summary>repetition=0.18</summary>\n반복 의심 원문.\n</details>"
VISIBLE: Final = "[00:00:00]\n정상 회의 발화입니다.\n\n"
RAW: Final = VISIBLE + FOLD + "\n\n뒤의 정상 문장입니다.\n"
FILTERED: Final = VISIBLE + "\n\n뒤의 정상 문장입니다.\n"


@pytest.mark.parametrize("text,expected", [
    (RAW, FILTERED), (VISIBLE, VISIBLE), (FOLD + FOLD, ""),
    ('a<details open><summary>x</summary>\n<details>nested</details>hidden</details>b', 'ab'),
    ('a<DETAILS>hidden</DETAILS>b', 'ab'),
    ('a<details>unfinished', 'a'),
])
def test_strip_folded_keeps_other_bytes_when_details_are_present(text: str, expected: str) -> None:
    # Given / When
    result = meeting_extract.strip_folded(text)
    # Then
    assert result == expected


def test_prompt_excludes_fold_when_transcript_becomes_extraction_input() -> None:
    # Given: machine-consumed placeholders, no pinned prompt prose.
    template = "{{MY_NAMES}}\n{{MEETING_TEXT}}"
    # When
    prompt = meeting_llm.build_prompt(template, meeting_text=RAW, my_names="fixture")
    # Then
    assert prompt == "fixture\n" + FILTERED


def test_ingest_keeps_appendix_raw_when_extraction_excludes_fold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: real file ingest and rendering, only the external LLM is replaced.
    source = tmp_path / "transcript.md"
    source.write_text(RAW, encoding="utf-8")
    template = tmp_path / "prompt.md"
    template.write_text("<<<PROMPT>>>\n{{MY_NAMES}}\n{{MEETING_TEXT}}", encoding="utf-8")
    for name, value in {
        "MEETING_NOTES_DIR": tmp_path / "notes",
        "MEETING_PROMPT_FILE": template,
        "MEETING_LOG_DIR": tmp_path / "logs",
        "MEETING_PLAN_DIR": tmp_path / "plan",
        "MEETING_CONFIG": tmp_path / "absent.json",
    }.items():
        monkeypatch.setenv(name, str(value))
    monkeypatch.setenv("DRIVE_PUBLISH_ENABLED", "0")
    prompts: list[str] = []
    recorded = (SKILL / "fixtures/recorded-clean.json").read_text(encoding="utf-8")

    def call(prompt: str) -> str:
        prompts.append(prompt)
        return recorded

    monkeypatch.setattr(meeting_llm, "call_codex", call)
    # When
    code = meeting_cli.main(["ingest", "--file", str(source), "--offline"])
    # Then
    assert code == 0
    assert len(prompts) == 1 and prompts[0].endswith(FILTERED)
    note = next((tmp_path / "notes").glob("*.md")).read_text(encoding="utf-8")
    assert RAW.rstrip() in note
