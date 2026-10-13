"""Real-mail readiness: the deployed MailOn layout, the project catalog, and a read-only dry run.

The deployed runtime links ``data/`` to a shared state directory; reading mail only from
inside the release directory failed every real mail (2026-10-13 dry run: 120 of 120).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from automation.project_board import dryrun
from automation.project_board.analysis import build_prompt
from automation.project_board.config import load_config
from automation.project_board.llm import LlmAnswer
from automation.project_board.models import ProjectBoardError
from automation.project_board.proposals import ProposalStore, new_record
from automation.project_board.models import proposal_from_dict
from automation.project_board.sources import MailRuntime, mail_attachment, read_mail, thread_key

TODAY = date(2026, 10, 13)


def _runtime(tmp_path: Path) -> MailRuntime:
    state = tmp_path / "state" / "data"
    (state / "mails").mkdir(parents=True)
    (state / "attachments" / "u-1").mkdir(parents=True)
    release = tmp_path / "releases" / "r1"
    release.mkdir(parents=True)
    (release / "data").symlink_to(state, target_is_directory=True)
    (state / "mails" / "a.md").write_text(
        "---\nuid: u-1\nsubject: 굴착기 3기관 원격회의\nfrom: a@example.invalid\ndate: 2026-10-12T09:00:00\n"
        "attachments:\n  - 일정.txt\n---\n\n# 굴착기 3기관 원격회의\n\n10월 20일 회의 자료를 준비해 주세요.\n",
        encoding="utf-8",
    )
    (state / "mails" / "b.md").write_text("---\nuid: u-2\nsubject: 광고\n---\n\n할인 행사\n", encoding="utf-8")
    (state / "attachments" / "u-1" / "일정.txt").write_text("x", encoding="utf-8")
    (tmp_path / "outside.md").write_text("secret", encoding="utf-8")
    db = sqlite3.connect(state / "state.db")
    db.executescript(
        "CREATE TABLE messages (uid TEXT, folder TEXT, subject TEXT, sender TEXT, recv_date TEXT, markdown_path TEXT,"
        " saved_at INTEGER); CREATE TABLE attachments (uid TEXT, filename TEXT, href TEXT, status TEXT,"
        " size_bytes INTEGER, error_msg TEXT, attempts INTEGER, local_path TEXT, first_seen INTEGER,"
        " last_attempt INTEGER);"
    )
    db.executemany("INSERT INTO messages VALUES (?, 'inbox', ?, '', ?, ?, 1)", [
        ("u-1", "굴착기 3기관 원격회의", "2026-10-12T09:00:00", "data/mails/a.md"),
        ("u-2", "광고", "2026-10-11T09:00:00", "data/mails/b.md"),
        ("u-3", "밖", "2026-10-10T09:00:00", "../../outside.md"),
    ])
    db.execute("INSERT INTO attachments VALUES ('u-1', '일정.txt', 'x', 'ok', 1, NULL, 1, '/old/host/path/일정.txt', 1, 1)")
    db.commit()
    return MailRuntime(release, release / "data" / "state.db")


def test_mail_behind_the_data_symlink_is_read_and_escapes_are_refused(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    document = read_mail(runtime, "u-1")
    assert document is not None and document.ref.subject == "굴착기 3기관 원격회의"
    assert document.ref.attachments == ("일정.txt",) and "10월 20일" in document.text
    assert read_mail(runtime, "u-3") is None
    found = mail_attachment(runtime, "u-1", "일정.txt")
    assert found is not None and found.read_text(encoding="utf-8") == "x"
    assert mail_attachment(runtime, "u-1", "../일정.txt") is None


def test_reply_and_forward_prefixes_fold_into_one_thread() -> None:
    base = thread_key("굴착기 3기관 원격회의")
    assert thread_key("RE: Re: 굴착기 3기관  원격회의") == base
    assert thread_key("[RE]Re: FW: 굴착기 3기관 원격회의") == base
    assert thread_key("답장: 굴착기 3기관 원격회의") == base
    assert thread_key("굴착기 3기관 원격회의 결과") != base


def test_a_newer_mail_of_a_pending_thread_does_not_open_a_second_card(tmp_path: Path) -> None:
    store = ProposalStore(tmp_path / "state")
    first = proposal_from_dict({
        "project": "프로젝트A", "summary": "s", "todos": [],
        "source": {"kind": "mail", "source_id": "u-1", "subject": "굴착기 3기관 원격회의"},
    })
    record = new_record(first, created_at="2026-10-13T00:00:00Z")
    store.create(record, first)
    assert store.live_for_source("mail", "u-2", "Re: 굴착기 3기관 원격회의") == record
    assert store.live_for_source("mail", "u-3", "다른 요청") is None


def test_pending_proposal_todos_count_as_already_there(tmp_path: Path) -> None:
    from automation.project_board.service import Board, open_and_pending

    board = Board(tmp_path / "board")
    pending = proposal_from_dict({
        "project": "프로젝트A", "summary": "s", "todos": [{"title": "발표자료 제출", "due": "2026-11-03"}],
        "source": {"kind": "mail", "source_id": "u-9", "subject": "워크숍 안내"},
    })
    board.proposals.create(new_record(pending, created_at="2026-10-13T00:00:00Z"), pending)
    assert open_and_pending(board) == ("프로젝트A · 발표자료 제출 (검토 대기)",)


def _config(tmp_path: Path) -> dict[str, str]:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"owner": {"names": ["소유자1", "Owner One"]}, "team": {"names": ["팀원1"]}, "projects": [
        {"name": "프로젝트A(굴착)", "aliases": ["굴착기", "HR"], "note": "실증 과제"},
        {"name": "사무처리"},
    ]}, ensure_ascii=False), encoding="utf-8")
    return {"PROJECT_BOARD_CONFIG": str(path), "HOME": str(tmp_path)}


def test_catalog_reaches_the_v2_prompt_with_its_cues(tmp_path: Path) -> None:
    config = load_config(_config(tmp_path))
    document = read_mail(_runtime(tmp_path), "u-1")
    assert document is not None
    prompt = build_prompt(document, config.catalog_lines(("프로젝트Z",)), TODAY, open_todos=("프로젝트Z · 보고",))
    assert "- 프로젝트A(굴착) (별칭: 굴착기, HR) — 실증 과제" in prompt and "- 프로젝트Z" in prompt
    assert "미분류" in prompt and "- 프로젝트Z · 보고" in prompt and "{{" not in prompt
    owned = build_prompt(document, (), TODAY, owner=config.owner_names, team=config.team_names)
    assert "소유자)은 소유자1 / Owner One 이다" in owned and "팀원은 팀원1 이다" in owned
    assert "{{" not in owned
    with pytest.raises(ProjectBoardError):
        build_prompt(document, (), TODAY, version="v9")


class _Model:
    name = "fake"

    def complete(self, prompt: str) -> LlmAnswer:
        if "할인 행사" in prompt:
            return LlmAnswer('{"relevant": false, "reason": "광고"}', "fake", "m")
        return LlmAnswer(json.dumps({
            "relevant": True, "project": "프로젝트A(굴착)", "summary": "회의 자료 요청",
            "todos": [{"title": "회의 자료 준비", "owner": "", "due": "2026-10-20", "due_reason": "원문"}],
        }, ensure_ascii=False), "fake", "m")


def test_dry_run_writes_only_its_private_report(tmp_path: Path) -> None:
    config = load_config(_config(tmp_path))
    out = tmp_path / "private" / "v2.jsonl"
    summary = dryrun.run(_runtime(tmp_path), _Model(), config, today=TODAY, days=7, limit=10, out=out, workers=2)
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    by_uid = {row["uid"]: row for row in rows}
    assert (summary.total, summary.relevant, summary.unrelated, summary.errors) == (3, 1, 1, 1)
    assert by_uid["u-1"]["todos"][0]["due"] == "2026-10-20" and by_uid["u-2"]["reason"] == "광고"
    assert out.stat().st_mode & 0o777 == 0o600 and summary.projects == {"프로젝트A(굴착)": 1}
    assert sorted(p.name for p in (tmp_path / "private").iterdir()) == ["v2.jsonl"]

    checkout = tmp_path / "repo"
    (checkout / ".git").mkdir(parents=True)
    with pytest.raises(ProjectBoardError, match="outside any git checkout"):
        dryrun.run(_runtime(tmp_path / "again"), _Model(), config, today=TODAY, days=7, limit=10,
                   out=checkout / "x.jsonl")
