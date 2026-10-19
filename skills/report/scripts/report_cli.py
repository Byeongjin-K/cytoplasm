#!/usr/bin/env python3
"""Private command surface for !report, !slides, and !script."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from importlib import import_module
from pathlib import Path
from typing import Protocol, cast

_SCRIPT_DIR = Path(__file__).resolve().parent
if __package__ in (None, ""):
    sys.path.insert(0, str(_SCRIPT_DIR))
    report_core = import_module("report_core")
    report_llm = import_module("report_llm")
    report_knowledge = import_module("report_knowledge")
else:
    report_core = import_module(".report_core", __package__)
    report_llm = import_module(".report_llm", __package__)
    report_knowledge = import_module(".report_knowledge", __package__)

if __package__ in (None, ""):
    report_governed = import_module("report_governed")
else:
    report_governed = import_module(".report_governed", __package__)


def _private_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def _output_path(directory: Path, kind: str, suffix: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return directory / f"{kind}-{stamp}{suffix}"


def _write_private(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)


def _publish_report(output: Path, artifact_title: str, period_date: date | None) -> str | None:
    try:
        drive_outputs = import_module("automation.drive_outputs")
    except ImportError:
        print("DRIVE-PUBLISH-SKIP reason=ImportError", file=sys.stderr)
        return None
    result = drive_outputs.publish_best_effort(
        "report", "주간연구동향", [(output, artifact_title)], on=period_date
    )
    return result.links[0] if result is not None and result.links else None


class _Item(Protocol):
    id: str
    store: str
    source_type: str
    ref: str
    title: str
    doc_date: str | None
    date_basis: str
    score: float | None
    grounded: bool | None
    authority: str | None
    expired: bool | None
    content: str
    sha256: str


class _Query(Protocol):
    text: str
    purpose: str
    sources: frozenset[str]
    tags: frozenset[str]
    limit: int
    caller: str


class _Pack(Protocol):
    version: str
    query: _Query
    verdict: str
    items: tuple[_Item, ...]
    layers: dict[str, str]
    notes: tuple[str, ...]


class _CitationReport(Protocol):
    text: str
    stripped_ids: tuple[str, ...]


class _Renderer(Protocol):
    def render_citations(self, pack: _Pack, style: str) -> str: ...
    def render_verdict(self, pack: _Pack) -> str: ...
    def validate_citations(self, text: str, pack: _Pack) -> _CitationReport: ...


def _rendering() -> _Renderer:
    module = report_knowledge.module("automation.knowledge.render")
    return cast(_Renderer, cast(object, module))


def _evidence_block(pack: _Pack) -> str:
    if pack.verdict != "hit":
        try:
            return str(_rendering().render_citations(pack, "sources"))
        except ImportError:
            return "EVIDENCE: unavailable — 근거 수집 불가"
    records = [
        f"[{item.id}] store={item.store}; ref={item.ref}; "
        f"date={item.doc_date or '날짜 미상'}; content={item.content}"
        for item in pack.items
    ]
    return "EVIDENCE:\n" + "\n".join(records)


def _finalize_evidence(draft: str, pack: _Pack) -> tuple[str, str]:
    try:
        rendering = _rendering()
        report = rendering.validate_citations(draft, pack)
        verdict = rendering.render_verdict(pack)
        sources = rendering.render_citations(pack, "sources")
        print(f"CITATIONS-STRIPPED count={len(report.stripped_ids)}", file=sys.stderr)
        return "\n\n".join(part for part in (verdict, report.text) if part), str(sources)
    except ImportError:
        message = "근거 수집 불가 — 지식 파사드를 불러오지 못했지만 생성을 계속함"
        return f"{message}\n\n{draft.strip()}", ""


def _pack_dict(pack: _Pack) -> dict[str, object]:
    query = pack.query
    return {
        "version": pack.version,
        "query": {
            "text": query.text, "purpose": query.purpose,
            "sources": sorted(query.sources), "tags": sorted(query.tags),
            "limit": query.limit, "caller": query.caller,
        },
        "verdict": pack.verdict,
        "items": [
            {
                "id": item.id, "store": item.store, "source_type": item.source_type,
                "ref": item.ref, "title": item.title, "doc_date": item.doc_date,
                "date_basis": item.date_basis, "score": item.score,
                "grounded": item.grounded, "authority": item.authority,
                "expired": item.expired,
                "content": item.content, "sha256": item.sha256,
            }
            for item in pack.items
        ],
        "layers": pack.layers,
        "notes": list(pack.notes),
    }


def _report(args: argparse.Namespace, evidence_pack: object | None = None) -> int:
    material_files = getattr(args, "material_file", None) or []
    if material_files:
        notes = report_core.load_material(tuple(Path(p).expanduser() for p in material_files))
    else:
        notes = report_core.select_notes(
            Path(args.notes_root).expanduser(), limit=args.limit, query=args.query
        )
    notes, cuts = report_core.fit_budget(notes)
    if not notes:
        print("자료 부족: 선택 조건에 맞는 노트가 없습니다.")
        return 0
    title = args.title or "연구 노트 보고서"
    pack = cast(_Pack | None, evidence_pack)
    if args.with_evidence and pack is None:
        material = "\n\n".join(note.text for note in notes)
        pack = cast(_Pack, report_knowledge.collect(title, args.query, material))
    prompt_evidence = _evidence_block(pack) if pack is not None else ""
    draft = (
        Path(args.response_file).read_text(encoding="utf-8")
        if args.response_file
        else report_llm.generate(report_core.build_prompt(notes, title, prompt_evidence))
    )
    sources = ""
    if pack is not None:
        draft, sources = _finalize_evidence(draft, pack)
    output = _output_path(
        _private_directory(Path(args.outputs_root).expanduser()), "report", ".md"
    )
    _write_private(output, report_core.assemble_report(title, notes, draft, sources, cuts))
    if pack is not None:
        _write_private(
            output.with_suffix(".evidence.json"),
            json.dumps(_pack_dict(pack), ensure_ascii=False, indent=2) + "\n",
        )
    link = _publish_report(output, "주간연구동향", args.period_date)
    print(
        f"REPORT-CREATED path={output} drive={link} notes={len(notes)}"
    )
    return 0


def _evidence(args: argparse.Namespace) -> int:
    notes = report_core.select_notes(
        Path(args.notes_root).expanduser(), limit=args.limit, query=args.query
    )
    title = args.title or "연구 노트 보고서"
    material = "\n\n".join(note.text for note in notes)
    pack = cast(_Pack, report_knowledge.collect(title, args.query, material))
    if args.json:
        print(json.dumps(
            {"evidence_count": len(pack.items), "layers": pack.layers},
            ensure_ascii=False, sort_keys=True,
        ))
    else:
        print(f"EVIDENCE verdict={pack.verdict} count={len(pack.items)}")
        try:
            print(_rendering().render_citations(pack, "sources"))
        except ImportError:
            print("근거 수집 불가")
    return 0


def _slides(args: argparse.Namespace) -> int:
    report = Path(args.report).expanduser().read_text(encoding="utf-8")
    deck = report_core.render_slides(report)
    output = _output_path(_private_directory(Path(args.outputs_root).expanduser()), "slides", ".html")
    _write_private(output, deck.html)
    link = _publish_report(output, "발표슬라이드", args.period_date)
    print(f"SLIDES-CREATED path={output} drive={link} slides={len(deck.titles)}")
    return 0


def _script(args: argparse.Namespace) -> int:
    report = Path(args.report).expanduser().read_text(encoding="utf-8")
    titles = report_core.render_slides(report).titles
    output = _output_path(_private_directory(Path(args.outputs_root).expanduser()), "script", ".md")
    _write_private(output, report_core.generate_script(titles))
    link = _publish_report(output, "발표스크립트", args.period_date)
    print(f"SCRIPT-CREATED path={output} drive={link} slides={len(titles)}")
    return 0


def _organize(args: argparse.Namespace) -> int:
    now = datetime.now(timezone.utc).isocalendar()
    week = f"{now.year}-W{now.week:02d}"
    index = report_core.organize_notes(Path(args.notes_root).expanduser(), week)
    print(f"NOTES-ORGANIZED path={index}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="report")
    commands = parser.add_subparsers(dest="command", required=True)
    report = commands.add_parser("report")
    report.add_argument("--notes-root", default="~/notes")
    report.add_argument("--outputs-root", default="~/outputs")
    report.add_argument("--query", default="")
    report.add_argument("--material-file", action="append", default=[],
                        help="note file to use as material (repeatable); replaces the notes folder")
    report.add_argument("--limit", type=int, default=12)
    report.add_argument("--title", default="")
    report.add_argument("--response-file", default="")
    report.add_argument("--with-evidence", action="store_true")
    report.add_argument("--period-date", type=date.fromisoformat, default=None)
    report.set_defaults(func=_report)
    evidence = commands.add_parser("evidence")
    evidence.add_argument("--notes-root", default="~/notes")
    evidence.add_argument("--query", default="")
    evidence.add_argument("--limit", type=int, default=12)
    evidence.add_argument("--title", default="")
    evidence.add_argument("--json", action="store_true")
    evidence.set_defaults(func=_evidence)
    slides = commands.add_parser("slides")
    slides.add_argument("--report", required=True)
    slides.add_argument("--outputs-root", default="~/outputs")
    slides.add_argument("--period-date", type=date.fromisoformat, default=None)
    slides.set_defaults(func=_slides)
    script = commands.add_parser("script")
    script.add_argument("--report", required=True)
    script.add_argument("--slides", default="")
    script.add_argument("--outputs-root", default="~/outputs")
    script.add_argument("--period-date", type=date.fromisoformat, default=None)
    script.set_defaults(func=_script)
    organize = commands.add_parser("organize")
    organize.add_argument("--notes-root", default="~/notes")
    organize.set_defaults(func=_organize)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command in {"report", "slides", "script"}:
            message = report_governed.refusal(Path(__file__))
            if message:
                print(message, file=sys.stderr)
                return 3
        return int(args.func(args))
    except (OSError, report_llm.LlmInvocationError) as error:
        print(f"REPORT-REFUSED {error.__class__.__name__}: {error}"[:400], file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
