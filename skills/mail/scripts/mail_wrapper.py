#!/usr/bin/env python3
"""W4-1 institutional-mail READ-ONLY wrapper public command surface.

Thin subprocess wrapper over mailon (`python -m mailon.main …`). Stage 1 is
strictly READ-ONLY: `list` / `get` / `search` / `classify` / `status` / `resolve`.

The cached mailon interface and read/classification helpers live in cohesive
sibling modules and are re-exported here for stable W4-2 public imports. W4-2
consumes this wrapper via subprocess (`--json`-like stdout: exactly one JSON
object) or by importing its pure functions.

Live read path: mailon has no `list`/`get` subcommand and no read-side
`--json`; the READ surface is `sync --limit N` (incremental scrape into
SQLite `data/state.db` + Markdown under `data/mails` → `~agent/mail`, 700)
followed by a read-only SELECT from state.db. `list --sync` therefore runs a
real mailon sync first; on the known structural sync failure (exit 3, e.g.
the W0-7a folderUid selector) it degrades to the local state.db read and says
so; on auth/browser failure (exit 2) it surfaces re-auth guidance and exit 2.

Explicit redaction: `--masked` replaces subjects/senders with opaque sha256-derived
ids and omits bodies — the only mode whose output may leave the agent home
(QA/repo/git). Classification is metadata-only (subject/sender strings);
this wrapper itself never sends mail content to a model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from mailon_interface import (
    CONFIG_GUIDANCE,
    MAILON_INTERFACE,
    SYNC_FALLBACK_NOTE,
    WRAPPER_EXIT,
    WRAPPER_VERSION,
)
from mail_wrapper_classification import classify_metadata
from mailon_failure import classify_exit_two
from mail_wrapper_read import (
    _cfg,
    _db_rows,
    _render_mail,
    distinct_addresses,
    rank_candidates,
    render_candidate,
    build_subprocess_env,  # noqa: F401 - stable public helper re-export
    classify_stderr,
    mask_value,
    run_mailon,
)


def _emit(payload: dict, exit_code: int) -> int:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return exit_code


def _error(command: str, error_code: str, guidance: str, **extra) -> int:
    payload = {
        "wrapper": WRAPPER_VERSION, "command": command, "status": "error",
        "error_code": error_code, "guidance": guidance, **extra,
    }
    return _emit(payload, WRAPPER_EXIT[error_code])


def _mailon_failure(command: str, rc: int, stderr: str, **extra) -> int:
    detail = {
        "mailon_exit_code": rc,
        "mailon_exit_meaning": MAILON_INTERFACE["exit_codes"].get(rc, "unknown"),
        "stderr_lines": len(stderr.splitlines()),
        "stderr_bytes": len(stderr.encode("utf-8")),
        "failure_signature": classify_stderr(stderr),
        **extra,
    }
    if rc == 2:
        # exit 2 folds auth and browser failures together; the signature splits them.
        return _error(command, *classify_exit_two(detail["failure_signature"]), **detail)
    if rc == 1:
        return _error(command, "config_error", CONFIG_GUIDANCE, **detail)
    if rc in (10, 11) or rc == -6:
        return _error(command, "environment_error",
                      "mailon 실행 환경 없음: repo/.venv/agent-browser 배치 확인.", **detail)
    if rc == -7:
        return _error(command, "timeout",
                      "mailon 응답 없음(타임아웃). 잔여 chrome 프로세스 정리 후 재시도.", **detail)
    return _error(command, "read_path_error", SYNC_FALLBACK_NOTE, **detail)


# --------------------------------------------------------------- commands


def cmd_list(args) -> int:
    cfg = _cfg()
    sync_info = None
    if args.sync:
        sync_argv = ["sync", "--limit", str(args.limit)] + (
            ["--folders", "all"] if args.all_folders else []
        )
        rc, out, err = run_mailon(cfg, sync_argv)
        if rc in (1, 2, 10, 11, -6, -7):
            return _mailon_failure("list", rc, err)
        sync_info = {"exit_code": rc,
                     "meaning": MAILON_INTERFACE["exit_codes"].get(rc, "unknown")}
        if rc == 0:
            m = re.search(MAILON_INTERFACE["stdout_patterns"]["sync_ok"], out, re.M)
            sync_info["new_mails"] = int(m.group("new")) if m else None
        else:  # exit 3: structural sync crash → documented local fallback
            sync_info.update(fallback="local-state-db",
                             failure_signature=classify_stderr(err),
                             stderr_lines=len(err.splitlines()),
                             stderr_bytes=len(err.encode("utf-8")),
                             note=SYNC_FALLBACK_NOTE)
    where, params = ("1 = 1", ()) if args.all_folders else ("folder = ?", ("inbox",))
    rows = _db_rows(cfg, where, params, args.limit)
    if not rows:
        if sync_info and sync_info.get("exit_code") == 3:
            return _error("list", "read_path_error", SYNC_FALLBACK_NOTE, sync=sync_info)
        return _error("list", "not_found",
                      "state.db가 없거나 비어 있음. 먼저 `list --sync`로 수집할 것.",
                      sync=sync_info)
    payload = {
        "wrapper": WRAPPER_VERSION, "command": "list", "status": "ok",
        "masked": args.masked, "synced": bool(args.sync), "sync": sync_info,
        "count": len(rows),
        "mails": [_render_mail(r, args.masked, cfg["mask_salt"]) for r in rows],
    }
    payload.update({"scope": "all-folders"} if args.all_folders else {})
    return _emit(payload, 0)


def cmd_get(args) -> int:
    cfg = _cfg()
    rows = _db_rows(cfg, "uid = ?", (args.uid,), 1)
    if not rows:
        return _error("get", "not_found", f"uid {args.uid} 없음 (state.db 기준).")
    mail = _render_mail(rows[0], args.masked, cfg["mask_salt"])
    if args.body:
        md_rel = rows[0]["markdown_path"]
        md_path = cfg["repo"] / md_rel if md_rel else None
        if md_path is None or not md_path.is_file():
            mail["body"] = None
            mail["body_note"] = "markdown 파일 없음"
        else:
            body = md_path.read_text(encoding="utf-8")
            if args.masked:
                mail["body_sha256"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
                mail["body_bytes"] = len(body.encode("utf-8"))
            else:
                mail["body"] = body
                html_path = md_path.with_suffix(".html")  # mailon writer: the original's HTML
                if getattr(args, "with_html", False) and html_path.is_file():
                    mail["body_html"] = html_path.read_text(encoding="utf-8")
    payload = {"wrapper": WRAPPER_VERSION, "command": "get", "status": "ok",
               "masked": args.masked, "mail": mail}
    return _emit(payload, 0)


SNIPPET_CONTEXT = 60


def _snippet(body: str, word: str) -> str:
    flat = " ".join(body.split())
    at = flat.casefold().find(word)
    start = max(at - SNIPPET_CONTEXT, 0)
    return flat[start:at + len(word) + SNIPPET_CONTEXT]


def cmd_search(args) -> int:
    """Every query word must appear in the subject, sender or saved Markdown body (case-insensitive).

    All folders; mails matched by subject or sender alone come first, then body matches, each newest
    first, so a remembered subject is not pushed past the limit by mails that merely mention the words.
    Read-only (state.db opened mode=ro, Markdown only read). A mail whose Markdown file is missing is
    still matched by subject and sender and counted in `without_body`.
    """
    cfg = _cfg()
    words = [word.casefold() for word in args.query.split()]
    if not words:
        return _error("search", "usage_error", "--query에 찾을 낱말이 필요.")
    rows = _db_rows(cfg, "1 = 1", (), None)
    if not rows:
        return _error("search", "not_found",
                      "state.db가 없거나 비어 있음. 먼저 `list --sync`로 수집할 것.")
    by_head: list[dict] = []
    by_body: list[dict] = []
    without_body = 0
    for row in rows:
        md_path = cfg["repo"] / row["markdown_path"] if row["markdown_path"] else None
        has_body = md_path is not None and md_path.is_file()
        without_body += not has_body
        if len(by_head) >= args.limit:
            continue
        fields = {"subject": (row["subject"] or "").casefold(),
                  "sender": (row["sender"] or "").casefold()}
        rest = [word for word in words if not any(word in text for text in fields.values())]
        body = md_path.read_text(encoding="utf-8", errors="replace") if rest and has_body else ""
        if any(word not in body.casefold() for word in rest) or (rest and len(by_body) >= args.limit):
            continue
        mail = _render_mail(row, args.masked, cfg["mask_salt"])
        mail["matched_in"] = [name for name, text in fields.items()
                              if any(word in text for word in words)] + (["body"] if rest else [])
        if rest and not args.masked:
            mail["snippet"] = _snippet(body, rest[0])
        (by_body if rest else by_head).append(mail)
    mails = (by_head + by_body)[:args.limit]
    payload = {
        "wrapper": WRAPPER_VERSION, "command": "search", "status": "ok",
        "masked": args.masked, "limit": args.limit, "searched": len(rows),
        "without_body": without_body, "count": len(mails), "mails": mails,
    }
    return _emit(payload, 0)


def cmd_classify(args) -> int:
    cfg = _cfg()
    if args.uid:
        rows = _db_rows(cfg, "uid = ?", (args.uid,), 1)
        if not rows:
            return _error("classify", "not_found", f"uid {args.uid} 없음.")
        subject, sender = rows[0]["subject"] or "", rows[0]["sender"] or ""
        ref: dict = {"uid": args.uid}
    else:
        subject, sender = args.subject or "", args.sender or ""
        ref = {}
    result = classify_metadata(subject, sender)
    if args.masked:
        ref["subject"] = mask_value(subject, cfg["mask_salt"])
        ref["sender"] = mask_value(sender, cfg["mask_salt"])
    payload = {"wrapper": WRAPPER_VERSION, "command": "classify", "status": "ok",
               "masked": args.masked, **ref, "classification": result}
    return _emit(payload, 0)


def cmd_status(args) -> int:
    cfg = _cfg()
    rc, out, err = run_mailon(cfg, ["status"])
    if rc != 0:
        return _mailon_failure("status", rc, err)
    pats = MAILON_INTERFACE["stdout_patterns"]
    info: dict = {"saved_mails": None, "no_db": False, "last_run": None}
    for line in out.splitlines():
        if re.match(pats["status_no_db"], line):
            info["no_db"] = True
            info["saved_mails"] = 0
        m = re.match(pats["status_saved"], line)
        if m:
            info["saved_mails"] = int(m.group("count"))
        m = re.match(pats["status_last_run"], line)
        if m:
            info["last_run"] = {"run_id": int(m.group("run_id")),
                                "status": m.group("status"),
                                "new_mails": int(m.group("new")),
                                "started": m.group("started")}
    payload = {"wrapper": WRAPPER_VERSION, "command": "status", "status": "ok", **info}
    return _emit(payload, 0)


def _last_json_dict(out: str) -> dict | None:
    for chunk in (out.strip(), *reversed(out.strip().splitlines())):
        try:
            parsed = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def cmd_resolve(args) -> int:
    cfg = _cfg()
    rc, out, err = run_mailon(cfg, ["resolve", "--name", args.name, "--json"])
    if rc != 0:
        return _mailon_failure("resolve", rc, err)
    parsed = _last_json_dict(out)
    cands = parsed.get("candidates") if parsed else None
    if (parsed is None or parsed.get("status") != "ok" or not isinstance(cands, list)
            or not all(isinstance(c, dict) for c in cands)):
        return _error("resolve", "read_path_error",
                      "mailon resolve stdout이 JSON 계약과 불일치 — 기관메일-인터페이스.md 참조.",
                      stdout_lines=len(out.splitlines()),
                      stdout_bytes=len(out.encode("utf-8")),
                      failure_signature=classify_stderr(err))
    salt = cfg["mask_salt"]
    query = str(parsed.get("query") or args.name)
    # Deterministic order + an explicit ambiguity signal: the caller must never have to
    # guess which of several addresses is current (mis-sending is irreversible).
    ranked = rank_candidates(cands)
    addresses = distinct_addresses(ranked)
    payload = {
        "wrapper": WRAPPER_VERSION, "command": "resolve", "status": "ok", "masked": args.masked,
        "query": mask_value(query, salt) if args.masked else query,
        "candidate_count": len(ranked),
        "distinct_address_count": addresses,
        "ambiguous": addresses > 1,
        "candidates": [render_candidate(c, args.masked, salt) for c in ranked],
    }
    return _emit(payload, 0)


# ---------------------------------------------------------------- dispatch


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mail_wrapper.py",
        description="W4-1 institutional-mail READ-ONLY wrapper (list/get/search/classify/resolve/status).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("list", help="recent inbox mails (state.db; --sync = live refresh)")
    s.add_argument("--limit", type=int, default=5)
    for flag in ("sync", "all-folders", "masked"):
        s.add_argument(f"--{flag}", action="store_true", default=False)
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("get", help="one mail by uid")
    s.add_argument("uid")
    s.add_argument("--body", action="store_true", default=False)
    s.add_argument("--with-html", action="store_true", default=False,
                   help="with --body: also the stored original HTML (reply quotes)")
    s.add_argument("--masked", action="store_true", default=False)
    s.set_defaults(fn=cmd_get)

    s = sub.add_parser("search", help="find mail in every folder by subject, sender or saved body")
    s.add_argument("--query", required=True)
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--masked", action="store_true", default=False)
    s.set_defaults(fn=cmd_search)

    s = sub.add_parser("classify", help="metadata-only triage (subject/sender)")
    s.add_argument("--uid")
    s.add_argument("--subject")
    s.add_argument("--sender")
    s.add_argument("--masked", action="store_true", default=False)
    s.set_defaults(fn=cmd_classify)

    s = sub.add_parser(
        "resolve", help="recipient name→email via webmail autocomplete (read-only)")
    s.add_argument("--name", required=True)
    s.add_argument("--masked", action="store_true", default=False)
    s.set_defaults(fn=cmd_resolve)

    s = sub.add_parser("status", help="mailon status parsed to JSON")
    s.set_defaults(fn=cmd_status)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "classify" and not (args.uid or args.subject or args.sender):
        print(json.dumps({"wrapper": WRAPPER_VERSION, "command": "classify",
                          "status": "error", "error_code": "usage_error",
                          "guidance": "--uid 또는 --subject/--sender 필요."},
                         ensure_ascii=False, separators=(",", ":")))
        return WRAPPER_EXIT["usage_error"]
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
