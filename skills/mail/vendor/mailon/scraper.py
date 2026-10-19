"""Scrape the mailon.kr inbox using its internal JSON APIs.

REVERSE-ENGINEERED APIs (Crinity G-Cloud):

  1) POST /mail/list_async.json
     Body (URL-encoded form):
       folderUid=<id>, page=<n>, listCountPerPage=20, currentPage=<n>,
       sortField=timeMillis, sortDir=DESC, start=<n*20>, limit=20,
       includeContent=false
     Response (JSON):
       { "result": true,
         "folder": { "folderUid": 49527, "newMsgNum": 220, ... },
         "contents": [
           { "mailUid": 7425666, "folderUid": 49527,
             "adrFrom": "..., adrFromName, adrFromEml,
             "adrTo": "...", "subject": "...",
             "timeMillis": 1776836838000, "attachCount": 0,
             "isSeen": 1, "isFlagged": 0, "msgSize": 118742, ... },
           ...
         ],
         "pagingInfo": {...}
       }

  2) GET /mail/view_async?mailUid=<uid>&folderUid=<fid>&_dc=<ms>
     Response: HTML fragment with:
       <input id="timeMillis" value="..." />         (Unix ms)
       <input id="userEmail" value="..." />
       <div id="mvw_wrap">
         <div class="hd"><h4>...subject...</h4></div>
         <li id="from-item-0" data-address="..." data-personal="..." />
         <li id="to-item-0"   data-address="..." data-personal="..." />
         <li id="cc-item-0"   ... />                 (optional)
         <div class="bd">
           <div class="ct">...full body HTML...</div>
         </div>
       </div>

  3) INBOX folderUid resolution:
     Harvested once from the DOM (sidebar link with name="받은메일함")
     or from any mail row's onclick attribute.

This eliminates all DOM-scraping fragility for listing + parsing.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Generator
from urllib.parse import urljoin, urlencode, urlsplit

from bs4 import BeautifulSoup

from .browser import AgentBrowser, BrowserError
from .folders import _fetch_all_folder_page, recover_inbox_uid
from .state import StateDB
from .writer import Attachment, Mail


log = logging.getLogger(__name__)


# ------------------------------------------------------------ constants

MAILON_ORIGIN = "https://mailon.kr"
LIST_ENDPOINT = "/mail/list_async.json"
VIEW_ENDPOINT = "/mail/view_async"
PAGE_SIZE = 20

# Attachment download strategy thresholds.
# Small attachments: use in-browser fetch + base64 (simple, fast).
# Large attachments: extract cookies from browser session and download
# via Python urllib streaming to disk (no memory limit, streams 1GB+).
SMALL_ATTACHMENT_MAX_BYTES = 30 * 1024 * 1024   # 30 MB
# Mailplug "대용량 첨부" links point outside the webmail origin, so an in-page fetch() fails
# with a CORS "Failed to fetch". They are public download URLs (no login, 2026-10-19 check), so
# they are fetched directly — never with the webmail session cookies, which stay on mailon.kr.
LARGE_FILE_HOSTS = frozenset({"filefree.mailplug.com"})
# MailOn's own "대용량 첨부파일" box links to mailon.kr/bigfileDownload/<token>/ (mixed case, so the
# '/download' selector never matched it). Also public: 200 + the file without cookies (2026-10-19).
# Other institutes run the same webmail (e.g. a sender's own mail host), with the same path.
MAILON_LARGE_FILE_PATH = "/bigfileDownload/"
# Links that failed under the old in-page fetch get a fresh retry budget on top of the usual 5.
EXTERNAL_MAX_ATTEMPTS = 10
# Webmail buttons that look like attachment links: English UI "Save" (the file's own href) and
# "Download all" (a zip of every file), the large-file box's "PC에 저장", and the Korean labels.
BUTTON_NAMES = frozenset({
    "모두저장", "저장", "미리보기", "전체저장", "다운로드", "Save", "Download all", "PC에 저장",
})


def is_large_file_link(href: str) -> bool:
    parts = urlsplit(href or "")
    if parts.scheme == "https" and parts.path.startswith(MAILON_LARGE_FILE_PATH):
        return True
    return parts.hostname in LARGE_FILE_HOSTS
URLLIB_CHUNK_SIZE = 1024 * 1024                  # 1 MB per read
MAX_MAIL_FETCH_ATTEMPTS = 3
MAIL_FETCH_RETRY_DELAY_S = 2.0


# ------------------------------------------------------------ helpers

def _unwrap_eval(raw: str):
    """Unwrap agent-browser eval output and JSON-parse."""
    raw = raw.strip()
    if raw.startswith('"') and raw.endswith('"'):
        raw = json.loads(raw)
    return json.loads(raw) if isinstance(raw, str) else raw


def parse_korean_date(text: str) -> datetime | None:
    """Parse list-view date strings like '26.04.20 13:28'."""
    if not text:
        return None
    text = re.sub(r"\([^)]*\)", "", text).strip()
    patterns = [
        "%y.%m.%d %H:%M",
        "%Y.%m.%d %H:%M:%S",
        "%Y.%m.%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y.%m.%d",
        "%Y-%m-%d",
        "%y.%m.%d",
    ]
    for fmt in patterns:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def parse_millis(ms: int | str | None) -> datetime | None:
    """Convert Unix millisecond timestamp to datetime."""
    if ms is None:
        return None
    try:
        n = int(ms)
        if n <= 0:
            return None
        return datetime.fromtimestamp(n / 1000)
    except (ValueError, TypeError, OSError):
        return None


@dataclass
class MailRef:
    """Metadata for one mail in the inbox list (from list_async.json)."""
    uid: str
    folder_uid: str
    subject: str
    sender: str          # adrFrom (formatted string)
    to: str              # adrTo
    date: datetime | None
    size: int            # msgSize (bytes)
    unread: bool
    flagged: bool
    attach_count: int


# ---------------------------------------------------- filename helpers

# Matches trailing " <digits>" where digits = byte size reported by UI.
# Observed pattern: "파일명.확장자 547980148" (size = 547 MB mp4).
# We require at least 4 digits to avoid eating legitimate numeric suffixes.
_TRAILING_SIZE_RE = re.compile(r"\s+(\d{4,})\s*$")
# Also seen (2024-2026 node data, 25 rows): "파일명.pptx (1.37 MB)" / "파일명.mp4 12.9 MB".
_TRAILING_HUMAN_SIZE_RE = re.compile(r"\s*\(?\s*(\d+(?:\.\d+)?)\s*([KMG])B\s*\)?\s*$", re.I)
_UNIT = {"K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}


def split_filename_and_size(raw: str) -> tuple[str, int | None]:
    """Split '파일.mp4 547980148' into ('파일.mp4', 547980148).

    mailon.kr's UI renders attachment links as "<filename> <bytes>" in
    the text content; our scraper picks up both. Strip the trailing size.

    Also handles common variants:
      - 'file.pdf 123456'      -> ('file.pdf', 123456)
      - 'file.pdf'             -> ('file.pdf', None)
      - 'file 123 v2.pdf 456'  -> ('file 123 v2.pdf', 456)   (only trailing)
      - '이름.hwp'             -> ('이름.hwp', None)
    """
    if not raw:
        return "", None
    s = raw.strip()
    m = _TRAILING_SIZE_RE.search(s)
    if not m:
        human = _TRAILING_HUMAN_SIZE_RE.search(s)
        name = s[:human.start()].strip() if human else ""
        if human and "." in name:
            return name, int(float(human.group(1)) * _UNIT[human.group(2).upper()])
        return s, None
    try:
        size = int(m.group(1))
    except ValueError:
        return s, None
    return _TRAILING_SIZE_RE.sub("", s).strip(), size


# ---------------------------------------------------- view_async parser

def _format_address(personal: str, address: str) -> str:
    """Format 'Name <email>' or just 'email'."""
    personal = (personal or "").strip()
    address = (address or "").strip()
    if personal and address:
        return f'"{personal}" <{address}>'
    return address or personal or ""


def parse_view_async_html(html: str) -> dict:
    """Parse /mail/view_async response HTML into structured fields."""
    soup = BeautifulSoup(html, "lxml")

    out: dict = {
        "subject": "", "from": "", "to": "", "cc": "",
        "date": None, "body_html": "", "body_text": "",
        "attachments": [],
    }

    # Hidden inputs (timeMillis, mailUid, etc.)
    hidden = {}
    for inp in soup.select('input[type="hidden"][id]'):
        hidden[inp.get("id")] = inp.get("value", "")
    tm = hidden.get("timeMillis")
    if tm and str(tm).isdigit():
        out["date"] = parse_millis(tm)

    # Subject: <div class="hd"> <h4>...subject...</h4> </div>
    subject = ""
    for sel in ("#mvw_wrap .hd h4", ".hd h4", "#mvw_wrap .hd"):
        el = soup.select_one(sel)
        if el:
            text = re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()
            if text and len(text) < 500:
                subject = text
                break
    out["subject"] = subject

    # From / To / Cc
    for kind, key in (("from", "from-item-"), ("to", "to-item-"), ("cc", "cc-item-")):
        parts: list[str] = []
        for li in soup.select(f'li[id^="{key}"]'):
            addr = str(li.get("data-address") or "").strip()
            personal = str(li.get("data-personal") or "").strip()
            pair = _format_address(personal, addr)
            if pair:
                parts.append(pair)
        out[kind] = ", ".join(parts)

    # Body
    body_el = soup.select_one("#mvw_wrap .bd .ct") \
        or soup.select_one(".bd .ct") \
        or soup.select_one("#mvw_wrap .bd") \
        or soup.select_one(".bd")
    if body_el:
        out["body_html"] = body_el.decode_contents()
        out["body_text"] = body_el.get_text("\n", strip=True)

    # Attachments - filter out UI buttons (BUTTON_NAMES) and the zip-of-everything link
    seen = set()
    for sel in ('a[href*="/mail/download"]', 'a[href*="/download"]', 'a[href*="attach"]',
                f'a[href*="{MAILON_LARGE_FILE_PATH}"]'):
        for a in soup.select(sel):
            link = attachment_link(
                str(a.get("href") or ""),
                str(a.get("data-filename") or "")
                or str(a.get("download") or "")
                or a.get_text(" ", strip=True),
            )
            if link is None:
                continue
            key = (link["href"], link["filename"])
            if key in seen:
                continue
            seen.add(key)
            out["attachments"].append(link)

    return out


def attachment_link(href: str, raw_name: str) -> dict | None:
    """One attachment from a link, or None for scripts, buttons and the zip-of-everything link."""
    if not href or href.startswith("javascript") or "allDownload=true" in href:
        return None
    # Strip trailing size bytes (mailon.kr's UI quirk: "file.mp4 547980148")
    name, declared_size = split_filename_and_size(raw_name.strip())
    if not name or name in BUTTON_NAMES:
        return None
    return {"filename": name, "href": href, "declared_size": declared_size}


# ------------------------------------------------------------ scraper class

class InboxScraper:
    """Scrape inbox using Crinity JSON APIs.

    Usage:
        scr = InboxScraper(browser, attachments_dir)
        scr.resolve_inbox_folder_uid()   # once, after login
        for mail in scr.iter_new_mails(skip_uids, limit=0):
            ...
    """

    def __init__(
        self, browser: AgentBrowser, attachments_dir: Path,
        *, folder_label: str = "inbox", all_folders: bool = False,
    ) -> None:
        self.browser = browser
        self.attachments_dir = attachments_dir
        self.folder_label = folder_label
        self.all_folders = all_folders
        self.folder_uid: str | None = None

    # ------------------------------------------------------------ setup

    def resolve_inbox_folder_uid(
        self, *, timeout_s: float = 90.0, poll_interval_s: float = 3.0,
    ) -> str:
        """Determine the inbox's folderUid from any mail row's onclick.

        The SPA renders the mail list tens of seconds after login on slow
        days (2026-07-20: one-shot probes failed repeatedly), so poll until
        the deadline like the send path does.
        """
        # Try navigating via sidebar text link - any failure is OK as long as
        # mail rows become visible.
        for text in ("받은메일함", "Inbox", "INBOX"):
            try:
                self.browser.find_click("text", text, exact=False)
                self.browser.wait_ms(1500)
                break
            except BrowserError:
                continue

        # Extract folderUid from any .mail-metadata anchor
        js = r"""
        (() => {
          const a = document.querySelector('a.mail-metadata[onclick*="folderUid"]');
          if (!a) return null;
          const m = (a.getAttribute('onclick')||'').match(/folderUid:'(\d+)'/);
          return m ? m[1] : null;
        })()
        """
        deadline = time.monotonic() + timeout_s
        attempts = 0
        while True:
            attempts += 1
            raw = self.browser.eval_js(js).strip()
            if raw.startswith('"') and raw.endswith('"'):
                raw = json.loads(raw)
            if raw not in (None, "null", ""):
                self.folder_uid = str(raw)
                log.info("inbox folderUid resolved: %s (attempt %d)",
                         self.folder_uid, attempts)
                return self.folder_uid
            if time.monotonic() >= deadline:
                found = self._resolve_without_rows(poll_interval_s)
                if found:
                    self.folder_uid = found[0]
                    log.warning("inbox folderUid %s resolved by %s "
                                "(list rows never rendered after %d attempt(s))",
                                found[0], found[1], attempts)
                    return found[0]
                raise RuntimeError(
                    "could not resolve inbox folderUid from DOM after "
                    f"{attempts} attempt(s)")
            self.browser.wait_ms(int(poll_interval_s * 1000))

    def _resolve_without_rows(self, poll_interval_s: float) -> tuple[str, str] | None:
        """Recovery when the mail list never renders (2026-10-06: rows stayed
        at 0 while the sidebar and its folder data were present): reload, then
        a label-anchored uid only if the list API confirms it is that folder."""
        return recover_inbox_uid(self.browser, self._list_api_confirms,
                                 poll_s=poll_interval_s)

    def _list_api_confirms(self, uid: str) -> bool:
        saved = (self.folder_uid, self.all_folders)
        self.folder_uid, self.all_folders = uid, False
        try:
            data = self.fetch_list_page(1)
        except Exception as error:
            log.warning("list_async check for folderUid %s failed: %s", uid, str(error)[:150])
            return False
        finally:
            self.folder_uid, self.all_folders = saved
        folder = data.get("folder") or {}
        return bool(data.get("result")) and str(folder.get("folderUid")) == uid

    # ------------------------------------------------------ list API

    def fetch_list_page(self, page: int) -> dict:
        """Call POST /mail/list_async.json for one page of the inbox.

        Returns the parsed JSON response.
        """
        assert self.folder_uid, "call resolve_inbox_folder_uid() first"
        if self.all_folders:
            return {
                "result": True,
                "contents": _fetch_all_folder_page(self.browser, self.folder_uid, page),
            }
        start = (page - 1) * PAGE_SIZE
        form_params = {
            "tenant": "",
            "value": "0",
            "longValue": "0",
            "listCountPerPage": str(PAGE_SIZE),
            "currentPage": str(page),
            "sortField": "timeMillis",
            "sortDir": "DESC",
            "folderUid": self.folder_uid,
            "allFolder": "false",
            "includeContent": "false",
            "page": str(page),
            "start": str(start),
            "limit": str(PAGE_SIZE),
            "sort": "timeMillis",
            "dir": "DESC",
        }
        # URL-encoded form body, UTF-8 safe
        body = urlencode(form_params)

        js = (
            "(async () => {"
            f"  const r = await fetch({json.dumps(LIST_ENDPOINT)}, {{"
            "     method:'POST',"
            "     credentials:'include',"
            "     headers:{'Content-Type':'application/x-www-form-urlencoded'},"
            f"    body:{json.dumps(body)}"
            "  });"
            "   if (!r.ok) throw new Error('HTTP ' + r.status);"
            "   return await r.text();"
            "})()"
        )
        raw = self.browser.eval_js(js).strip()
        if raw.startswith('"') and raw.endswith('"'):
            raw = json.loads(raw)
        return json.loads(raw)

    def list_inbox(
        self, max_pages: int = 500, *, known: frozenset[str] = frozenset(),
    ) -> list[MailRef]:
        """Iterate /mail/list_async.json until empty/exhausted; return all MailRefs.

        The list is newest-first, so with `known` (already-saved uids) the walk
        stops at the first page made only of known mails (2026-10-06: a
        9366-mail inbox at ~2.3s/page outlived the 900s wrapper timeout).
        """
        assert self.folder_uid
        refs: list[MailRef] = []
        seen: set[str] = set()

        for page in range(1, max_pages + 1):
            try:
                data = self.fetch_list_page(page)
            except Exception as e:
                log.warning("list_async page %d failed: %s", page, e)
                break

            if not data.get("result"):
                log.warning("list_async returned result=false on page %d", page)
                break

            contents = data.get("contents") or []
            if not contents:
                log.info("no more mails after page %d", page - 1)
                break

            page_new = 0
            for item in contents:
                uid = str(item.get("mailUid") or "")
                if not uid or uid in seen:
                    continue
                seen.add(uid)
                # NOTE: isSeen is 0 for unread, 1 for read. Must use `!= None`
                # pattern, NOT `or 1` (which would treat 0 as "no value").
                is_seen_val = item.get("isSeen")
                is_seen = int(is_seen_val) if is_seen_val is not None else 1
                is_flag_val = item.get("isFlagged")
                is_flag = int(is_flag_val) if is_flag_val is not None else 0
                refs.append(MailRef(
                    uid=uid,
                    folder_uid=str(item.get("folderUid") or self.folder_uid),
                    subject=str(item.get("subject") or ""),
                    sender=str(item.get("adrFrom") or ""),
                    to=str(item.get("adrTo") or ""),
                    date=parse_millis(item.get("timeMillis")),
                    size=int(item.get("msgSize") or 0),
                    unread=(is_seen == 0),
                    flagged=(is_flag != 0),
                    attach_count=int(item.get("attachCount") or 0),
                ))
                page_new += 1

            total = data.get("folder", {}).get("newMsgNum")
            log.info("list page %d: %d items (%d new; running total %d; unread badge %s)",
                     page, len(contents), page_new, len(refs), total)

            if page_new == 0:
                # Duplicates only means we've looped
                break
            if known and all(str(item.get("mailUid") or "") in known for item in contents):
                log.info("page %d holds only already-saved mails; stopping the walk", page)
                break
            if len(contents) < PAGE_SIZE:
                # Short page = last page
                log.info("last page reached (page %d, %d < %d)",
                         page, len(contents), PAGE_SIZE)
                break

        return refs

    # ---------------------------------------------------- mail detail

    def fetch_mail_html(
        self, uid: str, folder_uid: str,
        *, attempts: int = MAX_MAIL_FETCH_ATTEMPTS,
    ) -> str:
        """Fetch /mail/view_async HTML for a single mail. Retries on transient errors."""
        last_err: Exception | None = None
        for attempt in range(1, attempts + 1):
            cache_bust = int(time.time() * 1000)
            url = f"{VIEW_ENDPOINT}?mailUid={uid}&folderUid={folder_uid}&_dc={cache_bust}"
            js = (
                "(async () => {"
                f"  const r = await fetch({json.dumps(url)}, {{credentials:'include'}});"
                "   if (!r.ok) throw new Error('HTTP ' + r.status);"
                "   return await r.text();"
                "})()"
            )
            try:
                raw = self.browser.eval_js(js).strip()
                if raw.startswith('"') and raw.endswith('"'):
                    raw = json.loads(raw)
                return raw
            except BrowserError as e:
                last_err = e
                if attempt < attempts:
                    log.info("view_async fetch attempt %d/%d failed for uid=%s: %s; retrying",
                             attempt, attempts, uid, str(e)[:120])
                    time.sleep(MAIL_FETCH_RETRY_DELAY_S * attempt)
                else:
                    log.warning("view_async fetch uid=%s exhausted %d attempts",
                                uid, attempts)
        assert last_err is not None
        raise last_err

    def read_mail(self, ref: MailRef, state_db: StateDB | None = None) -> Mail:
        """Fetch view_async HTML and parse it into a Mail object.

        If `state_db` is provided:
          - Attachments already downloaded (status='ok') are SKIPPED
          - Attachment outcomes are recorded (ok or fail + error_msg)
        """
        html = self.fetch_mail_html(ref.uid, ref.folder_uid)
        parsed = parse_view_async_html(html)

        # Prefer parsed fields; fall back to list-API fields
        subject = parsed["subject"] or ref.subject
        sender = parsed["from"] or ref.sender
        date = parsed["date"] or ref.date

        folder = self.folder_label
        if self.all_folders:
            folder = "inbox" if ref.folder_uid == self.folder_uid else f"folder:{ref.folder_uid}"
        mail = Mail(
            uid=ref.uid,
            folder=folder,
            subject=subject.strip(),
            sender=sender.strip(),
            to=(parsed["to"] or ref.to).strip(),
            cc=parsed["cc"].strip(),
            date=date,
            body_text=parsed["body_text"],
            body_html=parsed["body_html"],
        )

        for att in parsed["attachments"]:
            filename = att["filename"]
            href = att["href"]
            declared_size = att.get("declared_size")
            full_url = urljoin(MAILON_ORIGIN, href)

            # Skip already-downloaded attachments (duplicate prevention)
            if state_db is not None and state_db.has_attachment_ok(
                ref.uid, filename
            ):
                # Reattach to Mail from known local path
                target = (
                    self.attachments_dir / str(ref.uid)
                    / self._sanitize_filename(filename)
                )
                if target.exists():
                    mail.attachments.append(Attachment(
                        filename=filename, local_path=target,
                        size=target.stat().st_size,
                    ))
                    log.debug("attachment already saved uid=%s %s",
                              ref.uid, filename)
                    continue

            try:
                saved = self._download_attachment(
                    ref.uid, filename, full_url, declared_size=declared_size,
                )
                mail.attachments.append(saved)
                if state_db is not None:
                    state_db.record_attachment(
                        ref.uid, filename=filename, href=full_url,
                        status="ok", size_bytes=saved.size,
                        local_path=str(saved.local_path),
                    )
            except Exception as e:
                err_msg = str(e)[:500]
                log.warning("attachment failed uid=%s name=%r: %s",
                            ref.uid, filename, err_msg)
                if state_db is not None:
                    state_db.record_attachment(
                        ref.uid, filename=filename, href=full_url,
                        status="fail", size_bytes=declared_size,
                        error_msg=err_msg,
                    )

        return mail

    # ---------------------------------------------------- attachments

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        """Return a filesystem-safe version of an attachment filename."""
        safe = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name).strip()
        return safe or "attachment.bin"

    def _download_attachment(
        self, uid: str, filename: str, href: str,
        *, declared_size: int | None = None,
    ) -> Attachment:
        """Save attachment using size-appropriate strategy.

        - declared_size <= SMALL_ATTACHMENT_MAX_BYTES (or unknown): in-browser
          fetch + base64 → Python decodes. Fast, simple. ~30MB memory ceiling.
        - declared_size >  SMALL_ATTACHMENT_MAX_BYTES: extract cookies from the
          agent-browser session, stream via Python urllib to disk. No memory
          limit. Handles 1GB+ files.
        """
        target_dir = self.attachments_dir / str(uid)
        target_dir.mkdir(parents=True, exist_ok=True)
        safe_name = self._sanitize_filename(filename)
        target = target_dir / safe_name

        if is_large_file_link(href):
            log.info("large-file link; downloading directly: uid=%s %s", uid, filename)
            self._download_large_via_urllib(href, target, cookies=False)
        elif declared_size is not None and declared_size > SMALL_ATTACHMENT_MAX_BYTES:
            log.info("large attachment (%d bytes > %d); streaming via urllib: uid=%s %s",
                     declared_size, SMALL_ATTACHMENT_MAX_BYTES, uid, filename)
            self._download_large_via_urllib(href, target)
        else:
            self._download_small_via_browser(href, target)

        size = target.stat().st_size
        log.info("saved attachment uid=%s %s (%d bytes)",
                 uid, target.name, size)
        return Attachment(filename=filename, local_path=target, size=size)

    def _download_small_via_browser(self, href: str, target: Path) -> None:
        """In-browser fetch + base64 (small files only)."""
        js = (
            "(async () => { "
            "const r = await fetch(" + json.dumps(href) + ", "
                     "{credentials:'include'}); "
            "if (!r.ok) throw new Error('HTTP ' + r.status); "
            "const buf = await r.arrayBuffer(); "
            "const b = new Uint8Array(buf); "
            "const CHUNK = 8192; "
            "let s = ''; "
            "for (let i=0; i<b.length; i+=CHUNK) "
            "  s += String.fromCharCode.apply(null, b.subarray(i, i+CHUNK)); "
            "return btoa(s); "
            "})()"
        )
        raw = self.browser.eval_js(js).strip()
        if raw.startswith('"') and raw.endswith('"'):
            raw = json.loads(raw)
        target.write_bytes(base64.b64decode(raw))

    def _download_large_via_urllib(self, href: str, target: Path, *, cookies: bool = True) -> None:
        """Stream a download via Python urllib, with the webmail session cookies unless told not to."""
        cookie_header = self._get_cookie_header_from_browser() if cookies else ""
        # User-Agent from browser matters less, but we mimic Chrome for safety
        req = urllib.request.Request(
            href,
            headers={
                **({"Cookie": cookie_header} if cookie_header else {}),
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "*/*",
                "Referer": MAILON_ORIGIN + "/mail",
            },
        )

        # urllib.request.urlopen streams the response; copy to disk in chunks
        tmp_path = target.with_suffix(target.suffix + ".part")
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                # An expired large-file link answers 200 with MailOn's HTML notice page, not the file.
                if not cookies and resp.headers.get_content_type() == "text/html":
                    raise RuntimeError("large-file link returned an HTML page (expired link)")
                with tmp_path.open("wb") as f:
                    while True:
                        chunk = resp.read(URLLIB_CHUNK_SIZE)
                        if not chunk:
                            break
                        f.write(chunk)
            tmp_path.replace(target)
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise RuntimeError(f"urllib download failed: {e}") from e

    def _get_cookie_header_from_browser(self) -> str:
        """Export cookies from the agent-browser session and format as
        a Cookie header for urllib requests to mailon.kr.

        agent-browser's `state save <path>` writes JSON with a `cookies` array.
        We filter to cookies whose domain matches mailon.kr.
        """
        import tempfile
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".state.json", delete=False, encoding="utf-8"
        ) as tf:
            state_path = Path(tf.name)
        try:
            self.browser.save_state(state_path)
            data = json.loads(state_path.read_text(encoding="utf-8"))
        finally:
            try:
                state_path.unlink()
            except OSError:
                pass

        cookies = data.get("cookies") or []
        pairs: list[str] = []
        for c in cookies:
            name = c.get("name")
            value = c.get("value")
            domain = (c.get("domain") or "").lstrip(".")
            if not name or value is None:
                continue
            # Match mailon.kr cookies (include subdomains)
            if domain and "mailon.kr" not in domain:
                continue
            pairs.append(f"{name}={value}")
        if not pairs:
            raise RuntimeError("no mailon.kr cookies found in browser state")
        return "; ".join(pairs)

    # ---------------------------------------------------- iteration

    def iter_new_mails(
        self, skip_uids: set[str], *, limit: int = 0,
        state_db: StateDB | None = None, full_scan: bool = False,
    ) -> Generator[Mail, None, None]:
        """Yield Mail records for every inbox message not in `skip_uids`.

        Duplicate prevention:
          - skip_uids: messages already fully saved (body + Markdown)
          - state_db: per-attachment tracking for partial-failure retry
        """
        refs = self.list_inbox(known=frozenset() if full_scan else frozenset(skip_uids))
        log.info("total inbox: %d mails (skipping %d already-saved)",
                 len(refs), len(skip_uids))
        new_refs = [r for r in refs if r.uid not in skip_uids]
        log.info("new mails to fetch: %d", len(new_refs))

        count = 0
        for ref in new_refs:
            if limit and count >= limit:
                log.info("reached limit=%d; stopping", limit)
                break
            try:
                yield self.read_mail(ref, state_db=state_db)
                count += 1
            except Exception as e:
                log.error("failed uid=%s subject=%r: %s",
                          ref.uid, ref.subject, e)

    def retry_failed_attachments(
        self, state_db: StateDB, *, max_attempts: int = 5,
    ) -> tuple[int, int]:
        """Retry attachments previously marked 'fail'. Returns (succeeded, still_failing)."""
        failed = [
            rec for rec in state_db.all_failed_attachments(max_attempts=EXTERNAL_MAX_ATTEMPTS)
            if rec["attempts"] < max_attempts or is_large_file_link(rec["href"])
        ]
        if not failed:
            return (0, 0)
        log.info("retrying %d previously-failed attachment(s)", len(failed))

        succeeded = 0
        still_failing = 0
        for rec in failed:
            uid = rec["uid"]
            filename = rec["filename"]
            href = rec["href"]
            declared_size = rec.get("size_bytes")
            try:
                full_url = urljoin(MAILON_ORIGIN, href) if href else href
                saved = self._download_attachment(
                    uid, filename, full_url, declared_size=declared_size,
                )
                state_db.record_attachment(
                    uid, filename=filename, href=full_url,
                    status="ok", size_bytes=saved.size,
                    local_path=str(saved.local_path),
                )
                succeeded += 1
                log.info("retry OK: uid=%s %s", uid, filename)
            except Exception as e:
                state_db.record_attachment(
                    uid, filename=filename, href=href,
                    status="fail", size_bytes=declared_size,
                    error_msg=str(e)[:500],
                )
                still_failing += 1
                log.warning("retry STILL FAILED: uid=%s %s: %s",
                            uid, filename, str(e)[:200])
        return (succeeded, still_failing)

    # --------------------------------------------------------- probe

    def probe_and_dump(self, out_dir: Path) -> Path:
        """Dump current page AX + HTML for offline analysis."""
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        ax_path = out_dir / f"probe-{ts}-ax.txt"
        html_path = out_dir / f"probe-{ts}.html"
        url_path = out_dir / f"probe-{ts}-url.txt"

        ax_path.write_text(self.browser.snapshot(True), encoding="utf-8")
        raw = self.browser.eval_js("document.documentElement.outerHTML").strip()
        if raw.startswith('"') and raw.endswith('"'):
            raw = json.loads(raw)
        html_path.write_text(raw, encoding="utf-8")
        url_path.write_text(self.browser.current_url(), encoding="utf-8")
        log.info("probe dumped: %s", ax_path.name)
        return ax_path

    # ----------------------------------------- backward-compat alias

    def goto_inbox(self) -> None:
        """Alias for resolve_inbox_folder_uid() for backward compat."""
        self.resolve_inbox_folder_uid()
