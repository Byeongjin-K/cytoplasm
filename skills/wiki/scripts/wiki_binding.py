"""Resolve and replay wiki approval bindings through the shared directory."""
from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Protocol

import wiki_gate


class ApprovalBindingLike(Protocol):
    @property
    def kind(self) -> str: ...

    @property
    def surface(self) -> str: ...

    @property
    def channel_id(self) -> str: ...

    @property
    def policy_version(self) -> int: ...

    @property
    def guild_id(self) -> str | None: ...


class OwnerDmDirectory(Protocol):
    def owner_dm(self) -> str: ...


def repo_root() -> Path:
    """The checkout that actually carries ``automation.interop``.

    A mounted release makes ``parents[3]`` point at ``.../releases``. On
    2026-08-18 that made the approval surface refuse its own shared lifecycle.
    """
    override = os.environ.get("AUTOPHAGY_REPO_ROOT")
    if override:
        return Path(override).expanduser()
    here = Path(__file__).resolve()
    candidates = [*here.parents[2:6], Path("/srv/autophagy-agent-current"), Path("/srv/autophagy-agents")]
    for candidate in candidates:
        if (candidate / "automation" / "interop").is_dir():
            return candidate
    current = Path("/srv/autophagy-agent-current")
    return current if (current / "automation").is_dir() else Path("/srv/autophagy-agents")


def _repo_module(name: str) -> ModuleType:
    root = repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        return importlib.import_module(f"automation.interop.{name}")
    except ImportError:
        raise wiki_gate.GateError(
            f"승인 라이프사이클 모듈 불가 (AUTOPHAGY_REPO_ROOT={root}) — 승인 게시 거부",
            3,
        ) from None


def _surface() -> ModuleType:
    return _repo_module("approval_surface")


def _bot_token() -> str | None:
    _repo_module("approval_directory")
    from automation.notice_bot import resolve_token

    token = resolve_token()
    return token or None


def approval_directory() -> OwnerDmDirectory:
    """Build the shared directory bound to this skill's own credential (SI-7)."""
    directory = _repo_module("approval_directory")
    return directory.DiscordChannelDirectory(
        token=_bot_token(),
        owner_id=wiki_gate.owner_id(),
        api=wiki_gate._api,
        cache_path=wiki_gate.GATE_DIR / "channel.json",
    )


def _request_thread(record: Mapping[str, str | int | None], surface: ModuleType) -> object:
    """This request's thread spec — the draft id ONLY, never a note title or body.

    위키 본문·제목은 승인 본문 밖으로 나가면 안 되므로(skills/AGENTS.md) 스레드 이름에
    들어가는 값은 초안 id 하나뿐이다. 지시 메시지 좌표는 있으면 넘겨 앵커로만 쓴다.
    """
    draft_id = record.get("id")
    if not isinstance(draft_id, str) or not draft_id:
        raise wiki_gate.GateError("초안 id 없이 승인 스레드를 열 수 없음 — 게시 거부", 3)
    origin_channel = record.get("origin_channel_id")
    origin_message = record.get("origin_message_id")
    return surface.RequestThread(
        title=draft_id,
        origin_channel_id=origin_channel if isinstance(origin_channel, str) else "",
        origin_message_id=origin_message if isinstance(origin_message, str) else "",
    )


def new_binding(record: Mapping[str, str | int | None]) -> ApprovalBindingLike:
    """Resolve the one binding stamped on every new wiki approval — its own thread."""
    surface = _surface()
    try:
        return surface.resolve_new_binding(
            surface.ApprovalKind.WIKI,
            approval_directory(),
            wiki_gate.owner_id(),
            request=_request_thread(record, surface),
        )
    except surface.ApprovalSurfaceError as error:
        raise wiki_gate.GateError(f"승인 표면 해석 실패 — 게시 거부: {error}", 3) from error


def live_request_binding(record: Mapping[str, str | int | None]) -> ApprovalBindingLike | None:
    """The thread a LIVE request of this same approval key already opened, if any.

    한 승인 키(`wiki:{action}:{slug}`)는 스레드 하나다 — 같은 슬러그를 다시 편집하면
    초안은 새로 생기지만 키는 그대로라, 재요청(PENDING)·대체(supersede)마다 빈 스레드가
    하나씩 남던 것을 없앤다. 살아 있는 요청은 게이트의 ``outstanding`` 과 똑같이 읽는다.
    읽을 수 없는 레코드는 여기서 판정하지 않는다 — 파사드/게이트가 그대로 거부한다.
    """
    approval = importlib.import_module("wiki_approval")  # 지연 임포트: 순환 회피
    surface = _surface()
    try:
        gate = approval.WikiApprovalGate(draft=dict(record))
        outstanding = gate.outstanding(approval.approval_key(dict(record)))
        return surface.reuse_request_thread(
            surface.ApprovalKind.WIKI, outstanding, approval_directory(), wiki_gate.owner_id()
        )
    except RuntimeError:  # GateError·ApprovalRecordsError 포함 — 재사용만 포기한다
        return None


def stored_binding(record: Mapping[str, str | int | None]) -> ApprovalBindingLike:
    """Replay a stored binding or migrate its legacy channel sentinel exactly once."""
    has_bound_message = any(
        isinstance(record.get(name), str) and record[name]
        for name in ("confirm_message_id", "message_id")
    )
    if has_bound_message and persisted_channel_id(record) is None:
        draft_id = record.get("id")
        if isinstance(draft_id, str):
            record = wiki_gate.load_draft(draft_id)

    surface_module = _surface()
    kind = surface_module.ApprovalKind.WIKI
    record_kind = record.get("kind")
    if record_kind is not None and record_kind != kind.value:
        raise wiki_gate.GateError(f"레코드 승인 종류가 wiki가 아님 — 거부: {record_kind!r}", 1)
    channel_id = record.get("channel_id")
    record_surface = record.get("surface")
    version = record.get("policy_version")
    persisted_channel = persisted_channel_id(record)
    if has_bound_message:
        if persisted_channel is None:
            raise wiki_gate.GateError("저장된 승인 바인딩이 불완전함 — 승인 거부", 1)
        try:
            return surface_module.ApprovalBinding(
                kind,
                surface_module.ApprovalSurface(record_surface),
                persisted_channel,
                version,
                record.get("approval_guild_id"),
            )
        except (surface_module.ApprovalSurfaceError, TypeError, ValueError) as error:
            raise wiki_gate.GateError(f"저장된 승인 표면 검증 실패 — 거부: {error}", 1) from error

    if not (isinstance(record_surface, str) and type(version) is int and isinstance(channel_id, str)):
        # 아직 게시된 적 없는 초안: 레코드의 channel_id 는 지시 채널 표식이지 승인 바인딩이
        # 아니다. 2026-09-01 전에는 여기서 legacy_binding(정책 0=DM)으로 빠져 v7 이관이
        # 위키의 실제 게시 경로에는 닿지 않았다 — 이제 같은 키의 살아 있는 요청이 연
        # 스레드를 재사용하고, 없을 때만 이 요청의 스레드를 새로 연다.
        return live_request_binding(record) or new_binding(record)
    try:
        return surface_module.validate_stored_binding(
            surface_module.ApprovalBinding(
                kind,
                surface_module.ApprovalSurface(record_surface),
                channel_id,
                version,
                record.get("approval_guild_id"),
            ),
            approval_directory(),
            wiki_gate.owner_id(),
        )
    except (surface_module.ApprovalSurfaceError, TypeError, ValueError) as error:
        raise wiki_gate.GateError(f"저장된 승인 표면 검증 실패 — 거부: {error}", 1) from error


def bound_message_id(record: Mapping[str, str | int | None]) -> str:
    message_id = record.get("confirm_message_id")
    return message_id if isinstance(message_id, str) else ""


def persisted_channel_id(record: Mapping[str, str | int | None]) -> str | None:
    """Read a complete persisted binding's channel without resolving or fact-checking it."""
    channel_id = record.get("channel_id")
    surface = record.get("surface")
    policy_version = record.get("policy_version")
    if (
        isinstance(channel_id, str)
        and channel_id.isdigit()
        and isinstance(surface, str)
        and surface
        and type(policy_version) is int
        and policy_version >= 0
    ):
        return channel_id
    return None


def reaction_instruction() -> str:
    """Render the shared surface-neutral owner reaction instruction."""
    surface = _surface()
    kind = surface.ApprovalKind.WIKI
    return str(surface.reaction_instruction(kind, surface.required_surface(kind)))
