"""Owner-internal Google Tasks writes with a mandatory post-write re-read.

``create`` and ``write-now`` share the owner-command writer. Their caller is
dori-gate, which verifies the initiating Discord owner message before execution.
No CLI command produces a todo approval card. ``create_task`` remains solely
for draining already stored approval generations through the legacy watcher.
Both writers reuse the personal-entity preflight and verify the inserted task.

Env: TODO_APPROVAL_LOG, TODO_OWNER_ID, TODO_GWS_BIN,
     AUTOPHAGY_REPO_ROOT, INTEROP_CONFIG, E2E_TEST_MODE.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess  # noqa: S404 — gws CLI is the only supported Tasks transport
import sys
from collections.abc import Callable, Sequence
from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

from todo_cli_model import (
    ApprovalRequiredError,
    CreatedTask,
    EntityClarificationError,
    TaskRequest,
    TodoError,
    TodoReconciliationRequiredError,
    VerificationFailedError,
    get_argv,
    insert_argv,
    list_argv,
)

if TYPE_CHECKING:
    from automation.interop.approval_types import ApprovalContext, ExternalEffectDecision

GWS_TIMEOUT_S = 120
CommandRunner = Callable[[list[str]], dict[str, Any]]


def repo_root() -> Path:
    adapter = import_module("todo_preflight")
    try:
        return adapter.repo_root()
    except adapter.TodoPreflightError as error:
        raise TodoError(str(error), error.exit_code) from None


def gate_module() -> ModuleType:
    """Import exact stored-card validation for the legacy drain path."""
    root = repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        return import_module("automation.interop.effect_approval")
    except ImportError as error:
        raise TodoError(
            f"외부효과 게이트 모듈 불가 (AUTOPHAGY_REPO_ROOT={root}) — 쓰기 거부", 3
        ) from error


def approval_log() -> Path:
    default = "/srv/autophagy-agents/logs/approvals.jsonl"
    return Path(os.environ.get("TODO_APPROVAL_LOG", default)).expanduser()


def owner_id() -> str:
    env = os.environ.get("TODO_OWNER_ID")
    if env:
        return env
    config = Path(os.environ.get("INTEROP_CONFIG", "~/.hermes/interop/config.json")).expanduser()
    try:
        value = json.loads(config.read_text(encoding="utf-8")).get("owner_id")
    except (OSError, json.JSONDecodeError) as error:
        raise TodoError(f"interop config를 읽을 수 없습니다: {config}", 3) from error
    if not isinstance(value, str) or not value:
        raise TodoError("interop config에 owner_id가 없습니다 (fail-closed)", 3)
    return value


def approval_context() -> ApprovalContext:
    return gate_module().ApprovalContext(
        approval_log=approval_log(),
        owner_id=owner_id(),
        e2e_test_mode=os.environ.get("E2E_TEST_MODE") == "1",
    )


def build_tool_call(argv: Sequence[str]) -> Any:
    return gate_module().ToolCall(tool_name="gws", arguments={"command": " ".join(argv)})


def evaluate(argv: Sequence[str], *, context: ApprovalContext) -> ExternalEffectDecision:
    """Validate a previously issued task card, without classifying new writes."""
    module = gate_module()
    return module.validate_approval(
        build_tool_call(argv), "tool:gws_tasks_mutation:gws", context
    )


def run_gws(argv: Sequence[str]) -> dict[str, Any]:
    """Execute a frozen gws argv with an explicitly propagated child env."""
    override = os.environ.get("TODO_GWS_BIN", "")
    binary = override or shutil.which("gws") or os.path.expanduser("~/.local/bin/gws")
    if not override and not Path(binary).exists():
        raise TodoError("gws CLI를 찾을 수 없습니다 (TODO_GWS_BIN 설정 필요)", 3)
    command = [binary, *argv[1:]]
    env = dict(os.environ)
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    proc = subprocess.run(  # noqa: S603
        command, capture_output=True, text=True, timeout=GWS_TIMEOUT_S, check=False, env=env
    )
    if proc.returncode != 0:
        raise TodoError(
            f"gws 실행 실패 rc={proc.returncode}: {proc.stderr.strip()[:200]}", 5
        )
    decoded, _ = json.JSONDecoder().raw_decode(proc.stdout.strip() or "{}")
    if not isinstance(decoded, dict):
        raise TodoError("gws 응답이 객체가 아닙니다", 5)
    return decoded


def create_task(
    request: TaskRequest,
    *,
    runner: CommandRunner | None = None,
    context: ApprovalContext | None = None,
    claim_store: Any | None = None,
) -> CreatedTask:
    """THE single Google Tasks write path — gate first, write, then prove by re-read."""
    execute = runner if runner is not None else run_gws
    ctx = context if context is not None else approval_context()
    claims_module = import_module("todo_execution_claim")
    claims = claim_store if claim_store is not None else claims_module.ApprovalClaimStore(
        Path(os.environ.get("TODO_APPROVAL_ROOT", "~/.hermes/todo-approvals")).expanduser()
    )
    claim = None

    def claimed_execute(argv: list[str]) -> dict[str, Any]:
        nonlocal claim
        if argv[3] == "insert":
            decision = evaluate(argv, context=ctx)
            claim = claims.acquire(decision, ctx)
        return execute(argv)

    adapter = import_module("todo_preflight")
    try:
        result = adapter.create_task(
            adapter.TodoPreflightBindings(
                request,
                claimed_execute,
                ctx,
                insert_argv,
                get_argv,
                lambda argv, bound_context: evaluate(argv, context=bound_context),
            )
        )
    except claims_module.ApprovalAlreadyConsumedError as error:
        raise ApprovalRequiredError(str(error)) from None
    except claims_module.ApprovalReconciliationRequiredError as error:
        raise TodoReconciliationRequiredError(str(error)) from None
    except claims_module.ApprovalClaimError as error:
        raise ApprovalRequiredError(str(error)) from None
    except adapter.TodoPreflightError as error:
        if error.should_render:
            raise EntityClarificationError(str(error), True) from None
        if error.exit_code == 4:
            raise ApprovalRequiredError(str(error)) from None
        if error.exit_code == 6:
            raise VerificationFailedError(str(error)) from None
        raise TodoError(str(error), error.exit_code) from None
    if claim is None:
        raise TodoReconciliationRequiredError("todo write completed without an approval claim")
    claims.complete(claim, result.task_id, result.title)
    _notify_created(result.action_hash, result.task_id, result.title, ctx)
    return CreatedTask(result.task_id, result.title, request.tasklist, result.action_hash, True)


def _notify_created(action_hash: str, task_id: str, title: str, ctx: ApprovalContext) -> None:
    """Best-effort result notice after the verified write — never touches the exit code.

    E2E 승인(E2E_TEST_MODE)과 DUMMY-secret 샌드박스는 실제 통지를 열지 않는다
    (mail `_notify_sent` allowlist 선례). 목적지는 승인된 그 생성(generation)의 origin
    바인딩 — 원 채널 스레드, 없으면 레코드에 저장된 승인 채널.
    """
    dummy_secret = os.environ.get("AUTOPHAGY_DEMO_SECRET", "")
    if ctx.e2e_test_mode or dummy_secret.startswith("DUMMY-"):
        reason = "e2e_test_mode" if ctx.e2e_test_mode else "dummy_secret"
        print(f"NOTIFY-SKIP hash={action_hash[:19]} reason={reason}", file=sys.stderr)
        return
    try:
        runtime = import_module("todo_approval_runtime")
        record_like = runtime.origin_record(action_hash)
        if record_like is None:
            raise TodoError("approval record for the verified write is missing", 3)
        runtime.notify_result(
            record_like,
            f"✅ 할일 등록 완료: {title} (task {task_id})\n"
            "소유자 ✅ 승인 · tasks.get 재조회로 검증되었습니다.",
            thread_name=f"할일: {title}",
            outcome=runtime.OUTCOME_DONE,
        )
    except Exception as error:  # noqa: BLE001 — notice must never undo a verified write
        print(f"NOTIFY-FAIL hash={action_hash[:19]} err={type(error).__name__}", file=sys.stderr)


def _cmd_plan(args: argparse.Namespace) -> int:
    request = TaskRequest(args.tasklist, args.title, args.notes, args.due)
    decision = evaluate(insert_argv(request), context=approval_context())
    print(
        f"PLAN tasklist={request.tasklist} external_effect={decision.external_effect} "
        f"approved={decision.allowed} hash={decision.action_hash} target={decision.target_id}"
    )
    return 0


def _cmd_write_now(args: argparse.Namespace) -> int:
    return import_module("todo_owner_command").write_now(args)


def _cmd_list(args: argparse.Namespace) -> int:
    items = run_gws(list_argv(args.tasklist)).get("items", [])
    rows = items if isinstance(items, list) else []
    for item in rows:
        if isinstance(item, dict):
            print(f"TASK id={item.get('id', '')} status={item.get('status', '')}")
    print(f"LISTED tasklist={args.tasklist} count={len(rows)}")
    return 0


def _cmd_runtime_root(_args: argparse.Namespace) -> int:
    print(repo_root())
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="todo_cli", description="소유자 지시로 등록하는 Google Tasks")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, handler in (
        ("plan", _cmd_plan),
        ("create", _cmd_write_now),
        ("write-now", _cmd_write_now),
    ):
        sub = subparsers.add_parser(name)
        sub.add_argument("--title", required=True)
        sub.add_argument("--tasklist", default="@default")
        sub.add_argument("--notes", default=None)
        sub.add_argument("--due", default=None)
        sub.add_argument(
            "--origin-channel-id", default="", required=name != "plan",
            help="dori-gate가 확인한 소유자 지시의 Discord 채널 id",
        )
        sub.add_argument(
            "--origin-message-id", default="", required=name != "plan",
            help="dori-gate가 확인한 소유자 지시의 Discord 메시지 id",
        )
        sub.set_defaults(handler=handler)
    listing = subparsers.add_parser("list")
    listing.add_argument("--tasklist", default="@default")
    listing.set_defaults(handler=_cmd_list)
    diagnostic = subparsers.add_parser("runtime-root")
    diagnostic.set_defaults(handler=_cmd_runtime_root)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command in {"create", "write-now"}:
        import importlib

        todo_governed = importlib.import_module("todo_governed")
        message = todo_governed.refusal(Path(__file__))
        if message is not None:
            print(message, file=sys.stderr)
            return 3
    try:
        return int(args.handler(args))
    except EntityClarificationError as error:
        if error.should_render:
            print(error)
        return error.exit_code
    except TodoError as error:
        print(f"TODO-FAIL {error}", file=sys.stderr)
        return error.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
