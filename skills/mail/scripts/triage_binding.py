"""Lazy mail approval-binding adapter."""
from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Iterable
from pathlib import Path
from types import ModuleType
from typing import Protocol

import triage_confirm
import triage_gate


class ApprovalBindingLike(Protocol):
    """Read-only fields consumed from the lazily loaded, frozen ApprovalBinding."""

    @property
    def surface(self) -> str: ...

    @property
    def channel_id(self) -> str: ...

    @property
    def policy_version(self) -> int: ...

    @property
    def guild_id(self) -> str | None: ...


def repo_root() -> Path:
    """The checkout that actually carries ``automation.interop``.

    See `triage_approval.repo_root` — identical mounted-release depth-guess trap.
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
        raise triage_gate.GateError(
            f"승인 라이프사이클 모듈 불가 (AUTOPHAGY_REPO_ROOT={root}) — 승인 게시 거부", 3
        ) from None


def approval_directory() -> object:
    directory_module = _repo_module("approval_directory")
    try:
        token = triage_confirm.bot_token()
    except triage_gate.GateError:
        token = None
    return directory_module.DiscordChannelDirectory(
        token=token,
        owner_id=triage_confirm.owner_id(),
        api=triage_confirm._api,
    )


def approval_kind(draft: dict) -> object:
    surface_module = _repo_module("approval_surface")
    match draft.get("kind"):
        case "compose":
            return surface_module.ApprovalKind.MAIL_COMPOSE
        case "reply" | None:
            return surface_module.ApprovalKind.MAIL_REPLY
        case unsupported:
            raise triage_gate.GateError(
                f"지원하지 않는 메일 승인 kind: {unsupported!r}", 3,
            )


def draft_kind(draft: dict) -> str:
    return "compose" if draft.get("kind") == "compose" else "reply"


def request_thread(draft: dict) -> object:
    """This draft's own approval-thread spec — title is the mail subject.

    메일 제목은 마스킹하지 않는다: 승인 본문과 결과 통지가 이미 제목·수신자를 담고
    있어 스레드 이름만 가려도 얻는 것이 없다. 원 지시 메시지 쌍은 지시가 승인 채널에서
    시작된 경우에만 스레드 앵커로 쓰인다(해석은 표면 모듈이 한다).
    """
    surface_module = _repo_module("approval_surface")
    return surface_module.RequestThread(
        title=str(draft.get("subject") or ""),
        origin_channel_id=str(draft.get("origin_channel_id") or ""),
        origin_message_id=str(draft.get("origin_message_id") or ""),
    )


def approval_thread_id(binding: ApprovalBindingLike) -> str:
    """The thread a result notice returns to — empty unless the binding IS a thread."""
    surface_module = _repo_module("approval_surface")
    if binding.surface is surface_module.ApprovalSurface.AGENT_CHAT_THREAD:
        return str(binding.channel_id)
    return ""


def persisted_channel_id(draft: dict) -> str | None:
    channel_id = draft.get("channel_id")
    surface = draft.get("surface")
    policy_version = draft.get("policy_version")
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


def _request_binding(
    surface_module: ModuleType,
    kind: object,
    directory: object,
    draft: dict,
    outstanding: Iterable[object],
) -> ApprovalBindingLike:
    """This request's own thread — the one a LIVE request of the same key already opened.

    파사드는 스레드가 열린 뒤에야 PENDING(같은 해시)·supersede(내용 변경)를 판정하므로,
    재요청마다 빈 스레드가 하나씩 남았다. 같은 승인 키의 살아 있는 요청이 이미 연 요청
    스레드를 먼저 재사용하고, 재사용할 것이 없을 때만 새 스레드를 연다. 옛 kind 스레드와
    DM 은 재사용 대상이 아니다(표면 모듈이 판정한다).
    """
    owner_id = triage_confirm.owner_id()
    reused = surface_module.reuse_request_thread(kind, outstanding, directory, owner_id)
    if reused is not None:
        return reused
    import triage_digest_thread  # a reply asked for on a digest card joins that day's thread

    shared = triage_digest_thread.digest_binding(surface_module, kind, directory, draft)
    if shared is not None:
        return shared
    return surface_module.resolve_new_binding(
        kind,
        directory,
        owner_id,
        request=request_thread(draft),
    )


def stored_binding(draft: dict, *, outstanding: Iterable[object] = ()) -> ApprovalBindingLike:
    surface_module = _repo_module("approval_surface")
    kind = approval_kind(draft)
    channel_id = draft.get("channel_id")
    surface = draft.get("surface")
    policy_version = draft.get("policy_version")
    directory = approval_directory()
    try:
        if isinstance(surface, str) or policy_version is not None:
            if (
                not isinstance(surface, str)
                or not isinstance(channel_id, str)
                or not isinstance(policy_version, int)
            ):
                raise triage_gate.GateError("저장된 승인 바인딩이 불완전함 — 승인 거부", 3)
            # Normalize JSON coordinates in the projection only; never infer an absent guild.
            guild_id = draft.get("approval_guild_id")
            binding = surface_module.ApprovalBinding(
                kind,
                surface_module.ApprovalSurface(surface),
                channel_id,
                policy_version,
                None if guild_id is None else str(guild_id),
            )
            return surface_module.validate_stored_binding(
                binding,
                directory,
                triage_confirm.owner_id(),
            )
        if channel_id not in (None, "") or draft.get("kind") is None:
            return surface_module.legacy_binding(
                kind,
                channel_id if isinstance(channel_id, str) else None,
                directory,
                triage_confirm.owner_id(),
            )
        return _request_binding(surface_module, kind, directory, draft, outstanding)
    except (RuntimeError, TypeError, ValueError) as error:
        if isinstance(error, triage_gate.GateError):
            raise
        raise triage_gate.GateError(f"승인 바인딩을 확인할 수 없음 — {error}", 3) from error


def is_retired_binding(draft: dict) -> bool:
    """Whether persisted metadata names a surface no longer used for its kind."""
    surface_module = _repo_module("approval_surface")
    kind = approval_kind(draft)
    raw_surface = draft.get("surface")
    policy_version = draft.get("policy_version")
    if isinstance(raw_surface, str) and type(policy_version) is int:
        surface = surface_module.ApprovalSurface(raw_surface)
        stamped = surface_module.surface_at_policy(kind, policy_version)
        if surface is not stamped:
            raise triage_gate.GateError("저장된 승인 표면과 정책 버전이 모순됨 — 거부", 3)
        return surface is not surface_module.required_surface(kind)
    if raw_surface is not None or policy_version is not None:
        raise triage_gate.GateError("저장된 승인 바인딩이 불완전함 — 승인 거부", 3)
    if draft.get("kind") is None or persisted_channel_id(draft) is not None:
        return surface_module.surface_at_policy(kind, 0) is not surface_module.required_surface(kind)
    return False


def reaction_instruction(draft: dict, *, name_surface: bool = False) -> str:
    surface_module = _repo_module("approval_surface")
    kind = approval_kind(draft)
    raw_surface = draft.get("surface")
    surface = (
        surface_module.ApprovalSurface(raw_surface)
        if isinstance(raw_surface, str)
        else surface_module.required_surface(kind)
    )
    return str(surface_module.reaction_instruction(kind, surface, name_surface=name_surface))
