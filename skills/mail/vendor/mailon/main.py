"""CLI entry point for mailon.kr backup/sync."""
from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import traceback
from dataclasses import asdict
from datetime import datetime
from pathlib import Path


# --- Windows console UTF-8 fix --------------------------------------------
# On Windows, python.exe's stdout defaults to cp949 / cp1252 which mangles
# Korean text. Re-wrap stdout/stderr as UTF-8 when running interactively.
# (Has no effect when redirected or in Task Scheduler.)
if sys.platform == "win32":
    for _stream_name in ("stdout", "stderr"):
        _s = getattr(sys, _stream_name, None)
        _reconf = getattr(_s, "reconfigure", None)
        if callable(_reconf):
            try:
                _reconf(encoding="utf-8", errors="replace")
            except Exception:
                pass

from . import send_lock
from .browser import AgentBrowser, BrowserError, unique_session_name
from .config import PROJECT_ROOT, load_config, load_totp_secret
from .login import LoginError, login
from .folders import resolve_folder_uid
from .resolve import parse_candidates, resolve_name
from .scraper import InboxScraper
from .send import (
    ComposeSender,
    SendRequest,
    SendSafetyError,
    SendValidationError,
    failure_details,
    record_send_failure,
    record_send_result,
)
from .state import StateDB
from .totp import generate_code, seconds_until_next_code
from .writer import write_mail


log = logging.getLogger("mailon")


def _setup_logging(logs_dir: Path, level: str = "INFO", stream=None) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    logfile = logs_dir / f"sync-{datetime.now().strftime('%Y-%m-%d')}.log"

    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    root = logging.getLogger()
    root.setLevel(level)
    # Clear existing handlers to avoid duplication on repeated runs
    root.handlers.clear()

    fh = logging.FileHandler(logfile, encoding="utf-8")
    fh.setFormatter(logging.Formatter(fmt))
    root.addHandler(fh)

    ch = logging.StreamHandler(stream or sys.stdout)
    ch.setFormatter(logging.Formatter(fmt))
    root.addHandler(ch)


def _make_browser(cfg) -> AgentBrowser:
    return AgentBrowser(
        session_name=unique_session_name(cfg.session_name),
        headless=cfg.headless,
        ephemeral=True,
    )


def _split_addresses(values: list[str]) -> tuple[str, ...]:
    """Normalize CLI address args: the triage gate passes ONE comma-joined
    string (--to "a, b, c"); humans may pass repeated --to flags. Accept both."""
    out: list[str] = []
    for value in values:
        for part in value.replace(";", ",").split(","):
            addr = part.strip()
            if addr:
                out.append(addr)
    return tuple(out)


def _make_send_browser(cfg) -> AgentBrowser:
    """Sends get their OWN browser session: sharing the sync session let a
    concurrent send kill a running sent-folder sync (2026-07-20), and sharing one
    fixed send session let two concurrent sends overwrite each other's login form
    (2026-09-07)."""
    return AgentBrowser(
        session_name=unique_session_name(f"{cfg.session_name}-send"),
        headless=cfg.headless,
        ephemeral=True,
    )


def _make_resolve_browser(cfg) -> AgentBrowser:
    """Recipient resolution opens compose too, so it needs its own session as well."""
    return AgentBrowser(
        session_name=unique_session_name(f"{cfg.session_name}-resolve"),
        headless=cfg.headless,
        ephemeral=True,
    )


# --------------------------------------------------------- commands


def cmd_totp(args) -> int:
    """Print the current TOTP code. Useful to compare with your phone.

    Only MAILON_TOTP_SECRET is required; ID/PW may be empty.
    """
    secret = load_totp_secret()
    code = generate_code(secret)
    remaining = seconds_until_next_code()
    print(f"TOTP code: {code}  (valid for {remaining}s more)")
    return 0


def cmd_login_only(args) -> int:
    """Just log in, verify we reach the mailbox, then exit."""
    cfg = load_config()
    _setup_logging(cfg.logs_dir)
    browser = _make_browser(cfg)
    try:
        login(browser, cfg)
        url = browser.current_url()
        log.info("login OK. current url: %s", url)
        print(f"OK: {url}")
        return 0
    except LoginError as e:
        log.error("login failed: %s", e)
        print(f"FAIL: {e}", file=sys.stderr)
        return 2
    finally:
        browser.close()


def cmd_probe(args) -> int:
    """Log in, navigate inbox, and dump AX-tree + HTML to logs/ for inspection."""
    cfg = load_config()
    _setup_logging(cfg.logs_dir)
    browser = _make_browser(cfg)
    try:
        login(browser, cfg)
        scraper = InboxScraper(browser, cfg.attachments_dir, all_folders=False)
        scraper.resolve_inbox_folder_uid()
        out = scraper.probe_and_dump(cfg.logs_dir)
        print(f"probe saved: {out}")
        return 0
    except Exception as e:
        log.error("probe failed: %s\n%s", e, traceback.format_exc())
        print(f"FAIL: {e}", file=sys.stderr)
        return 2
    finally:
        browser.close()



_VALID_FOLDERS = ("inbox", "sent", "all")


def _parse_folders(csv: str) -> list[str]:
    folders = [token.strip() for token in csv.split(",") if token.strip()]
    invalid = [token for token in folders if token not in _VALID_FOLDERS]
    if invalid or not folders or ("all" in folders and len(folders) != 1):
        raise ValueError(
            f"invalid --folders value {csv!r} (allowed: {','.join(_VALID_FOLDERS)})")
    return folders


def _sync_summary_line(new_count: int, retry_ok: int, retry_fail: int) -> str:
    # Contract line parsed by skills/mail/scripts/mailon_interface.py:
    #   r"^OK: (?P<new>\d+) new mail\(s\)"  — keep the prefix EXACT.
    return (f"OK: {new_count} new mail(s) "
            f"(retries: {retry_ok} recovered, {retry_fail} still failing)")


def _sync_folder(db: StateDB, scraper: InboxScraper, cfg, limit: int,
                 project_root: Path, full_scan: bool = False) -> int:
    """Fetch/write/record every new mail of one folder. Returns new-mail count.

    uid is a GLOBAL primary key in state.db; a uid already recorded under a
    DIFFERENT folder is written as markdown but NOT re-recorded (would clobber
    the other folder's row). Real fix = (folder, uid) composite key migration.
    """
    label = scraper.folder_label
    skip = db.existing_uids() if scraper.all_folders else db.existing_uids(folder=label)
    log.info("[%s] %d already-saved uids will be skipped", label, len(skip))
    new_count = 0
    for mail in scraper.iter_new_mails(skip, limit=limit, state_db=db, full_scan=full_scan):
        if not mail.uid or mail.uid == "None":
            log.warning("skipping mail with empty uid: subject=%r", mail.subject)
            continue
        if mail.uid not in skip and db.has_message(mail.uid):
            log.warning("[%s] uid %s already recorded under another folder; "
                        "markdown only (no DB row)", label, mail.uid)
            skip.add(mail.uid)  # within-run dedupe
            write_mail(mail, cfg.mails_dir, project_root)
            continue
        md_path = write_mail(mail, cfg.mails_dir, project_root)
        db.record_message(
            mail.uid,
            folder=mail.folder,
            subject=mail.subject,
            sender=mail.sender,
            recv_date=(mail.date.isoformat() if mail.date else ""),
            markdown_path=str(md_path.relative_to(project_root)),
        )
        skip.add(mail.uid)
        new_count += 1
    return new_count


class SyncTerminated(Exception):
    """The caller asked the sync to stop (SIGTERM from the wrapper's timeout)."""


def _raise_terminated(signum, _frame) -> None:
    raise SyncTerminated(f"terminated by signal {signum} (caller timeout)")


def cmd_sync(args) -> int:
    """Full sync: log in, save new mails from the requested folders as Markdown."""
    cfg = load_config()
    _setup_logging(cfg.logs_dir)
    db = StateDB(cfg.state_db_path)
    run_id = db.start_run()
    # SIGTERM ends the sync through the failure path below, so the run is recorded and
    # `finally` closes the browser (2026-10-13: killed syncs left both behind).
    signal.signal(signal.SIGTERM, _raise_terminated)

    try:
        folders = _parse_folders(args.folders)
    except ValueError as e:
        db.finish_run(run_id, status="fail", error=str(e))
        print(f"FAIL: {e}", file=sys.stderr)
        return 2

    browser = _make_browser(cfg)
    new_count = 0
    try:
        login(browser, cfg)
        all_folders = folders == ["all"]
        scraper = InboxScraper(
            browser,
            cfg.attachments_dir,
            folder_label="all" if all_folders else "inbox",
            all_folders=all_folders,
        )
        # Inbox uid is resolved unconditionally: cheap, proven, and the sent
        # folder inference (allFolder histogram) needs it as the exclusion uid.
        scraper.resolve_inbox_folder_uid()

        limit = args.limit if args.limit is not None else cfg.max_mails_per_run
        full_scan = args.full_scan

        if all_folders:
            new_count += _sync_folder(db, scraper, cfg, limit, PROJECT_ROOT, full_scan)
        elif "inbox" in folders:
            new_count += _sync_folder(db, scraper, cfg, limit, PROJECT_ROOT, full_scan)

        if not all_folders and "sent" in folders:
            sent_uid = resolve_folder_uid(
                browser, "보낸메일함",
                inbox_uid=scraper.folder_uid or "",
                account_email=cfg.mailon_id,
            )
            if sent_uid:
                sent_scraper = InboxScraper(
                    browser, cfg.attachments_dir, folder_label="sent", all_folders=False)
                sent_scraper.folder_uid = sent_uid
                new_count += _sync_folder(db, sent_scraper, cfg, limit, PROJECT_ROOT, full_scan)
            # unresolvable -> warning already logged; sync stays fail-open

        # Retry previously-failed attachments (global, once per run).
        retry_ok, retry_fail = scraper.retry_failed_attachments(db)
        if retry_ok or retry_fail:
            log.info("attachment retry phase: %d succeeded, %d still failing",
                     retry_ok, retry_fail)

        db.finish_run(run_id, status="ok", new_mails=new_count)
        att_stats = db.attachment_stats()
        log.info("sync OK: %d new mail(s); attachments ok=%d fail=%d",
                 new_count, att_stats["ok"], att_stats["fail"])
        print(_sync_summary_line(new_count, retry_ok, retry_fail))
        return 0
    except (LoginError, BrowserError) as e:
        db.finish_run(run_id, status="fail", new_mails=new_count, error=str(e))
        log.error("sync failed: %s", e)
        print(f"FAIL: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        db.finish_run(
            run_id, status="fail", new_mails=new_count,
            error=f"{e}\n{traceback.format_exc()}",
        )
        log.error("sync crashed: %s\n%s", e, traceback.format_exc())
        print(f"FAIL: {e}", file=sys.stderr)
        return 3
    finally:
        browser.close()


def cmd_status(args) -> int:
    """Show current state: count of stored mails + last run info.

    Does NOT require credentials - reads only from the local SQLite DB.
    """
    from .config import PROJECT_ROOT as _ROOT
    state_db_path = _ROOT / "data" / "state.db"
    if not state_db_path.exists():
        print("No database yet. Run a sync first.")
        return 0

    db = StateDB(state_db_path)
    import sqlite3
    conn = sqlite3.connect(state_db_path)
    conn.row_factory = sqlite3.Row

    print(f"Saved mails: {db.message_count('inbox')}")
    print(f"Sent mails: {db.message_count('sent')}")
    row = conn.execute(
        "SELECT * FROM runs ORDER BY run_id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        print("No runs recorded yet.")
    else:
        print(
            f"Last run #{row['run_id']}: status={row['status']} "
            f"new={row['new_mails']} "
            f"started={datetime.fromtimestamp(row['started_at']).isoformat(timespec='seconds')} "
            + (f"finished={datetime.fromtimestamp(row['finished_at']).isoformat(timespec='seconds')} "
               if row['finished_at'] else "")
        )
        if row["error"]:
            print(f"  error: {row['error'][:300]}")
    conn.close()
    return 0


def cmd_send(args) -> int:
    if not args.dry_run and not args.confirm_send:
        _print_send_error(args.json, "confirmation_required")
        return 1

    cfg = load_config()
    _setup_logging(cfg.logs_dir, stream=sys.stderr if args.json else sys.stdout)
    try:
        if args.dry_run:
            return _perform_send(args, cfg)
        # Only the irreversible step is serialized. The loser of a race re-runs its own
        # duplicate check and finds the winner's mail already in the mailbox.
        with send_lock.hold(send_lock.lock_path(cfg.data_dir)):
            return _perform_send(args, cfg)
    except (LoginError, BrowserError, SendSafetyError, SendValidationError) as error:
        # 2026-09-07: an auth_error that blocked an approved mail left no line in the log
        # file, because this branch only ever wrote to stdout/stderr.
        log.error("send failed [%s]: %s", type(error).__name__, error)
        _print_send_error(args.json, error)
        if not args.dry_run:
            try:
                record_send_failure(cfg.logs_dir, _send_error_payload(error))
            except OSError as record_error:
                log.warning("send-attempts failure line not written: %s", record_error)
        return 2


def _perform_send(args, cfg) -> int:
    browser = _make_send_browser(cfg)
    try:
        request = SendRequest(
            recipients=_split_addresses(args.to),
            cc=_split_addresses(args.cc),
            bcc=_split_addresses(getattr(args, "bcc", [])),
            subject=args.subject,
            body=args.body,
            body_html=args.body_html,
            attachments=tuple(Path(item) for item in args.attachment),
        )
        login(browser, cfg)
        browser.clear_network_requests()
        result = ComposeSender(browser).send(request, dry_run=args.dry_run)
        record_send_result(cfg.logs_dir, result)
        if args.json:
            print(result.to_json())
        else:
            print(f"OK: {result.status}; POST={result.network_post_count}")
        return 0
    finally:
        browser.close()


_SAFE_SEND_ERROR_MESSAGES = {
    "confirmation_required": "실제 발송에는 명시적인 발송 확인이 필요합니다.",
    "validation_error": "메일 입력값을 확인한 후 다시 시도하세요.",
    "attachment_invalid": "첨부 파일이 존재하는 일반 파일인지 확인한 후 새 초안을 만드세요.",
    "attachment_unsupported": (
        "현재 첨부 업로드 기능을 사용할 수 없습니다. "
        "첨부를 제거하거나 기능이 활성화된 후 다시 시도하세요."
    ),
    "attachment_upload_failed": "첨부 업로드에 실패했습니다. 잠시 후 다시 시도하세요.",
    "auth_error": "메일 서비스 인증에 실패했습니다. 재인증 후 다시 시도하세요.",
    "external_service_error": "메일 서비스 또는 브라우저 연결에 실패했습니다. 잠시 후 다시 시도하세요.",
    "send_failed": "메일 발송에 실패했습니다. 잠시 후 다시 시도하세요.",
}


def _send_error_payload(error: BaseException | str) -> dict:
    """Return a stable, provider-neutral error contract without raw details."""
    cause = None
    if isinstance(error, str):
        error_code = error
        stage = "validation" if error == "confirmation_required" else "send"
        retryable = error != "confirmation_required"
    elif isinstance(error, LoginError):
        error_code, stage, retryable = "auth_error", "authentication", False
        cause = "login_failed"
    elif isinstance(error, BrowserError) and not isinstance(error, SendSafetyError):
        error_code, stage, retryable = "external_service_error", "provider", True
        cause = "browser_command_failed"
    else:
        error_code = str(getattr(error, "error_code", "send_failed"))
        stage = str(getattr(error, "stage", "send"))
        retryable = bool(getattr(error, "retryable", True))
        cause = getattr(error, "cause", None)
    payload = {
        "status": "error",
        "error_code": error_code,
        "stage": stage,
        "retryable": retryable,
        "message": _SAFE_SEND_ERROR_MESSAGES.get(
            error_code, _SAFE_SEND_ERROR_MESSAGES["send_failed"]
        ),
    }
    # 2026-10-12: two approved sends stopped and the record could not say whether
    # login or the webmail page failed. A fixed cause code names the check; the
    # free-text message still never leaves the log file.
    if isinstance(cause, str) and cause:
        payload["cause"] = cause
    if isinstance(error, BaseException):
        payload.update(failure_details(error))
        if isinstance(error, LoginError):
            payload["step"] = "login"
    return payload


def _print_send_error(json_mode: bool, error: BaseException | str) -> None:
    payload = _send_error_payload(error)
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    else:
        print(
            f"FAIL [{payload['error_code']}]: {payload['message']}",
            file=sys.stderr,
        )


def cmd_resolve(args) -> int:
    """Resolve a recipient name to email candidates via compose autocomplete.

    Read-only: opens compose, types the name, scrapes the suggestion grid.
    Nothing is submitted.
    """
    cfg = load_config()
    _setup_logging(cfg.logs_dir, stream=sys.stderr if args.json else sys.stdout)
    browser = _make_resolve_browser(cfg)
    try:
        login(browser, cfg)
        cells, post_count = resolve_name(browser, args.name)
        candidates = parse_candidates(cells, args.name)
        if args.json:
            print(json.dumps({
                "status": "ok",
                "query": args.name,
                "candidates": [asdict(c) for c in candidates],
                "post_count": post_count,
            }, separators=(",", ":")))
        else:
            for c in candidates:
                print(f'{c.group}\t"{c.name}" <{c.email}>\t{c.org}')
            print(f"OK: {len(candidates)} candidate(s) for {args.name!r}; "
                  f"POST={post_count}")
        return 0
    except (LoginError, BrowserError) as error:
        _print_send_error(args.json, error)
        return 2
    finally:
        browser.close()


# ----------------------------------------------------------- dispatch


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m mailon.main",
        description="Automated mailon.kr inbox backup + incremental sync.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("totp", help="print current TOTP code")
    sub.add_parser("login", help="log in and stop (smoke-test credentials)")
    sub.add_parser("probe", help="dump inbox HTML / AX-tree for inspection")

    s_sync = sub.add_parser(
        "sync", help="log in + save new mails (normal cron mode)"
    )
    s_sync.add_argument(
        "--limit", type=int, default=None,
        help="max mails to process PER FOLDER this run "
             "(default: config MAX_MAILS_PER_RUN)",
    )
    s_sync.add_argument(
        "--folders", default="inbox,sent",
        help="comma-separated folders to sync: inbox,sent; or all (default: inbox,sent)",
    )
    s_sync.add_argument(
        "--full-scan", action="store_true",
        help="walk every list page instead of stopping at the first page of "
             "already-saved mails (picks up older unsaved mails; slow on big folders)",
    )

    s_send = sub.add_parser("send", help="compose a mail with an explicit dry-run mode")
    s_send.add_argument("--to", action="append", required=True)
    s_send.add_argument("--cc", action="append", default=[])
    s_send.add_argument("--bcc", action="append", default=[])
    s_send.add_argument("--subject", required=True)
    s_send.add_argument("--body", required=True)
    s_send.add_argument("--body-html", default="",
                        help="HTML put in the editor instead of --body (the gate still checks --body)")
    s_send.add_argument("--attachment", action="append", default=[])
    s_send.add_argument("--dry-run", action="store_true")
    s_send.add_argument("--confirm-send", action="store_true")
    s_send.add_argument("--json", action="store_true")

    s_resolve = sub.add_parser(
        "resolve",
        help="recipient name→email autocomplete resolution (read-only)",
    )
    s_resolve.add_argument(
        "--name", required=True,
        help="recipient name to type into the compose To field",
    )
    s_resolve.add_argument("--json", action="store_true")

    sub.add_parser("status", help="show DB state + last run result")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.cmd == "totp":
            return cmd_totp(args)
        if args.cmd == "login":
            return cmd_login_only(args)
        if args.cmd == "probe":
            return cmd_probe(args)
        if args.cmd == "sync":
            return cmd_sync(args)
        if args.cmd == "status":
            return cmd_status(args)
        if args.cmd == "send":
            return cmd_send(args)
        if args.cmd == "resolve":
            return cmd_resolve(args)
    except RuntimeError as e:
        # Config errors, etc.
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
