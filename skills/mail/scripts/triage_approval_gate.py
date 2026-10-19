"""Mail lifecycle adapter, retaining facade injection seams."""
from __future__ import annotations
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.error import HTTPError
from urllib.request import Request
from importlib import import_module
import triage_confirm
import triage_binding
import triage_gate
import mail_approval_attachment
import mail_card_compact
if TYPE_CHECKING:
    from automation.entity_preflight.contracts import JsonValue
    from automation.interop.approval_lifecycle import ApprovalIntent, ApprovalRequest, PostedApproval, Probe

def refuse_unpostable_content(content: str) -> None:
    """Refuse exact post bytes BEFORE the journal reserves its key (t_82644d12).

    Existing bindings are resolved first without rendering; only a new post reaches
    this check, through the lifecycle's preparation callback under the key lease.
    """
    limit = import_module("triage_approval")._MESSAGE_LIMIT
    size = len(content)
    if size > limit:
        raise triage_gate.GateError(
            f"승인 메시지가 Discord 한도를 넘음({size}/{limit}자) — 게시하지 않음. "
            "본문을 줄여 다시 시도하거나 첨부 방식 승인이 필요하다.",
            3,
        )


@dataclass(frozen=True, slots=True)
class MailApprovalGate:
    """``approval_lifecycle.ApprovalGate`` over the triage draft store + Discord REST."""

    draft: dict[str, JsonValue]
    notice: str = ""
    content: str = ""

    def outstanding(self, key: str) -> tuple[ApprovalRequest, ...]:
        approval = import_module("triage_approval")
        request_type = approval.lifecycle().ApprovalRequest
        return tuple(
            request_type(
                key=key,
                action_hash=approval._approval_action_hash(record),
                message_id=approval._bound_message_id(record),
                channel_id=approval._request_channel_id(record),
                created_at=str(record.get("approval_created_at", record["created"])),
            )
            for _, record, record_key in approval._pending_drafts()
            if record_key == key and approval._bound_message_id(record)
        )

    def probe(self, request: ApprovalRequest) -> Probe:
        approval = import_module("triage_approval")
        state = approval.lifecycle().Probe
        content = self._content(request)
        if content is None:
            return state.MISSING
        if not mail_card_compact.references(self._record(request), content, request.action_hash):
            return state.BINDING_MISMATCH
        channel, message = request.channel_id, request.message_id
        try:
            owner = triage_confirm.owner_id()
            cancel = triage_confirm._reaction_users(channel, message, triage_confirm.CANCEL_EMOJI)
            approve = triage_confirm._reaction_users(channel, message, triage_confirm.APPROVE_EMOJI)
        except approval._TRANSPORT_ERRORS as error:
            raise approval.lifecycle().ApprovalSurfaceError(str(error)) from error
        if triage_confirm._owner_reacted(cancel, owner):
            return state.CANCELLED
        if triage_confirm._owner_reacted(approve, owner):
            return state.APPROVED
        return state.BOUND_PENDING

    def _record(self, request: ApprovalRequest) -> dict[str, JsonValue]:
        approval = import_module("triage_approval")
        if self.draft.get("message_id") == request.message_id:
            return self.draft
        return next((item for _, item, key in approval._pending_drafts()
                     if key == request.key and item.get("message_id") == request.message_id), self.draft)

    def _content(self, request: ApprovalRequest) -> str | None:
        approval = import_module("triage_approval")
        try:
            message = triage_confirm._api(
                "GET", f"/channels/{request.channel_id}/messages/{request.message_id}"
            )
        except HTTPError as error:
            if error.code == 404:
                return None
            raise approval.lifecycle().ApprovalSurfaceError(str(error)) from error
        except approval._TRANSPORT_ERRORS as error:
            raise approval.lifecycle().ApprovalSurfaceError(str(error)) from error
        if not isinstance(message, dict):
            raise approval.lifecycle().ApprovalSurfaceError("승인 메시지 응답이 유효하지 않음")
        if not mail_approval_attachment.matches(self._record(request), message):
            return ""
        return str(message.get("content", "")) or None

    def delete(self, request: ApprovalRequest) -> None:
        """Remove the superseded approval message before its record may be unbound."""
        approval = import_module("triage_approval")
        try:
            triage_confirm.delete_message(request.message_id, request.channel_id)
        except HTTPError as error:
            if error.code != 404:
                raise approval.lifecycle().ApprovalSurfaceError(str(error)) from error
        except approval._TRANSPORT_ERRORS as error:
            raise approval.lifecycle().ApprovalSurfaceError(str(error)) from error

    def drop(self, request: ApprovalRequest) -> None:
        """Compare-and-swap: unbind the record ONLY while it still holds this message."""
        approval = import_module("triage_approval")
        bound = (request.action_hash, request.message_id)
        for path, record, key in approval._pending_drafts():
            if key != request.key or (approval._approval_action_hash(record), approval._bound_message_id(record)) != bound:
                continue
            unbound = {**record, "message_id": ""}
            if str(record["id"]) != str(self.draft["id"]):
                # 이 키의 최신 내용에 밀려난 형제 초안 — pending으로 두면 다음 tick이
                # 되받아 게시해 핑퐁이 된다.
                unbound["status"] = approval.SUPERSEDED_STATUS
            triage_gate.write_json(path, unbound)
            return

    def post(self, intent: ApprovalIntent) -> PostedApproval:
        approval = import_module("triage_approval")
        content = self.content or approval._approval_content(self.draft, self.notice)
        if self.draft.get("approval_format") == mail_approval_attachment.FORMAT:
            request = mail_approval_attachment.upload_request(content, Request(
                f"{triage_confirm.API}/channels/{intent.channel_id}/messages",
                headers={"Authorization": f"Bot {triage_confirm.bot_token()}",
                         "User-Agent": triage_confirm.USER_AGENT},
            ), mail_approval_attachment.body_attachment(self.draft))
            match triage_confirm._send(request):
                case {"id": str(message_id)} if message_id:
                    pass
                case _:
                    raise triage_gate.GateError("승인 첨부 게시 응답에 메시지 id 없음 — 거부", 3)
        else:
            card = _digest_card(self.draft, intent.channel_id)
            reply = {"reply_to": card} if card else {}
            message_id = triage_confirm.post_approval_request(content, intent.channel_id, **reply)
        for emoji in (triage_confirm.APPROVE_EMOJI, triage_confirm.CANCEL_EMOJI):
            try:
                triage_confirm.add_reaction(message_id, emoji, intent.channel_id)
            except HTTPError as error:
                reason = getattr(error, "code", None) or str(error)
                print(
                    f"APPROVAL-REACTION-FAIL {emoji} message={message_id} "
                    f"reason={type(error).__name__}:{reason}",
                    file=sys.stderr,
                )
        return approval.lifecycle().PostedApproval(message_id=message_id, channel_id=intent.channel_id)

    def commit(self, intent: ApprovalIntent, posted: PostedApproval, created_at: str) -> None:
        """The ONLY writer of the Discord ``message_id`` and its timer anchor."""
        triage_gate.set_message_id(
            self.draft,
            posted.message_id,
            intent.channel_id,
            approval_created_at=created_at,
        )


def _digest_card(draft: dict[str, JsonValue], channel_id: str) -> str:
    """The digest mail card to reply under; '' outside a digest thread or on any doubt."""
    try:
        import triage_digest_thread

        return triage_digest_thread.card_for(draft, channel_id)
    except Exception as error:  # noqa: BLE001 — a reply reference never blocks the card
        print(f"DIGEST-CARD-REF-FAIL draft={draft.get('id')} err={type(error).__name__}", file=sys.stderr)
        return ""


def prepare_request(draft: dict[str, JsonValue], notice: str) -> tuple[ApprovalIntent, MailApprovalGate]:
    """Use attachment-v1 bytes with the new card layout only when inline is too long."""
    approval = import_module("triage_approval")
    cards = approval._repo_module("approval_card")
    version = draft.get("render_version", "1" if draft.get("message_id") else None)
    selected_version = version or mail_card_compact.VERSION
    prepared = dict(draft)
    try:
        try:
            card = cards.prepare(
                lambda selected: approval._approval_content({**draft, "render_version": selected}, notice),
                selected_version,
            )
        except triage_gate.GateError:
            if version is not None:
                raise
            metadata = mail_approval_attachment.body_attachment(draft).metadata
            prepared.update(approval_format=mail_approval_attachment.FORMAT, approval_attachment={
                "filename": metadata["filename"], "size": metadata["size"], "sha256": metadata["sha256"],
            })
            card = cards.prepare(
                lambda selected: approval._approval_content({**prepared, "render_version": selected}, notice),
                selected_version,
            )
    except cards.CardRenderError as error:
        raise triage_gate.GateError(str(error), 3) from error
    prepared["render_version"] = card.render_version
    if card.render_version in mail_card_compact.DIGEST_VERSIONS:
        prepared["card_sha256"] = mail_card_compact.digest(card.content)
    return approval.confirm_intent(prepared), MailApprovalGate(prepared, notice, card.content)


def expire_retired_approval(draft: dict[str, JsonValue]) -> bool:
    """Expire an undecided request whose persisted surface has been retired.

    The approval-key lease closes the reaction/delete/store race. An owner decision
    always wins: decided requests return to the normal resolver and are never expired.
    """
    approval = import_module("triage_approval")
    if not triage_binding.is_retired_binding(draft):
        return False
    created_at = draft.get("created")
    if not isinstance(created_at, str) or not created_at:
        return False
    key = approval.approval_key(draft)
    with approval.confirm_lease().hold(key) as owned:
        if not owned:
            raise triage_gate.GateError("승인 처리 lease 사용 중 — 다음 tick 재시도", 1)
        message_id = approval._bound_message_id(draft)
        if not message_id:
            triage_gate.expire_draft(str(draft["id"]), "", "approval-surface-retired")
            return True
        channel_id = triage_binding.persisted_channel_id(draft)
        if channel_id is None:
            channel_id = str(approval.stored_binding(draft).channel_id)
        request = approval.lifecycle().ApprovalRequest(
            key=key,
            action_hash=approval._approval_action_hash(draft),
            message_id=message_id,
            channel_id=channel_id,
            created_at=str(draft.get("approval_created_at", created_at)),
        )
        gate = MailApprovalGate(draft)
        probe = gate.probe(request)
        state = approval.lifecycle().Probe
        if probe in (state.APPROVED, state.CANCELLED):
            return False
        if probe is state.BOUND_PENDING:
            gate.delete(request)
        elif probe is not state.MISSING:
            raise triage_gate.GateError(
                f"폐지 승인 표면의 바인딩 검증 실패 ({probe.value}) — 만료 거부", 3,
            )
        triage_gate.expire_draft(str(draft["id"]), message_id, "approval-surface-retired")
        return True
