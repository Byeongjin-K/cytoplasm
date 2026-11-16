"""mailon 사용자 폴더 수집 (사이토플라즘 #251) — 가짜 MailOn 응답으로만 본다.

웹메일 규칙이 받은편지함을 거치지 않고 사용자 폴더로 바로 넣은 메일을 정기 수집이 놓쳤다.
사용자 폴더 수집은 휴지통·스팸·임시보관을 가져오지 않고, 최근 N일 밖은 목록도 더 읽지 않으며,
보낸메일은 어느 경로로 받든 folder='sent'로 남고, 폴더 이름은 folders 표에 남는다.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
import time
import types
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_VENDOR = _ROOT / "skills" / "mail" / "vendor"


def _install_third_party_stubs() -> None:
    for name in ("pyotp", "dotenv", "bs4"):
        if name in sys.modules:
            continue
        try:
            __import__(name)
        except ImportError:
            stub = types.ModuleType(name)
            stub.TOTP = stub.load_dotenv = stub.BeautifulSoup = object  # type: ignore[attr-defined]
            sys.modules[name] = stub


_install_third_party_stubs()
for _path in (_VENDOR, _ROOT / "skills" / "mail" / "scripts"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from mailon import main as mailon_main  # noqa: E402
from mailon import scraper as scraper_mod  # noqa: E402
from mailon.scraper import InboxScraper, MailRef  # noqa: E402
from mailon.state import StateDB  # noqa: E402

import mail_wrapper_read  # noqa: E402

INBOX, SENT, TRASH, SPAM, DRAFTS, PROJECT, UNNAMED = "100", "200", "700", "701", "702", "800", "900"
DAY_MS = 24 * 60 * 60 * 1000
_BODY = re.compile(r'body:("(?:[^"\\]|\\.)*")')
_VIEW = "<view {subject}>"


@pytest.fixture(autouse=True)
def _parser(monkeypatch: pytest.MonkeyPatch) -> None:
    # The HTML parser needs bs4+lxml, which the CI image does not carry; the folder logic does not.
    monkeypatch.setattr(scraper_mod, "parse_view_async_html", lambda html: {
        "subject": html.removeprefix("<view ").removesuffix(">"), "from": "sender@example.invalid",
        "to": "owner@example.invalid", "cc": "", "bcc": "", "date": datetime(2026, 11, 16),
        "body_text": "synthetic body", "body_html": "", "attachments": []})


def _row(uid: str, folder: str, age_days: float) -> dict:
    return {"mailUid": uid, "folderUid": folder, "subject": f"subject {uid}",
            "adrFrom": "sender@example.invalid", "adrTo": "owner@example.invalid",
            "timeMillis": int(time.time() * 1000 - age_days * DAY_MS),
            "isSeen": 1, "isFlagged": 0, "msgSize": 10, "attachCount": 0}


class FakeMailOn:
    """Answers the scraper's in-page fetches like the webmail would; records what was asked."""

    def __init__(self, pages: list[list[dict]], names: dict[str, str]) -> None:
        self.pages = pages
        self.names = names
        self.all_pages: list[int] = []
        self.viewed: list[str] = []

    def eval_js(self, script: str) -> str:
        if "/mail/view_async" in script:
            uid = script.split("mailUid=")[1].split("&")[0]
            self.viewed.append(uid)
            return json.dumps(_VIEW.format(subject=f"subject {uid}"))
        if "list_async" in script:
            match = _BODY.search(script)
            assert match, "list fetch without a form body"
            body = parse_qs(json.loads(match[1]))
            if body["allFolder"] == ["true"]:
                page = int(body["currentPage"][0])
                self.all_pages.append(page)
                rows = self.pages[page - 1] if page <= len(self.pages) else []
                return json.dumps(json.dumps({"result": True, "contents": rows}))
            uid = body["folderUid"][0]
            folder = {"folderUid": int(uid), **({"folderName": self.names[uid]} if uid in self.names else {})}
            return json.dumps(json.dumps({"result": True, "folder": folder, "contents": []}))
        if "const NAME =" in script:  # sidebar uid harvest by folder name
            return json.dumps(TRASH if json.dumps("휴지통") in script else None)
        return json.dumps("{}")  # sidebar name harvest: nothing readable

    def wait_ms(self, _ms: int) -> None:
        return None


def _scraper(mailon: FakeMailOn) -> InboxScraper:
    scraper = InboxScraper(mailon, Path("/nonexistent"), folder_label="user", all_folders=True)  # type: ignore[arg-type]
    scraper.folder_uid, scraper.sent_uid = INBOX, SENT
    return scraper


def _messages(db_path: Path) -> dict[str, str]:
    with sqlite3.connect(db_path) as conn:
        return dict(conn.execute("SELECT uid, folder FROM messages"))


def _sync(tmp_path: Path, mailon: FakeMailOn, since_days: int = 7) -> tuple[int, StateDB]:
    db = StateDB(tmp_path / "state.db")
    cfg = SimpleNamespace(mails_dir=tmp_path / "data" / "mails")
    return mailon_main._sync_user_folders(db, _scraper(mailon), cfg, 50, tmp_path, since_days), db


def test_user_folders_are_collected_and_trash_spam_drafts_are_not(tmp_path: Path) -> None:
    # Given: one page of recent rows across every kind of folder; the trash uid comes from the
    # sidebar, spam and drafts are named only by the list API, one folder has no name at all.
    rows = [_row("u-inbox", INBOX, 0.1), _row("u-sent", SENT, 0.2), _row("u-trash", TRASH, 0.3),
            _row("u-spam", SPAM, 0.4), _row("u-drafts", DRAFTS, 0.5), _row("u-project", PROJECT, 0.6),
            _row("u-unnamed", UNNAMED, 0.7)]
    mailon = FakeMailOn([rows], {SPAM: "스팸메일함", DRAFTS: "임시보관함", PROJECT: "과제A (3)", TRASH: "과제B"})

    # When
    new, db = _sync(tmp_path, mailon)

    # Then: only the named user folder's mail is fetched and stored, under its own folder id.
    assert new == 1
    assert mailon.viewed == ["u-project"]
    assert _messages(db.path) == {"u-project": f"folder:{PROJECT}"}
    with sqlite3.connect(db.path) as conn:
        assert conn.execute("SELECT folder_uid, name, kind FROM folders").fetchall() == [(PROJECT, "과제A", "user")]


def test_the_walk_stops_at_the_day_limit_and_never_backfills(tmp_path: Path) -> None:
    # Given: page 1 recent, page 2 crosses the 7-day cutoff, page 3 would be older still.
    page1 = [_row(f"r{n}", PROJECT, 1) for n in range(20)]
    page2 = [_row("r-new", PROJECT, 6), _row("r-old", PROJECT, 8)] + [_row(f"o{n}", PROJECT, 9) for n in range(18)]
    page3 = [_row(f"x{n}", PROJECT, 20) for n in range(20)]
    mailon = FakeMailOn([page1, page2, page3], {PROJECT: "과제A"})

    # When
    new, db = _sync(tmp_path, mailon)

    # Then: page 3 is never asked for, and nothing older than the cutoff is fetched or stored.
    assert mailon.all_pages == [1, 2]
    assert new == 21 and "r-old" not in _messages(db.path)
    assert not {"r-old", "o0", "x0"} & set(mailon.viewed)


def test_a_second_run_fetches_only_new_mail(tmp_path: Path) -> None:
    mailon = FakeMailOn([[_row("a", PROJECT, 1), _row("b", PROJECT, 2)]], {PROJECT: "과제A"})
    _sync(tmp_path, mailon)
    mailon.viewed.clear()

    new, _db = _sync(tmp_path, mailon)

    assert new == 0 and mailon.viewed == []


def test_sent_mail_stays_sent_on_the_all_folder_path() -> None:
    # Given: the manual --folders all walk meets a row of the sent folder (issue #251 item 2).
    mailon = FakeMailOn([], {})
    scraper = _scraper(mailon)
    ref = MailRef(uid="s1", folder_uid=SENT, subject="", sender="", to="", date=datetime(2026, 11, 16),
                  size=1, unread=False, flagged=False, attach_count=0)
    other = MailRef(uid="p1", folder_uid=PROJECT, subject="", sender="", to="", date=datetime(2026, 11, 16),
                    size=1, unread=False, flagged=False, attach_count=0)

    # When / Then: the digest's reply check (folder == 'sent') still sees it.
    assert scraper.read_mail(ref).folder == "sent"
    assert scraper.read_mail(other).folder == f"folder:{PROJECT}"


def test_user_scope_needs_a_day_limit(tmp_path: Path, monkeypatch) -> None:
    finished: list[str] = []

    class _Db:
        def __init__(self, _path) -> None:
            pass

        def start_run(self) -> int:
            return 1

        def finish_run(self, _run_id, *, status, new_mails=0, error=None) -> None:
            finished.append(status)

    monkeypatch.setattr(mailon_main, "load_config", lambda: SimpleNamespace(
        logs_dir=tmp_path / "logs", state_db_path=tmp_path / "s.db"))
    monkeypatch.setattr(mailon_main, "_setup_logging", lambda *_a, **_k: None)
    monkeypatch.setattr(mailon_main, "StateDB", _Db)
    monkeypatch.setattr(mailon_main, "_make_browser", _no_browser)
    for folders, days in (("inbox,sent,user", None), ("inbox,sent", 7), ("inbox,sent,user", 0)):
        args = SimpleNamespace(folders=folders, limit=1, full_scan=False, since_days=days)
        assert mailon_main.cmd_sync(args) == 2
    assert finished == ["fail", "fail", "fail"]


def _no_browser(_cfg):
    raise AssertionError("no browser may open for a refused scope")


def test_the_wrapper_lists_inbox_and_user_folders_with_their_names(tmp_path: Path) -> None:
    # Given: a state.db after a user-folder sync, plus a trash row an old --folders all run left.
    db = StateDB(tmp_path / "state.db")
    for uid, folder, day in (("i1", "inbox", "01"), ("s1", "sent", "02"), ("p1", f"folder:{PROJECT}", "03"),
                             ("t1", f"folder:{TRASH}", "04")):
        db.record_message(uid, folder=folder, subject=uid, sender="x", recv_date=f"2026-11-{day}",
                          markdown_path=f"{uid}.md")
    db.record_folder(PROJECT, name="과제A")
    cfg = {"db": db.path}

    # When
    where, params = mail_wrapper_read._user_folder_where(cfg)
    rows = mail_wrapper_read._db_rows(cfg, where, params, None)

    # Then: inbox and the user folder, never sent or the trash row; the folder name rides along.
    assert [(r["uid"], r.get("folder_name")) for r in rows] == [("p1", "과제A"), ("i1", None)]
    assert mail_wrapper_read._render_mail(rows[0], False, "")["folder_name"] == "과제A"
    assert "folder_name" not in mail_wrapper_read._render_mail(rows[1], False, "")


@pytest.mark.parametrize("marker,extra", [(None, []), (b"off", []), (b"on", ["--user-folders"]),
                                          (b"yes", [])])
def test_triage_lists_user_folder_mail_only_under_the_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, marker: bytes | None, extra: list[str],
) -> None:
    # Given: the node marker of the mail-user-folders release switch.
    import triage_transport

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(_ROOT))
    if marker is not None:
        (tmp_path / ".hermes").mkdir()
        (tmp_path / ".hermes" / "mail-user-folders.mode").write_bytes(marker)
    calls: list[list[str]] = []
    monkeypatch.setattr(triage_transport, "_wrapper_json", lambda argv: calls.append(argv) or (0, {"mails": []}))
    # When
    triage_transport._list_mails(5, True)
    triage_transport._list_mails(5, False)
    # Then: off, absent or broken keeps today's inbox-only list.
    assert calls == [["list", "--limit", "5", "--sync", *extra], ["list", "--limit", "5", *extra]]


def test_an_older_state_db_without_the_folders_table_lists_the_inbox_only(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE messages (uid TEXT PRIMARY KEY, folder TEXT, subject TEXT, sender TEXT, "
                     "recv_date TEXT, markdown_path TEXT, saved_at INTEGER)")
        conn.executemany("INSERT INTO messages VALUES (?, ?, 's', 'x', '2026-11-01', 'm.md', 0)",
                         [("i1", "inbox"), ("c1", f"folder:{PROJECT}")])
    cfg = {"db": path}

    where, params = mail_wrapper_read._user_folder_where(cfg)

    assert [r["uid"] for r in mail_wrapper_read._db_rows(cfg, where, params, None)] == ["i1"]
    # Opening it with mailon adds the table and keeps every row.
    StateDB(path)
    assert _messages(path) == {"i1": "inbox", "c1": f"folder:{PROJECT}"}
