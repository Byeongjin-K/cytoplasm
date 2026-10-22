"""Glossary-grounded correction command: owner ledger, short-pair hints, name guard, evidence and fail-open; synthetic text only."""

from __future__ import annotations

import io
import json
import sys
from typing import Final

import pytest

import unit_node

from automation import codex_llm
from automation.plaud_sync import glossary_correct
from automation.plaud_sync.transcript_corrector import REPORT_ENV, preserves_structure

RAW: Final = (
    "# synthetic\n\n---\n\n"
    "[00:00:01] 화자1: 홍길둥 박사님 오셨어요\n"
    "[00:00:05] 화자2: 오토파즈 과제의 면도 계획을 봅니다\n"
    "[00:00:09] 화자1: 이몽룡 위원님도 오셨어요\n"
)


def _run(tmp_path, monkeypatch, capsys, answer: str, *, ledger: str | None = None, raw: str = RAW):
    report = tmp_path / "report.json"
    prompts: list[str] = []

    def served(prompt: str, *, timeout: float, env: object) -> codex_llm.Served:
        prompts.append(prompt)
        return codex_llm.Served(text=answer, provider="synthetic", model="synthetic")

    monkeypatch.setattr(codex_llm, "complete_served", served)
    monkeypatch.setattr(sys, "stdin", io.StringIO(raw))
    glossary = tmp_path / "용어집.csv"
    glossary.write_text("틀린표기,올바른표기\n" + (ledger or ""), encoding="utf-8")
    env = {**unit_node.env(), "HOME": str(tmp_path), "TERM_GLOSSARY_FILE": str(glossary),
           REPORT_ENV: str(report)}
    code = glossary_correct.main([], env)
    out = capsys.readouterr()
    evidence = json.loads(report.read_text(encoding="utf-8")) if report.exists() else None
    return code, out.out, evidence, prompts, out.err


def _edits(*edits: tuple[int, str, str]) -> str:
    return json.dumps({"edits": [
        {"line": line, "before": before, "after": after} for line, before, after in edits
    ]}, ensure_ascii=False)


NONE: Final = _edits()


def test_long_owner_pair_is_applied_before_the_model(tmp_path, monkeypatch, capsys):
    # Given the owner's ledger pair 오토파즈 -> 오토파지 and a model with nothing to add.
    code, out, evidence, prompts, err = _run(
        tmp_path, monkeypatch, capsys, NONE, ledger="오토파즈,오토파지\n")
    # When the command runs.
    # Then the pair lands without the model, labelled as the owner's ledger, and the model saw it fixed.
    assert code == 0 and "오토파지 과제의" in out and preserves_structure(RAW, out)
    assert evidence == {"evidence": [
        {"before": "오토파즈", "after": "오토파지", "evidence": "형 교정 장부"}], "rejected": 0}
    assert "- 오토파즈 → 오토파지" in prompts[0] and "L6: 오토파지 과제의" in prompts[0]
    assert "오토" not in err


def test_short_owner_pair_is_only_a_hint(tmp_path, monkeypatch, capsys):
    # Given a two-syllable owner pair that is also an ordinary word.
    code, out, evidence, prompts, _ = _run(tmp_path, monkeypatch, capsys, NONE, ledger="면도,연도\n")
    # When the model does not take the hint, then nothing changes.
    assert code == 0 and out == RAW and evidence == {"evidence": [], "rejected": 0}
    assert "- 면도 → 연도" in prompts[0]


def test_model_taking_a_short_hint_is_labelled_with_the_ledger(tmp_path, monkeypatch, capsys):
    # Given the same short pair and a model that judges the context and applies it.
    code, out, evidence, _, _ = _run(
        tmp_path, monkeypatch, capsys, _edits((6, "면도", "연도")), ledger="면도,연도\n")
    # When the command runs, then the fix lands with the owner's ledger as its reason.
    assert code == 0 and "연도 계획을" in out
    assert evidence["evidence"] == [{"before": "면도", "after": "연도", "evidence": "형 교정 장부"}]


def test_guard_blocks_swap_to_another_person(tmp_path, monkeypatch, capsys):
    # Given a close fix and a swap of one person for another name, with no ledger.
    answer = _edits((5, "홍길둥", "홍길동"), (7, "이몽룡", "홍길동"))
    # When the command runs.
    code, out, evidence, prompts, _ = _run(tmp_path, monkeypatch, capsys, answer)
    # Then only the close fix lands, judged from context; the other person keeps his name.
    assert code == 0 and "홍길동 박사님" in out and "이몽룡 위원님" in out
    assert evidence == {"evidence": [
        {"before": "홍길둥", "after": "홍길동", "evidence": "문맥"}], "rejected": 1}
    assert "## 형 교정 장부\n(없음)" in prompts[0]


def test_a_right_spelling_from_the_ledger_is_never_replaced(tmp_path, monkeypatch, capsys):
    # Given the owner already wrote 이몽룡 as a right spelling, and a model that wants to change it.
    code, out, evidence, _, _ = _run(
        tmp_path, monkeypatch, capsys, _edits((7, "이몽룡", "이몽용")), ledger="이몽용,이몽룡\n")
    # When the command runs, then the owner's spelling stays.
    assert code == 0 and out == RAW and evidence["rejected"] == 1


@pytest.mark.parametrize("edit", [
    (1, "synthetic", "changed"),
    (5, "화자1", "화자2"),
    (5, "없는말", "홍길동"),
    (5, "홍길둥", "홍길동\n# injected"),
])
def test_edits_outside_spoken_words_are_rejected(tmp_path, monkeypatch, capsys, edit):
    # Given an edit to the header, a speaker label, absent text or a structure injection.
    code, out, evidence, _, _ = _run(tmp_path, monkeypatch, capsys, _edits(edit))
    # When the command runs, then nothing changes.
    assert code == 0 and out == RAW and evidence["rejected"] == 1


@pytest.mark.parametrize("answer", ["모르겠다", "{\"edits\": 3}"])
def test_unusable_model_answer_exits_for_fail_open(tmp_path, monkeypatch, capsys, answer):
    # Given a model answer without an edit list.
    code, out, evidence, _, err = _run(tmp_path, monkeypatch, capsys, answer)
    # When the command runs, then it exits non-zero so the hook keeps the raw note.
    assert code == glossary_correct.EXIT_MODEL
    assert out == "" and evidence is None
    assert "PLAUD-GLOSSARY-CORRECT failed" in err


def test_every_occurrence_in_the_line_is_fixed_once(tmp_path, monkeypatch, capsys):
    # Given a line where the same misheard word appears twice and a repeated edit.
    raw = "# synthetic\n\n---\n\n[00:00:01] 화자1: 오토파즈 과제와 오토파즈 회의\n"
    answer = _edits((5, "오토파즈", "오토파지"), (5, "오토파즈", "오토파지"))
    # When the command runs.
    code, out, evidence, _, _ = _run(tmp_path, monkeypatch, capsys, answer, raw=raw)
    # Then both words change and the repeat is not counted as a guard rejection.
    assert code == 0 and out == raw.replace("오토파즈", "오토파지")
    assert evidence["rejected"] == 0


@pytest.mark.parametrize("words,before,after,expected", [
    ("가나다는 왔다", "가나다는", "가나닥는", "가나닥은 왔다"),
    ("가나다를 본다", "가나다", "가나닥", "가나닥을 본다"),
    ("가나닥이 왔다", "가나닥", "가나다", "가나다가 왔다"),
    ("가나닥과 함께", "가나닥", "가나다", "가나다와 함께"),
    ("가나다랑 같이", "가나다", "가나닥", "가나닥이랑 같이"),
    ("가나다로 간다", "가나다", "가나닥", "가나닥으로 간다"),
    ("가나다로 간다", "가나다", "가나달", "가나달로 간다"),
    ("가나닥으로.", "가나닥", "가나달", "가나달로."),
    ("가나다는.", "가나다는.", "가나닥는.", "가나닥은."),
])
def test_particle_follows_the_new_final_consonant(words, before, after, expected):
    # Given a fix that adds, drops or changes the final consonant before a particle.
    # When it is applied, then the particle pair matches the new ending (ㄹ takes 로).
    assert glossary_correct._replace(words, before, after) == expected


@pytest.mark.parametrize("words,before,after,expected", [
    ("가나다의 일", "가나다", "가나닥", "가나닥의 일"),
    ("가나다에서", "가나다", "가나닥", "가나닥에서"),
    ("가나다이야기", "가나다", "가나닥", "가나닥이야기"),
    ("가나닥이야기", "가나닥", "가나다", "가나다이야기"),
    ("가나다이라고", "가나다", "가나닥", "가나닥이라고"),
    ("가나다는 왔다", "가나다", "ABC", "ABC는 왔다"),
    ("6m 발로 서브", "발로", "관로", "6m 관로 서브"),
    ("발로를 묻는다", "발로", "관로", "관로를 묻는다"),
    ("발이 늦다", "발", "바", "바이 늦다"),
])
def test_words_that_are_not_paired_particles_are_left_alone(words, before, after, expected):
    # Given text after the fix that is not a paired particle, or a one-syllable stem.
    # When the fix is applied, then only the fixed word changes.
    assert glossary_correct._replace(words, before, after) == expected


@pytest.mark.parametrize("before,after,raw_words,expected", [
    ("가나다는", "가나닥은", "가나다 회의", "형 교정 장부"),
    ("마바사", "가나닥", "가나다 회의", "형 교정 장부"),
    ("마바사", "아자차", "아자차는 맞다", "같은 전사 다른 곳"),
    ("마바사", "아자차", "가나다 회의", "문맥"),
])
def test_evidence_is_read_from_the_bytes(before, after, raw_words, expected):
    # Given the owner's pair 가나다 -> 가나닥 and what the transcript already spells.
    edit = glossary_correct.Edit(5, before, after, "")
    # When the table reason is chosen, then it follows the ledger, then the transcript, then context.
    assert glossary_correct.evidence_label(edit, (("가나다", "가나닥"),), raw_words) == expected
