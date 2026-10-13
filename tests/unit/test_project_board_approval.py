"""Project proposals: one bound card, ✅ applies exactly once, ⛔ and tampering apply nothing, revise supersedes."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from automation.interop.approval_lifecycle import Outcome
from automation.interop.approval_surface import (
    POLICY_VERSION,
    ApprovalBinding,
    ApprovalKind,
    required_surface,
)
from automation.project_board import proposal_gate, service
from automation.project_board.models import ProjectBoardError, proposal_from_dict
from automation.project_board.proposal_watch import run_tick
from automation.project_board.proposals import ProposalRecord, new_record

OWNER = "100"


class FakeTransport:
    def __init__(self) -> None:
        self.owner_id = OWNER
        self.messages: dict[str, tuple[str, str]] = {}
        self.reactions: dict[tuple[str, str], list[tuple[str, bool]]] = {}
        self.deleted: list[str] = []

    def api(self, method: str, path: str, payload: object = None) -> object:
        return {"id": "9"}

    def post_message(self, channel_id: str, content: str) -> str:
        message_id = str(500 + len(self.messages) + len(self.deleted))
        self.messages[message_id] = (channel_id, content)
        return message_id

    def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None:
        self.reactions.setdefault((message_id, emoji), []).append(("777", True))

    def get_message(self, channel_id: str, message_id: str) -> str | None:
        found = self.messages.get(message_id)
        return None if found is None else found[1]

    def get_reaction_users(self, channel_id: str, message_id: str, emoji: str) -> tuple[tuple[str, bool], ...]:
        return tuple(self.reactions.get((message_id, emoji), ()))

    def delete_message(self, channel_id: str, message_id: str) -> None:
        self.messages.pop(message_id, None)
        self.deleted.append(message_id)


def _proposal(due: str = "2026-10-20") -> dict[str, object]:
    return {
        "project": "프로젝트A", "project_owner": "담당자1", "summary": "중간보고 자료 요청",
        "todos": [
            {"title": "중간보고서 초안 제출", "owner": "담당자2", "due": due, "due_reason": "원문 기한"},
            {"title": "실험 결과표 정리", "owner": "", "due": None, "due_reason": "원문에 기한 없음"},
        ],
        "source": {"kind": "mail", "source_id": "u-1", "subject": "중간보고 요청", "sender": "a@example.invalid",
                   "date": "2026-10-13T09:00:00", "attachments": ["양식.hwpx"]},
        "provider": "fake", "model": "fake-model",
    }


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[service.Board, FakeTransport]:
    binding = ApprovalBinding(
        ApprovalKind.PROJECT_BOARD, required_surface(ApprovalKind.PROJECT_BOARD), "4242", POLICY_VERSION, "77"
    )
    monkeypatch.setattr(proposal_gate, "reuse_request_thread", lambda *args: None)
    monkeypatch.setattr(proposal_gate, "resolve_new_binding", lambda *args, **kwargs: binding)
    return service.Board(tmp_path / "state"), FakeTransport()


def _post(board: service.Board, transport: FakeTransport, record: ProposalRecord) -> ProposalRecord:
    runtime = proposal_gate.DiscordRuntime(transport, object(), OWNER)  # type: ignore[arg-type]
    verdict = proposal_gate.request_approval(record, board.proposals, runtime)
    assert verdict.outcome is Outcome.POSTED
    return board.proposals.get(record.request_id) or record


def _create(board: service.Board, transport: FakeTransport, raw: dict[str, object]) -> ProposalRecord:
    proposal = proposal_from_dict(raw)
    record = new_record(proposal, created_at="2026-10-13T00:00:00Z")
    board.proposals.create(record, proposal)
    return _post(board, transport, record)


def _tick(board: service.Board, transport: FakeTransport) -> list[str]:
    rebuilt: list[str] = []
    runtime = proposal_gate.DiscordRuntime(transport, object(), OWNER)  # type: ignore[arg-type]
    run_tick(board.proposals, board.store, runtime, token="", now=datetime(2026, 10, 13, tzinfo=UTC),
             on_change=lambda: rebuilt.append("x"))
    return rebuilt


def _approve(transport: FakeTransport, record: ProposalRecord, emoji: str = "\u2705") -> None:
    transport.reactions[(record.message_id or "", emoji)].append((OWNER, False))


def test_card_carries_hash_todos_and_source_in_a_project_board_thread(wired: tuple[service.Board, FakeTransport]) -> None:
    board, transport = wired
    posted = _create(board, transport, _proposal())
    content = transport.messages[posted.message_id or ""][1]
    assert posted.action_hash in content and "1. 중간보고서 초안 제출 — 담당자2 · 2026-10-20" in content
    assert "근거: 기관 메일 「중간보고 요청」" in content and "첨부 1개" in content
    assert (posted.status, posted.kind, posted.approval_thread_id) == ("posted", "project-board", "4242")


def test_only_the_owner_check_applies_and_only_once(wired: tuple[service.Board, FakeTransport]) -> None:
    board, transport = wired
    posted = _create(board, transport, _proposal())
    transport.reactions[(posted.message_id or "", "\u2705")].append(("999", False))
    assert _tick(board, transport) == [] and board.store.project_names() == ()

    _approve(transport, posted)
    assert _tick(board, transport) == ["x"]
    _tick(board, transport)
    snapshot = board.store.snapshot(today=date(2026, 10, 13))
    assert [t.title for t in snapshot.todos] == ["중간보고서 초안 제출", "실험 결과표 정리"]
    assert (board.proposals.get(posted.request_id) or posted).status == "applied"


def test_cancel_wins_and_tampered_bytes_never_apply(wired: tuple[service.Board, FakeTransport]) -> None:
    board, transport = wired
    posted = _create(board, transport, _proposal())
    _approve(transport, posted)
    _approve(transport, posted, "\u26d4")
    _tick(board, transport)
    assert (board.proposals.get(posted.request_id) or posted).status == "cancelled"
    assert board.store.project_names() == ()

    other = _create(board, transport, {**_proposal(), "source": {**_proposal()["source"], "source_id": "u-2"}})  # type: ignore[dict-item]
    path = board.proposals.frozen_path(other.request_id)
    path.write_text(path.read_text(encoding="utf-8").replace("2026-10-20", "2026-12-31"), encoding="utf-8")
    _approve(transport, other)
    for _ in range(3):
        _tick(board, transport)
    final = board.proposals.get(other.request_id) or other
    assert final.status == "failed" and "changed" in final.last_error and board.store.project_names() == ()


def test_revise_supersedes_the_pending_card_and_the_new_card_applies(wired: tuple[service.Board, FakeTransport]) -> None:
    board, transport = wired
    first = _create(board, transport, _proposal())
    change = service.Revision(set_due=((1, "2026-10-24"),), drop=(2,))
    revised = service.revise(
        board, first.request_id, change,
        post=lambda record: _post(board, transport, record),
    )
    assert first.message_id in transport.deleted
    assert (board.proposals.get(first.request_id) or first).status == "superseded"
    assert revised.revision == 2 and revised.todo_count == 1 and revised.key == first.key

    _approve(transport, board.proposals.get(revised.request_id) or revised)
    _tick(board, transport)
    todos = board.store.snapshot(today=date(2026, 10, 13)).todos
    assert [(t.title, t.due) for t in todos] == [("중간보고서 초안 제출", "2026-10-24")]


def test_an_applied_proposal_is_not_revised(wired: tuple[service.Board, FakeTransport]) -> None:
    board, transport = wired
    posted = _create(board, transport, _proposal())
    _approve(transport, posted)
    _tick(board, transport)
    with pytest.raises(ProjectBoardError, match="not revised"):
        service.revise(board, posted.request_id, service.Revision(summary="다른 요약"), post=None)
