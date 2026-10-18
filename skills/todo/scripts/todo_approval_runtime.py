"""Runtime dependency assembly for the todo approval adapter."""
from __future__ import annotations

import importlib
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

from todo_approval import (
    ApprovalRuntime,
    TodoApprovalError,
    TodoApprovalIntent,
    _repo_module,
    lifecycle,
    prepare_request_card,
    request_approval,
    surface_module,
)
from todo_approval_store import TodoApprovalStore, TodoApprovalStoreError, approval_ttl

if TYPE_CHECKING:
    from todo_approval_model import TodoApprovalRecord
    from automation.interop.approval_lifecycle import Verdict
    from automation.interop.approval_surface import ApprovalBinding


def _live_request_binding(
    surface: ModuleType,
    store: TodoApprovalStore,
    key: str,
    directory: object,
    owner: str,
) -> ApprovalBinding | None:
    """The thread a LIVE request of this same key already opened, if any.

    한 승인 키는 스레드 하나다 — `todo:<argv hash>` 는 같은 argv 재요청마다 같은 키라
    그때마다 빈 스레드가 하나씩 생기고, 저장된 바인딩과 채널이 달라져 재요청 자체가
    거부됐다. 읽을 수 없는 저장소는 여기서 판정하지 않는다 — 파사드가 store-unreadable
    로 거부한다.
    """
    try:
        live = store.outstanding(key)
    except TodoApprovalStoreError:
        return None
    return surface.reuse_request_thread(surface.ApprovalKind.TODO, live, directory, owner)


def request_cli_approval(intent: TodoApprovalIntent, owner: str) -> Verdict:
    _repo_module("approval_directory")
    from automation.notice_bot import resolve_token

    token = resolve_token()
    if not token or not owner:
        raise TodoApprovalError("todo approval identity is unavailable")
    root = Path(os.environ.get("TODO_APPROVAL_ROOT", "~/.hermes/todo-approvals")).expanduser()
    transport = importlib.import_module("todo_discord").TodoDiscordTransport(token)
    directory_module = _repo_module("approval_directory")
    directory = directory_module.DiscordChannelDirectory(
        token,
        owner,
        transport.api,
        root / "approval-directory.json",
    )
    surface = surface_module()
    # 요청 하나가 스레드 하나를 연다: 승인 카드·리마인더·결과가 한 스레드에서 끝나도록
    # 이 할 일의 제목(노트 제외)과 지시 메시지를 정책에 넘긴다.
    request = surface.RequestThread(
        title=intent.title,
        origin_channel_id=intent.origin_channel_id,
        origin_message_id=intent.origin_message_id,
    )
    store = TodoApprovalStore(root)
    now = datetime.now(UTC)
    try:
        card = prepare_request_card(intent, store, now)
    except TodoApprovalStoreError as error:
        raise TodoApprovalError("todo approval store is unreadable") from error
    # 파사드가 PENDING/supersede 를 판정하기 전에 스레드가 열리므로, 같은 키의 살아 있는
    # 요청이 이미 연 스레드를 먼저 재사용한다.
    binding = _live_request_binding(
        surface, store, intent.key, directory, owner
    ) or surface.resolve_new_binding(
        surface.ApprovalKind.TODO, directory, owner, request=request
    )
    lease_module = _repo_module("approval_lease")
    runtime = ApprovalRuntime(
        store,
        transport,
        directory,
        owner,
        binding,
        lease_module.FileKeyLease(root / "approval-leases"),
        lease_module.PostingJournal(root / "posting-journal"),
        lambda: now,
        render_version=None if card is None else card[0],
        card=card,
    )
    verdict = request_approval(intent, runtime)
    if verdict.outcome not in {lifecycle().Outcome.POSTED, lifecycle().Outcome.PENDING}:
        reason = "unknown" if verdict.reason is None else verdict.reason.value
        raise TodoApprovalError(f"todo approval request was not posted: {reason}")
    return verdict


def origin_record(action_hash: str) -> dict[str, str] | None:
    """Routing facts of the latest approval generation for this exact argv hash."""
    root = Path(os.environ.get("TODO_APPROVAL_ROOT", "~/.hermes/todo-approvals")).expanduser()
    store = TodoApprovalStore(root)
    key = f"todo:{action_hash}"
    candidates = [record for record in (store.active(key), *store.archives(key)) if record is not None]
    if not candidates:
        return None
    latest = max(candidates, key=lambda record: record.generation)
    return {
        "id": action_hash[:19],
        "channel_id": latest.channel_id,
        "origin_channel_id": latest.origin_channel_id,
        "origin_message_id": latest.origin_message_id,
        "approval_thread_id": latest.approval_thread_id,
        "approval_guild_id": latest.approval_guild_id or "",
        "message_id": latest.message_id or "",
        "title": latest.title,
    }


#: 종결 결과의 상태어 이름 — 공유 `origin_notice.ThreadOutcome` 멤버를 가리킨다.
OUTCOME_DONE = "DONE"
OUTCOME_CANCELLED = "CANCELLED"
OUTCOME_EXPIRED = "EXPIRED"


def notify_expired(record: TodoApprovalRecord) -> object:
    """만료 결과도 요청 스레드에 남겨 열린 스레드가 pending 요청만 뜻하게 한다."""
    return notify_result(
        {
            "id": record.action_hash[:19],
            "channel_id": record.channel_id,
            "origin_channel_id": record.origin_channel_id,
            "origin_message_id": record.origin_message_id,
            "approval_thread_id": record.approval_thread_id,
            "approval_guild_id": record.approval_guild_id or "",
            "message_id": record.message_id or "",
            "title": record.title,
        },
        f"⌛ 할일 등록 승인 만료: {record.title} (key {record.key})\\n"
        f"승인 TTL {int(approval_ttl().total_seconds())}초가 지나 Google Tasks에 등록되지 않았습니다.",
        thread_name="할일 등록",
        outcome=OUTCOME_EXPIRED,
    )


def _thread_outcome(origin_notice: object, outcome: str) -> object | None:
    """The shared terminal marker, or None when the runtime predates it (best-effort)."""
    if not outcome:
        return None
    return getattr(getattr(origin_notice, "ThreadOutcome", None), outcome, None)


def notify_result(
    record_like: dict,
    content: str,
    *,
    thread_name: str,
    transport: object | None = None,
    transport_factory: object | None = None,
    outcome: str = "",
) -> object:
    """Result notice: the request's approval thread first, the stored channel as fallback.

    라우팅·폴백·NOTIFY-THREAD-FAIL 의미는 공유 `automation.interop.origin_notice.deliver`가
    소유한다(2026-08-23 전 스킬 공통화). 레코드의 `approval_thread_id` 가 있으면 스레드를
    새로 열지 않고 거기에 게시하며, 종결 결과면 그 스레드를 상태어로 닫는다. 폴백 목적지는
    새로 해석하지 않고 레코드에 저장된 승인 채널을 그대로 쓴다(워처의 stored-binding 규약).
    """
    from automation.notice_bot import resolve_token

    token = resolve_token()
    if transport is None:
        if not token:
            raise TodoApprovalError("todo notice identity is unavailable")
        transport = importlib.import_module("todo_discord").TodoDiscordTransport(token)
    if transport_factory is None:
        discord_transport = _repo_module("discord_transport")

        def transport_factory(channel_id: str):
            return discord_transport.DiscordTransport(token=token, channel_id=channel_id)

    fallback_channel = str(record_like.get("channel_id") or "")
    if not fallback_channel:
        raise TodoApprovalError("todo notice has no stored channel to fall back to")
    try:
        origin_notice = _repo_module("origin_notice")
    except (ImportError, TodoApprovalError) as error:  # 낡은 interop 런타임/샌드박스
        print(
            f"NOTIFY-HELPER-MISSING id={record_like.get('id', '')} err={type(error).__name__}",
            file=sys.stderr,
        )
        return transport.post_message(fallback_channel, content)  # type: ignore[attr-defined]
    from todo_result_message import result_message

    marker = _thread_outcome(origin_notice, outcome)
    delivery = dict(
        api=transport.api,  # type: ignore[attr-defined]
        transport_factory=transport_factory,  # type: ignore[arg-type]
        record=record_like,
        thread_name=thread_name,
        fallback=lambda body: transport.post_message(fallback_channel, body),  # type: ignore[attr-defined]
        **({} if marker is None else {"outcome": marker}),
    )
    message = result_message(record_like, content, outcome)
    if message is not None and getattr(origin_notice, "ACCEPTS_OWNER_MESSAGE", False):
        return origin_notice.deliver(
            **delivery, content=content, message=message,
            fallback_destination=(message.location
                                  if fallback_channel == record_like.get("approval_thread_id") else None),
        )
    return origin_notice.deliver(**delivery, content=content)
