"""One terminal batch completion notice on the existing approval thread."""
from __future__ import annotations

from pathlib import Path

from automation import owner_notice
from automation.interop import origin_notice, owner_message as om
from automation.interop.origin_notice import deliver
from automation.interop.discord_transport import DiscordTransport as MessageTransport
from automation.interop.reaction_approval import DiscordTransport

from .choice_files import atomic_text
from .choices import Choice, RENDER_VERSION
from .model import RelocationState
from .line_choices import RENDER_VERSION as LINE_VERSION


def notify_completed(state: RelocationState, root: Path, transport: DiscordTransport) -> None:
    """One terminal batch notice; no close while any member is still executing."""
    batches = {r.batch_hash for r in state.relocations.values() if r.render_version in (RENDER_VERSION, LINE_VERSION)}
    for batch in batches:
        records = [r for r in state.relocations.values() if r.batch_hash == batch]
        if any(r.status not in ("reconciled", "abandoned") for r in records):
            continue
        record = records[0]
        if not record.message_id or not record.message_id.isdigit():
            continue
        marker = root / f"{record.message_id}.notified"
        if marker.exists():
            continue
        moved = sum(r.status == "reconciled" and r.choice is Choice.MOVE for r in records)
        deleted = sum(r.status == "reconciled" and r.choice is Choice.DELETE for r in records)
        kept = len(records) - moved - deleted
        location = origin_notice.approval_location({
            "approval_thread_id": record.channel_id, "approval_guild_id": record.approval_guild_id,
            "message_id": record.message_id,
        })
        message = om.OwnerMessage(
            subject_key=batch or "", subject="메모리 정리 결과",
            fact=f"이동 {moved}건 · 양 계정 삭제 {deleted}건 · 유지/취소 {kept}건. 삭제 전 백업은 각 계정에 보관했습니다.",
            location=location, owner=om.Action("none"), agent_next=None,
            recovery=om.Action("reply", location, "백업 복원이 필요하면 요청해 주세요"),
            detail=om.Result("executed"), render_version="owner-ko-v2",
        )
        sent = deliver(
            api=transport.api,
            transport_factory=lambda channel: MessageTransport(token=transport.token, channel_id=channel),
            record={"approval_thread_id": record.channel_id, "approval_guild_id": record.approval_guild_id,
                    "message_id": record.message_id},
            thread_name="메모리 정리", content="", message=message,
            fallback=lambda _body: owner_notice.notify_owner(message=message),
            outcome=None if any(
                r.channel_id == record.channel_id and r.status not in ("reconciled", "abandoned")
                for r in state.relocations.values()
            ) else origin_notice.ThreadOutcome.DONE,
        )
        if sent:
            atomic_text(marker, "delivered")
