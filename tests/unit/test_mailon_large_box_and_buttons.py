"""MailOn's own large-file box (bigfileDownload) is collected; webmail buttons are not attachments."""
from __future__ import annotations

import http.server
import importlib.util
import sys
import threading
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _installed(name: str) -> bool:
    if name in sys.modules:
        return getattr(sys.modules[name], "__file__", None) is not None
    return importlib.util.find_spec(name) is not None


# The HTML parser needs bs4+lxml, which the CI image does not carry; the rest runs everywhere.
HAVE_PARSER = _installed("bs4") and _installed("lxml")
needs_parser = pytest.mark.skipif(not HAVE_PARSER, reason="bs4/lxml not installed")
for name in ("pyotp", "dotenv", "bs4"):
    if name not in sys.modules:
        try:
            __import__(name)
        except ImportError:
            stub = types.ModuleType(name)
            stub.TOTP = stub.load_dotenv = stub.BeautifulSoup = object  # type: ignore[attr-defined]
            sys.modules[name] = stub
sys.path.insert(0, str(ROOT / "skills/mail/vendor"))

from mailon import heal, scraper as scraper_mod  # noqa: E402
from mailon.state import StateDB  # noqa: E402
from mailon.writer import Attachment, Mail, write_mail  # noqa: E402

BIG_A = "https://mailon.kr:443/bigfileDownload/QUFBQQ==/"
BIG_B = "https://mailon.kr:443/bigfileDownload/QkJCQg==/"

# Shape of the real view (2026-10-19): each file has a name link and a "PC에 저장" link to the same URL.
LARGE_BOX = f"""
<div id="mvw_wrap"><div class="hd"><h4>spec</h4></div><div class="bd"><div class="ct">
<strong>대용량 첨부파일</strong> <strong>2 개</strong>(33.6MB)
<a href="{BIG_A}" target="_blank"> 규격서-최종.pdf </a> <span>(19.2MB)</span>
<span><a href="{BIG_A}" target="_blank">PC에 저장</a></span>
<a href="{BIG_B}" target="_blank"> 규격서-표시.pdf </a> <span>(14.4MB)</span>
<span><a href="{BIG_B}" target="_blank">PC에 저장</a></span>
<p>본문</p></div></div></div>
"""

# Shape of the English webmail attachment bar: "Download all" zip plus a "Save" per file.
BUTTON_BAR = """
<div id="mvw_wrap"><div class="hd"><h4>draft</h4></div><div class="bd"><div class="ct">body</div></div>
<a href="https://mailon.kr/mail/manage/attach/download?mailUid=7&allDownload=true">Download all</a>
<a href="https://mailon.kr/mail/manage/attach/download?mailUid=7&position=4">1. 과업지시서.hwpx</a>
<a href="https://mailon.kr/mail/manage/attach/download?mailUid=7&position=4">Save</a>
<a href="https://mailon.kr/mail/manage/attach/download?mailUid=7&position=5">3. 사유서.hwpx</a>
<a href="https://mailon.kr/mail/manage/attach/download?mailUid=7&position=5">Save</a>
</div>
"""


def test_button_and_zip_links_are_not_attachments():
    file_href = "https://mailon.kr/mail/manage/attach/download?mailUid=7&position=4"
    assert scraper_mod.attachment_link(file_href, "Save") is None
    assert scraper_mod.attachment_link(BIG_A, "PC에 저장") is None
    assert scraper_mod.attachment_link(file_href.replace("position=4", "allDownload=true"), "x.zip") is None
    assert scraper_mod.attachment_link(file_href, " 1. 과업지시서.hwpx ") == {
        "filename": "1. 과업지시서.hwpx", "href": file_href, "declared_size": None}


@needs_parser
def test_the_large_file_box_yields_its_files_and_not_its_save_links():
    out = scraper_mod.parse_view_async_html(LARGE_BOX)
    assert [(a["filename"], a["href"]) for a in out["attachments"]] == [
        ("규격서-최종.pdf", BIG_A), ("규격서-표시.pdf", BIG_B)]
    assert all(scraper_mod.is_large_file_link(a["href"]) for a in out["attachments"])


@needs_parser
def test_webmail_buttons_are_not_attachments():
    out = scraper_mod.parse_view_async_html(BUTTON_BAR)
    assert [a["filename"] for a in out["attachments"]] == ["1. 과업지시서.hwpx", "3. 사유서.hwpx"]


def test_bigfile_paths_of_any_institute_count_as_large_file_links():
    assert scraper_mod.is_large_file_link(BIG_A)
    assert scraper_mod.is_large_file_link("https://mail.example.re.kr:443/bigfileDownload/QUFBQQ==/")
    assert not scraper_mod.is_large_file_link("https://mailon.kr/mail/manage/attach/download?mailUid=7&position=4")
    assert not scraper_mod.is_large_file_link("https://mailon.kr/mail/x?next=/bigfileDownload/QUFBQQ==/")


class _Expired(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b"<!DOCTYPE html><html>expired</html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html;charset=UTF-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_an_expired_large_file_link_fails_instead_of_saving_the_notice_page(tmp_path, monkeypatch):
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _Expired)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setattr(scraper_mod, "LARGE_FILE_HOSTS", frozenset({"127.0.0.1"}))
    s = object.__new__(scraper_mod.InboxScraper)
    s.attachments_dir = tmp_path / "att"
    try:
        with pytest.raises(RuntimeError, match="HTML page"):
            s._download_attachment("1", "a.pdf", f"http://127.0.0.1:{httpd.server_address[1]}/x")
    finally:
        httpd.shutdown()
    assert not (tmp_path / "att/1/a.pdf").exists()


def _project(tmp_path):
    root = tmp_path / "proj"
    att = root / "data/attachments"
    att.mkdir(parents=True)
    return root, att, StateDB(root / "data/state.db")


def _save(db, root, att, uid, files, *, body="본문", date=None, real_file_status="ok", folder="inbox"):
    date = date or datetime.now() - timedelta(days=1)
    attachments = []
    for name in files:
        p = att / uid / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(name.encode())
        attachments.append(Attachment(name, p, size=len(name)))
        db.record_attachment(uid, filename=name, href=f"https://mailon.kr/d?{name}",
                             status="ok" if name in scraper_mod.BUTTON_NAMES else real_file_status,
                             local_path=str(p))
    mail = Mail(uid=uid, folder="inbox", subject=f"s{uid}", sender="a@b", date=date,
                body_text=body, attachments=attachments)
    md = write_mail(mail, root / "data/mails", root)
    db.record_message(uid, folder=folder, subject=mail.subject, sender=mail.sender,
                      recv_date=date.isoformat(), markdown_path=str(md.relative_to(root)))
    return md


def test_earlier_button_rows_leave_the_list_the_markdown_and_the_disk(tmp_path):
    root, att, db = _project(tmp_path)
    md = _save(db, root, att, "7", ["Download all", "a.hwpx", "Save"])

    assert heal.prune_buttons(db, root, att) == 1

    text = md.read_text(encoding="utf-8")
    assert '"Save"' not in text and '"Download all"' not in text and "[Save]" not in text
    assert '  - "a.hwpx"' in text and "- [a.hwpx](" in text
    assert [r["filename"] for r in db.attachment_rows("7")] == ["a.hwpx"]
    assert sorted(p.name for p in (att / "7").iterdir()) == ["a.hwpx"]
    assert heal.prune_buttons(db, root, att) == 0


def test_the_zip_stays_on_disk_when_a_real_file_was_not_saved(tmp_path):
    root, att, db = _project(tmp_path)
    _save(db, root, att, "8", ["Download all", "b.hwpx"], real_file_status="fail")

    heal.prune_buttons(db, root, att)

    assert (att / "8/Download all").exists()
    assert [r["filename"] for r in db.attachment_rows("8")] == ["b.hwpx"]


def test_a_markdown_left_with_only_buttons_lists_nothing():
    md = '---\nuid: "9"\nattachments:\n  - "Save"\n---\n\n# s\n\n## Attachments\n\n- [Save](x)\n\n## Body\n\nb\n'
    assert heal.drop_listed_names(md, {"Save"}) == '---\nuid: "9"\nattachments:[]\n---\n\n# s\n\n## Body\n\nb\n'


class _Rereader:
    folder_uid = "F"

    def __init__(self, att, *, finds=True):
        self.att, self.calls, self.finds = att, [], finds

    def read_mail(self, ref, state_db=None):
        self.calls.append(ref.uid)
        self.folders = [*getattr(self, "folders", []), ref.folder_uid]
        if not self.finds:
            return Mail(uid=ref.uid, folder="inbox", subject=ref.subject, sender=ref.sender,
                        date=ref.date, body_text="대용량 첨부파일")
        p = self.att / ref.uid / "big.pdf"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"%PDF")
        state_db.record_attachment(ref.uid, filename="big.pdf", href=BIG_A, status="ok", local_path=str(p))
        return Mail(uid=ref.uid, folder="inbox", subject=ref.subject, sender=ref.sender, date=ref.date,
                    body_text="대용량 첨부파일", attachments=[Attachment("big.pdf", p, size=4)])


def test_recent_large_box_mails_without_their_files_are_read_again_once(tmp_path):
    root, att, db = _project(tmp_path)
    missed = _save(db, root, att, "1", [], body="대용량 첨부파일 2 개")
    _save(db, root, att, "2", [], body="일반 메일")
    _save(db, root, att, "3", [], body="대용량 첨부파일", date=datetime.now() - timedelta(days=40))
    _save(db, root, att, "4", [], body="대용량 첨부파일")
    db.record_attachment("4", filename="w.wrl", href="https://filefree.mailplug.com/download/x", status="ok")
    reader = _Rereader(att)

    assert heal.backfill_large_links(db, reader, root / "data/mails", root) == 1
    assert heal.backfill_large_links(db, reader, root / "data/mails", root) == 0

    assert reader.calls == ["1"]
    text = missed.read_text(encoding="utf-8")
    assert '  - "big.pdf"' in text and "attachments:[]" not in text


def test_a_sent_mail_is_read_from_the_sent_folder_and_keeps_its_label(tmp_path):
    root, att, db = _project(tmp_path)
    _save(db, root, att, "6", [], body="대용량 첨부파일", folder="sent")
    reader = _Rereader(att)

    assert heal.backfill_large_links(db, reader, root / "data/mails", root, folder_uids={"sent": "S"}) == 1

    assert reader.folders == ["S"]
    assert db.message_row("6")["folder"] == "sent"


def test_a_sent_mail_waits_for_a_run_that_resolved_the_sent_folder(tmp_path):
    root, att, db = _project(tmp_path)
    _save(db, root, att, "6", [], body="대용량 첨부파일", folder="sent")
    reader = _Rereader(att)

    heal.backfill_large_links(db, reader, root / "data/mails", root)
    heal.backfill_large_links(db, reader, root / "data/mails", root, folder_uids={"sent": "S"})

    assert reader.calls == ["6"]


def test_a_mail_whose_reread_finds_no_link_is_not_read_every_run(tmp_path):
    root, att, db = _project(tmp_path)
    _save(db, root, att, "5", [], body="인용된 대용량 첨부파일 안내")
    reader = _Rereader(att, finds=False)

    heal.backfill_large_links(db, reader, root / "data/mails", root)
    heal.backfill_large_links(db, reader, root / "data/mails", root)

    assert reader.calls == ["5"]
