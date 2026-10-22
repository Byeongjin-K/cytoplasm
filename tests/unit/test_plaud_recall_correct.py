"""Recall-grounded correction command: name guard, evidence report and fail-open, synthetic text only."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Final

import pytest

import unit_node

from automation import codex_llm
from automation.plaud_sync import recall_correct
from automation.plaud_sync.transcript_corrector import REPORT_ENV, preserves_structure

RAW: Final = (
    "# synthetic\n\n---\n\n"
    "[00:00:01] 화자1: 홍길둥 박사님 오셨어요\n"
    "[00:00:05] 화자2: 오토파즈 과제 회의를 시작합니다\n"
    "[00:00:09] 화자1: 이몽룡 위원님도 오셨어요\n"
)
MEMORY: Final = [{"title": "합성 노트", "excerpt": "홍길동 박사 · 오토파지 과제"}]


def _recall(tmp_path: Path, rows: list[dict[str, str]]) -> Path:
    script = tmp_path / "recall_cli.py"
    script.write_text(
        "import json,sys\n"
        f"print(json.dumps({{'status': 'hit' if {rows!r} else 'no_memory', 'results': {rows!r}}}))\n",
        encoding="utf-8",
    )
    return script


def _run(tmp_path, monkeypatch, capsys, answer: str, rows=MEMORY):
    report = tmp_path / "report.json"
    prompts: list[str] = []

    def served(prompt: str, *, timeout: float, env: object) -> codex_llm.Served:
        prompts.append(prompt)
        return codex_llm.Served(text=answer, provider="synthetic", model="synthetic")

    monkeypatch.setattr(codex_llm, "complete_served", served)
    monkeypatch.setattr(sys, "stdin", io.StringIO(RAW))
    env = {**unit_node.env(), "HOME": str(tmp_path), "PLAUD_NOTE_TITLE": "2026-10-22_1000_합성_회의", REPORT_ENV: str(report)}
    code = recall_correct.main(["--recall-cli", str(_recall(tmp_path, rows))], env)
    out = capsys.readouterr()
    return code, out.out, report, prompts, out.err


def _edits(*edits: tuple[int, str, str, str]) -> str:
    return json.dumps({"edits": [
        {"line": line, "before": before, "after": after, "evidence": evidence}
        for line, before, after, evidence in edits
    ]}, ensure_ascii=False)


def test_guard_blocks_swap_to_another_known_person(tmp_path, monkeypatch, capsys):
    # Given a close fix and a swap of one person for another name recall knows.
    answer = _edits(
        (5, "홍길둥", "홍길동", "R1"),
        (7, "이몽룡", "홍길동", "R1"),
    )
    # When the command corrects the transcript.
    code, out, report, prompts, err = _run(tmp_path, monkeypatch, capsys, answer)
    # Then only the close fix lands; the other person keeps his name.
    assert code == 0
    assert "홍길동 박사님" in out and "이몽룡 위원님" in out
    assert preserves_structure(RAW, out)
    assert json.loads(report.read_text(encoding="utf-8")) == {
        "evidence": [{"before": "홍길둥", "after": "홍길동", "evidence": "기억: 합성 노트"}],
        "rejected": 1,
    }
    assert "합성 노트" in prompts[0] and "L5: 홍길둥 박사님 오셨어요" in prompts[0]
    assert "홍길" not in err


def test_original_known_to_recall_is_not_replaced(tmp_path, monkeypatch, capsys):
    # Given an edit whose original word is itself in the recall evidence.
    rows = [{"title": "합성 노트", "excerpt": "오토파즈 과제와 오토파지 과제는 다르다"}]
    answer = _edits((6, "오토파즈", "오토파지", "R1"))
    # When the command runs.
    code, out, report, _, _ = _run(tmp_path, monkeypatch, capsys, answer, rows)
    # Then the known original stays.
    assert code == 0 and out == RAW
    assert json.loads(report.read_text(encoding="utf-8"))["rejected"] == 1


@pytest.mark.parametrize("edit", [
    (6, "오토파즈", "오토파지", "문맥"),
])
def test_context_fix_without_memory_is_labelled_context(tmp_path, monkeypatch, capsys, edit):
    # Given no memory and a close context fix.
    code, out, report, prompts, _ = _run(tmp_path, monkeypatch, capsys, _edits(edit), rows=[])
    # When the command runs, then the fix lands with context as its reason.
    assert code == 0 and "오토파지 과제" in out
    assert json.loads(report.read_text(encoding="utf-8"))["evidence"][0]["evidence"] == "문맥"
    assert "(찾은 기억 없음)" in prompts[0]


@pytest.mark.parametrize("edit", [
    (1, "synthetic", "changed", "문맥"),
    (5, "화자1", "화자2", "문맥"),
    (5, "없는말", "홍길동", "문맥"),
    (5, "홍길둥", "홍길동\n# injected", "문맥"),
])
def test_edits_outside_spoken_words_are_rejected(tmp_path, monkeypatch, capsys, edit):
    # Given an edit to the header, a speaker label, absent text or a structure injection.
    code, out, report, _, _ = _run(tmp_path, monkeypatch, capsys, _edits(edit))
    # When the command runs, then nothing changes.
    assert code == 0 and out == RAW
    assert json.loads(report.read_text(encoding="utf-8"))["rejected"] == 1


@pytest.mark.parametrize("answer", ["모르겠다", "{\"edits\": 3}"])
def test_unusable_model_answer_exits_for_fail_open(tmp_path, monkeypatch, capsys, answer):
    # Given a model answer without an edit list.
    code, out, report, _, err = _run(tmp_path, monkeypatch, capsys, answer)
    # When the command runs, then it exits non-zero so the hook keeps the raw note.
    assert code == recall_correct.EXIT_MODEL
    assert out == "" and not report.exists()
    assert "PLAUD-RECALL-CORRECT failed" in err


def test_every_occurrence_in_the_line_is_fixed_once(tmp_path, monkeypatch, capsys):
    # Given a line where the same misheard word appears twice and a repeated edit.
    raw = "# synthetic\n\n---\n\n[00:00:01] 화자1: 오토파즈 과제와 오토파즈 회의\n"
    monkeypatch.setattr(sys, "stdin", io.StringIO(raw))
    monkeypatch.setattr(codex_llm, "complete_served", lambda prompt, **_: codex_llm.Served(
        text=_edits((5, "오토파즈", "오토파지", "문맥"), (5, "오토파즈", "오토파지", "문맥")),
        provider="synthetic", model="synthetic",
    ))
    report = tmp_path / "report.json"
    env = {**unit_node.env(), "HOME": str(tmp_path), REPORT_ENV: str(report)}
    # When the command runs.
    code = recall_correct.main(["--recall-cli", str(_recall(tmp_path, []))], env)
    # Then both words change and the repeat is not counted as a guard rejection.
    assert code == 0
    assert capsys.readouterr().out == raw.replace("오토파즈", "오토파지")
    assert json.loads(report.read_text(encoding="utf-8"))["rejected"] == 0
