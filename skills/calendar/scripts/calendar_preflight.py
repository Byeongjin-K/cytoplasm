"""Entity-preflight adapter for the single Calendar draft execution boundary."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

import calendar_core
import calendar_gate

if TYPE_CHECKING:
    from automation.entity_preflight.contracts import (
        JsonValue,
        VerificationRecord,
        WriteReceipt,
    )
    from automation.entity_preflight.gate import GateDependencies, GuardRequest
    from calendar_gate import Approval


@dataclass(frozen=True, slots=True)
class CalendarPreflightError(RuntimeError):
    message: str
    exit_code: int
    should_render: bool = False

    def __str__(self) -> str:
        return self.message


def repo_root() -> Path:
    """The checkout that actually carries ``automation``.

    A mounted release runs from ``/srv/autophagy-skills/releases/<skill>/<hash>/scripts``,
    so the ``parents[3]`` depth guess lands on ``.../releases`` — no automation package
    there, and the guard would fail closed on every send. Probe the candidates and take
    the first that really holds the package; fall back to the node's ops checkout.
    """
    override = os.environ.get("AUTOPHAGY_REPO_ROOT")
    if override:
        return Path(override).expanduser()
    here = Path(__file__).resolve()
    candidates = [*here.parents[2:6], Path("/srv/autophagy-agent-current"), Path("/srv/autophagy-agents")]
    for candidate in candidates:
        if (candidate / "automation" / "entity_preflight").is_dir():
            return candidate
    # No candidate carries the package. Return the ops checkout so the failure names a
    # real, diagnosable location instead of the meaningless depth guess (.../releases).
    current = Path("/srv/autophagy-agent-current")
    return current if (current / "automation").is_dir() else Path("/srv/autophagy-agents")


def _repo_module(name: str) -> ModuleType:
    root = repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    # A partial 'automation' regular package may already be bound (interop_runtime);
    # its fixed __path__ won't extend on sys.path change, so extend it to the repo's
    # automation dir so entity_preflight submodules resolve in the deployed runtime.
    bound = sys.modules.get("automation")
    repo_automation = str(root / "automation")
    if bound is not None and hasattr(bound, "__path__") and repo_automation not in bound.__path__:
        bound.__path__.append(repo_automation)
    try:
        return importlib.import_module(f"automation.entity_preflight.{name}")
    except ImportError:
        raise CalendarPreflightError(
            f"엔티티 사전검증 모듈 불가 (AUTOPHAGY_REPO_ROOT={root}) — Calendar 실행 거부", 3
        ) from None


def _contracts() -> ModuleType:
    return _repo_module("contracts")


def _gate() -> ModuleType:
    return _repo_module("gate")


@dataclass(frozen=True, slots=True)
class _CalendarWriteAdapter:
    draft: Mapping[str, JsonValue]
    approval: Approval

    def write(self, payload: Mapping[str, JsonValue]) -> WriteReceipt:
        contracts = _contracts()
        draft = _draft_with_summary(self.draft, _text(payload, "summary"))
        event_id = calendar_gate.execute_draft(draft, self.approval)
        return contracts.WriteReceipt("google_calendar", event_id, "calendar.events.insert", "executed")

    def requery(self, receipt: WriteReceipt, expected_fingerprint: str) -> VerificationRecord:
        contracts = _contracts()
        event = read_event(_text(self.draft, "calendar_id"), receipt.resource_id)
        if event is None:
            return contracts.VerificationRecord(
                external_system=receipt.external_system,
                resource_id=receipt.resource_id,
                api_operation="calendar.events.get",
                queried_at="verified",
                outcome=contracts.VerificationOutcome.ERROR,
                expected_fingerprint=expected_fingerprint,
                observed_fingerprint=None,
                sensitive_evidence_ref="private://entity-preflight/calendar-reread",
            )
        event_id = _text(event, "id")
        observed = _fingerprint({"summary": _text(event, "summary")}) if event_id == receipt.resource_id else None
        outcome = (
            contracts.VerificationOutcome.MATCH
            if observed == expected_fingerprint
            else contracts.VerificationOutcome.MISMATCH
        )
        return contracts.VerificationRecord(
            external_system=receipt.external_system,
            resource_id=receipt.resource_id,
            api_operation="calendar.events.get",
            queried_at="verified",
            outcome=outcome,
            expected_fingerprint=expected_fingerprint,
            observed_fingerprint=observed,
            sensitive_evidence_ref="private://entity-preflight/calendar-reread",
        )


def calendar_guard_request(draft: Mapping[str, JsonValue]) -> GuardRequest:
    """Build the one Calendar call-site request; a resolver integration supplies sources later."""

    contracts = _contracts()
    gate = _gate()
    summary = _text(draft, "summary")
    draft_id = _text(draft, "id")
    return gate.GuardRequest(
        request=contracts.PreflightInput(draft_id, summary, "google_calendar", _text(draft, "action"), ()),
        payload={"summary": summary},
        sources=(),
        idempotency_key=draft_id,
        actor="owner",
        purpose="calendar_execute",
        requested_at="runtime",
    )


def guarded_execute_draft(
    draft: Mapping[str, JsonValue],
    approval: Approval,
    dependencies: GateDependencies | None = None,
    *,
    guard_request: GuardRequest | None = None,
) -> str:
    """Run preflight directly before the frozen Calendar argv reaches ``gws``.

    A create first checks the calendar for the same event and raises
    ``calendar_dedupe.AlreadyExists`` instead of inserting. After the insert, the
    race cleanup and the node default color run on that one new event only.
    """

    import calendar_after_insert
    import calendar_dedupe

    existing = calendar_dedupe.before_insert(draft)
    if existing is not None:
        calendar_dedupe.record_skip(draft, approval, existing)
        raise calendar_dedupe.AlreadyExists(existing)
    gate = _gate()
    try:
        result = gate.guarded_write(
            calendar_guard_request(draft) if guard_request is None else guard_request,
            _CalendarWriteAdapter(draft, approval),
            gate.production_dependencies() if dependencies is None else dependencies,
        )
    except gate.EntityClarificationRequired as error:
        raise CalendarPreflightError(str(error), 6, error.should_render) from None
    except gate.EntityPreflightUnavailable as error:
        raise CalendarPreflightError(str(error), 3) from None
    except gate.PostWriteVerificationFailed as error:
        raise CalendarPreflightError(str(error), 6) from None
    event_id = result.receipt.resource_id
    if _text(draft, "action") == "create" and event_id:
        # The approved insert is already sealed as executed; a failed follow-up must not
        # turn it into a retried (and refused) confirm, so it only leaves a marker.
        try:
            outcome, keep = calendar_after_insert.settle_race(draft, event_id)
            if keep is None:
                calendar_after_insert.fill_color(draft, event_id)
        except calendar_gate.GateError as error:
            print(f"AFTER-INSERT-FAILED draft={_text(draft, 'id')} event={event_id} reason={error}")
        else:
            if outcome == "removed" and keep is not None:
                raise calendar_dedupe.AlreadyExists(keep)
    return event_id


def read_event(calendar_id: str, event_id: str) -> Mapping[str, JsonValue] | None:
    """Read back one Calendar event; connector failure becomes an explicit verification mismatch."""

    params = json.dumps({"calendarId": calendar_id, "eventId": event_id}, ensure_ascii=False)
    result = subprocess.run(  # noqa: S603 -- frozen local gws argv.
        [calendar_gate.gws_bin(), "calendar", "events", "get", "--params", params],
        capture_output=True,
        check=False,
        cwd=str(Path.home()),
        text=True,
        timeout=calendar_gate.GWS_TIMEOUT_S,
    )
    if result.returncode != 0:
        return None
    try:
        decoded = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _draft_with_summary(draft: Mapping[str, JsonValue], summary: str) -> dict[str, JsonValue]:
    updated = dict(draft)
    argv = updated.get("argv")
    if not isinstance(argv, list):
        raise CalendarPreflightError("드래프트 argv가 없습니다", 3)
    updated_argv: list[JsonValue] = [item for item in argv if isinstance(item, str)]
    if len(updated_argv) != len(argv):
        raise CalendarPreflightError("드래프트 argv 형식이 올바르지 않습니다", 3)
    if "--json" in updated_argv:
        json_index = updated_argv.index("--json") + 1
        if json_index >= len(updated_argv):
            raise CalendarPreflightError("드래프트 JSON 본문이 없습니다", 3)
        body = json.loads(str(updated_argv[json_index]))
        if not isinstance(body, dict):
            raise CalendarPreflightError("드래프트 JSON 본문 형식이 올바르지 않습니다", 3)
        body["summary"] = summary
        updated_argv[json_index] = json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    updated["argv"] = updated_argv
    updated["summary"] = summary
    updated["sha256"] = calendar_core.draft_sha256(updated)
    return updated


def _text(payload: Mapping[str, JsonValue], key: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) else ""


def _fingerprint(payload: Mapping[str, JsonValue]) -> str:
    encoded = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"
