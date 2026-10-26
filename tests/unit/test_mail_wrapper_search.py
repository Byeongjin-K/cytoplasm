"""mail_wrapper `search`: find institutional mail by subject, sender or saved body (2026-10-26).

New file rather than more cases in test_mail_wrapper.py: that file drives a stubbed mailon
subprocess, while search never runs mailon and only reads state.db plus the saved Markdown.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "mail" / "scripts"

# (uid, folder, subject, sender, recv_date, markdown_path, saved_at, body or None = no file)
ROWS = [
    ("u-4", "inbox", "주간 회의 안내 delta", "office@example.invalid",
     "2026-10-20T09:00:00", "data/mails/2026/10/u4.md", 4, "회의실 변경 안내입니다."),
    ("u-3", "inbox", "연구비카드 추가 등록 안내", "card@example.invalid",
     "2026-09-17T10:00:00", "data/mails/2026/09/u3.md", 3,
     "카드 번호 끝자리 NK264F 의 추가 등록이 끝났습니다. 문의는 재무팀으로."),
    ("u-2", "sent", "회의 자료 송부", "owner@example.invalid",
     "2025-03-02T08:00:00", "data/mails/2025/03/u2.md", 2,
     "첨부한 자료에 연구비카드 사용 내역을 정리했습니다."),
    ("u-1", "inbox", "NK264F 승인 결과", "bank@example.invalid",
     "2023-03-23T06:36:13", "data/mails/2023/03/u1.md", 1, None),
]


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    (root / "data").mkdir(parents=True)
    conn = sqlite3.connect(root / "data" / "state.db")
    conn.execute(
        """CREATE TABLE messages (
        uid TEXT PRIMARY KEY, folder TEXT NOT NULL DEFAULT 'inbox', subject TEXT,
        sender TEXT, recv_date TEXT, markdown_path TEXT, saved_at INTEGER NOT NULL)"""
    )
    conn.executemany("INSERT INTO messages VALUES (?,?,?,?,?,?,?)", [r[:7] for r in ROWS])
    conn.commit()
    conn.close()
    for row in ROWS:
        if row[7] is not None:
            path = root / row[5]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"---\nuid: {row[0]}\n---\n\n{row[7]}\n", encoding="utf-8")
    monkeypatch.setenv("MAIL_WRAPPER_REPO", str(root))
    monkeypatch.delenv("MAIL_WRAPPER_MASK_SALT", raising=False)
    return root


def search(*argv: str) -> tuple[int, dict]:
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / "mail_wrapper.py"), "search", *argv],
        capture_output=True, text=True, check=False,
    )
    return proc.returncode, json.loads(proc.stdout)


def uids(payload: dict) -> list[str]:
    return [mail["uid"] for mail in payload["mails"]]


def test_subject_word_finds_mail_in_every_folder_newest_first(repo: Path) -> None:
    rc, out = search("--query", "회의")
    assert rc == 0 and out["status"] == "ok"
    assert uids(out) == ["u-4", "u-2"]
    assert [m["matched_in"] for m in out["mails"]] == [["subject"], ["subject"]]


def test_body_only_word_is_found_with_a_short_snippet(repo: Path) -> None:
    rc, out = search("--query", "재무팀")
    assert rc == 0
    assert uids(out) == ["u-3"]
    mail = out["mails"][0]
    assert mail["matched_in"] == ["body"]
    assert "재무팀" in mail["snippet"] and len(mail["snippet"]) <= 200


def test_every_word_must_match_somewhere_in_subject_sender_or_body(repo: Path) -> None:
    # u-1 has NK264F in its subject but no 연구비카드 anywhere (and no saved body).
    rc, out = search("--query", "연구비카드 nk264f")
    assert rc == 0
    assert uids(out) == ["u-3"]
    assert out["mails"][0]["matched_in"] == ["subject", "body"]


def test_sender_and_case_insensitive_match(repo: Path) -> None:
    rc, out = search("--query", "BANK@example")
    assert rc == 0
    assert uids(out) == ["u-1"]
    assert out["mails"][0]["matched_in"] == ["sender"]


def test_limit_and_counts_of_mails_searched_without_a_saved_body(repo: Path) -> None:
    rc, out = search("--query", "연구비카드", "--limit", "1")
    assert rc == 0
    assert uids(out) == ["u-3"]
    assert (out["searched"], out["without_body"], out["limit"]) == (4, 1, 1)


def test_a_subject_match_is_not_crowded_out_by_body_matches(repo: Path) -> None:
    rc, out = search("--query", "nk264f", "--limit", "1")
    assert rc == 0
    assert uids(out) == ["u-1"]
    assert out["mails"][0]["matched_in"] == ["subject"]


def test_no_match_is_ok_with_zero_mails(repo: Path) -> None:
    rc, out = search("--query", "존재하지않는낱말")
    assert (rc, out["status"], out["count"], out["mails"]) == (0, "ok", 0, [])


def test_masked_search_carries_no_subject_sender_or_snippet(repo: Path) -> None:
    rc, out = search("--query", "재무팀", "--masked")
    assert rc == 0
    text = json.dumps(out, ensure_ascii=False)
    for plain in ("재무팀", "연구비카드", "card@example.invalid", "NK264F"):
        assert plain not in text
    assert "snippet" not in out["mails"][0]


def test_search_never_writes_the_state_db(repo: Path) -> None:
    db = repo / "data" / "state.db"
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    search("--query", "회의")
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("query", ["", "   "])
def test_empty_query_is_a_usage_error(repo: Path, query: str) -> None:
    rc, out = search("--query", query)
    assert rc != 0
    assert out["error_code"] == "usage_error"
