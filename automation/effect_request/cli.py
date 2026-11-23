"""``request drive-save|command``, owner-command ``save-now``, and ``status``.

``request`` freezes what will run, posts ONE standard approval card in the request's own
thread and prints the ``APPROVAL-THREAD`` line the reply must cite. It never runs the
effect: the reaction watcher does that after the owner's ✅. Exit codes: 0 posted or
pending, 2 usage or invalid input, 3 the card could not be posted, 4 refused (owner-only
action or an effect with a dedicated path — the reason and the right command are printed).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import re
import json
import secrets
import shlex
import sys
import unicodedata
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Final

from automation.interop.approval_lifecycle import ApprovalSurfaceError, Outcome

from automation.effect_request.gate import discord_runtime, owner_id_from_config, request_approval
from .model import (
    COMMAND,
    DRIVE_SAVE,
    LIVE_STATUSES,
    EffectRequest,
    EffectRequestError,
    EffectRequestStore,
    action_hash,
    canonical,
    file_sha256,
)
from . import render
from .policy import refusal

if TYPE_CHECKING:
    from automation.drive_taxonomy import Category

EXIT_REFUSED: Final = 4
DEFAULT_KIND: Final = "doctype"
DEFAULT_TIMEOUT: Final = 600
MAX_TIMEOUT: Final = 3600
#: Files one drive-save card may carry (issue #241); the 1900-character card cap may refuse fewer.
MAX_FILES: Final = 20
#: Rounds an owner-command save tries the failed files of a batch again (owner Q549).
SAVE_NOW_ROUNDS: Final = 3


def _summary(raw: str) -> str:
    text = " ".join(unicodedata.normalize("NFC", raw).split())
    if not text:
        raise EffectRequestError("--summary 가 비어 있다 — 카드 제목이 된다")
    return text[:120]


def _sources(args: argparse.Namespace) -> tuple[list[Path], list[str | None]]:
    # The parser appends; a caller that builds the namespace itself may still pass one value.
    raw_files = [args.file] if isinstance(args.file, str) else list(args.file or [])
    raw_titles = [args.title] if isinstance(args.title, str) else list(args.title or [])
    files = [Path(item).expanduser() for item in raw_files]
    titles: list[str | None] = list(raw_titles)
    if not 1 <= len(files) <= MAX_FILES:
        raise EffectRequestError(f"--file 은 1~{MAX_FILES}개 — 더 많으면 나눠서 요청한다")
    if titles and len(titles) != len(files):
        raise EffectRequestError("--title 은 주지 않거나 --file 마다 하나씩 같은 순서로 준다")
    for source in files:
        if not source.is_file():
            raise EffectRequestError(f"파일 없음: {source}")
    return files, titles or [None] * len(files)


def _title(raw: str | None, source: Path) -> str:
    title = unicodedata.normalize("NFC", raw or source.stem).strip()
    if not title or "/" in title:
        raise EffectRequestError(f"파일 제목이 비었거나 / 가 있다: {source.name}")
    return title


def _drive_payload(args: argparse.Namespace, request_id: str, store: EffectRequestStore) -> dict[str, object]:
    """One file into the standard tree keeps the v1 payload; several files or a folder id use v2.

    v1 is the payload every stored card and action hash was made from, so a one-file request
    without ``--folder`` still builds it byte for byte (issue #241 keeps old cards replayable).
    """
    files, titles = _sources(args)
    folder = getattr(args, "folder", "")
    if folder and args.project:
        raise EffectRequestError("--folder 와 --project 는 함께 쓰지 않는다 — 폴더 id 가 위치를 정한다")
    if len(files) == 1 and not folder:
        return _tree_single(args, files[0], titles[0], request_id, store)
    return _batch_payload(args, files, titles, request_id, store)


def _tree_place(args: argparse.Namespace) -> tuple[Category, date, tuple[str, ...]]:
    from automation.drive_taxonomy import TaxonomyError, category, folder_parts

    try:
        selected = category(args.kind)
        if selected.gate_only or selected.skill_owned:
            raise EffectRequestError(f"kind={args.kind} 는 전용 경로로만 저장한다")
        on = date.fromisoformat(args.on) if args.on else date.today()
        parts = folder_parts(args.kind, on.year, project=args.project or None)
    except (TaxonomyError, ValueError) as error:
        raise EffectRequestError(f"Drive 위치를 정할 수 없다: {error}") from error
    return selected, on, parts


def _tree_name(selected: Category, on: date, parts: tuple[str, ...], title: str, suffix: str) -> str:
    from automation.drive_taxonomy import TaxonomyError, artifact_name, ensure_depth, period_key

    try:
        drive_name = artifact_name(period_key(selected.periodicity, on), title, suffix)
        ensure_depth((*parts, drive_name))
    except (TaxonomyError, ValueError) as error:
        raise EffectRequestError(f"Drive 위치를 정할 수 없다: {error}") from error
    return drive_name


def _tree_single(
    args: argparse.Namespace, source: Path, raw_title: str | None, request_id: str, store: EffectRequestStore,
) -> dict[str, object]:
    selected, on, parts = _tree_place(args)
    title = _title(raw_title, source)
    drive_name = _tree_name(selected, on, parts, title, source.suffix)
    frozen = store.freeze_file(request_id, source)
    digest, size = file_sha256(frozen)
    return {
        "kind": args.kind, "title": title, "project": args.project or "", "on": on.isoformat(),
        "folder_parts": list(parts), "drive_name": drive_name, "frozen_path": str(frozen),
        "sha256": digest, "size": size, "sharing": "owner-only",
    }


def _batch_payload(
    args: argparse.Namespace, files: list[Path], titles: list[str | None], request_id: str,
    store: EffectRequestStore,
) -> dict[str, object]:
    """v2: every file frozen and listed in order; the action hash binds the whole list and place."""
    place: dict[str, object]
    if getattr(args, "folder", ""):
        from automation.drive_outputs import folder_target

        try:
            target = folder_target(args.folder)
        except Exception as error:  # noqa: BLE001 - any lookup or permission doubt refuses the request
            raise EffectRequestError(
                f"Drive 폴더를 쓸 수 없다(형만 볼 수 있는 폴더여야 한다): {type(error).__name__}: {error}"[:300]
            ) from error
        place = {"mode": "folder", "folder_id": target.folder_id, "folder_name": target.name,
                 "folder_path": list(target.path)}
        names = [f"{_title(raw, source)}{source.suffix}" for source, raw in zip(files, titles, strict=True)]
    else:
        selected, on, parts = _tree_place(args)
        place = {"mode": "tree", "kind": args.kind, "project": args.project or "", "on": on.isoformat(),
                 "folder_parts": list(parts)}
        names = [_tree_name(selected, on, parts, _title(raw, source), source.suffix)
                 for source, raw in zip(files, titles, strict=True)]
    if len(set(names)) != len(names):
        raise EffectRequestError("같은 Drive 이름이 둘 이상이다 — --title 로 이름을 다르게 준다")
    items: list[dict[str, object]] = []
    for index, (source, raw, name) in enumerate(zip(files, titles, names, strict=True)):
        frozen = store.freeze_file(f"{request_id}/{index + 1}", source)
        digest, size = file_sha256(frozen)
        items.append({"title": _title(raw, source), "drive_name": name, "frozen_path": str(frozen),
                      "sha256": digest, "size": size})
    return {"version": 2, **place, "files": items, "sharing": "owner-only"}


#: Characters the YooDori gate refuses in a raw SSH command (``yoodori_read_gate._SHELL``) plus quotes.
_GATE_REFUSED: Final = re.compile(r"[;&|`$<>\\'\"]")
#: Statuses a repeated tree request answers with instead of a new card: pending, saved, or cancelled.
_TREE_KEPT: Final = LIVE_STATUSES | {"done", "cancelled"}


def _tree_record(args: argparse.Namespace, store: EffectRequestStore, summary: str) -> EffectRequest:
    """``drive-save --manifest``: one job (owner message + parent folder + file list) is one card.

    The key is the origin, the folder and the manifest digest, so a retry of the same job
    finds its card (or its result) instead of raising another; a changed list is a new job.
    """
    from automation.drive_outputs import TreeWriter

    from .tree import folders_of, manifest_digest, read_manifest

    if args.file or args.title or args.project or args.on or args.kind != DEFAULT_KIND:
        raise EffectRequestError("--manifest 는 --folder 와 --summary 만 함께 받는다(파일은 매니페스트에)")
    if not getattr(args, "folder", ""):
        raise EffectRequestError("--manifest 는 --folder <부모 폴더 id> 가 필요하다")
    entries = read_manifest(Path(args.manifest).expanduser())
    digest = manifest_digest(entries)
    key_basis = canonical({"effect": DRIVE_SAVE, "folder": args.folder, "manifest": digest,
                           "origin": [args.origin_channel_id, args.origin_message_id]})
    key = "effect:tree:" + hashlib.sha256(key_basis.encode("utf-8")).hexdigest()[:24]
    earlier = [existing for existing in store.all() if existing.key == key]
    for existing in earlier:
        if existing.status in _TREE_KEPT:
            return existing
    # The id follows the key (and how many earlier tries failed), so two copies of the same
    # request that both got here race for one id and the store keeps the first: one card.
    request_id = hashlib.sha256(f"{key}#{len(earlier)}".encode()).hexdigest()[:16]
    writer = TreeWriter()
    try:
        target = writer.parent(args.folder)
        scan = writer.scan(target.folder_id, folders_of(entries),
                           [(entry.path, entry.sha256, entry.size) for entry in entries])
    except Exception as error:  # noqa: BLE001 - any lookup or permission doubt refuses the request
        raise EffectRequestError(
            f"Drive 폴더를 쓸 수 없다(형만 볼 수 있는 폴더여야 한다): {type(error).__name__}: {error}"[:300]
        ) from error
    skipped = set(scan.unchanged) | {path for path, _ in scan.overwrite}
    todo = tuple(entry for entry in entries if entry.path not in skipped)
    by_path = {entry.path: entry for entry in entries}
    overwrite = [{"path": path, "folder_id": folder_id, "staged": by_path[path].source.name,
                  "title": Path(by_path[path].name).stem} for path, folder_id in scan.overwrite]
    if not todo:
        lines = "; ".join(f"{item['path']} → {overwrite_command(item)}" for item in overwrite)
        raise EffectRequestError(
            f"올릴 파일이 없다 — 같은 내용으로 있음 {len(scan.unchanged)}개, 덮어쓸 파일 {len(overwrite)}개"
            + (f"(덮어쓸 파일은 하나씩 카드로: {lines})" if lines else ""))
    seed = f"{request_id}-{secrets.token_hex(4)}"
    folders = folders_of(todo)
    items: list[dict[str, object]] = []
    try:
        for index, entry in enumerate(todo, start=1):
            frozen = store.freeze_file(f"{seed}/{index}", entry.source)
            if file_sha256(frozen) != (entry.sha256, entry.size):
                raise EffectRequestError(f"sha256 이 매니페스트와 다르다: {entry.path} — 카드를 올리지 않았다")
            items.append({"path": entry.path, "folder": entry.folder, "drive_name": entry.name,
                          "frozen_path": str(frozen), "sha256": entry.sha256, "size": entry.size})
        payload: dict[str, object] = {
            "version": 3, "mode": "tree", "folder_id": target.folder_id, "folder_name": target.name,
            "folder_path": list(target.path), "manifest_sha256": digest, "folders": list(folders),
            "existing_folders": [folder for folder in folders if folder in scan.existing],
            "files": items, "unchanged": sorted(scan.unchanged), "overwrite": overwrite, "sharing": "owner-only",
        }
        record = EffectRequest(
            request_id=request_id, key=key, effect=DRIVE_SAVE, summary=summary, payload=payload,
            action_hash=action_hash(DRIVE_SAVE, summary, payload),
            created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            origin_channel_id=args.origin_channel_id, origin_message_id=args.origin_message_id,
        )
        _postable(record)
        if len(render.render_effect_list(record)) > render.MAX_LIST_MESSAGES:
            raise EffectRequestError(
                f"목록 글이 {render.MAX_LIST_MESSAGES}개를 넘는다 — 나눠서 요청한다(카드는 올리지 않았다)")
    except EffectRequestError:
        shutil.rmtree(store.frozen_dir(seed), ignore_errors=True)
        raise
    kept = store.create_if_absent(record)
    if kept is not record:
        shutil.rmtree(store.frozen_dir(seed), ignore_errors=True)
    return kept


def overwrite_command(item: dict[str, object]) -> str:
    """The gate request that asks for a same-name file's overwrite on its own card, keeping its tree name."""
    title = str(item["title"])
    # The YooDori gate refuses these characters in the raw command; its --title-b64 carries them.
    named = (["--title-b64", base64.b64encode(title.encode("utf-8")).decode("ascii")]
             if _GATE_REFUSED.search(title) else ["--title", title])
    return shlex.join(["drive-save", "--card", "--folder", str(item["folder_id"]), "--file", str(item["staged"]),
                       *named])


def _command_payload(args: argparse.Namespace) -> dict[str, object]:
    argv = [str(item) for item in args.argv]
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        raise EffectRequestError("실행할 명령이 없다 — `--` 뒤에 argv 를 준다")
    target = " ".join(args.target.split())
    if not target:
        raise EffectRequestError("--target(영향 대상)이 비어 있다")
    timeout = int(args.timeout)
    if not 1 <= timeout <= MAX_TIMEOUT:
        raise EffectRequestError(f"--timeout 은 1~{MAX_TIMEOUT}초")
    cwd = Path(args.cwd).expanduser().resolve()
    if not cwd.is_dir():
        raise EffectRequestError(f"작업 폴더 없음: {cwd}")
    return {"argv": argv, "target": target, "cwd": str(cwd), "timeout": timeout}


def _postable(record: EffectRequest) -> None:
    """Refuse before anything is stored when the card would exceed the postable length."""
    from .render import RenderError, render_effect_approval

    try:
        render_effect_approval(record)
    except RenderError as error:
        raise EffectRequestError(
            f"카드 한 장에 다 들어가지 않는다({error}) — 파일을 나눠 두 번 요청한다"
        ) from error


def _new_record(args: argparse.Namespace, store: EffectRequestStore, *, reuse_live: bool = True) -> EffectRequest:
    summary = _summary(args.summary)
    effect = args.effect
    if effect == DRIVE_SAVE and getattr(args, "manifest", ""):
        return _tree_record(args, store, summary)
    seed = hashlib.sha256(f"{effect}\0{summary}\0{datetime.now(UTC).isoformat()}".encode()).hexdigest()[:16]
    payload = _drive_payload(args, seed, store) if effect == DRIVE_SAVE else _command_payload(args)
    digest = action_hash(effect, summary, payload)
    key_basis = json.dumps({"effect": effect, "summary": summary}, ensure_ascii=False, sort_keys=True)
    key = "effect:" + hashlib.sha256(key_basis.encode("utf-8")).hexdigest()[:24]
    for existing in store.all():
        if reuse_live and existing.key == key and existing.action_hash == digest and existing.status in LIVE_STATUSES:
            return existing
    record = EffectRequest(
        request_id=seed, key=key, effect=effect, summary=summary, payload=payload,
        action_hash=digest, created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        origin_channel_id=args.origin_channel_id, origin_message_id=args.origin_message_id,
    )
    if reuse_live:
        try:
            _postable(record)
        except EffectRequestError:
            shutil.rmtree(store.frozen_dir(seed), ignore_errors=True)
            raise
    store.create(record)
    return record


def _print_thread_line(record: EffectRequest) -> None:
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

    if args.effect == COMMAND:
        refused = refusal(args.summary + "\n" + args.target, tuple(str(item) for item in args.argv))
        if refused is not None:
            print(f"EFFECT-REQUEST-REFUSED code={refused.code} reason={refused.reason}", file=sys.stderr)
            return EXIT_REFUSED
    store = EffectRequestStore()
    record = _new_record(args, store)
    for item in record.payload.get("overwrite", ()):  # type: ignore[union-attr]
        print(f"DRIVE-SAVE-OVERWRITE-NEEDS-CARD path={item['path']} folder={item['folder_id']} "
              f"request: {overwrite_command(item)}")
    if record.status not in LIVE_STATUSES:
        return _already(record)
    runtime = discord_runtime(resolve_token(), owner_id_from_config(), store)
    try:
        verdict = request_approval(record, store, runtime)
    except ApprovalSurfaceError:
        settled = store.get(record.request_id) or record
        if settled.status in LIVE_STATUSES:
            raise
        return _already(settled)
    settled = store.get(record.request_id) or record
    if verdict.outcome is not Outcome.POSTED and settled.status not in LIVE_STATUSES:
        return _already(settled)
    if verdict.outcome not in {Outcome.POSTED, Outcome.PENDING}:
        reason = "unknown" if verdict.reason is None else verdict.reason.value
        print(f"EFFECT-REQUEST-NOT-POSTED request={record.request_id} reason={reason}", file=sys.stderr)
        return 3
    current = store.get(record.request_id) or record
    print(f"REQUESTED request={current.request_id} effect={current.effect} hash={current.action_hash}")
    _print_thread_line(current)
    return 0


def _already(record: EffectRequest) -> int:
    """The same tree job again (a retry, or one that overlapped): its result, never a second card."""
    print(f"EFFECT-REQUEST-ALREADY request={record.request_id} status={record.status}")
    _print_thread_line(record)
    return 0 if record.status == "done" else EXIT_REFUSED


def cmd_status(args: argparse.Namespace) -> int:
    chosen = [r for r in EffectRequestStore().all() if not args.request or r.request_id == args.request]
    for record in sorted(chosen, key=lambda item: item.created_at):
        rows = (record.receipt or {}).get("files") if record.payload.get("version") in (2, 3) else None
        saved = ""
        if isinstance(rows, list):
            ok = sum(1 for row in rows if isinstance(row, dict) and row.get("status") == "ok")
            saved = f" saved={ok}/{len(record.payload.get('files', ()))}"  # type: ignore[arg-type]
        print(
            f"EFFECT-REQUEST request={record.request_id} effect={record.effect} status={record.status} "
            f"attempts={record.attempts}{saved} error={record.last_error or '-'}"
        )
    return 0 if chosen else 1


def cmd_save_now(args: argparse.Namespace) -> int:
    """Save from a gate-verified owner command; no Discord approval or watcher retry."""
    from automation.drive_outputs import publish

    from .execute import execute
    from .render import drive_location

    refused = refusal(args.summary)
    if refused is not None:
        print(f"EFFECT-REQUEST-REFUSED code={refused.code} reason={refused.reason}", file=sys.stderr)
        return EXIT_REFUSED
    if getattr(args, "manifest", ""):
        # Owner 2026-11-23 "건별로 파일들 묶어서 승인 받도록": a folder tree is always one approval card.
        print("EFFECT-REQUEST-REFUSED code=tree-needs-card reason=폴더 트리 저장은 카드 한 장으로만 한다 "
              "— request drive-save --manifest 를 쓴다", file=sys.stderr)
        return EXIT_REFUSED
    store = EffectRequestStore()
    record = _new_record(args, store, reuse_live=False)
    if record.payload.get("version") == 2:
        return _save_now_batch(record, store, publish)
    try:
        receipt = execute(record, publisher=publish)
    except EffectRequestError as error:
        store.update(replace(
            record, status="failed", attempts=1, last_error=str(error)[:300],
            finished_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        ))
        raise
    receipt.update(
        method="owner_command",
        origin_channel_id=record.origin_channel_id,
        origin_message_id=record.origin_message_id,
    )
    store.update(replace(
        record, status="done", attempts=1, receipt=receipt,
        finished_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    ))
    links = receipt["links"]
    location = str(links[0]) if links else drive_location(record.payload)
    print(f"OWNER-COMMAND-WRITTEN drive-save {location}")
    return 0


def _save_now_batch(record: EffectRequest, store: EffectRequestStore, publish: object) -> int:
    """Save every file; a failed file is tried again (failed ones only), then one line per file.

    Owner Q549: the rest is saved when one fails, each file keeps its own result, and only
    the failed files are tried again under the same owner command. Exit 0 only when all saved.
    """
    from .execute import BatchIncomplete, execute

    attempts, error_text, receipt = 0, "", record.receipt
    for _ in range(SAVE_NOW_ROUNDS):
        attempts += 1
        try:
            receipt = execute(replace(record, receipt=receipt), publisher=publish)  # type: ignore[arg-type]
            error_text = ""
            break
        except BatchIncomplete as error:
            receipt, error_text = error.receipt, str(error)
            if not error.retryable:
                break
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    complete = not error_text
    assert receipt is not None
    receipt.update(method="owner_command", origin_channel_id=record.origin_channel_id,
                   origin_message_id=record.origin_message_id)
    store.update(replace(record, status="done" if complete else "failed", attempts=attempts,
                         receipt=receipt, last_error=error_text[:300], finished_at=stamp))
    for row in receipt.get("files", ()):  # type: ignore[union-attr]
        if row.get("status") == "ok":
            links = row.get("links") or [row.get("name")]
            print(f"OWNER-COMMAND-WRITTEN drive-save {links[0]}")
        else:
            print(f"DRIVE-SAVE-FAILED {row.get('name')} {row.get('error')}")
    if not complete:
        print(f"EFFECT-REQUEST-ERROR {error_text}", file=sys.stderr)
    return 0 if complete else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m automation.effect_request")
    commands = parser.add_subparsers(dest="command", required=True)
    request = commands.add_parser("request", help="post one approval card for an effect no skill owns")
    effects = request.add_subparsers(dest="effect", required=True)
    for name, helptext in ((DRIVE_SAVE, "save one file to Drive, owner-only"),
                           (COMMAND, "run one command after approval"),
                           ("save-now", "save from a gate-verified owner command without a card")):
        immediate = name == "save-now"
        sub = (commands if immediate else effects).add_parser(name, help=helptext)
        sub.add_argument("--summary", required=True, help="한 줄 설명 — 카드·스레드 제목")
        sub.add_argument("--origin-channel-id", default="", required=immediate)
        sub.add_argument("--origin-message-id", default="", required=immediate)
        sub.set_defaults(handler=cmd_save_now if immediate else cmd_request)
        if immediate:
            sub.set_defaults(effect=DRIVE_SAVE)
        if name == DRIVE_SAVE or immediate:
            sub.add_argument("--file", default=None, action="append",
                             help=f"저장할 파일 — 여러 개면 반복(최대 {MAX_FILES}개, 카드 하나·승인 한 번)")
            sub.add_argument("--manifest", default="",
                             help="폴더 트리 매니페스트(JSON) — --folder <부모 폴더 id> 와 함께, 카드 한 장")
            sub.add_argument("--title", default=None, action="append",
                             help="Drive 파일 제목(기본: 파일 이름) — 여러 파일이면 --file 마다 하나씩")
            sub.add_argument("--kind", default=DEFAULT_KIND, help="Drive 카테고리(기본 doctype=문서)")
            sub.add_argument("--project", default="", help="과제 폴더 한 단(선택)")
            sub.add_argument("--folder", default="",
                             help="저장할 Drive 폴더 id(선택) — 형만 볼 수 있는 폴더여야 한다")
            sub.add_argument("--on", default=None, help="기준 날짜 YYYY-MM-DD(기본 오늘)")
        else:
            sub.add_argument("--target", required=True, help="영향을 받는 대상(사람·서비스·위치)")
            sub.add_argument("--cwd", default=str(Path.home()))
            sub.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
            sub.add_argument("argv", nargs=argparse.REMAINDER, help="-- 뒤에 실행할 명령")
    status = commands.add_parser("status", help="show effect requests and their receipts")
    status.add_argument("--request", default="")
    status.set_defaults(handler=cmd_status)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (EffectRequestError, OSError, UnicodeDecodeError) as error:
        print(f"EFFECT-REQUEST-ERROR {error}", file=sys.stderr)
        return 2
