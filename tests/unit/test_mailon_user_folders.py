"""mailon 사용자 폴더 수집 (사이토플라즘 #251) — 실측 모양을 본뜬 가짜 MailOn 응답으로 본다.

웹메일 규칙이 받은편지함을 거치지 않고 사용자 폴더로 바로 넣은 메일을 정기 수집이 놓쳤다.
사용자 폴더 수집은 휴지통·스팸·임시보관·태그를 가져오지 않고, 폴더마다 최근 N일 밖은 목록도 더
읽지 않으며, 보낸메일은 어느 경로로 받든 folder='sent'로 남고, 폴더 이름은 folders 표에 남는다.
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

INBOX, SELF, SENT, DRAFTS, TRASH, SPAM = "100", "101", "102", "103", "105", "107"
PROJECT, OTHER, SPAM_NAMED, UNNAMED, TAG = "800", "801", "802", "803", "300"
# The folder tree of the live webmail (2026-11-16, read-only probe), uids and user names
# replaced: system folders keep their real actionType, the "my mailboxes" group (-5) holds the
# user folders (actionType 20, folderType 0) - one of them really is named like the spam box -
# and the tag group (-3) holds tags (actionType 20, folderType 1). Group and root ids are not
# numbers. The list API's folder object names no folder (nameFolder is empty).
_LIVE_TREE = [
    {"nodeId": "0", "text": "", "actionType": 0, "folderType": 0},
    {"nodeId": INBOX, "text": "Inbox", "actionType": 1, "folderType": 0},
    {"nodeId": SELF, "text": "Self box", "actionType": 8, "folderType": 0},
    {"nodeId": SENT, "text": "Sent", "actionType": 4, "folderType": 0},
    {"nodeId": DRAFTS, "text": "Drafts", "actionType": 5, "folderType": 0},
    {"nodeId": "104", "text": "Scheduled", "actionType": 6, "folderType": 0},
    {"nodeId": SPAM, "text": "Spam folder", "actionType": 91, "folderType": 0},
    {"nodeId": "-8", "text": "Approval mail", "actionType": 80, "folderType": 0},
    {"nodeId": TRASH, "text": "Trash", "actionType": 7, "folderType": 0},
    {"nodeId": "-5", "text": "My mailboxes", "actionType": 10, "folderType": 0},
    {"nodeId": SPAM_NAMED, "text": "스팸메일함", "actionType": 20, "folderType": 0},
    {"nodeId": PROJECT, "text": "과제A", "actionType": 20, "folderType": 0},
    {"nodeId": OTHER, "text": "과제B 2026", "actionType": 20, "folderType": 0},
    {"nodeId": UNNAMED, "text": "", "actionType": 20, "folderType": 0},
    {"nodeId": "-6", "text": "Shared mailbox", "actionType": 60, "folderType": 0},
    {"nodeId": "-3", "text": "Tags", "actionType": 10, "folderType": 0},
    {"nodeId": TAG, "text": "Work", "actionType": 20, "folderType": 1},
]
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
    """Answers the scraper's in-page calls like the webmail would; records what was asked."""

    def __init__(self, folders: dict[str, list[list[dict]]], tree: list[dict] | None = _LIVE_TREE) -> None:
        self.folders = folders
        self.tree = tree
        self.all_pages: list[int] = []
        self.folder_pages: list[tuple[str, int]] = []
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
            page = int(body["currentPage"][0])
            if body["allFolder"] == ["true"]:
                self.all_pages.append(page)
                rows = [row for pages in self.folders.values() for rows in pages for row in rows]
                return json.dumps(json.dumps({"result": True, "contents": rows[(page - 1) * 20:page * 20]}))
            uid = body["folderUid"][0]
            self.folder_pages.append((uid, page))
            pages = self.folders.get(uid, [])
            folder = {"folderUid": int(uid), "nameFolder": "", "folderType": 0, "totalMsgNum": 0}
            rows = pages[page - 1] if page <= len(pages) else []
            return json.dumps(json.dumps({"result": True, "folder": folder, "contents": rows}))
        if "mailboxTree" in script:
            return json.dumps(json.dumps(self.tree))
        return json.dumps("null")

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


def test_user_folders_come_from_the_folder_tree_and_system_folders_and_tags_do_not(tmp_path: Path) -> None:
    # Given: the live tree shape and one recent mail in every folder it lists.
    uids = [INBOX, SELF, SENT, DRAFTS, TRASH, SPAM, PROJECT, OTHER, SPAM_NAMED, UNNAMED, TAG]
    mailon = FakeMailOn({uid: [[_row(f"u{uid}", uid, 0.5)]] for uid in uids})

    # When
    new, db = _sync(tmp_path, mailon)

    # Then: only the two named user folders are listed, fetched and stored under their own ids.
    assert sorted(uid for uid, _page in mailon.folder_pages) == [PROJECT, OTHER]
    assert new == 2 and sorted(mailon.viewed) == [f"u{PROJECT}", f"u{OTHER}"]
    assert _messages(db.path) == {f"u{PROJECT}": f"folder:{PROJECT}", f"u{OTHER}": f"folder:{OTHER}"}
    with sqlite3.connect(db.path) as conn:
        assert sorted(conn.execute("SELECT folder_uid, name, kind FROM folders")) == [
            (PROJECT, "과제A", "user"), (OTHER, "과제B 2026", "user")]


def test_an_unreadable_folder_tree_collects_nothing(tmp_path: Path) -> None:
    mailon = FakeMailOn({PROJECT: [[_row("p", PROJECT, 0.5)]]}, tree=None)

    new, db = _sync(tmp_path, mailon)

    assert new == 0 and mailon.folder_pages == [] and mailon.viewed == [] and _messages(db.path) == {}


def test_each_folder_walk_stops_at_the_day_limit_and_never_backfills(tmp_path: Path) -> None:
    # Given: page 1 recent, page 2 crosses the 7-day cutoff, page 3 would be older still.
    page1 = [_row(f"r{n}", PROJECT, 1) for n in range(20)]
    page2 = [_row("r-new", PROJECT, 6), _row("r-old", PROJECT, 8)] + [_row(f"o{n}", PROJECT, 9) for n in range(18)]
    page3 = [_row(f"x{n}", PROJECT, 20) for n in range(20)]
    mailon = FakeMailOn({PROJECT: [page1, page2, page3]})

    # When
    new, db = _sync(tmp_path, mailon)

    # Then: page 3 is never asked for, and nothing older than the cutoff is fetched or stored.
    assert [page for uid, page in mailon.folder_pages if uid == PROJECT] == [1, 2]
    assert new == 21 and "r-old" not in _messages(db.path)
    assert not {"r-old", "o0", "x0"} & set(mailon.viewed)


def test_old_user_folder_mail_is_reached_past_a_full_inbox(tmp_path: Path) -> None:
    # Given: 600 newer inbox mails (more than the 25-page cap of the all-folder list) and an
    # old user-folder mail - the 2026-11-16 ten-year run hit the cap and stored nothing.
    inbox = [[_row(f"i{p}-{n}", INBOX, 0.01) for n in range(20)] for p in range(30)]
    mailon = FakeMailOn({INBOX: inbox, PROJECT: [[_row("old", PROJECT, 900)]]})

    # When
    new, db = _sync(tmp_path, mailon, since_days=3650)

    # Then: the user folder is read on its own; the inbox is never walked.
    assert new == 1 and _messages(db.path) == {"old": f"folder:{PROJECT}"}
    assert mailon.all_pages == [] and {uid for uid, _page in mailon.folder_pages} == {PROJECT, OTHER}


def test_a_second_run_fetches_only_new_mail(tmp_path: Path) -> None:
    mailon = FakeMailOn({PROJECT: [[_row("a", PROJECT, 1), _row("b", PROJECT, 2)]]})
    _sync(tmp_path, mailon)
    mailon.viewed.clear()

    new, _db = _sync(tmp_path, mailon)

    assert new == 0 and mailon.viewed == []


def test_sent_mail_stays_sent_on_the_all_folder_path() -> None:
    # Given: the manual --folders all walk meets a row of the sent folder (issue #251 item 2).
    mailon = FakeMailOn({})
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
