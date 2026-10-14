"""Typed lazy boundary for the shared approval lifecycle."""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Final, Protocol, TypeGuard

from .model import RelocationRecord

if TYPE_CHECKING:
    from automation.interop.approval_lease import ApprovalLease, PostingJournal
    from automation.interop.approval_lifecycle import (
        ApprovalIntent,
        ApprovalGate,
        ApprovalRequest,
        ApprovalSurfaceError,
        PostedApproval,
        Probe,
        Verdict,
    )


_LIFECYCLE_API: Final = (
    "ApprovalIntent",
    "ApprovalRequest",
    "ApprovalSurfaceError",
    "PostedApproval",
    "Probe",
    "request_owner_approval",
)

class RelocationStoreLike(Protocol):
    """Persistent operations required by the shared approval lifecycle."""

    def pending(self) -> tuple[RelocationRecord, ...]: ...

    def set_message_id(
        self, record: RelocationRecord, message_id: str, channel_id: str
    ) -> None: ...

    def clear_message_id(self, key: str, action_hash: str, message_id: str) -> None: ...

    def clear_batch_message_id(self, batch_hash: str, message_id: str) -> None: ...


class DiscordTransportLike(Protocol):
    """Discord operations injected at the owner-approval boundary."""

    owner_id: str

    def post_message(self, channel_id: str, content: str) -> str: ...

    def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None: ...

    def get_message(self, channel_id: str, message_id: str) -> str | None: ...

    def get_reaction_users(
        self,
        channel_id: str,
        message_id: str,
        emoji: str,
    ) -> tuple[tuple[str, bool], ...]: ...

    def delete_message(self, channel_id: str, message_id: str) -> None: ...


class LifecycleModule(Protocol):
    """Typed view of the lazily imported shared lifecycle module."""

    ApprovalIntent: type[ApprovalIntent]
    ApprovalRequest: type[ApprovalRequest]
    ApprovalSurfaceError: type[ApprovalSurfaceError]
    PostedApproval: type[PostedApproval]
    Probe: type[Probe]

    def request_owner_approval(
        self,
        intent: ApprovalIntent,
        gate: ApprovalGate,
        lease: ApprovalLease,
        journal: PostingJournal,
    ) -> Verdict: ...


def repo_root() -> Path:
    default = Path(__file__).resolve().parents[2]
    return Path(os.environ.get("AUTOPHAGY_REPO_ROOT", str(default))).expanduser()


def _repo_module(name: str) -> ModuleType:
    root = repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return importlib.import_module(f"automation.interop.{name}")


def _is_lifecycle_module(module: ModuleType) -> TypeGuard[LifecycleModule]:
    return all(hasattr(module, name) for name in _LIFECYCLE_API)


def lifecycle() -> LifecycleModule:
    """Load the deployed repository's shared approval lifecycle."""
    module = _repo_module("approval_lifecycle")
    if not _is_lifecycle_module(module):
        raise ModuleNotFoundError("shared approval lifecycle has an invalid API")
    return module

