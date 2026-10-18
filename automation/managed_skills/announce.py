"""Managed-skill release announcements, posted once into the group's channel.

Targeting comes from the roster (``announce_channel_id``) so a group announces
where its own members read, not where one installation's environment happens to
point. A roster without that field keeps the pre-existing environment behaviour
unchanged.

Duplicate posting is prevented by ``announce_ledger``, which reuses the approval
singularity technique (lease-held critical section, content-bound action hash,
stored message ids that are never replaced).
"""
from __future__ import annotations

import logging
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from automation.managed_skills.announce_ledger import (
    AnnounceLedger,
    AnnounceLedgerError,
    AnnounceRecord,
    announce_action_hash,
    announce_key,
    now,
    state_dir,
)
from automation.managed_skills.manifest import ManagedManifest, manifest_digest


_LOGGER = logging.getLogger(__name__)
_MAX_EXCERPT = 120
_URLISH = re.compile(r"https?://\S+|git@\S+|\b[\w.-]+\.git\b", re.IGNORECASE)
_TOKENISH = re.compile(r"\b(?:sk-[A-Za-z0-9_-]+|ghp_[A-Za-z0-9_-]+)\b")
_PEMISH = re.compile(r"-----BEGIN[^\n]*-----|-----END[^\n]*-----")


class SentRecord(Protocol):
    @property
    def message_id(self) -> str: ...


class AnnounceTransport(Protocol):
    def send(self, body: str) -> Sequence[SentRecord]: ...


class AnnounceOutcome(StrEnum):
    POSTED = "posted"
    NO_CHANNEL = "no-channel"
    ALREADY_ANNOUNCED = "already-announced"
    LEASE_HELD = "lease-held"
    POSTING_JOURNAL_STALE = "posting-journal-stale"
    BINDING_MISMATCH = "binding-mismatch"
    LEDGER_UNREADABLE = "ledger-unreadable"
    SEND_FAILED = "send-failed"


@dataclass(frozen=True, slots=True)
class AnnounceResult:
    sent: bool
    outcome: AnnounceOutcome = AnnounceOutcome.NO_CHANNEL
    message_id: str | None = None
    channel_id: str | None = None


def _excerpt(text: str) -> str:
    cleaned = " ".join(text.split())
    cleaned = _URLISH.sub("[redacted]", cleaned)
    cleaned = _TOKENISH.sub("[redacted]", cleaned)
    cleaned = _PEMISH.sub("[redacted]", cleaned)
    if len(cleaned) <= _MAX_EXCERPT:
        return cleaned
    return f"{cleaned[: _MAX_EXCERPT - 1].rstrip()}…"


def _render(manifest: ManagedManifest, tag: str) -> str:
    breaking = "⚠ BREAKING" if manifest.breaking else "breaking=false"
    excerpt = _excerpt(manifest.changelog) or "(no changelog excerpt)"
    return "\n".join(
        (
            f"skill={manifest.skill}",
            f"tag={tag}",
            f"digest={manifest.skill_sha256[:12]}",
            f"breaking={breaking}",
            f"delta={excerpt}",
            "note=publisher-node canary 진행 중",
        )
    )


def _first_message_id(sent: Sequence[SentRecord]) -> str:
    return sent[0].message_id if sent else "unknown"


def announce_release(
    manifest: ManagedManifest,
    tag: str,
    *,
    transport: AnnounceTransport,
    channel_id: str | None,
    ledger: AnnounceLedger,
) -> AnnounceResult:
    """Post one release announcement at most once, under the ledger's lease."""
    if not channel_id:
        _LOGGER.info("managed release announce skipped: missing channel_id")
        return AnnounceResult(sent=False, outcome=AnnounceOutcome.NO_CHANNEL)
    key = announce_key(manifest.skill, tag)
    action_hash = announce_action_hash(
        manifest_digest=manifest_digest(manifest), tag=tag, channel_id=channel_id
    )
    with ledger.lease.hold(key) as owned:
        if not owned:
            _LOGGER.info("managed release announce deferred: lease held for %s", key)
            return AnnounceResult(sent=False, outcome=AnnounceOutcome.LEASE_HELD)
        return _announce_under_lease(
            manifest,
            tag,
            transport=transport,
            channel_id=channel_id,
            ledger=ledger,
            key=key,
            action_hash=action_hash,
        )


def _announce_under_lease(
    manifest: ManagedManifest,
    tag: str,
    *,
    transport: AnnounceTransport,
    channel_id: str,
    ledger: AnnounceLedger,
    key: str,
    action_hash: str,
) -> AnnounceResult:
    try:
        existing = ledger.read(key)
    except AnnounceLedgerError:
        _LOGGER.exception("managed release announce refused: unreadable ledger")
        return AnnounceResult(sent=False, outcome=AnnounceOutcome.LEDGER_UNREADABLE)
    if existing is not None:
        if existing.action_hash != action_hash:
            _LOGGER.error("managed release announce refused: binding changed for %s", key)
            return AnnounceResult(sent=False, outcome=AnnounceOutcome.BINDING_MISMATCH)
        _LOGGER.info("managed release already announced: %s", key)
        return AnnounceResult(
            sent=False,
            outcome=AnnounceOutcome.ALREADY_ANNOUNCED,
            message_id=existing.message_id,
            channel_id=existing.channel_id,
        )
    if ledger.journal.outstanding(key) is not None:
        _LOGGER.error("managed release announce refused: stale posting journal for %s", key)
        return AnnounceResult(sent=False, outcome=AnnounceOutcome.POSTING_JOURNAL_STALE)
    created_at = now()
    ledger.journal.reserve(key, action_hash, created_at)
    body = _render(manifest, tag)
    try:
        sent = transport.send(body)
    except Exception:
        # The reservation is deliberately LEFT in place: a failed send may still
        # have landed, so a retry could double-post. The next run refuses loudly.
        _LOGGER.exception("managed release announce failed")
        return AnnounceResult(sent=False, outcome=AnnounceOutcome.SEND_FAILED)
    message_id = _first_message_id(sent)
    ledger.commit(
        AnnounceRecord(
            key=key,
            action_hash=action_hash,
            message_id=message_id,
            channel_id=channel_id,
            created_at=created_at,
        )
    )
    ledger.journal.clear(key)
    return AnnounceResult(
        sent=True,
        outcome=AnnounceOutcome.POSTED,
        message_id=message_id,
        channel_id=channel_id,
    )


def resolve_announce_channel_id() -> str | None:
    """Prefer the roster's group channel; fall back to the environment unchanged."""
    from automation.notice_bot import notice_channel

    selected = notice_channel()
    if selected is not None:
        return selected
    try:
        # Lazy: the roster parser needs PyYAML, which must not break config-free surfaces.
        from automation.group_roster.parser import load_roster, roster_path

        roster = load_roster(roster_path())
    except Exception:
        _LOGGER.info("managed release announce: no usable roster, using environment target")
    else:
        if roster.announce_channel_id:
            return roster.announce_channel_id
        _LOGGER.info("managed release announce: roster declares no announce channel")
    return os.environ.get("MANAGED_ANNOUNCE_CHANNEL_ID")


def announce_release_from_environment(manifest: ManagedManifest, tag: str) -> AnnounceResult:
    from automation.notice_bot import resolve_token

    channel_id = resolve_announce_channel_id()
    token = resolve_token(os.environ.get("MANAGED_ANNOUNCE_BOT_TOKEN", ""))
    if not channel_id or not token:
        return AnnounceResult(sent=False)
    try:
        from automation.interop.discord_transport import DiscordTransport

        return announce_release(
            manifest,
            tag,
            transport=DiscordTransport(token, channel_id),
            channel_id=channel_id,
            ledger=AnnounceLedger(root=state_dir()),
        )
    except Exception:
        return AnnounceResult(sent=False)
