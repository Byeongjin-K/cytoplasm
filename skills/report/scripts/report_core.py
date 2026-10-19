from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

_TITLE_RE: Final = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_SECTION_RE: Final = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class Note:
    path: Path
    title: str
    body: str
    modified_at: float

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.body}"


@dataclass(frozen=True, slots=True)
class SlideDeck:
    html: str
    titles: tuple[str, ...]


def _title(text: str, fallback: str) -> str:
    match = _TITLE_RE.search(text)
    return match.group(1).strip() if match else fallback


def select_notes(root: Path, *, limit: int, query: str = "") -> tuple[Note, ...]:
    if not root.is_dir():
        return ()
    needle = query.casefold().strip()
    candidates: list[Note] = []
    for path in root.rglob("*.md"):
        relative = path.relative_to(root)
        if any(part.startswith(".") for part in relative.parts) or "_weekly" in relative.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        title = _title(text, path.stem)
        note = Note(path, title, text, path.stat().st_mtime)
        if not needle or needle in note.text.casefold():
            candidates.append(note)
    ranked = sorted(candidates, key=lambda note: (-note.modified_at, note.path.as_posix()))
    return tuple(ranked[:limit])


# The model relay refuses input over 256 KiB (live QA 4, 2026-10-19: twelve whole notes were
# 262,145 bytes); the material fits a budget below it and every cut is listed in the report.
MATERIAL_BUDGET: Final = 200 * 1024
_MIN_PART: Final = 2000


def load_material(paths: tuple[Path, ...]) -> tuple[Note, ...]:
    """Notes given as files (a staged Obsidian note, for example), in the order given."""
    notes = []
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        notes.append(Note(path, _title(text, path.stem), text, path.stat().st_mtime))
    return tuple(notes)


def fit_budget(notes: tuple[Note, ...], budget: int = MATERIAL_BUDGET) -> tuple[tuple[Note, ...], tuple[str, ...]]:
    """Keep notes whole while they fit, cut the first that does not, drop the rest; say what went."""
    kept: list[Note] = []
    cuts: list[str] = []
    left = budget
    for note in notes:
        size = len(note.body.encode("utf-8"))
        if size <= left:
            kept.append(note)
            left -= size
        elif left >= _MIN_PART:
            body = note.body.encode("utf-8")[:left].decode("utf-8", errors="ignore")
            kept.append(Note(note.path, note.title, body, note.modified_at))
            cuts.append(f"{note.title}: {size:,}바이트 중 앞 {len(body.encode('utf-8')):,}바이트만 씀")
            left = 0
        else:
            cuts.append(f"{note.title}: 뺌({size:,}바이트)")
    return tuple(kept), tuple(cuts)


def build_prompt(notes: tuple[Note, ...], title: str, evidence: str = "") -> str:
    material = "\n\n".join(
        f"[노트 {index}: {note.title}]\n{note.body}"
        for index, note in enumerate(notes, start=1)
    )
    evidence_block = f"\n\n{evidence}" if evidence else ""
    instruction = (
        " Use only MATERIAL/EVIDENCE, cite [En], do not invent."
        if evidence else ""
    )
    return (
        "다음 개인 연구 노트를 근거로 한국어 보고서 초안을 작성하세요. "
        "사실을 만들지 말고, 핵심 내용·제약·다음 단계를 간결하게 서술하세요."
        f"{instruction}\n\n보고서 제목: {title}\n\nMATERIAL:\n{material}"
        f"{evidence_block}"
    )


def assemble_report(
    title: str, notes: tuple[Note, ...], draft: str, evidence_sources: str = "",
    cuts: tuple[str, ...] = (),
) -> str:
    note_titles = "\n".join(f"- {note.title}" for note in notes)
    evidence = f"\n\n## 근거\n\n{evidence_sources}" if evidence_sources else ""
    if cuts:
        evidence += "\n\n자료 줄임(모델 입력 한도): " + "; ".join(cuts)
    return (
        f"# {title}\n\n"
        "## 자료 범위\n\n"
        f"선택 노트 {len(notes)}건을 바탕으로 작성한 초안입니다.\n\n"
        "## 핵심 내용\n\n"
        f"{draft.strip()}\n\n"
        "## 근거 노트\n\n"
        f"{note_titles}{evidence}\n"
    )


def _report_sections(report: str) -> tuple[tuple[str, str], ...]:
    title = _title(report, "발표 자료")
    parts = _SECTION_RE.split(report)
    sections: list[tuple[str, str]] = [(title, "")]
    for index in range(1, len(parts), 2):
        sections.append((parts[index].strip(), parts[index + 1].strip()))
    return tuple(sections)


def render_slides(report: str) -> SlideDeck:
    sections = _report_sections(report)
    slides = "\n".join(
        "<section><h1>{}</h1><p>{}</p></section>".format(
            html.escape(title), html.escape(body).replace("\n", "<br>"),
        )
        for title, body in sections
    )
    document = f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/reveal.js@5.1.0/dist/reveal.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/reveal.js@5.1.0/dist/theme/white.css">
<title>{html.escape(sections[0][0])}</title></head>
<body><div class="reveal"><div class="slides">{slides}</div></div>
<script src="https://cdn.jsdelivr.net/npm/reveal.js@5.1.0/dist/reveal.js"></script>
<script>Reveal.initialize({{hash: true, slideNumber: true}});</script></body></html>
"""
    return SlideDeck(document, tuple(title for title, _ in sections))


def generate_script(titles: tuple[str, ...]) -> str:
    sections = ["# 발표 대본", ""]
    for index, title in enumerate(titles, start=1):
        sections.extend(
            (
                f"## 슬라이드 {index} — {title}",
                "이 슬라이드의 핵심을 설명하고, 다음 슬라이드와의 연결을 짚습니다.",
                "",
            )
        )
    return "\n".join(sections).rstrip() + "\n"


def organize_notes(root: Path, week: str) -> Path:
    notes = select_notes(root, limit=10_000)
    directory = root / "_weekly"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    index = directory / f"notes-{week}.md"
    lines = ["# 주간 노트 정리", "", "## 포함 노트", ""]
    lines.extend(f"- {note.title}" for note in notes)
    index.write_text("\n".join(lines) + "\n", encoding="utf-8")
    index.chmod(0o600)
    return index
