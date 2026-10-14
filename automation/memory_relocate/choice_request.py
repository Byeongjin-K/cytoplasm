"""Parse an untrusted mailbox manifest into a fully re-derived approval request."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from automation.memory_curator.binding import entry_digest

from .binding import RelocationHashFields, relocation_action_hash
from .choices import MAX_ROWS, RENDER_VERSION, ChoiceError, batch_choices_hash
from . import choice_json
from .model import RelocationRecord, RelocationState, parse_state, record_key, serialize_state
from .line_choices import RENDER_VERSION as LINE_VERSION, line_hash
from .plan import build_relocation_plan
from .render import prepare_approval_card, prepare_batch_card


@dataclass(frozen=True, slots=True)
class ChoiceRequest:
    members: tuple[tuple[RelocationRecord, str], ...]

    @property
    def record(self) -> RelocationRecord:
        return self.members[0][0]

    @property
    def key(self) -> str:
        record = self.record
        return hashlib.sha256(
            f"{record.channel_id}\0{record.message_id}\0{record.batch_hash}".encode()
        ).hexdigest()

    @property
    def content(self) -> str:
        if self.record.render_version == LINE_VERSION:
            return prepare_approval_card(self.record, self.members[0][1])[1]
        return prepare_batch_card(self.members)[1]

    def index(self, record: RelocationRecord) -> int:
        for number, (member, _) in enumerate(self.members):
            if (member.source_kind, member.entry_sha256, member.action_hash, member.batch_index,
                member.destination, member.note_relpath, member.note_plan_sha256) == (
                record.source_kind, record.entry_sha256, record.action_hash, record.batch_index,
                record.destination, record.note_relpath, record.note_plan_sha256,
            ):
                return number
        raise ChoiceError("member_binding")


def encode_request(request: ChoiceRequest) -> str:
    records = {record_key(r.source_kind, r.entry_sha256): r for r, _ in request.members}
    return json.dumps({
        "state": serialize_state(RelocationState(1, records)),
        "texts": [text for _, text in request.members],
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def parse_request(document: str) -> ChoiceRequest:
    """Never let a caller supply a delete path, selected choice or an unchecked hash."""
    payload = choice_json.mapping(choice_json.decode(document))
    texts = choice_json.sequence(payload.get("texts"))
    if not 1 <= len(texts) <= MAX_ROWS:
        raise ChoiceError("request_shape")
    records = sorted(parse_state(payload.get("state")).relocations.values(), key=lambda r: r.batch_index or 0)
    if len(records) != len(texts):
        raise ChoiceError("member_count")
    members: list[tuple[RelocationRecord, str]] = []
    for number, (record, raw_text) in enumerate(zip(records, texts, strict=True), start=1):
        text = choice_json.text(raw_text)
        if not text or len(text) > 1900:
            raise ChoiceError("entry_text")
        if any(previous == text for _, previous in members):
            raise ChoiceError("duplicate_entry_text")
        if record.render_version not in (RENDER_VERSION, LINE_VERSION) or record.batch_index != number:
            raise ChoiceError("member_version_or_order")
        if record.render_version == LINE_VERSION and len(records) != 1:
            raise ChoiceError("line_member_count")
        if record.entry_sha256 != entry_digest(record.source_kind, text):
            raise ChoiceError("entry_digest")
        plan = build_relocation_plan(text, source_kind=record.source_kind, destination=record.destination)
        fields = RelocationHashFields(
            record.source_kind, record.entry_sha256, plan.note_plan.relpath.as_posix(),
            plan.note_plan_sha256, record.destination,
        )
        if (record.note_relpath, record.note_plan_sha256, record.action_hash) != (
            fields.note_relpath, fields.note_plan_sha256, relocation_action_hash(fields),
        ):
            raise ChoiceError("member_hash")
        if record.choice is not None or record.status != "posted":
            raise ChoiceError("request_already_decided")
        members.append((record, text))
    first = records[0]
    batch = line_hash(first) if first.render_version == LINE_VERSION else batch_choices_hash([r.action_hash for r, _ in members])
    if not first.message_id or not first.message_id.isascii() or not first.message_id.isdigit():
        raise ChoiceError("message_id")
    if not first.channel_id.isascii() or not first.channel_id.isdigit():
        raise ChoiceError("channel_id")
    if any(
        (r.batch_hash, r.channel_id, r.message_id, r.approval_thread_id) != (
            batch, first.channel_id, first.message_id, first.channel_id,
        ) for r in records
    ):
        raise ChoiceError("batch_binding")
    request = ChoiceRequest(tuple(members))
    _ = request.content  # Length and owner-envelope contract before any network/effect.
    return request
