"""Q233: project-board auto writes reuse the approved store and shared receipt ledger."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from automation import auto_write
from automation.project_board import service
from automation.project_board.llm import LlmAnswer
from automation.project_board.models import SourceRef
from automation.project_board.proposals import ProposalRecord
from automation.project_board.sources import SourceDocument

NOW = datetime(2026, 10, 18, 10, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Provider:
    name: str = "fake"

    def complete(self, prompt: str) -> LlmAnswer:
        return LlmAnswer(json.dumps({
            "relevant": True, "project": "프로젝트A", "summary": "비공개 메일 요약",
            "todos": [{"title": "자료 제출", "due": "2026-10-20"}],
        }), self.name, "fake")


@pytest.fixture
def mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    path = auto_write.state_root() / "project-board.mode"
    path.parent.mkdir(parents=True)
    return path


def _no_card(record: ProposalRecord) -> None:
    pytest.fail(f"automatic proposal posted a card: {record.request_id}")


def _propose(board: service.Board, post: service.Poster | None, kind: str = "mail") -> ProposalRecord:
    document = SourceDocument(
        SourceRef(kind, "u-1", "자료 요청", "sender@example.invalid", "2026-10-18"),
        "비공개 메일 본문",
    )
    record, _ = service.propose(board, document, Provider(), today=NOW.date(), post=post, now=NOW)
    assert record is not None
    return record


@pytest.mark.parametrize("kind", ["mail", "meeting"])
def test_on_applies_and_records_once_without_posting(mode: Path, tmp_path: Path, kind: str) -> None:
    # Given: the node's exact on switch and a real private board.
    mode.write_bytes(b"on")
    board = service.Board(tmp_path / "board")
    # When: analysis is submitted twice for the same source.
    first = _propose(board, _no_card, kind)
    again = _propose(board, _no_card, kind)
    # Then: one proposal and todo are stored, with one metadata-only receipt.
    assert first.request_id == again.request_id
    assert first.status == again.status == "applied"
    snapshot = board.store.snapshot(today=NOW.date())
    assert [(todo.title, todo.proposal_id) for todo in snapshot.todos] == [("자료 제출", first.request_id)]
    ledger = (mode.parent / "ledger.jsonl").read_text(encoding="utf-8")
    assert [json.loads(row) for row in ledger.splitlines()] == [{
        "kind": "project-board", "source_id": first.request_id,
        "path": "프로젝트A · 할 일 1건", "revision": first.action_hash,
        "recorded_at": "2026-10-18T10:00:00Z", "written_at": "2026-10-18T10:00:00Z",
    }]


@pytest.mark.parametrize("setting", ["off", "malformed", "unreadable"])
def test_disabled_or_invalid_mode_keeps_the_card_path(mode: Path, tmp_path: Path, setting: str) -> None:
    # Given: absent, malformed or unreadable node mode.
    if setting == "malformed":
        mode.write_bytes(b"on\n")
    if setting == "unreadable":
        mode.mkdir()
    board = service.Board(tmp_path / "board")
    posted: list[ProposalRecord] = []
    # When: a new analysis would post its card.
    record = _propose(board, posted.append)
    # Then: the poster is called and neither board data nor a receipt is written.
    assert posted == [record]
    assert record.status == "proposed"
    assert board.store.project_names() == ()
    assert not (mode.parent / "ledger.jsonl").exists()


def test_no_post_keeps_a_proposal_unapplied_even_when_on(mode: Path, tmp_path: Path) -> None:
    # Given: auto-write is on but the caller requested proposal storage only.
    mode.write_bytes(b"on")
    board = service.Board(tmp_path / "board")
    # When: no poster is supplied.
    record = _propose(board, None)
    # Then: the explicit no-post workflow still has no applied data or receipt.
    assert record.status == "proposed"
    assert board.store.project_names() == ()
    assert not (mode.parent / "ledger.jsonl").exists()


def test_revision_uses_its_own_id_and_hash(mode: Path, tmp_path: Path) -> None:
    # Given: a frozen proposal awaiting submission and the automatic switch.
    board = service.Board(tmp_path / "board")
    first = _propose(board, None)
    mode.write_bytes(b"on")
    # When: the owner revises that proposal under the same key.
    revised = service.revise(
        board, first.request_id, service.Revision(set_due=((1, "2026-10-24"),)),
        post=_no_card, now=NOW,
    )
    # Then: only the revised id/hash enters the board and ledger.
    assert revised.key == first.key and revised.revision == 2
    assert revised.status == "applied" and revised.action_hash != first.action_hash
    assert [(todo.proposal_id, todo.due) for todo in board.store.snapshot(today=NOW.date()).todos] == [
        (revised.request_id, "2026-10-24"),
    ]
    rows = [json.loads(row) for row in (mode.parent / "ledger.jsonl").read_text().splitlines()]
    assert [(row["source_id"], row["revision"]) for row in rows] == [(revised.request_id, revised.action_hash)]


def test_receipt_retry_does_not_duplicate_applied_todos(
    mode: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a write that reaches SQLite but cannot yet append its receipt.
    mode.write_bytes(b"on")
    board = service.Board(tmp_path / "board")
    def refuse(kind: str, entry: auto_write.Entry) -> None:
        raise OSError("ledger unavailable")
    with monkeypatch.context() as patch:
        patch.setattr(auto_write, "record", refuse)
        with pytest.raises(OSError, match="ledger unavailable"):
            _propose(board, _no_card)
    # When: the next analysis retries the same pending proposal.
    record = _propose(board, _no_card)
    # Then: SQLite remains idempotent and the receipt is recovered.
    assert record.status == "applied"
    assert len(board.store.snapshot(today=NOW.date()).todos) == 1
    rows = (mode.parent / "ledger.jsonl").read_text().splitlines()
    assert len(rows) == 1 and json.loads(rows[0])["source_id"] == record.request_id
