"""``python3 -m automation.obsidian_write request|status`` — the agent's note-write entry.

``request`` freezes the body, checks its [[links]] against the read-only mirror,
suggests related notes, and posts ONE standard approval card bound to the gate hash
of exactly (path, title, body). It never writes the vault: the reaction watcher does
that after the owner's ✅ on that card. Exit codes: 0 posted/pending, 2 usage or
invalid input, 3 the card could not be posted (reason printed).
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from automation.interop.approval_lifecycle import Outcome

from . import gate_binding, links
from .config import READ_ONLY_MIRROR_DIR, ObsidianWriteError
from .note_request import (
    LIVE_STATUSES,
    NoteRequest,
    NoteRequestStore,
    body_sha256,
    build_plan,
    request_key,
    validate_relpath,
)
from automation.obsidian_write.note_request_gate import (
    discord_runtime,
    owner_id_from_config,
    request_approval,
)


def _mirror() -> Path:
    return Path(os.environ.get("OBSIDIAN_MIRROR_DIR", str(READ_ONLY_MIRROR_DIR))).expanduser()


def _read_body(source: str) -> str:
    if source == "-":
        return sys.stdin.read()
    return Path(source).expanduser().read_text(encoding="utf-8")


def _new_record(args: argparse.Namespace, store: NoteRequestStore) -> NoteRequest:
    relpath = validate_relpath(args.relpath)
    plan = build_plan(relpath, _read_body(args.body_file), args.title)
    decision = gate_binding.evaluate(plan)
    index = links.build_index(_mirror())
    key = request_key(relpath.as_posix())
    for existing in store.all():
        if existing.key == key and existing.action_hash == decision.action_hash and existing.status in LIVE_STATUSES:
            return existing
    unresolved: tuple[str, ...] = ()
    related: tuple[str, ...] = ()
    source = "unavailable(mirror)"
    overwrite = False
    if index is not None:
        unresolved = links.unresolved_links(plan.body, index, own_stem=relpath.stem)
        related, source = links.related_notes(plan.title, plan.body, index, own_stem=relpath.stem)
        overwrite = index.resolves(relpath.with_suffix("").as_posix())
    record = NoteRequest(
        request_id=decision.action_hash.removeprefix("sha256:")[:16],
        key=key,
        relpath=relpath.as_posix(),
        title=plan.title,
        body_sha256=body_sha256(plan),
        action_hash=decision.action_hash,
        target_id=decision.target_id,
        created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        origin_channel_id=args.origin_channel_id,
        origin_message_id=args.origin_message_id,
        overwrite=overwrite,
        unresolved_links=unresolved,
        links_checked=index is not None,
        related=related,
        related_source=source,
    )
    store.create(record, plan)
    return record


def _print_thread_line(record: NoteRequest) -> None:
    try:
        from automation.interop.thread_pointer import approval_thread_line
    except ImportError as error:
        print(f"APPROVAL-THREAD-UNAVAILABLE request={record.request_id} err={type(error).__name__}", file=sys.stderr)
        return
    print(approval_thread_line("request", record.request_id, {
        "approval_thread_id": record.approval_thread_id,
        "approval_guild_id": record.approval_guild_id,
    }))


def cmd_request(args: argparse.Namespace) -> int:
    from automation.notice_bot import resolve_token

    store = NoteRequestStore()
    record = _new_record(args, store)
    runtime = discord_runtime(resolve_token(), owner_id_from_config(), store)
    verdict = request_approval(record, store, runtime)
    if verdict.outcome not in {Outcome.POSTED, Outcome.PENDING}:
        reason = "unknown" if verdict.reason is None else verdict.reason.value
        print(f"NOTE-REQUEST-REFUSED request={record.request_id} reason={reason}", file=sys.stderr)
        return 3
    current = store.get(record.request_id) or record
    print(
        f"REQUESTED request={current.request_id} hash={current.action_hash} "
        f"path={current.relpath} overwrite={str(current.overwrite).lower()}"
    )
    _print_thread_line(current)
    if not current.links_checked:
        print("LINKS-UNVERIFIED reason=mirror-unreadable")
    for target in current.unresolved_links:
        print(f"LINK-UNRESOLVED [[{target}]]")
    for stem in current.related:
        print(f"RELATED [[{stem}]] source={current.related_source}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    records = NoteRequestStore().all()
    chosen = [record for record in records if not args.request or record.request_id == args.request]
    for record in sorted(chosen, key=lambda item: item.created_at):
        print(
            f"NOTE-REQUEST request={record.request_id} status={record.status} path={record.relpath} "
            f"remote={record.remote_ref or '-'} sha256={record.content_sha256 or '-'} "
            f"error={record.last_error or '-'}"
        )
    return 0 if chosen else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m automation.obsidian_write")
    commands = parser.add_subparsers(dest="command", required=True)
    request = commands.add_parser("request", help="post one Obsidian note approval card")
    request.add_argument("--relpath", required=True, help="exact vault path, e.g. 000_PARA/Resource/<dir>/<name>.md")
    request.add_argument("--body-file", required=True, help="Markdown body file, or - for stdin")
    request.add_argument("--title", default=None, help="note title (default: the body's leading # H1, else the file stem)")
    request.add_argument("--origin-channel-id", default="", help="channel id of the instruction message")
    request.add_argument("--origin-message-id", default="", help="message id of the instruction message")
    request.set_defaults(handler=cmd_request)
    status = commands.add_parser("status", help="show note requests and their receipts")
    status.add_argument("--request", default="")
    status.set_defaults(handler=cmd_status)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (ObsidianWriteError, OSError, UnicodeDecodeError) as error:
        print(f"NOTE-REQUEST-ERROR {error}", file=sys.stderr)
        return 2
