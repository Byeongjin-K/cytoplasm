"""N1 — 다이제스트 실패를 수리 티켓으로 올리는 no-agent 워처(`mail_digest_repair_watch.py`).

10/02 노드에 손으로 넣은 에이전트 모드 잡의 판정을 결정적으로 옮긴 것이라, 같은 재료(다이제스트 DB·
`DIGEST-FAIL` 표식·트리아지 워처 실패 상태)마다 티켓 한 번, 정상이면 0 번, 실패하면 위치를 전진시키지
않는 것을 고정한다. 티켓 문구에 메일 내용이 실리지 않는 것도 본다.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/mail/scripts"))
import mail_digest_repair_watch as watch  # noqa: E402

NOW = datetime(2026, 10, 18, 0, 10, tzinfo=UTC)


def _db(path: Path, items: list[tuple[int, str, str]]) -> None:
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE digest_runs (run_id INTEGER PRIMARY KEY, sent_at TEXT, item_count INTEGER)")
        db.execute("CREATE TABLE digest_items (run_id INTEGER, item_no INTEGER, uid TEXT, subject TEXT, "
                   "flags TEXT, summary TEXT)")
        db.execute("INSERT INTO digest_runs VALUES (1, ?, 1)", ((NOW - timedelta(days=2)).isoformat(),))
        db.execute("INSERT INTO digest_runs VALUES (2, ?, 2)", ((NOW - timedelta(hours=1)).isoformat(),))
        for run, flags, summary in items:
            db.execute("INSERT INTO digest_items VALUES (?, 1, 'u', '비밀 제목', ?, ?)", (run, flags, summary))


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRIAGE_DB", str(tmp_path / "triage.db"))
    monkeypatch.setenv("WATCH_FAILURE_ROOT", str(tmp_path / "wf"))
    (tmp_path / ".hermes" / "cron").mkdir(parents=True)
    return tmp_path


def _tickets() -> tuple[list[str], watch.FileTicket]:
    sent: list[str] = []

    def file_ticket(message: str) -> dict[str, object]:
        sent.append(message)
        return {"ticket": "t_1", "occurrence": 1, "created": len(sent) == 1}

    return sent, file_ticket


def test_each_failure_kind_files_one_ticket_without_mail_content(home: Path) -> None:
    _db(home / "triage.db", [(1, "classification_failed", "(요약 실패)"),
                             (2, "reply_needed,classification_failed", "요약"),
                             (2, "", "(요약 실패)")])
    (home / ".hermes" / "cron" / "jobs.json").write_text(json.dumps({"jobs": [{"id": "d1", "name": "mail-daily-digest"}]}))
    out = home / ".hermes" / "cron" / "output" / "d1"
    out.mkdir(parents=True)
    (out / "run.md").write_text("DIGEST-FAIL stage=deliver retry_safe=false code=discord_post detail=x\n")
    (home / "wf").mkdir()
    (home / "wf" / "mail-triage-watch.json").write_text(json.dumps(
        {"consecutive_failures": 2, "incident_open": False,
         "last_failure": {"last_failed_at": (NOW - timedelta(hours=3)).isoformat(), "count": 2}}))
    sent, file_ticket = _tickets()

    rc, line = watch.run(home, NOW, file_ticket)

    assert rc == 0
    assert sorted(sent) == sorted([
        "!repair 메일 다이제스트 점검: 분류 실패(classification_failed)",
        "!repair 메일 다이제스트 점검: 요약 실패((요약 실패) 대체 문구)",
        "!repair 메일 다이제스트 점검: 다이제스트 실패 DIGEST-FAIL stage=deliver code=discord_post",
        "!repair 메일 다이제스트 점검: 트리아지 워처 실패(mail-triage-watch)",
    ])
    assert not any("비밀" in message for message in sent)
    assert line == "등록 1건 / occurrence 갱신 3건"


def test_a_clean_morning_files_nothing_and_old_runs_are_not_replayed(home: Path) -> None:
    _db(home / "triage.db", [(1, "classification_failed", "(요약 실패)"), (2, "reply_needed", "요약")])
    sent, file_ticket = _tickets()

    assert watch.run(home, NOW, file_ticket) == (0, "없음")
    assert sent == []
    state = json.loads((home / ".hermes" / "mail-triage" / "repair-daily.json").read_text())
    assert state["last_run_id"] == 2
    assert oct(os.stat(home / ".hermes" / "mail-triage" / "repair-daily.json").st_mode & 0o777) == "0o600"
    assert watch.run(home, NOW + timedelta(days=1), file_ticket) == (0, "없음")


def test_a_failed_ticket_leaves_the_position_for_the_next_run(home: Path) -> None:
    _db(home / "triage.db", [(2, "classification_failed", "요약")])

    def broken(_message: str) -> dict[str, object]:
        raise RuntimeError("repair_cli manual exit 1")

    with pytest.raises(RuntimeError):
        watch.run(home, NOW, broken)
    assert not (home / ".hermes" / "mail-triage" / "repair-daily.json").exists()
    sent, file_ticket = _tickets()
    assert watch.run(home, NOW, file_ticket)[1] == "등록 1건 / occurrence 갱신 0건"
