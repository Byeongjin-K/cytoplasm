from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from importlib import import_module
from pathlib import Path
from typing import Final, TypeAlias

import pytest
from tests.unit.entity_preflight_fixtures import FixtureCase, discover_cases, fixture_sources

from automation.entity_preflight.contracts import (
    CandidateResolver,
    JsonValue,
    PreflightInput,
    RelationshipQuery,
    ResolutionCandidate,
    SourceKind,
    VerificationOutcome,
    VerificationRecord,
    WriteReceipt,
)
from automation.entity_preflight.gate import (
    EntityClarificationRequired,
    EntityPreflightUnavailable,
    GateDependencies,
    GuardRequest,
    InMemoryIdempotencyStore,
    guarded_write,
)
from automation.entity_preflight.resolver import DataSourceFailure
from automation.interop.effect_approval import ApprovalContext, ExternalEffectDecision

_REPO: Final = Path(__file__).resolve().parents[2]
for _scripts in (_REPO / "skills" / "calendar" / "scripts", _REPO / "skills" / "todo" / "scripts"):
    if str(_scripts) not in sys.path:
        sys.path.insert(0, str(_scripts))

calendar_gate = import_module("calendar_gate")
calendar_preflight = import_module("calendar_preflight")
todo_cli = import_module("todo_cli")
todo_preflight = import_module("todo_preflight")
_CASES: Final = {case.case_id: case for case in discover_cases()}
_TIMESTAMP: Final = "2026-07-29T00:00:00Z"
Evaluation: TypeAlias = Callable[[Sequence[str], ApprovalContext], ExternalEffectDecision]


@dataclass(slots=True)
class AuditSink:
    events: list[Mapping[str, JsonValue]] = field(default_factory=list)

    def append(self, event: Mapping[str, JsonValue]) -> str:
        self.events.append(event)
        return "private://synthetic/entity-preflight"


@dataclass(slots=True)
class OperationalLog:
    events: list[Mapping[str, JsonValue]] = field(default_factory=list)

    def emit(self, event: Mapping[str, JsonValue]) -> None:
        self.events.append(event)


@dataclass(slots=True)
class FakeGws:
    stored_title: str | None = None
    calls: list[tuple[str, ...]] = field(default_factory=list)

    def __call__(self, argv: list[str]) -> dict[str, JsonValue]:
        self.calls.append(tuple(argv))
        if argv[3] == "insert":
            body = json.loads(argv[argv.index("--json") + 1])
            title = body["title"]
            assert isinstance(title, str)
            self.stored_title = self.stored_title or title
            return {"id": "task-e2e-1", "title": self.stored_title}
        return {"id": "task-e2e-1", "title": self.stored_title or ""}


@dataclass(frozen=True, slots=True)
class FailingSource(CandidateResolver):
    source: SourceKind

    def resolve(
        self, request: PreflightInput, query: RelationshipQuery | None
    ) -> tuple[ResolutionCandidate, ...]:
        del request, query
        raise DataSourceFailure(self.source, "synthetic source unavailable")


@dataclass(slots=True)
class FailingWriter:
    writes: list[Mapping[str, JsonValue]] = field(default_factory=list)
    rereads: int = 0

    def write(self, payload: Mapping[str, JsonValue]) -> WriteReceipt:
        self.writes.append(payload)
        raise OSError("synthetic external write failure")

    def requery(self, receipt: WriteReceipt, expected_fingerprint: str) -> VerificationRecord:
        del receipt, expected_fingerprint
        self.rereads += 1
        raise AssertionError("requery must not follow a failed write")


def _guard(case: FixtureCase) -> GuardRequest:
    payload_key = "title" if case.request.target_system == "google_tasks" else "summary"
    return GuardRequest(
        request=case.request,
        payload={payload_key: case.request.raw_text},
        sources=fixture_sources(case),
        idempotency_key=f"e2e-{case.case_id}",
        actor="synthetic-owner",
        purpose="entity-preflight-e2e",
        requested_at=_TIMESTAMP,
    )


def _dependencies(audit: AuditSink, log: OperationalLog) -> GateDependencies:
    return GateDependencies(audit, log, InMemoryIdempotencyStore())


def _allowed(_argv: Sequence[str], _context: ApprovalContext) -> ExternalEffectDecision:
    return ExternalEffectDecision(True, True, "approved", "synthetic-approved", "synthetic-target")


def _todo_bindings(case: FixtureCase, gws: FakeGws, evaluate: Evaluation = _allowed):
    request = todo_cli.TaskRequest("@default", case.request.raw_text)
    return todo_preflight.TodoPreflightBindings(
        request, gws, ApprovalContext(None, "synthetic-owner", False), todo_cli.insert_argv, todo_cli.get_argv, evaluate
    )


def test_todo_auto_normalizes_fixture_before_fake_gws_write_and_rereads() -> None:
    case = _CASES["spelling-variant-auto"]
    audit, log, gws = AuditSink(), OperationalLog(), FakeGws()

    result = todo_preflight.create_task(_todo_bindings(case, gws), _dependencies(audit, log), guard_request=_guard(case))

    body = json.loads(gws.calls[0][gws.calls[0].index("--json") + 1])
    assert [call[3] for call in gws.calls] == ["insert", "get"]
    assert case.request.entities[0].surface not in body["title"]
    assert case.expected.selected["m-1"] in body["title"]
    assert result.title == body["title"]
    assert audit.events[0]["raw_text"] == case.request.raw_text
    assert audit.events[0]["decision_method"] == case.expected.reason.value
    assert audit.events[0]["candidates"]
    assert json.dumps(log.events, ensure_ascii=False).find(case.request.raw_text) == -1


def test_calendar_homophone_fixture_normalizes_then_rereads_fake_gws(monkeypatch: pytest.MonkeyPatch) -> None:
    case = _CASES["homophone-transcription-auto"]
    audit, log = AuditSink(), OperationalLog()
    argv_calls: list[tuple[JsonValue, ...]] = []
    summaries: list[str] = []
    draft: dict[str, JsonValue] = {
        "id": "calendar-e2e-1", "action": "create", "calendar_id": "primary", "event_id": "",
        "summary": case.request.raw_text, "start": "", "end": "",
        "argv": ["gws", "calendar", "events", "insert", "--json", json.dumps({"summary": case.request.raw_text})],
        "sha256": "synthetic",
    }

    def execute(current: Mapping[str, JsonValue], _approval: object) -> str:
        argv = current["argv"]
        assert isinstance(argv, list) and all(isinstance(item, str) for item in argv)
        argv_calls.append(tuple(argv))
        summary = current["summary"]
        assert isinstance(summary, str)
        summaries.append(summary)
        return "calendar-e2e-event"

    monkeypatch.setattr(calendar_preflight.calendar_gate, "execute_draft", execute)
    monkeypatch.setattr(calendar_preflight, "read_event", lambda _calendar, event: {"id": event, "summary": summaries[0]})

    event_id = calendar_preflight.guarded_execute_draft(
        draft, calendar_gate.Approval("synthetic", "e2e", "synthetic-owner"), _dependencies(audit, log), guard_request=_guard(case)
    )

    assert event_id == "calendar-e2e-event"
    assert case.request.entities[0].surface not in summaries[0]
    assert case.expected.selected["m-1"] in summaries[0]
    assert argv_calls[0][0:4] == ("gws", "calendar", "events", "insert")
    assert audit.events[-1]["outcome"] == VerificationOutcome.MATCH.value


def test_multi_candidate_conflict_asks_exactly_one_confirmation_before_calendar_write() -> None:
    case = _CASES["homophone-transcription-conflict"]
    writer = FailingWriter()

    with pytest.raises(EntityClarificationRequired) as raised:
        guarded_write(_guard(case), writer, _dependencies(AuditSink(), OperationalLog()))

    assert raised.value.should_render is True
    assert str(raised.value).count("ENTITY-CLARIFY") == 1
    assert writer.writes == []


def test_low_confidence_candidate_asks_exactly_one_confirmation_without_write() -> None:
    case = _CASES["low-confidence-clarify"]
    writer = FailingWriter()

    with pytest.raises(EntityClarificationRequired) as raised:
        guarded_write(_guard(case), writer, _dependencies(AuditSink(), OperationalLog()))

    assert raised.value.should_render is True
    assert str(raised.value).count("ENTITY-CLARIFY") == 1
    assert writer.writes == []


def test_no_candidate_asks_exactly_one_confirmation_without_write() -> None:
    case = _CASES["no-candidate-clarify"]
    writer = FailingWriter()

    with pytest.raises(EntityClarificationRequired) as raised:
        guarded_write(_guard(case), writer, _dependencies(AuditSink(), OperationalLog()))

    assert raised.value.should_render is True
    assert str(raised.value).count("ENTITY-CLARIFY") == 1
    assert writer.writes == []


def test_user_cancellation_after_normalization_never_invokes_fake_gws() -> None:
    case = _CASES["exact-spelling-auto"]
    audit, gws, evaluated = AuditSink(), FakeGws(), []

    def cancelled(argv: Sequence[str], _context: ApprovalContext) -> ExternalEffectDecision:
        evaluated.append(tuple(argv))
        return ExternalEffectDecision(True, False, "cancelled", "synthetic-cancelled", "synthetic-target")

    with pytest.raises(todo_preflight.TodoPreflightError) as raised:
        todo_preflight.create_task(_todo_bindings(case, gws, cancelled), _dependencies(audit, OperationalLog()), guard_request=_guard(case))

    assert raised.value.exit_code == 4
    assert gws.calls == []
    assert case.expected.selected["m-1"] in " ".join(evaluated[0])
    assert audit.events[0]["raw_text"] == case.request.raw_text


def test_data_source_failure_is_audited_and_never_invokes_external_write() -> None:
    case = _CASES["exact-spelling-auto"]
    audit, writer = AuditSink(), FailingWriter()
    request = replace(_guard(case), sources=(FailingSource(SourceKind.PERSONAL_RAG),))

    with pytest.raises(EntityPreflightUnavailable):
        guarded_write(request, writer, _dependencies(audit, OperationalLog()))

    assert writer.writes == []
    assert audit.events == [
        {"event": "entity_preflight_failure", "raw_text": case.request.raw_text, "task_id": case.request.request_id,
         "target_system": case.request.target_system, "timestamp": _TIMESTAMP}
    ]


def test_external_write_failure_is_reported_without_a_post_write_reread() -> None:
    case = _CASES["exact-spelling-auto"]
    audit, writer = AuditSink(), FailingWriter()

    with pytest.raises(OSError, match="synthetic external write failure"):
        guarded_write(_guard(case), writer, _dependencies(audit, OperationalLog()))

    assert len(writer.writes) == 1
    assert writer.rereads == 0
    assert audit.events[0]["decision_method"] == case.expected.reason.value


def test_post_write_reread_mismatch_is_reported_and_recorded() -> None:
    case = _CASES["spelling-variant-auto"]
    audit, gws = AuditSink(), FakeGws(stored_title="박예서")

    with pytest.raises(todo_preflight.TodoPreflightError, match="ENTITY-VERIFY-FAIL outcome=mismatch"):
        todo_preflight.create_task(_todo_bindings(case, gws), _dependencies(audit, OperationalLog()), guard_request=_guard(case))

    assert [call[3] for call in gws.calls] == ["insert", "get"]
    assert audit.events[-1]["phase"] == "verification_failed"
    assert audit.events[-1]["outcome"] == VerificationOutcome.MISMATCH.value


def test_retry_after_clarify_neither_repeats_confirmation_nor_writes() -> None:
    case = _CASES["homophone-transcription-conflict"]
    dependencies, writer = _dependencies(AuditSink(), OperationalLog()), FailingWriter()

    with pytest.raises(EntityClarificationRequired) as first:
        guarded_write(_guard(case), writer, dependencies)
    with pytest.raises(EntityClarificationRequired) as second:
        guarded_write(_guard(case), writer, dependencies)

    assert first.value.should_render is True
    assert second.value.should_render is False
    assert writer.writes == []


def test_retry_after_verified_todo_write_reuses_receipt_without_duplicate_gws_call() -> None:
    case = _CASES["exact-spelling-auto"]
    dependencies, gws = _dependencies(AuditSink(), OperationalLog()), FakeGws()

    first = todo_preflight.create_task(_todo_bindings(case, gws), dependencies, guard_request=_guard(case))
    second = todo_preflight.create_task(_todo_bindings(case, gws), dependencies, guard_request=_guard(case))

    assert first.task_id == second.task_id == "task-e2e-1"
    assert [call[3] for call in gws.calls] == ["insert", "get"]
