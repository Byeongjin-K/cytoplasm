"""Where a skill supply-chain approval lives: one declared kind, one shared directory.

SI-6  ``skill-deploy``, ``skill-attest``, ``skill-publish``, ``skill-submit`` and ``managed-activate``
      resolve to the guild approval surface at EVERY policy version. A SECOND bot —
      the peer — posts its independent attestation beside the deploy request, and a
      direct-message channel between the owner and one bot cannot carry another
      bot's message. So this flow declares its surface rather than being swept along
      by a later flip; ``tests/unit/test_skill_gate.py`` pins that forever.

The gate stops resolving anything itself: it names its :class:`ApprovalKind` and asks
:mod:`automation.interop.approval_directory`, which owns every guild scan, cache and
config read. The operator's ``deploy_approvals_channel_id`` pin survives (AS-3.2 keeps
it) as an explicitly supplied channel the directory still verifies before use.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol, TypeAlias

from automation.interop.approval_directory import DiscordApi, DiscordChannelDirectory
from automation.interop.approval_surface import (
    POLICY_VERSION,
    ApprovalBinding,
    ApprovalKind,
    ApprovalSurface,
    ApprovalSurfaceError,
    ChannelDirectory,
    legacy_binding,
    reaction_instruction,
    required_surface,
    resolve_new_binding,
    validate_stored_binding,
)

SUPPLY_CHAIN_KINDS: Final[tuple[ApprovalKind, ...]] = (
    ApprovalKind.SKILL_DEPLOY,
    ApprovalKind.SKILL_ATTEST,
    ApprovalKind.SKILL_PUBLISH,
    ApprovalKind.SKILL_SUBMIT,
    ApprovalKind.MANAGED_ACTIVATE,
    ApprovalKind.RELEASE,
)

CACHE_NAME: Final = "config.json"
MANAGED_PREFIX: Final = "managed-"
_PINNED_CHANNEL_KEY: Final = "deploy_approvals_channel_id"
_OWNER_KEY: Final = "owner_id"
JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


class _JsonLoader(Protocol):
    def __call__(self, raw: str, /) -> JsonValue: ...


_JSON_LOADS: _JsonLoader = json.loads


def deploy_kind(skill: str) -> ApprovalKind:
    """``managed-`` is a reserved prefix, so the name alone decides what ✅ authorizes."""
    if skill.startswith(MANAGED_PREFIX):
        return ApprovalKind.MANAGED_ACTIVATE
    return ApprovalKind.SKILL_DEPLOY


def where_to_look(kind: ApprovalKind) -> str:
    """The one owner-facing line allowed to NAME this flow's surface (decision 6)."""
    return reaction_instruction(kind, required_surface(kind), name_surface=True)


def _config_value(config: Path, key: str) -> str | None:
    try:
        decoded = _JSON_LOADS(config.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(decoded, dict):
        return None
    found = decoded.get(key)
    return found if isinstance(found, str) and found else None


def pinned_channel_id(config: Path) -> str | None:
    """The operator's ``deploy_approvals_channel_id`` pin — supplied, never trusted blindly."""
    from automation.notice_bot import notice_channel

    return (_config_value(config, _PINNED_CHANNEL_KEY)
            or (_config_value(config, "personal_approvals_channel_id") if notice_channel() else None))


def owner_id(config: Path) -> str:
    """The owner whose ✅ counts; an unreadable config refuses rather than guesses."""
    found = _config_value(config, _OWNER_KEY)
    if found is None:
        raise ApprovalSurfaceError(f"owner_id missing from interop config: {config}")
    return found


class ApprovalBindings(Protocol):
    """What one gate needs of its surface: a fresh binding, or a record's own."""

    @property
    def kind(self) -> ApprovalKind: ...

    def new(self) -> ApprovalBinding: ...

    def stored(self, record: Mapping[str, str]) -> ApprovalBinding: ...


@dataclass(frozen=True, slots=True)
class GateIdentity:
    """One gate process's identity: its bot token, its API funnel, its runtime paths."""

    token: str
    api: DiscordApi
    gate_dir: Path
    interop_config: Path

    def directory(self) -> DiscordChannelDirectory:
        """This bot's view of the shared directory — the ONLY resolver of a surface."""
        return DiscordChannelDirectory(
            token=self.token,
            owner_id=owner_id(self.interop_config),
            api=self.api,
            cache_path=self.gate_dir / CACHE_NAME,
        )


@dataclass(frozen=True, slots=True)
class SupplyChainSurface:
    """The declared approval surface for ONE supply-chain kind — never a DM (SI-6)."""

    kind: ApprovalKind
    owner: str
    directory: ChannelDirectory
    pinned: str | None = None

    def new(self) -> ApprovalBinding:
        """Decide, once, where a brand-new supply-chain approval must be posted."""
        from automation.notice_bot import notice_channel

        return resolve_new_binding(self.kind, self.directory, self.owner, notice_channel() or self.pinned)

    def stored(self, record: Mapping[str, str]) -> ApprovalBinding:
        """Replay a record's own binding; a pre-schema record drains through the migrator."""
        from automation.notice_bot import notice_channel

        stamped, channel_id = record.get("policy_version", ""), record.get("channel_id", "")
        selected = notice_channel()
        pinned = selected if selected is not None and channel_id == selected else self.pinned
        surface, kind = record.get("surface", ""), record.get("kind", "")
        if not stamped.isdigit() or not channel_id or not surface:
            return legacy_binding(
                self.kind, channel_id or None, self.directory, self.owner,
                skill_channel_id=pinned,
            )
        if kind != self.kind.value:
            raise ApprovalSurfaceError(f"stored kind {kind!r} contradicts {self.kind.value!r}")
        try:
            binding = ApprovalBinding(self.kind, ApprovalSurface(surface), channel_id, int(stamped))
        except ValueError as error:
            raise ApprovalSurfaceError(f"stored surface is unknown: {surface!r}") from error
        return validate_stored_binding(
            binding, self.directory, self.owner, skill_channel_id=pinned,
        )


def surface_for(kind: ApprovalKind, identity: GateIdentity) -> SupplyChainSurface:
    """Bind one declared kind to this process's directory and the operator's channel pin."""
    config = identity.interop_config
    return SupplyChainSurface(kind, owner_id(config), identity.directory(), pinned_channel_id(config))


def binding_of_post(kind: ApprovalKind, channel_id: str) -> ApprovalBinding:
    """The binding a just-posted approval carries — pure policy, stamped at this version."""
    return ApprovalBinding(kind, required_surface(kind), channel_id, POLICY_VERSION)
