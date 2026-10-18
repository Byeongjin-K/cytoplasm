"""Content keywords do not change search, extraction, or storage eligibility."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from automation.memory_curator.classify_veto import pre_llm_veto
from automation.plaud_sync.lifelog_extract_live import build_extractor
from automation.plaud_sync.lifelog_model import LifelogExtraction, LifelogRecording
from automation.rag_ingest.sources.obsidian import scan_obsidian

ROOT = Path(__file__).resolve().parents[2]
KEYWORDS = ("특허 출원", "patent confidential", "기밀 발명 명세서")


@pytest.mark.parametrize("text", ("ordinary research", *KEYWORDS))
def test_recall_returns_content_unchanged_without_a_model_config(tmp_path: Path, text: str) -> None:
    # Given a strong retrieval hit and no account model configuration.
    rows = tmp_path / "rows.json"
    rows.write_text(json.dumps([{
        "score": 0.8, "source": "wiki:note", "content": text,
        "metadata": {"source_type": "wiki", "sensitivity": "-".join(("patent", "sensitive"))},
    }]), encoding="utf-8")
    # When the real CLI searches the offline source.
    result = subprocess.run(
        [sys.executable, str(ROOT / "skills/recall/scripts/recall_cli.py"), "search", "research", "--json"],
        env={"PATH": os.environ["PATH"], "HOME": str(tmp_path),
             "RECALL_FAKE_RESULTS": str(rows), "RECALL_LOG_DIR": str(tmp_path / "logs")},
        capture_output=True, text=True, check=False,
    )
    # Then the same hit contract holds and no marker or warning is added.
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "hit"
    assert payload["results"][0]["excerpt"] == text
    assert result.stderr == ""


@pytest.mark.parametrize("text", ("ordinary research", *KEYWORDS))
def test_obsidian_ingests_content_without_rules_or_labels(tmp_path: Path, text: str) -> None:
    # Given one note, without a classification config.
    (tmp_path / "note.md").write_text(text, encoding="utf-8")
    # When the normal scanner ingests it.
    docs, keys = scan_obsidian(tmp_path, (), {}, 1500)
    # Then all content reaches the normal chunk path unchanged.
    assert keys == {"obsidian:note.md"}
    assert docs[0].chunks[0].content == text
    metadata = docs[0].chunks[0].metadata
    assert metadata["source_type"] == "obsidian"
    assert metadata["path"] == "note.md"
    assert "sensitivity" not in metadata


@pytest.mark.parametrize("text", ("ordinary research", *KEYWORDS))
def test_curator_only_vetoes_security_and_native_rules(text: str) -> None:
    # Given a long entry that does not contain credentials or native policy cues.
    entry = f"{text} " + "research context " * 8
    # When deterministic preflight runs.
    verdict = pre_llm_veto(entry, source_kind="memory")
    # Then content keywords do not prevent the model call.
    assert verdict is None


@pytest.mark.parametrize("text", ("ordinary research", *KEYWORDS))
def test_lifelog_extracts_all_content_without_classification_assets(tmp_path: Path, text: str) -> None:
    # Given only the prompt and a model seam, with no rule asset.
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "lifelog-extraction-v5.md").write_text("{{SUMMARY}}\n{{TRANSCRIPT}}", encoding="utf-8")
    seen: list[str] = []

    def complete(prompt: str) -> str:
        seen.append(prompt)
        return '{"summary":"same result","people":[]}'

    recording = LifelogRecording("r", "recording", "", "", 10000, "", text)
    extractor = build_extractor({}, repo_root=tmp_path, complete=complete)
    # When extraction runs.
    result = extractor(recording)
    # Then every input reaches the same model seam and parser.
    assert result == LifelogExtraction(summary="same result")
    assert seen == [f"\n{text}"]


@pytest.mark.parametrize("text", ("ordinary research", *KEYWORDS))
def test_speaker_question_uses_the_same_model_for_all_content(text: str) -> None:
    # Given the opt-in speaker question and an ordinary completer.
    sys.path.insert(0, str(ROOT / "skills/speechtotext/scripts"))
    import stt_speaker_ask

    seen: list[str] = []

    def complete(prompt: str) -> str:
        seen.append(prompt)
        return "3"

    ask = stt_speaker_ask.resolve(
        {"SPEECHTOTEXT_SPEAKER_COUNT_LLM": "1"}, repo_root=ROOT, complete=complete,
    )
    assert ask is not None
    # When any transcript is submitted.
    answer = ask(text)
    # Then the model ran once and the text was neither masked nor skipped.
    assert answer == "3"
    assert len(seen) == 1
    assert text in seen[0]
