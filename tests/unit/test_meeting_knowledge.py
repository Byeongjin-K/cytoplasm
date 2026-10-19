from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "skills" / "meeting" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from automation.knowledge.pack import EvidenceItem, EvidencePack, KnowledgeQuery, Verdict  # noqa: E402
import meeting_cli  # noqa: E402
import meeting_knowledge  # noqa: E402


def _item(*, content: str = "지난 회의에서 현장 실증을 결정함") -> EvidenceItem:
    return EvidenceItem(
        "E1", "rag", "meeting", "meetings/previous.md", "선행 회의", "2026-08-14",
        "path", 0.9, True, None, None, content, "a" * 64,
    )


def _pack(verdict: Verdict = "hit", *, item: EvidenceItem | None = None) -> EvidencePack:
    query = KnowledgeQuery("실증 회의 참석자 연구팀", "cite", caller="meeting")
    items = (_item() if item is None else item,) if verdict == "hit" else ()
    state = "hit" if verdict == "hit" else "no_memory" if verdict == "no_evidence" else "unavailable"
    return EvidencePack(
        "knowledge-v1", query, verdict, items,
        {"rag": state, "wiki": "none" if verdict != "unavailable" else state,
         "twin": "none" if verdict != "unavailable" else state},
    )


def _args(tmp_path: Path, response: str) -> argparse.Namespace:
    source = tmp_path / "meeting.md"
    source.write_text("# 실증 회의\n참석자: 연구팀\n주제: 현장 실증 일정과 결과\n", encoding="utf-8")
    recorded = tmp_path / "response.json"
    recorded.write_text(response, encoding="utf-8")
    return argparse.Namespace(
        file=str(source), body_file=None, label="실증 회의", notify_channel=None,
        recorded_response=str(recorded), offline=True, with_evidence=True,
    )


def _environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEETING_NOTES_DIR", str(tmp_path / "notes"))
    monkeypatch.setenv("MEETING_PROMPT_FILE", str(ROOT / "prompts/meeting-extraction-v3.md"))
    monkeypatch.setenv("MEETING_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MEETING_PLAN_DIR", str(tmp_path / "plan"))
    monkeypatch.setenv("MEETING_CONFIG", str(tmp_path / "absent.json"))


def test_hit_pack_enters_prompt_validates_citations_and_writes_private_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _environment(tmp_path, monkeypatch)
    prompts: list[str] = []
    original = meeting_cli.meeting_llm.build_prompt

    def capture(template: str, **kwargs: Any) -> str:
        prompt = original(template, **kwargs)
        prompts.append(prompt)
        return prompt

    monkeypatch.setattr(meeting_cli.meeting_llm, "build_prompt", capture)
    response = json.dumps({"decisions": ["선행 결정을 유지한다 [E1] [E9]"], "todos": [], "milestones": [], "others": []})

    assert meeting_cli.cmd_ingest(_args(tmp_path, response), evidence_pack=_pack()) == 0

    note = next((tmp_path / "notes").glob("*.md"))
    document = note.read_text(encoding="utf-8")
    assert "[E1]" in document and "[E9]" not in document
    assert "[E1] RAG/회의: meetings/previous.md (2026-08-14, path)" in document
    assert "EVIDENCE:" in prompts[0] and "지난 회의에서 현장 실증을 결정함" in prompts[0]
    assert "Use only MATERIAL/EVIDENCE, cite [En], do not invent" in prompts[0]
    sidecar = note.with_suffix(".evidence.json")
    assert json.loads(sidecar.read_text(encoding="utf-8"))["verdict"] == "hit"
    assert sidecar.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(("verdict", "message"), (("no_evidence", "근거 없음"), ("unavailable", "근거 수집 불가")))
def test_non_hit_pack_marks_note_and_continues(
    verdict: Verdict, message: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _environment(tmp_path, monkeypatch)
    response = json.dumps({"decisions": ["회의를 계속 진행함"], "todos": [], "milestones": [], "others": []})
    assert meeting_cli.cmd_ingest(_args(tmp_path, response), evidence_pack=_pack(verdict)) == 0
    assert message in next((tmp_path / "notes").glob("*.md")).read_text(encoding="utf-8")


def test_adapter_builds_bounded_cite_query(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[KnowledgeQuery] = []

    class Facade:
        KnowledgeQuery = KnowledgeQuery

        @staticmethod
        def collect_evidence(query: KnowledgeQuery) -> EvidencePack:
            captured.append(query)
            return _pack()

    monkeypatch.setattr(meeting_knowledge, "module", lambda name: Facade)
    meeting_knowledge.collect("실증 회의", "연구팀", "현장 일정", limit=20)
    assert captured == [KnowledgeQuery(
        "실증 회의\n연구팀\n현장 일정", "cite", limit=8, caller="meeting"
    )]


def test_evidence_preview_json_exposes_only_count_and_layers(
    capsys: pytest.CaptureFixture[str]
) -> None:
    args = argparse.Namespace(title="실증", attendees="연구팀", topics="현장", limit=8, json=True)
    assert meeting_cli.meeting_evidence.command(args, _pack()) == 0
    assert json.loads(capsys.readouterr().out) == {
        "evidence_count": 1, "layers": _pack().layers,
    }
