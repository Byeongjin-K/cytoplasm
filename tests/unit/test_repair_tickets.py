from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import pytest

from automation.repair import repair_cli
from automation.repair.repair_cli import HermesKanban
from automation.repair.repair_core import RepairEvent, RepairRegistry, RepairService


_CARD_JSON: Final = '{"task":{"id":"t_repair01"}}'
_CLOSED_CARD_JSON: Final = '{"task":{"id":"t_repair01","status":"done"}}'


@dataclass
class FakeKanban:
    created: list[tuple[str, str, str]] = field(default_factory=list)
    comments: list[tuple[str, str]] = field(default_factory=list)
    blocked: list[tuple[str, str]] = field(default_factory=list)

    def create(self, title: str, body: str, idempotency_key: str) -> str:
        self.created.append((title, body, idempotency_key))
        return "t_repair01"

    def comment(self, ticket_id: str, text: str) -> None:
        self.comments.append((ticket_id, text))

    def block_for_repair(self, ticket_id: str, reason: str) -> None:
        self.blocked.append((ticket_id, reason))


@dataclass
class FakePrivateLogs:
    entries: list[tuple[str, int, str]] = field(default_factory=list)

    def write(self, ticket_id: str, occurrence: int, raw_log: str) -> str:
        self.entries.append((ticket_id, occurrence, raw_log))
        return f"/srv/autophagy-private/repair-logs/{ticket_id}/occurrence-{occurrence}.log"


def test_hermes_kanban_create_omits_triage_so_card_can_be_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: the production adapter is observed at its subprocess boundary.
    calls: list[tuple[str, ...]] = []

    def capture(*args: str) -> str:
        calls.append(args)
        return _CARD_JSON

    monkeypatch.setattr(repair_cli.HermesKanban, "_run", staticmethod(capture))

    # When: it creates a repair ticket before RepairService applies needs_input.
    ticket_id = HermesKanban().create("repair title", "repair body", "repair-key")

    # Then: the CLI receives a default-ready create request rather than an unblockable triage card.
    assert ticket_id == "t_repair01"
    assert "--triage" not in calls[0]


def test_status_read_gives_up_sooner_than_a_board_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: the production adapter observed at its real subprocess boundary.
    timeouts: dict[str, float] = {}

    def capture(args: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[str]:
        timeouts[args[2]] = float(kwargs["timeout"])  # pyright: ignore[reportArgumentType]
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=_CLOSED_CARD_JSON, stderr="")

    monkeypatch.setattr(repair_cli.subprocess, "run", capture)

    # When: the detector reads one card state and creates one card.
    HermesKanban().is_closed("t_repair01")
    HermesKanban().create("repair title", "repair body", "repair-key")

    # Then: an unresponsive board costs the detect path far less than a mutation may.
    assert timeouts["show"] < timeouts["create"]
    assert timeouts["show"] <= 10


def test_creates_blocked_repair_ticket_with_redacted_path_and_hash(tmp_path: Path) -> None:
    # Given: a skill failure that includes a token-shaped secret.
    service = RepairService(FakeKanban(), FakePrivateLogs(), RepairRegistry(tmp_path / "repair-state.json"))
    event = RepairEvent(source="skill:report", location="report_cli.py:42", raw_log="RuntimeError sk-secretvalue")

    # When: the detector records it.
    result = service.record(event)

    # Then: the ticket data is masked while the full raw log is only handed to the private sink.
    assert result.ticket_id == "t_repair01"
    assert result.occurrence == 1
    assert result.created is True
    assert "sk-secretvalue" not in result.excerpt
    assert result.private_path.endswith("t_repair01/occurrence-1.log")
    assert len(result.log_hash) == 64


def test_recurring_error_adds_occurrence_without_creating_duplicate(tmp_path: Path) -> None:
    # Given: a previously recorded normalized failure.
    kanban = FakeKanban()
    private_logs = FakePrivateLogs()
    service = RepairService(kanban, private_logs, RepairRegistry(tmp_path / "repair-state.json"))
    event = RepairEvent(source="healthcheck", location="agent gateway", raw_log="FAIL agent gateway")
    first = service.record(event)

    # When: the same failure reoccurs.
    repeated = service.record(event)

    # Then: its original ticket is retained and its occurrence count advances.
    assert repeated.ticket_id == first.ticket_id
    assert repeated.created is False
    assert repeated.occurrence == 2
    assert len(kanban.created) == 1
    assert private_logs.entries[-1][1] == 2


def test_closed_stored_ticket_mints_fresh_repair_ticket(tmp_path: Path) -> None:
    # Given: a recurrence whose deduplicated card has already been closed.
    registry = RepairRegistry(tmp_path / "repair-state.json")
    first = registry.claim("signature", lambda _superseded: "t_closed")

    # When: its card-state probe reports that stored card as closed.
    repeated = registry.claim("signature", lambda _superseded: "t_fresh", lambda ticket_id: ticket_id == first.ticket_id)

    # Then: the recurrence receives a fresh card and a new occurrence sequence.
    assert repeated.ticket_id == "t_fresh"
    assert repeated.occurrence == 1
    assert repeated.created is True


def test_status_probe_absence_none_or_failure_keeps_existing_ticket(tmp_path: Path) -> None:
    # Given: recurrences linked to existing cards.
    absent_probe = RepairRegistry(tmp_path / "absent-probe.json")
    _ = absent_probe.claim("signature", lambda _superseded: "t_existing")
    none_probe = RepairRegistry(tmp_path / "none-probe.json")
    _ = none_probe.claim("signature", lambda _superseded: "t_unknown")
    failed_probe = RepairRegistry(tmp_path / "failed-probe.json")
    _ = failed_probe.claim("signature", lambda _superseded: "t_unreadable")

    def unavailable(_ticket_id: str) -> None:
        return None

    def unreadable(_ticket_id: str) -> bool:
        raise RuntimeError("kanban unavailable")

    # When: no card-state probe is available, it has no result, or its read fails.
    without_probe = absent_probe.claim("signature", lambda _superseded: "t_unexpected")
    with_none_probe = none_probe.claim("signature", lambda _superseded: "t_unexpected", unavailable)
    with_failed_probe = failed_probe.claim("signature", lambda _superseded: "t_unexpected", unreadable)

    # Then: deduplication remains conservative and advances the original card.
    assert without_probe.ticket_id == "t_existing"
    assert without_probe.occurrence == 2
    assert without_probe.created is False
    assert with_none_probe.ticket_id == "t_unknown"
    assert with_none_probe.occurrence == 2
    assert with_none_probe.created is False
    assert with_failed_probe.ticket_id == "t_unreadable"
    assert with_failed_probe.occurrence == 2
    assert with_failed_probe.created is False


@dataclass(frozen=True, slots=True)
class _CreateRequest:
    title: str
    body: str
    idempotency_key: str


@dataclass
class _ReopeningKanban:
    """Board fake whose cards can be closed, so the detector must open a new one."""

    closed: set[str] = field(default_factory=set)
    created: list[str] = field(default_factory=list)
    requests: list[_CreateRequest] = field(default_factory=list)
    comments: list[tuple[str, str]] = field(default_factory=list)
    blocked: list[tuple[str, str]] = field(default_factory=list)

    def create(self, title: str, body: str, idempotency_key: str) -> str:
        ticket_id = f"t_card{len(self.created) + 1}"
        self.created.append(ticket_id)
        self.requests.append(_CreateRequest(title, body, idempotency_key))
        return ticket_id

    def comment(self, ticket_id: str, text: str) -> None:
        self.comments.append((ticket_id, text))

    def block_for_repair(self, ticket_id: str, reason: str) -> None:
        self.blocked.append((ticket_id, reason))

    def is_closed(self, ticket_id: str) -> bool:
        return ticket_id in self.closed


def test_service_opens_a_new_card_when_the_deduplicated_card_is_closed(tmp_path: Path) -> None:
    # Given: a service wired to its board's card-state probe.
    kanban = _ReopeningKanban()
    service = RepairService(
        kanban, FakePrivateLogs(), RepairRegistry(tmp_path / "repair-state.json"), kanban.is_closed
    )
    event = RepairEvent(source="cron", location="mail-triage-watch", raw_log="ValueError: boom")
    first = service.record(event)

    # When: the owner closes that card and the same failure recurs.
    kanban.closed.add(first.ticket_id)
    second = service.record(event)

    # Then: the recurrence opens a fresh card instead of thickening the closed one.
    assert second.ticket_id != first.ticket_id
    assert second.created is True
    assert second.occurrence == 1
    assert kanban.created == [first.ticket_id, second.ticket_id]


def test_superseding_card_names_the_closed_card_and_asks_a_distinct_dedup_key(tmp_path: Path) -> None:
    # Given: a service whose first card the owner has already closed.
    kanban = _ReopeningKanban()
    service = RepairService(
        kanban, FakePrivateLogs(), RepairRegistry(tmp_path / "repair-state.json"), kanban.is_closed
    )
    event = RepairEvent(source="cron", location="mail-triage-watch", raw_log="ValueError: boom")
    first = service.record(event)
    kanban.closed.add(first.ticket_id)

    # When: the same failure recurs against that closed card.
    second = service.record(event)

    # Then: the fresh card names the card it supersedes, so the board itself links the pair.
    assert first.ticket_id in kanban.requests[1].body

    # And: it asks for a dedup key the closed card does not own. `hermes kanban create
    # --idempotency-key` returns the existing non-archived task for a repeated key
    # (docs/qa/RRC-0/01-cli-contract.md), so reusing it would hand back the closed card.
    assert kanban.requests[1].idempotency_key != kanban.requests[0].idempotency_key
    assert second.ticket_id != first.ticket_id


def test_ticket_excerpt_and_comment_never_include_sensitive_error_text(tmp_path: Path) -> None:
    # Given: a bearer token in an error log.
    kanban = FakeKanban()
    service = RepairService(kanban, FakePrivateLogs(), RepairRegistry(tmp_path / "repair-state.json"))

    # When: the event is persisted.
    service.record(RepairEvent(source="skill:repair", location="repair_cli.py:1", raw_log="Bearer secret-value"))

    # Then: all Kanban-visible strings omit the sensitive value and include linkage instead.
    visible = "\n".join(text for _, text in kanban.comments) + "\n" + kanban.created[0][1]
    assert "secret-value" not in visible
    assert "sha256=" in visible
    assert "/srv/autophagy-private/repair-logs/t_repair01/" in visible


def _run_manual(
    monkeypatch: pytest.MonkeyPatch,
    service: RepairService,
    message: str,
) -> None:
    """Drive the real manual entry point with the board and registry replaced."""
    monkeypatch.setattr(repair_cli, "_service", lambda: service)
    monkeypatch.setattr(repair_cli, "publish", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(sys, "argv", ["repair-cli", "manual", message])
    assert repair_cli.main() == 0


def test_two_manual_requests_sharing_a_first_word_open_separate_cards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given: two unrelated manual repairs whose text happens to start with the same word.

    When: each is submitted through the manual entry point.
    Then: each opens its own card.

    The dedup signature is digest(source, location, first token of the excerpt), and the
    manual path used to pass constant source and location — so the first word decided the
    card alone. 2026-09-09 실측: "캘린더 등록 승인 요청이…" 와 "메일 다이제스트 오류" 가 7월·8월에
    종결된 무관한 카드로 각각 빨려 들어가 보드에서 사라졌다.
    """
    kanban = _ReopeningKanban()
    service = RepairService(kanban, FakePrivateLogs(), RepairRegistry(tmp_path / "state.json"))

    _run_manual(monkeypatch, service, "!repair 캘린더 등록 승인 요청이 보이지 않고 폐기된다")
    _run_manual(monkeypatch, service, "!repair 캘린더 초대 메일이 참석자에게 가지 않는다")

    assert kanban.created == ["t_card1", "t_card2"]
    assert kanban.requests[0].idempotency_key != kanban.requests[1].idempotency_key
    assert len(kanban.blocked) == 2


def test_the_same_manual_request_still_thickens_one_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """중복제거 자체는 살아 있어야 한다 — 같은 요청을 두 번 말하면 카드는 하나다."""
    kanban = _ReopeningKanban()
    service = RepairService(kanban, FakePrivateLogs(), RepairRegistry(tmp_path / "state.json"))

    _run_manual(monkeypatch, service, "!repair 메일 다이제스트 오류")
    _run_manual(monkeypatch, service, "!repair   메일   다이제스트\n오류  ")

    assert kanban.created == ["t_card1"]
    assert "Repair occurrence: 2" in kanban.comments[1][1]
