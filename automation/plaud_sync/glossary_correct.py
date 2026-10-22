"""Glossary-grounded transcript correction command for the pre-freeze hook (opt-in, off by default).

The node points `~/.hermes/plaud-sync/transcript-corrector.json` at this file. It reads the
finished transcript on stdin and the owner's correction ledger, the existing term glossary
(`autophagy/라이프로그/용어집.csv`, wrong,right rows; YooDori adds a row when the owner points
out a mistake, `glossary-add`). Pairs of three syllables or more are applied first; every pair
and right spelling goes to the account's main model (`codex_llm`, no model named here) as a
hint, and the model returns a list of word edits. stdout is the transcript with those edits;
the hook diffs it against the raw bytes, so the note's change table cannot miss an edit.

Short pairs are hints only (lead 2026-10-22): a two-syllable wrong form such as an ordinary
word would be replaced everywhere without context. Model edits pass the name guard: an edit is
dropped when its jamo distance is beyond ``max(tolerance, 2)``, when a title marks a person
whose name would change beyond that, or when the original word is itself a right spelling in
the ledger. Recall is no longer consulted (owner 2026-10-22 11:09 KST): on a 50-minute meeting
it held 1 of 13 needed terms and added 50-60 s.

Fail-open is the hook's job: any failure here exits non-zero and the note keeps the raw
transcript. Nothing from the transcript, ledger or model is written to stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation import codex_llm, term_correction  # noqa: E402
from automation.plaud_sync import terms  # noqa: E402
from automation.plaud_sync.transcript_corrector import (  # noqa: E402
    REPORT_ENV,
    editable_prefix,
    header_end,
)

EXIT_USAGE: Final = 2
EXIT_MODEL: Final = 3
EXIT_TOO_LONG: Final = 4
DEFAULT_MODEL_TIMEOUT: Final = 900.0
#: The omo relay accepts 256 KiB; leave room for the instructions and the ledger.
PROMPT_LIMIT: Final = 240 * 1024
EDIT_LIMIT: Final = 40
MIN_GUARD: Final = 2
#: A ledger pair is applied without the model only from this many syllables up.
LEDGER_DIRECT: Final = 3
LEDGER_HINTS: Final = 300
LEDGER_REASON: Final = "형 교정 장부"
#: A one-syllable stem is a noun ending in 로·이·가 more often than a stem plus a particle (관로).
MIN_STEM: Final = 2
MARKER: Final = "PLAUD-GLOSSARY-CORRECT"
_HANGUL: Final = re.compile(r"[가-힣]{2,}")
#: (받침 뒤, 받침 없는 뒤) 조사 짝. 긴 것을 먼저 본다(`이랑` 안의 `이`, `으로` 안의 `로`).
PARTICLES: Final = (("이랑", "랑"), ("으로", "로"), ("은", "는"), ("이", "가"), ("을", "를"), ("과", "와"))
_PARTICLE_FORMS: Final = tuple(
    sorted({form for pair in PARTICLES for form in pair}, key=len, reverse=True)
)
_RIEUL: Final = 8
_MARKS: Final = re.compile(r"[.,?!…]+$")
_ENDINGS: Final = (
    "에서의", "에서", "에게", "에는", "에도", "으로", "이랑", "에", "의", "도",
    "는", "은", "이", "가", "을", "를", "과", "와", "랑", "로",
)
_TOKEN_END: Final = r"(?=[\s.,?!…)\]\"'」』]|$)"

INSTRUCTIONS: Final = """\
너는 회의 전사본 교정자다. 음성 인식이 잘못 받아 적은 사람 이름·기관명·과제명·장비명·전문 용어만 고친다.
- 아래 '형 교정 장부'는 형이 직접 고쳐 준 낱말이다. 장부의 바른 표기를 우선으로 쓴다. 짧은 낱말은 문맥이 맞을 때만 바꾼다.
- 장부에 없어도 문맥으로 확실하면 고친다. 확신이 없으면 고치지 않는다.
- 말투·띄어쓰기·맞춤법·문장 다듬기는 하지 않는다. 다른 사람 이름으로 바꾸지 않는다.
- 'before'는 그 줄에 글자 그대로 있는 가장 짧은 낱말(어절 이하), 'after'는 바른 표기다.
- 같은 잘못이 여러 줄에 나오면 줄마다 하나씩 빠짐없이 적는다. 한 줄 안에서는 같은 낱말을 모두 고친다.
JSON 하나만 답한다: {"edits":[{"line":12,"before":"...","after":"..."}]}. 고칠 것이 없으면 {"edits":[]}.
"""


@dataclass(frozen=True, slots=True)
class Edit:
    line: int
    before: str
    after: str
    evidence: str


@dataclass(frozen=True, slots=True)
class Outcome:
    text: str
    applied: tuple[Edit, ...]
    rejected: int


@dataclass(frozen=True, slots=True)
class AnswerError(Exception):
    reason: str


Ledger = tuple[tuple[str, str], ...]


def ledger_pairs(glossary: term_correction.Glossary) -> Ledger:
    """Owner-given wrong→right pairs; a one-column row (a right spelling alone) is not a pair."""
    return tuple(
        (wrong.strip(), right.strip()) for wrong, right in glossary
        if wrong.strip() and right.strip() and wrong.strip() != right.strip()
    )


def editable_lines(markdown: str) -> dict[int, tuple[str, str]]:
    """1-based line number → (frozen prefix, words that may be edited)."""
    lines = markdown.splitlines()
    start = header_end(lines)
    editable: dict[int, tuple[str, str]] = {}
    for index, line in enumerate(lines[start:], start=start):
        prefix = editable_prefix(line)
        if prefix is not None:
            editable[index + 1] = (prefix, line[len(prefix):])
    return editable


def apply_ledger(
    markdown: str, editable: Mapping[int, tuple[str, str]], ledger: Ledger
) -> tuple[str, tuple[Edit, ...]]:
    """Long owner pairs, applied to spoken words only, before the model sees the transcript."""
    lines = markdown.splitlines(keepends=True)
    applied: list[Edit] = []
    direct = [(wrong, right) for wrong, right in ledger if len(wrong) >= LEDGER_DIRECT]
    for number, (prefix, _) in editable.items():
        words = lines[number - 1][len(prefix):]
        for wrong, right in direct:
            if wrong in words:
                words = _replace(words, wrong, right)
                applied.append(Edit(number, wrong, right, LEDGER_REASON))
        lines[number - 1] = prefix + words
    return "".join(lines), tuple(applied)


def build_prompt(ledger: Ledger, editable: Mapping[int, tuple[str, str]]) -> str:
    hints = "\n".join(f"- {wrong} → {right}" for wrong, right in ledger[:LEDGER_HINTS]) or "(없음)"
    lines = "\n".join(f"L{number}: {words}" for number, (_, words) in editable.items())
    return f"{INSTRUCTIONS}\n## 형 교정 장부\n{hints}\n\n## 전사본\n{lines}\n"


def parse_answer(answer: str) -> tuple[Edit, ...]:
    start, end = answer.find("{"), answer.rfind("}")
    if start < 0 or end < start:
        raise AnswerError("no-json")
    try:
        value: object = json.loads(answer[start : end + 1])
    except ValueError as error:
        raise AnswerError("bad-json") from error
    items = value.get("edits") if isinstance(value, dict) else None
    if not isinstance(items, list):
        raise AnswerError("no-edits")
    edits: list[Edit] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        line, before, after = item.get("line"), item.get("before"), item.get("after")
        if (
            isinstance(line, int) and not isinstance(line, bool)
            and isinstance(before, str) and isinstance(after, str)
        ):
            edits.append(Edit(line, before, after, ""))
    return tuple(edits)


def new_cores(edit: Edit) -> tuple[str, ...]:
    """The new words an edit introduces, without particles or punctuation."""
    return tuple(
        core for word in edit.after.split()
        if word not in edit.before.split() and len(core := _core(word)) >= 2
    )


def allowed(edit: Edit, words: str, known: str) -> bool:
    """The name guard: close in sound, and the original is not a right spelling the owner gave."""
    before, after = edit.before, edit.after
    if (
        not before.strip() or not after.strip() or before == after or before not in words
        or len(before) > EDIT_LIMIT or len(after) > EDIT_LIMIT
        or any(char in after for char in "\n\r[]|")
    ):
        return False
    spelled = term_correction.jamo(after)
    if term_correction.distance(term_correction.jamo(before), spelled) > max(
        term_correction.tolerance(spelled), MIN_GUARD
    ):
        return False
    return not any(run in known and run not in after for run in _HANGUL.findall(before))


def fit_particle(stem: str, particle: str) -> str:
    """The particle form that follows the stem's last syllable; anything not in a pair stays."""
    code = ord(stem[-1]) - 0xAC00 if stem else -1
    if not 0 <= code < 11172:
        return particle
    batchim = code % 28
    for with_batchim, without in PARTICLES:
        if particle in (with_batchim, without):
            if particle in ("으로", "로") and batchim == _RIEUL:
                return "로"
            return with_batchim if batchim else without
    return particle


def _replace(words: str, before: str, after: str) -> str:
    """Every `before` becomes `after`, with the particle after it fitted to the new ending."""
    mark = _MARKS.search(before)
    tail = mark.group(0) if mark is not None and after.endswith(mark.group(0)) else ""
    core_before, core_after = before[: len(before) - len(tail)], after[: len(after) - len(tail)]
    shared = next(
        (form for form in _PARTICLE_FORMS
         if core_before.endswith(form) and core_after.endswith(form)
         and len(core_after) - len(form) >= MIN_STEM),
        "",
    )
    stem = core_after[: len(core_after) - len(shared)]
    following = "|".join(form for form in _PARTICLE_FORMS)
    pattern = re.compile(re.escape(before) + f"(?:({following}){_TOKEN_END})?")

    def fitted(match: re.Match[str]) -> str:
        if shared:
            return stem + fit_particle(stem, shared) + tail + (match.group(1) or "")
        following_particle = match.group(1) or ""
        if len(after) < MIN_STEM:
            return after + following_particle
        return after + fit_particle(after, following_particle)

    return pattern.sub(fitted, words)


def correct(
    markdown: str, edits: Sequence[Edit], editable: Mapping[int, tuple[str, str]], known: str
) -> Outcome:
    lines = markdown.splitlines(keepends=True)
    applied: list[Edit] = []
    rejected = 0
    for edit in edits:
        if any(
            (done.line, done.before, done.after) == (edit.line, edit.before, edit.after)
            for done in applied
        ):
            continue
        slot = editable.get(edit.line)
        current = lines[edit.line - 1] if slot is not None else ""
        prefix = slot[0] if slot is not None else ""
        words = current[len(prefix):]
        if slot is None or not allowed(edit, words, known):
            rejected += 1
            continue
        lines[edit.line - 1] = prefix + _replace(words, edit.before, edit.after)
        applied.append(edit)
    return Outcome("".join(lines), tuple(applied), rejected)


def _core(word: str) -> str:
    bare = _MARKS.sub("", word)
    ending = next((e for e in _ENDINGS if bare.endswith(e) and len(bare) - len(e) >= 2), "")
    return bare[: len(bare) - len(ending)]


def evidence_label(edit: Edit, ledger: Ledger, raw: str) -> str:
    """Why the new word is right, read from the bytes, not from the model's own claim.

    The owner's ledger first (the pair itself, or its right spelling); failing that, the same
    transcript already spelling the word elsewhere; otherwise the model judged from context.
    """
    if edit.evidence == LEDGER_REASON or any(
        wrong in edit.before and right in edit.after for wrong, right in ledger
    ):
        return LEDGER_REASON
    cores = new_cores(edit)
    if not cores:
        return "문맥"
    rights = {right for _, right in ledger}
    if all(any(core in right or right in core for right in rights) for core in cores):
        return LEDGER_REASON
    if all(core in raw for core in cores):
        return "같은 전사 다른 곳"
    return "문맥"


def _arguments(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="glossary_correct", description=__doc__)
    parser.add_argument("--model-timeout", type=float, default=DEFAULT_MODEL_TIMEOUT)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    started = time.monotonic()
    source = os.environ if env is None else env
    try:
        args = _arguments(sys.argv[1:] if argv is None else argv)
    except SystemExit:
        return EXIT_USAGE
    raw = sys.stdin.read()
    raw_editable = editable_lines(raw)
    ledger = ledger_pairs(terms.glossary(source))
    markdown, direct = apply_ledger(raw, raw_editable, ledger)
    editable = editable_lines(markdown)
    prompt = build_prompt(ledger, editable)
    if len(prompt.encode("utf-8")) > PROMPT_LIMIT:
        print(f"{MARKER} refused reason=too-long", file=sys.stderr)
        return EXIT_TOO_LONG
    try:
        served = codex_llm.complete_served(prompt, timeout=args.model_timeout, env=source)
        edits = parse_answer(served.text)
    except (codex_llm.CodexError, AnswerError) as error:
        reason = error.reason if isinstance(error, AnswerError) else type(error).__name__
        print(f"{MARKER} failed reason={reason}", file=sys.stderr)
        return EXIT_MODEL
    known = " ".join(right for _, right in ledger)
    outcome = correct(markdown, edits, editable, known)
    raw_words = "\n".join(words for _, words in raw_editable.values())
    report = source.get(REPORT_ENV, "")
    if report:
        Path(report).write_text(json.dumps({
            "evidence": [
                {"before": edit.before, "after": edit.after,
                 "evidence": evidence_label(edit, ledger, raw_words)}
                for edit in (*direct, *outcome.applied)
            ],
            "rejected": outcome.rejected,
        }, ensure_ascii=False), encoding="utf-8")
    _ = sys.stdout.write(outcome.text)
    print(
        f"{MARKER} ok ledger={len(ledger)} ledger_applied={len(direct)} proposed={len(edits)} "
        f"applied={len(outcome.applied)} rejected={outcome.rejected} "
        f"prompt_chars={len(prompt)} answer_chars={len(served.text)} "
        f"total_secs={time.monotonic() - started:.1f} served={served.provider}/{served.model}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
