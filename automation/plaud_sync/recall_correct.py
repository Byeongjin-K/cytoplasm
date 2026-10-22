"""Recall-grounded transcript correction command for the pre-freeze hook (opt-in, off by default).

The node points `~/.hermes/plaud-sync/transcript-corrector.json` at this file. It reads the
finished transcript on stdin, looks the meeting up in the node's memory (recall), asks the
account's main model (`codex_llm`, no model named here) for a list of word edits, and keeps
only the edits the name guard allows. stdout is the transcript with those edits; the hook
diffs it against the raw bytes, so the note's change table cannot miss an edit.

Name guard (measured 2026-10-22: without it the model swapped 3 of 33 names for another
person recall knew): an edit is dropped when its jamo distance is beyond
``max(tolerance, 2)`` or when the original word itself appears in the recall evidence, which
means it is a real word or name the node already knows.

Fail-open is the hook's job: any failure here exits non-zero and the note keeps the raw
transcript. Nothing from the transcript, recall or model is written to stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation import codex_llm, term_correction  # noqa: E402
from automation.plaud_sync.transcript_corrector import (  # noqa: E402
    REPORT_ENV,
    editable_prefix,
    header_end,
)

EXIT_USAGE: Final = 2
EXIT_MODEL: Final = 3
EXIT_TOO_LONG: Final = 4
DEFAULT_MODEL_TIMEOUT: Final = 900.0
DEFAULT_RECALL_TIMEOUT: Final = 90.0
DEFAULT_RECALL_LIMIT: Final = 8
#: The omo relay accepts 256 KiB; leave room for the instructions and evidence.
PROMPT_LIMIT: Final = 240 * 1024
EDIT_LIMIT: Final = 40
MIN_GUARD: Final = 2
KEYWORDS: Final = 8
MARKER: Final = "PLAUD-RECALL-CORRECT"
_TITLE_STAMP: Final = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{4}_?")
_HANGUL: Final = re.compile(r"[가-힣]{2,}")
_KEYWORD: Final = re.compile(r"[가-힣]{3,}")
_EVIDENCE_REF: Final = re.compile(r"R(\d+)")

INSTRUCTIONS: Final = """\
너는 회의 전사본 교정자다. 음성 인식이 잘못 받아 적은 사람 이름·기관명·과제명·장비명·전문 용어만 고친다.
- 아래 '기억' 조각은 이 사람의 노트·메일에서 찾은 근거다. 근거나 문맥으로 확실할 때만 고친다. 확신이 없으면 고치지 않는다.
- 말투·띄어쓰기·맞춤법·문장 다듬기는 하지 않는다. 다른 사람 이름으로 바꾸지 않는다.
- 'before'는 그 줄에 글자 그대로 있는 가장 짧은 낱말(어절 이하), 'after'는 바른 표기다.
- 'evidence'는 근거가 된 기억 번호(예: "R2") 또는 "문맥"이다.
- 같은 잘못이 여러 줄에 나오면 줄마다 하나씩 빠짐없이 적는다. 한 줄 안에서는 같은 낱말을 모두 고친다.
JSON 하나만 답한다: {"edits":[{"line":12,"before":"...","after":"...","evidence":"R1"}]}. 고칠 것이 없으면 {"edits":[]}.
"""


@dataclass(frozen=True, slots=True)
class Memory:
    title: str
    excerpt: str


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


def recall_queries(title: str, bodies: Sequence[str]) -> tuple[str, ...]:
    """The meeting's own name, then its most frequent long words."""
    label = _TITLE_STAMP.sub("", title).replace("_", " ").strip()
    counts = Counter(word for body in bodies for word in _KEYWORD.findall(body))
    words = " ".join(word for word, _ in counts.most_common(KEYWORDS))
    return tuple(query for query in dict.fromkeys((label, words)) if query)


def search(
    recall_cli: Path, query: str, *, limit: int, timeout: float, env: Mapping[str, str]
) -> tuple[Memory, ...]:
    """One recall search; an unreachable or empty memory is simply no evidence."""
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [sys.executable, str(recall_cli), "search", query, "--json", "--limit", str(limit)],
            capture_output=True, text=True, check=False, timeout=timeout, env=dict(env),
            stdin=subprocess.DEVNULL,
        )
        payload: object = json.loads(completed.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return ()
    if not isinstance(payload, dict) or payload.get("status") != "hit":
        return ()
    rows = payload.get("results")
    memories: list[Memory] = []
    for row in rows if isinstance(rows, list) else ():
        if isinstance(row, dict):
            title, excerpt = row.get("title"), row.get("excerpt")
            if isinstance(title, str) and isinstance(excerpt, str):
                memories.append(Memory(title, excerpt))
    return tuple(memories)


def editable_lines(markdown: str) -> dict[int, tuple[str, str]]:
    """1-based line number → (frozen prefix, words the model may edit)."""
    lines = markdown.splitlines()
    start = header_end(lines)
    editable: dict[int, tuple[str, str]] = {}
    for index, line in enumerate(lines[start:], start=start):
        prefix = editable_prefix(line)
        if prefix is not None:
            editable[index + 1] = (prefix, line[len(prefix):])
    return editable


def build_prompt(memories: Sequence[Memory], editable: Mapping[int, tuple[str, str]]) -> str:
    evidence = "\n".join(
        f"R{number}. {memory.title}: {' '.join(memory.excerpt.split())}"
        for number, memory in enumerate(memories, start=1)
    ) or "(찾은 기억 없음)"
    lines = "\n".join(f"L{number}: {words}" for number, (_, words) in editable.items())
    return f"{INSTRUCTIONS}\n## 기억\n{evidence}\n\n## 전사본\n{lines}\n"


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
        evidence = item.get("evidence")
        if (
            isinstance(line, int) and not isinstance(line, bool)
            and isinstance(before, str) and isinstance(after, str)
        ):
            edits.append(Edit(line, before, after, evidence if isinstance(evidence, str) else ""))
    return tuple(edits)


def allowed(edit: Edit, words: str, known: str) -> bool:
    """The name guard: close in sound, and the original is not a word recall already knows."""
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
    return not any(
        run in known and run not in after for run in _HANGUL.findall(before)
    )


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
        lines[edit.line - 1] = prefix + words.replace(edit.before, edit.after)
        applied.append(edit)
    return Outcome("".join(lines), tuple(applied), rejected)


def evidence_label(edit: Edit, memories: Sequence[Memory]) -> str:
    found = _EVIDENCE_REF.search(edit.evidence)
    if found is not None and 1 <= int(found.group(1)) <= len(memories):
        return f"기억: {memories[int(found.group(1)) - 1].title}"
    return "문맥"


def _arguments(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="recall_correct", description=__doc__)
    parser.add_argument("--recall-cli", type=Path, required=True)
    parser.add_argument("--recall-limit", type=int, default=DEFAULT_RECALL_LIMIT)
    parser.add_argument("--recall-timeout", type=float, default=DEFAULT_RECALL_TIMEOUT)
    parser.add_argument("--model-timeout", type=float, default=DEFAULT_MODEL_TIMEOUT)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    started = time.monotonic()
    source = os.environ if env is None else env
    try:
        args = _arguments(sys.argv[1:] if argv is None else argv)
    except SystemExit:
        return EXIT_USAGE
    markdown = sys.stdin.read()
    editable = editable_lines(markdown)
    memories = tuple(
        memory
        for query in recall_queries(
            source.get("PLAUD_NOTE_TITLE", ""), [words for _, words in editable.values()]
        )
        for memory in search(
            args.recall_cli, query, limit=args.recall_limit, timeout=args.recall_timeout,
            env=source,
        )
    )
    recall_secs = time.monotonic() - started
    prompt = build_prompt(memories, editable)
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
    known = " ".join(f"{memory.title} {memory.excerpt}" for memory in memories)
    outcome = correct(markdown, edits, editable, known)
    report = source.get(REPORT_ENV, "")
    if report:
        Path(report).write_text(json.dumps({
            "evidence": [
                {"before": edit.before, "after": edit.after,
                 "evidence": evidence_label(edit, memories)}
                for edit in outcome.applied
            ],
            "rejected": outcome.rejected,
        }, ensure_ascii=False), encoding="utf-8")
    _ = sys.stdout.write(outcome.text)
    print(
        f"{MARKER} ok memories={len(memories)} proposed={len(edits)} "
        f"applied={len(outcome.applied)} rejected={outcome.rejected} "
        f"prompt_chars={len(prompt)} answer_chars={len(served.text)} "
        f"recall_secs={recall_secs:.1f} total_secs={time.monotonic() - started:.1f} "
        f"served={served.provider}/{served.model}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
