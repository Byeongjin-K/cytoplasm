"""Entity-preflight adapter for the one Google Tasks mutation path."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from automation.entity_preflight.contracts import JsonValue, VerificationRecord, WriteReceipt
    from automation.entity_preflight.gate import GateDependencies, GuardRequest
    from automation.interop.approval_types import ApprovalContext, ExternalEffectDecision
    from todo_cli import CommandRunner, TaskRequest


@dataclass(frozen=True, slots=True)
class TodoPreflightBindings:
    """Dependencies retained by the adapter that owns the Tasks write and readback."""

    request: TaskRequest
    runner: CommandRunner
    context: ApprovalContext
    insert_argv: Callable[[TaskRequest], tuple[str, ...]]
    get_argv: Callable[[str, str], tuple[str, ...]]
    evaluate: Callable[[Sequence[str], ApprovalContext], ExternalEffectDecision]


@dataclass(frozen=True, slots=True)
class PreflightTaskResult:
    task_id: str
    title: str
    action_hash: str


@dataclass(frozen=True, slots=True)
class TodoPreflightError(RuntimeError):
    message: str
    exit_code: int
    should_render: bool = False

    def __str__(self) -> str:
        return self.message


def repo_root() -> Path:
    """Resolve the runtime checkout through ``automation/runtime_root.py`` alone.

    The skill may run from an immutable mount before ``automation`` is importable, so
    this bootstrap only locates that one module file. Root priority and fallback
    decisions remain exclusively inside ``resolve_runtime_root``.
    """
    module = _load_runtime_root_module()
    resolver = getattr(module, "resolve_runtime_root", None)
    if not callable(resolver):
        raise TodoPreflightError("runtime_root.py에 resolve_runtime_root가 없습니다", 3)
    resolved = resolver(os.environ)
    if not isinstance(resolved, Path):
        raise TodoPreflightError("resolve_runtime_root 결과가 경로가 아닙니다", 3)
    return resolved


def _runtime_root_module_candidates() -> tuple[Path, ...]:
    """Return only locations capable of carrying the bootstrap module file."""
    override = os.environ.get("AUTOPHAGY_RUNTIME_ROOT")
    explicit = () if not override else (Path(override).expanduser() / "automation" / "runtime_root.py",)
    local = Path(__file__).resolve().parents[3] / "automation" / "runtime_root.py"
    return (
        *explicit,
        local,
        Path("/srv/autophagy-agent-current/automation/runtime_root.py"),
        Path("/srv/autophagy-agents/automation/runtime_root.py"),
    )


def _load_runtime_root_module() -> ModuleType:
    """Load the first present bootstrap module without importing ``automation``."""
    for candidate in _runtime_root_module_candidates():
        if not candidate.is_file():
            continue
        spec = importlib.util.spec_from_file_location("_todo_runtime_root", candidate)
        if spec is None or spec.loader is None:
            raise TodoPreflightError(f"runtime_root.py 로더를 만들 수 없습니다: {candidate}", 3)
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except (ImportError, OSError) as error:
            raise TodoPreflightError(f"runtime_root.py를 불러올 수 없습니다: {candidate}", 3) from error
        return module
    raise TodoPreflightError("automation/runtime_root.py를 찾을 수 없습니다 — 쓰기 거부", 3)


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
        raise TodoPreflightError(
            f"엔티티 사전검증 모듈 불가 (AUTOPHAGY_REPO_ROOT={root}) — Google Tasks 쓰기 거부", 3
        ) from None


def _contracts() -> ModuleType:
    return _repo_module("contracts")


def _gate() -> ModuleType:
    return _repo_module("gate")


@dataclass(frozen=True, slots=True)
class _TaskWriteAdapter:
    bindings: TodoPreflightBindings

    def write(self, payload: Mapping[str, JsonValue]) -> WriteReceipt:
        contracts = _contracts()
        request = _request_with_payload(self.bindings.request, payload)
        decision = self.bindings.evaluate(self.bindings.insert_argv(request), self.bindings.context)
        if not decision.allowed:
            raise TodoPreflightError(
                "소유자 승인 레코드가 없어 Google Tasks 쓰기를 거부했습니다 "
                f"(hash={decision.action_hash} target={decision.target_id})",
                4,
            )
        created = self.bindings.runner(list(self.bindings.insert_argv(request)))
        task_id = _text(created, "id")
        if not task_id:
            raise TodoPreflightError("insert 응답에 task id가 없습니다", 6)
        return contracts.WriteReceipt("google_tasks", task_id, "tasks.tasks.insert", "created")

    def requery(self, receipt: WriteReceipt, expected_fingerprint: str) -> VerificationRecord:
        contracts = _contracts()
        stored = self.bindings.runner(list(self.bindings.get_argv(self.bindings.request.tasklist, receipt.resource_id)))
        stored_id = _text(stored, "id")
        stored_title = _text(stored, "title")
        observed = _fingerprint({"title": stored_title}) if stored_id == receipt.resource_id else None
        return contracts.VerificationRecord(
            external_system=receipt.external_system,
            resource_id=receipt.resource_id,
            api_operation="tasks.tasks.get",
            queried_at="verified",
            outcome=(
                contracts.VerificationOutcome.MATCH
                if observed == expected_fingerprint
                else contracts.VerificationOutcome.MISMATCH
            ),
            expected_fingerprint=expected_fingerprint,
            observed_fingerprint=observed,
            sensitive_evidence_ref="private://entity-preflight/tasks-reread",
        )


def task_guard_request(request: TaskRequest) -> GuardRequest:
    """Build the one Todo call-site request; injected sources can replace the empty tuple later."""

    contracts = _contracts()
    gate = _gate()
    raw_text = request.title
    key = _fingerprint({"due": request.due, "notes": request.notes, "tasklist": request.tasklist, "title": raw_text})
    return gate.GuardRequest(
        request=contracts.PreflightInput(key, raw_text, "google_tasks", "create", ()),
        payload={"title": raw_text},
        sources=(),
        idempotency_key=key,
        actor="owner",
        purpose="task_create",
        requested_at="runtime",
    )


def create_task(
    bindings: TodoPreflightBindings,
    dependencies: GateDependencies | None = None,
    *,
    guard_request: GuardRequest | None = None,
) -> PreflightTaskResult:
    """Run the shared guard immediately before the only Tasks connector call."""

    gate = _gate()
    try:
        result = gate.guarded_write(
            task_guard_request(bindings.request) if guard_request is None else guard_request,
            _TaskWriteAdapter(bindings),
            gate.production_dependencies() if dependencies is None else dependencies,
        )
    except gate.EntityClarificationRequired as error:
        raise TodoPreflightError(str(error), 6, error.should_render) from None
    except gate.EntityPreflightUnavailable as error:
        raise TodoPreflightError(str(error), 3) from None
    except gate.PostWriteVerificationFailed as error:
        raise TodoPreflightError(str(error), 6) from None
    title = _payload_title(result.payload)
    approval = bindings.evaluate(
        bindings.insert_argv(_request_with_payload(bindings.request, result.payload)), bindings.context
    )
    return PreflightTaskResult(result.receipt.resource_id, title, approval.action_hash)


def _request_with_payload(request: TaskRequest, payload: Mapping[str, JsonValue]) -> TaskRequest:
    return type(request)(request.tasklist, _payload_title(payload), request.notes, request.due)


def _payload_title(payload: Mapping[str, JsonValue]) -> str:
    title = payload.get("title")
    return title if isinstance(title, str) else ""


def _text(payload: Mapping[str, JsonValue], key: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) else ""


def _fingerprint(payload: Mapping[str, JsonValue]) -> str:
    encoded = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"
