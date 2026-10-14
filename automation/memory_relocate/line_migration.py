"""Retire only untouched legacy batch approvals under their existing key lease."""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from automation.interop import owner_message as om, origin_notice
from automation.interop.approval_lease import FileKeyLease
from automation.interop.approval_surface import (
    ApprovalBinding, ApprovalKind, ApprovalSurface, validate_stored_binding,
)
from automation.interop.approval_directory import DiscordChannelDirectory
from automation.interop.reaction_approval import DiscordTransport

from .approval_gate import KEYCAPS, approval_key
from .choice_files import atomic_text
from .choices import ROW_REACTIONS, owner_reacted
from .line_choices import RENDER_VERSION, build_lines
from .model import RelocationRecord, RelocationState, record_key, serialize_state
from .render import prepare_batch_card
from .store import load_state, save_state

LEGACY_BATCH_VERSIONS = frozenset({"mc-reloc-batch-render-v1", "mc-reloc-batch-render-v2"})


@dataclass(frozen=True, slots=True)
class Migration:
    state_path: Path
    memory_dir: Path
    transport: DiscordTransport
    directory: DiscordChannelDirectory
    recover: Callable[[Path, RelocationRecord], str | None]

    def run(self, state: RelocationState) -> RelocationState:
        """Archive the old binding before replacing it; keep its Discord message intact."""
        batches = {r.batch_hash for r in state.relocations.values()
                   if r.render_version in LEGACY_BATCH_VERSIONS and r.status in ("posted", "proposed")}
        for batch in batches:
            members = sorted((r for r in state.relocations.values() if r.batch_hash == batch),
                             key=lambda r: r.batch_index or 0)
            if not members or any(r.status not in ("posted", "proposed") for r in members):
                continue
            first = members[0]
            with FileKeyLease(self.state_path.parent / "approval-leases").hold(approval_key(first)) as held:
                if not held:
                    continue
                try:
                    texts = tuple(self.recover(self.memory_dir, r) for r in members)
                    if any(text is None for text in texts):
                        continue
                    pairs = tuple((r, text) for r, text in zip(members, texts, strict=True) if text is not None)
                    lines = build_lines(pairs)
                    if len(lines) != len(members):
                        continue
                    if first.message_id is not None and not self.untouched(pairs):
                        continue
                    if any(r.message_id != first.message_id or r.channel_id != first.channel_id for r in members):
                        continue
                    current = load_state(self.state_path)
                    if any(current.relocations.get(record_key(r.source_kind, r.entry_sha256)) != r for r in members):
                        continue
                    archived = RelocationState(state.version, {
                        record_key(r.source_kind, r.entry_sha256): replace(
                            r, status="abandoned", last_block_reason="superseded-by-per-line",
                        ) for r in members
                    })
                    name = first.message_id or approval_key(first).replace(":", "-")
                    atomic_text(self.state_path.parent / "superseded-batches" / f"{name}.json",
                                json.dumps(serialize_state(archived), ensure_ascii=False, sort_keys=True))
                    records = dict(current.relocations)
                    records.update({record_key(r.source_kind, r.entry_sha256): r for r in lines})
                    state = RelocationState(state.version, records)
                    save_state(self.state_path, state)
                except (OSError, ValueError, RuntimeError):
                    continue  # Unknown is still pending, never grounds for retirement.
        return state

    def untouched(self, pairs: tuple[tuple[RelocationRecord, str], ...]) -> bool:
        """Inspect every emoji, including unrelated reactions, before retiring a batch."""
        record = pairs[0][0]
        _ = validate_stored_binding(
            ApprovalBinding(ApprovalKind.OBSIDIAN_WRITE, ApprovalSurface.AGENT_CHAT_THREAD,
                            record.channel_id, 8),
            self.directory, self.transport.owner_id,
        )
        raw = self.transport.api("GET", f"/channels/{record.channel_id}/messages/{record.message_id}")
        match raw:
            case {"id": str() as message, "channel_id": str() as channel,
                  "content": str() as content, "edited_timestamp": None,
                  "author": {"bot": True}}:
                if (message, channel, content) != (
                    record.message_id, record.channel_id, prepare_batch_card(pairs)[1],
                ):
                    return False
            case _:
                return False
        match raw.get("reactions", []):
            case list() as reactions:
                pass
            case _:
                return False
        # Read the finite legacy choices even when reaction summary metadata is absent.
        # Only complete per-emoji owner reads, never a missing summary, prove no decision.
        emojis = {"✅", "⛔"}
        if record.render_version == "mc-reloc-batch-render-v2":
            emojis.update(emoji for row in ROW_REACTIONS[:len(pairs)] for emoji in row)
        else:
            emojis.update(KEYCAPS[:len(pairs)])
        for reaction in reactions:
            match reaction:
                case {"emoji": {"id": None, "name": str() as emoji}}:
                    pass
                case {"emoji": {"id": str() as identity, "name": str() as name}}:
                    emoji = f"{name}:{identity}"
                case _:
                    return False
            emojis.add(emoji)
        for emoji in sorted(emojis):
            users = self.transport.get_reaction_users(record.channel_id, record.message_id or "", emoji)
            if owner_reacted(users, self.transport.owner_id):
                return False
        # A reaction can change while reading users; changed metadata requires a later tick.
        return raw == self.transport.api("GET", f"/channels/{record.channel_id}/messages/{record.message_id}")

    def notify_reposted(self, state: RelocationState) -> None:
        """One at-most-once result per migrated card, after every line has a new binding."""
        root = self.state_path.parent / "superseded-batches"
        for path in root.glob("*.json"):
            archived = load_state(path)
            first = next(iter(archived.relocations.values()))
            marker = path.with_suffix(".notified")
            if not first.message_id or marker.exists():
                continue
            current = [state.relocations.get(key) for key in archived.relocations]
            if any(r is None or r.render_version != RENDER_VERSION or not r.message_id for r in current):
                continue
            here = om.Ref(scope="self")
            message = om.OwnerMessage(
                subject_key=first.batch_hash or "", subject="메모리 정리",
                fact="기존 묶음은 종료하고 각 줄을 개별 메시지로 다시 올렸습니다.",
                location=here, owner=om.Action("none"), agent_next=None,
                recovery="not_applicable", detail=om.Result("executed"), render_version="owner-ko-v4",
            )
            atomic_text(marker, "claimed")  # Ambiguous network failures must not duplicate this notice.
            _ = origin_notice.deliver(
                api=self.transport.api,
                transport_factory=lambda channel: _NoticeTransport(self.transport, channel),
                record={"approval_thread_id": first.channel_id},
                thread_name="메모리 정리", content="", message=message,
                fallback=lambda _body: False,
            )


@dataclass(frozen=True, slots=True)
class _NoticeTransport:
    transport: DiscordTransport
    channel: str

    def send(self, content: str) -> tuple[_NoticeReceipt, ...]:
        return (_NoticeReceipt(self.transport.post_message(self.channel, content)),)


@dataclass(frozen=True, slots=True)
class _NoticeReceipt:
    message_id: str
