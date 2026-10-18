#!/usr/bin/env python3
"""Personal wiki draft gate, read-only query, cleanup, and knowledge consultation CLI."""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from importlib import import_module
from pathlib import Path
from typing import Any

wiki_evidence = import_module(".wiki_evidence", __package__) if __package__ else import_module("wiki_evidence")
wiki_gate = import_module(".wiki_gate", __package__) if __package__ else import_module("wiki_gate")
wiki_governed = import_module(".wiki_governed", __package__) if __package__ else import_module("wiki_governed")
wiki_store = import_module(".wiki_store", __package__) if __package__ else import_module("wiki_store")

WIKI_ROOT = Path(os.environ.get("WIKI_ROOT", "~/wiki")).expanduser()

TEMPLATE_HEADINGS = {
    "decision": (
        "## Context",
        "## Decision",
        "## Rationale & Trade-offs",
        "## What would change my mind",
    ),
    "principle": ("## Trigger", "## Rule", "## Exceptions"),
    "preference": ("## Preference", "## Boundary"),
}


def template_warnings(kind: str, body: str) -> list[str]:
    """Per-kind body-template SOFT warnings (guided, never blocks a draft)."""
    present = {line.strip() for line in body.splitlines()}
    return [
        f"TEMPLATE-WARN kind={kind} 누락 헤딩: {heading}"
        for heading in TEMPLATE_HEADINGS.get(kind, ())
        if heading not in present
    ]


def _with_twin_flags(meta: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    updated = dict(meta)
    for key in wiki_store.TWIN_KEYS:
        value = getattr(args, key, None)
        if value is None:
            continue
        updated[key] = _tags(value) if key in wiki_store.LIST_TWIN_KEYS else value
    return updated


def _tags(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _read_body(args: argparse.Namespace) -> str | None:
    if args.body is not None:
        return args.body
    if args.body_file:
        return Path(args.body_file).read_text(encoding="utf-8")
    if args.stdin:
        return sys.stdin.read()
    return None


def _summary(record: dict[str, Any]) -> str:
    meta, body = wiki_store.parse_note(record["note_text"])
    preview = "\n".join(body.strip().splitlines()[:5])
    return (
        f"DRAFT-CREATED id={record['id']} action={record['action']} "
        f"slug={record['slug']} sha256={record['sha256']}\n"
        f"제목: {meta['title']}\n"
        f"태그: {', '.join(meta['tags']) or '(없음)'}\n"
        f"링크: {', '.join(meta['links']) or '(없음)'}\n"
        f"본문 미리보기:\n{preview}\n"
        f"저장하려면 DM으로 `저장 {record['id']}` 라고 답장하세요. "
        f"취소는 `취소 {record['id']}`."
    )


def _compose(args: argparse.Namespace, pack: object | None) -> tuple[str, str, str, str, dict[str, Any], object | None]:
    body = _read_body(args)
    now = wiki_store.utc_now()
    if args.edit:
        note = wiki_store.load_note(WIKI_ROOT, args.edit)
        meta = dict(note.meta)
        if args.title:
            meta["title"] = args.title
        if args.tags is not None:
            meta["tags"] = _tags(args.tags)
        if args.links is not None:
            meta["links"] = _tags(args.links)
        meta["updated"] = now
        meta = _with_twin_flags(meta, args)
        final_body = body if body is not None else note.body
        final_body, pack = wiki_evidence.prepare_draft(
            str(meta["title"]), list(meta["tags"]), final_body, bool(args.with_evidence), pack)
        return "edit", args.edit, wiki_store.compose_note(meta, final_body), final_body, meta, pack
    if not args.title:
        raise wiki_store.SchemaError(["title: 새 노트에는 --title이 필요합니다"])
    slug = args.slug or wiki_store.slugify(args.title)
    meta = _with_twin_flags(
        {
            "title": args.title,
            "tags": _tags(args.tags or ""),
            "created": now,
            "updated": now,
            "links": _tags(args.links or ""),
        },
        args,
    )
    final_body = body or ""
    final_body, pack = wiki_evidence.prepare_draft(
        str(meta["title"]), list(meta["tags"]), final_body, bool(args.with_evidence), pack)
    return "create", slug, wiki_store.compose_note(meta, final_body), final_body, meta, pack


def cmd_draft(args: argparse.Namespace, evidence_pack: object | None = None) -> int:
    action, slug, note_text, final_body, meta, pack = _compose(args, evidence_pack)
    record = wiki_gate.create_draft(action, slug, note_text, args.channel_id)
    if pack is not None:
        wiki_evidence.write_sidecar(wiki_gate.GATE_DIR, str(record["id"]), pack)
    print(_summary(record))
    kind = meta.get("kind")
    if isinstance(kind, str):
        for warning in template_warnings(kind, final_body):
            print(warning)
    return 0


def cmd_confirm(args: argparse.Namespace) -> int:
    draft = wiki_gate.load_draft(args.draft)
    if args.injection_file:
        ref = wiki_gate.confirm_via_injection(draft, Path(args.injection_file))
        method = "signed_injection_e2e"
    else:
        if os.environ.get("E2E_TEST_MODE"):
            raise wiki_gate.GateError(
                "E2E_TEST_MODE인데 --injection-file이 없음 — 모호한 모드 거부", 3
            )
        path = wiki_gate.resolve_reaction(draft)
        if path is not None:
            message_id = draft.get("confirm_message_id") or draft.get("message_id")
            print(f"SAVED path={path} method=reaction ref=reaction:{message_id}")
            return 0
        ref = wiki_gate.confirm_via_owner_scan(draft)
        method = "dm_text"
    path = wiki_gate.apply_draft(WIKI_ROOT, draft, ref, method)
    print(f"SAVED path={path} method={method} ref={ref}")
    return 0


def cmd_write(args: argparse.Namespace) -> int:
    """Owner-command write without a card (owner 2026-10-18 Q233); the YooDori gate has
    already confirmed the origin is the owner's own recent message."""
    action, slug, note_text, _body, _meta, _pack = _compose(args, None)
    wiki_store.parse_note(note_text)
    WIKI_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    wiki_gate.GATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = wiki_store.note_path(WIKI_ROOT, slug)
    if action == "create" and path.exists():
        raise wiki_gate.GateError(f"이미 존재하는 노트: {slug} (수정은 --edit 사용)", 2)
    if action == "edit" and not path.exists():
        raise wiki_gate.GateError(f"수정 대상 노트 없음: {slug}", 2)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(note_text, encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)
    ref = f"discord:{args.origin_channel_id}/{args.origin_message_id}"
    digest = hashlib.sha256(note_text.encode("utf-8")).hexdigest()
    wiki_gate._append_audit({"action": action, "sha256": digest, "slug": slug,
                             "channel_id": args.origin_channel_id}, ref, "owner_command")
    print(f"SAVED path={path} slug={slug} action={action} method=owner_command ref={ref}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    print(wiki_store.load_note(WIKI_ROOT, args.slug).path.read_text(encoding="utf-8"))
    return 0


def cmd_discard(args: argparse.Namespace) -> int:
    wiki_gate.discard_draft(args.draft)
    print(f"DISCARDED draft={args.draft}")
    return 0


def cmd_list_drafts(_args: argparse.Namespace) -> int:
    for record in wiki_gate.list_drafts():
        print(
            f"DRAFT id={record['id']} status={record['status']} action={record['action']} "
            f"slug={record['slug']} created={record['created']}"
        )
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    hits = 0
    for note in wiki_store.query_notes(WIKI_ROOT, args.term or "", args.tag):
        hits += 1
        print(
            f"HIT slug={note.slug} title={note.meta['title']} "
            f"tags={','.join(note.meta['tags'])} path={note.path}"
        )
    print(f"QUERY-DONE hits={hits}")
    return 0


def cmd_consult(args: argparse.Namespace, evidence_pack: object | None = None) -> int:
    return wiki_evidence.command_consult(WIKI_ROOT, args, evidence_pack)


def cmd_backlinks(args: argparse.Namespace) -> int:
    count = 0
    for note in wiki_store.backlinks(WIKI_ROOT, args.slug):
        count += 1
        print(f"BACKLINK slug={note.slug} title={note.meta['title']} path={note.path}")
    print(f"BACKLINKS-DONE target={args.slug} count={count}")
    return 0


def cmd_cleanup_suggest(_args: argparse.Namespace) -> int:
    suggestions = wiki_store.cleanup_suggestions(WIKI_ROOT)
    for line in suggestions:
        print(line)
    print(f"SUGGESTIONS count={len(suggestions)} (적용은 반드시 초안→`저장` 확인 게이트로)")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    wiki_store.parse_note(Path(args.file).read_text(encoding="utf-8"))
    print(f"VALID {args.file}")
    return 0


def cmd_sign(args: argparse.Namespace) -> int:
    draft = wiki_gate.load_draft(args.draft)
    wiki_gate.sign_injection(
        draft, Path(args.out), args.user_id or None, args.channel_id or None,
        args.forge_signature,
    )
    print(f"SIGNED draft={args.draft} out={args.out} forged={args.forge_signature}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wiki", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    draft = sub.add_parser("draft", help="초안 생성 (위키에는 아무것도 쓰지 않음)")
    draft.add_argument("--title")
    draft.add_argument("--tags")
    draft.add_argument("--links")
    draft.add_argument("--slug")
    draft.add_argument("--edit", metavar="SLUG", help="기존 노트 수정 초안")
    draft.add_argument("--body")
    draft.add_argument("--body-file")
    draft.add_argument("--stdin", action="store_true")
    draft.add_argument("--channel-id", default="dm")
    draft.add_argument("--kind", help="twin: decision|principle|preference|note")
    draft.add_argument("--authority", help="twin: strict|default|advisory")
    draft.add_argument("--provenance", help="twin: stated|observed|inferred")
    draft.add_argument("--status", help="twin: active|superseded|archived")
    draft.add_argument("--review-after", metavar="YYYY-MM-DD", help="twin 재검토 기한")
    draft.add_argument("--supersedes", metavar="SLUG", help="twin: 대체하는 노트")
    draft.add_argument("--entity", help="twin v2: 노트가 다루는 고유명사 (쉼표 구분)")
    draft.add_argument("--relations", help="twin v2: '<술어>:<대상>' 쉼표 구분")
    draft.add_argument("--event-date", metavar="YYYY-MM-DD", help="twin v2: 사건 당일")
    draft.add_argument("--with-evidence", action="store_true")
    draft.set_defaults(func=cmd_draft)

    confirm = sub.add_parser("confirm", help="소유자 확인 검증 후에만 저장")
    confirm.add_argument("--draft", required=True)
    confirm.add_argument("--injection-file", default="")
    confirm.set_defaults(func=cmd_confirm)

    write = sub.add_parser("write", help="형 명령 쓰기 — 유돌 게이트가 형 메시지를 확인한 뒤에만(카드 없음)")
    for flag in ("--title", "--tags", "--links", "--slug", "--body", "--body-file"):
        write.add_argument(flag)
    write.add_argument("--edit", metavar="SLUG")
    write.add_argument("--origin-channel-id", required=True)
    write.add_argument("--origin-message-id", required=True)
    write.set_defaults(func=cmd_write, stdin=False, with_evidence=False)

    show = sub.add_parser("show", help="노트 한 개 원문 (읽기 전용)")
    show.add_argument("slug")
    show.set_defaults(func=cmd_show)

    discard = sub.add_parser("discard", help="초안 폐기")
    discard.add_argument("--draft", required=True)
    discard.set_defaults(func=cmd_discard)

    sub.add_parser("list-drafts", help="초안 목록").set_defaults(func=cmd_list_drafts)

    query = sub.add_parser("query", help="제목/태그/본문 검색 (읽기 전용)")
    query.add_argument("term", nargs="?")
    query.add_argument("--tag")
    query.set_defaults(func=cmd_query)

    consult = sub.add_parser("consult", help="지식 파사드 기반 판단 컨설트 (읽기 전용)")
    consult.add_argument("text")
    consult.add_argument("--limit", type=int, default=8)
    consult.add_argument("--json", action="store_true")
    consult.set_defaults(func=cmd_consult)

    back = sub.add_parser("backlinks", help="이 노트를 참조하는 노트 (읽기 전용)")
    back.add_argument("slug")
    back.set_defaults(func=cmd_backlinks)

    sub.add_parser("cleanup-suggest", help="주간 정리 제안 (읽기 전용)").set_defaults(
        func=cmd_cleanup_suggest
    )

    validate = sub.add_parser("validate", help="노트 파일 스키마 검증")
    validate.add_argument("--file", required=True)
    validate.set_defaults(func=cmd_validate)

    sign = sub.add_parser("sign", help="E2E 전용: 서명된 주입 승인 생성")
    sign.add_argument("--draft", required=True)
    sign.add_argument("--out", required=True)
    sign.add_argument("--user-id", default="")
    sign.add_argument("--channel-id", default="")
    sign.add_argument("--forge-signature", action="store_true")
    sign.set_defaults(func=cmd_sign)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command in {"draft", "confirm", "discard", "sign", "write"}:
        refusal = wiki_governed.refusal(Path(__file__))
        if refusal:
            print(refusal, file=sys.stderr)
            return 3
    try:
        return int(args.func(args))
    except wiki_store.SchemaError as error:
        print("SCHEMA-REJECTED 저장/초안 거부:", file=sys.stderr)
        for line in error.errors:
            print(f"  - {line}", file=sys.stderr)
        print(wiki_store.SCHEMA_GUIDE, file=sys.stderr)
        return 2
    except wiki_gate.GateError as error:
        print(f"GATE-REFUSED {error}", file=sys.stderr)
        return error.exit_code
    except FileNotFoundError as error:
        print(f"GATE-REFUSED 파일 없음: {error.filename}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
