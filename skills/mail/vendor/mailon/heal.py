"""Heal mails saved before the 2026-10-19 attachment fixes, from inside the regular sync.

Two repairs, both idempotent and cheap once done:

* ``prune_buttons`` - webmail buttons recorded as attachments ("Save", "Download all") are
  dropped from state.db and from the mail's Markdown lists. Their files go too when every real
  attachment of that mail is saved (otherwise the "Download all" zip may be the only copy).
* ``backfill_large_links`` - a mail of the last ``BACKFILL_DAYS`` whose body shows MailOn's
  "대용량 첨부파일" box but has no large-file link recorded is read again, so the links the old
  parser missed are downloaded and listed. Each mail is looked at once (``large_link_checked``).
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path

from .scraper import BUTTON_NAMES, InboxScraper, MailRef, is_large_file_link
from .state import StateDB
from .writer import _yaml_escape, write_mail


log = logging.getLogger(__name__)

LARGE_BOX_MARK = "대용량 첨부파일"
BACKFILL_DAYS = 30
# Re-reads per sync run; the sync wrapper stops a run after 15 minutes.
BACKFILL_MAX_PER_RUN = 3


def drop_listed_names(md: str, names: Iterable[str]) -> str:
    """Remove `names` from a mail Markdown's front-matter list and its ``## Attachments`` section."""
    names = frozenset(names)
    lines = md.split("\n")
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        return md
    end = lines.index("---", 1)
    items = {f"  - {_yaml_escape(n)}" for n in names}
    front = [line for line in lines[1:end] if line not in items]
    for i, line in enumerate(front):
        if line == "attachments:" and not (i + 1 < len(front) and front[i + 1].startswith("  - ")):
            front[i] = "attachments:[]"

    links = tuple(f"- [{n}](" for n in names)
    body: list[str] = []
    section: list[str] | None = None
    for line in lines[end + 1:] + ["## "]:  # sentinel closes a trailing section
        if line.startswith("## "):
            if section is not None and any(s.startswith("- [") for s in section):
                body.extend(section)
            section = [line] if line == "## Attachments" else None
            if section is None:
                body.append(line)
            continue
        if section is not None:
            if not line.startswith(links):
                section.append(line)
            continue
        body.append(line)
    body.pop()  # the sentinel
    return "\n".join(["---", *front, "---", *body])


def prune_buttons(db: StateDB, project_root: Path, attachments_dir: Path) -> int:
    """Drop button rows, Markdown entries and (when safe) files. Returns the number of mails healed."""
    healed = 0
    for uid in db.uids_with_attachment_names(BUTTON_NAMES):
        rows = db.attachment_rows(uid)
        real = [r for r in rows if r["filename"] not in BUTTON_NAMES]
        msg = db.message_row(uid)
        md = project_root / msg["markdown_path"] if msg and msg.get("markdown_path") else None
        if md is not None and md.exists():
            text = md.read_text(encoding="utf-8")
            new = drop_listed_names(text, BUTTON_NAMES)
            if new != text:
                md.write_text(new, encoding="utf-8")
        if real and all(r["status"] == "ok" for r in real):
            for r in rows:
                if r["filename"] in BUTTON_NAMES:
                    (attachments_dir / uid / InboxScraper._sanitize_filename(r["filename"])).unlink(
                        missing_ok=True)
        db.delete_attachments(uid, BUTTON_NAMES)
        healed += 1
    if healed:
        log.info("removed webmail buttons listed as attachments from %d mail(s)", healed)
    return healed


def _needs_backfill(db: StateDB, project_root: Path, msg: dict) -> bool:
    md = project_root / (msg.get("markdown_path") or "")
    if not msg.get("markdown_path") or not md.exists():
        return False
    if LARGE_BOX_MARK not in md.read_text(encoding="utf-8"):
        return False
    return not any(is_large_file_link(r["href"]) for r in db.attachment_rows(msg["uid"]))


def _folder_uid(folder: str, scraper: InboxScraper) -> str | None:
    if folder == "inbox":
        return scraper.folder_uid
    if folder.startswith("folder:"):
        return folder.removeprefix("folder:")
    return None


def backfill_large_links(
    db: StateDB, scraper: InboxScraper, mails_dir: Path, project_root: Path,
    *, now: datetime | None = None,
) -> int:
    """Re-read recent mails whose large-file links were missed. Returns the number re-read."""
    since = ((now or datetime.now()) - timedelta(days=BACKFILL_DAYS)).isoformat(timespec="seconds")
    tried = reread = 0
    for msg in db.unchecked_messages_since(since):
        if tried >= BACKFILL_MAX_PER_RUN:
            break
        uid = msg["uid"]
        folder_uid = _folder_uid(msg["folder"], scraper)
        if folder_uid is None or not _needs_backfill(db, project_root, msg):
            db.mark_large_link_checked(uid)
            continue
        tried += 1
        ref = MailRef(
            uid=uid, folder_uid=folder_uid, subject=msg["subject"] or "", sender=msg["sender"] or "",
            to="", date=datetime.fromisoformat(msg["recv_date"]), size=0, unread=False,
            flagged=False, attach_count=0,
        )
        try:
            mail = scraper.read_mail(ref, state_db=db)
        except Exception as e:  # the next run tries again while the mail is inside the window
            log.warning("large-link backfill: re-read failed uid=%s: %s", uid, str(e)[:200])
            continue
        mail.folder = msg["folder"]
        old = project_root / msg["markdown_path"]
        md_path = write_mail(mail, mails_dir, project_root)
        if old != md_path:
            old.unlink(missing_ok=True)
            old.with_suffix(".html").unlink(missing_ok=True)
        db.record_message(
            uid, folder=msg["folder"], subject=mail.subject, sender=mail.sender,
            recv_date=msg["recv_date"], markdown_path=str(md_path.relative_to(project_root)),
        )
        db.mark_large_link_checked(uid)
        reread += 1
        log.info("large-link backfill: uid=%s now lists %d attachment(s)", uid, len(mail.attachments))
    return reread
