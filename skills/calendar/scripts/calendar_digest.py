"""다이제스트가 감지한 일정을 하루 한 승인 스레드에 카드 하나씩 묶어 게시한다.

메일 다이제스트는 초안만 만들고 끝났고(2026-09 실측: 승인 카드 없이 24시간 뒤 고아 폐기),
소유자 결정(t_bacebc3a): 게시일마다 승인 스레드 하나, 그 안에 일정마다 ✅/⛔ 카드 하나,
같은 일정의 후속 메일은 미결 카드를 최신 내용으로 교체, 이미 결정한 일정은 다시 묻지
않는다. 승인 기계장치는 새로 만들지 않는다 — 초안 id 를 일정 동일성 키로 고정하고 공유
라이프사이클(`calendar_approval.request_confirmation`)에 그대로 맡기면 교체(supersede)·
멱등 재게시·결정 보존이 그 규칙대로 따라온다.

상태는 전부 `CALENDAR_GATE_DIR` 아래다(체크아웃 밖): 일별 스레드 바인딩은
`daily-threads/<날짜>.json`, 일정 레코드는 기존 `drafts/<키>.json` 하나뿐이다.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from functools import partial
from typing import TYPE_CHECKING

import calendar_binding
import calendar_confirm
import calendar_core
import calendar_gate
from calendar_confirm_input import DraftRecord

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from automation.interop.discord_transport import SentMessage


def event_key(draft: Mapping[str, object]) -> str:
    """캘린더 + KST 시작 분 + NFKC·casefold 제목 토큰 집합(정렬, 중복 제거) = 같은 일정.

    기간과 문장부호는 동일성이 아니다(후속 메일이 흔히 바꾸는 것). 시작 시각이나 제목
    토큰이 다르면 다른 일정이다 — 같은 시각의 다른 회의를 후속으로 오인해 합치지 않는다.
    """
    start = datetime.fromisoformat(str(draft["start"])).astimezone(calendar_core.KST)
    title = unicodedata.normalize("NFKC", str(draft["summary"])).casefold()
    identity = [str(draft["calendar_id"]), start.strftime("%Y-%m-%dT%H:%M"), sorted(set(re.findall(r"\w+", title)))]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode("utf-8")).hexdigest()


def daily_binding(draft: Mapping[str, object]) -> calendar_binding.ApprovalBindingLike:
    """게시일의 승인 스레드 — 처음 한 번만 공유 디렉터리로 열고, 이후에는 저장된 바인딩을 검증해 재사용."""
    import calendar_approval

    day = date.fromisoformat(str(draft["digest_day"])).isoformat()
    shared = _mail_digest_binding(day)
    if shared is not None:
        return shared
    path = calendar_gate._gate_dir() / "daily-threads" / f"{day}.json"
    with calendar_approval.confirm_lease().hold(f"digest-day:{day}") as owned:
        if not owned:
            raise calendar_gate.GateError("다이제스트 승인 스레드를 여는 중 — 다음 틱에 재시도", 1)
        if path.exists():
            return calendar_binding.stored_binding(json.loads(path.read_text(encoding="utf-8")))
        surface = calendar_binding._surface()
        binding = surface.resolve_new_binding(
            surface.ApprovalKind.CALENDAR, calendar_binding.approval_directory(), calendar_confirm.owner_id(),
            request=surface.RequestThread(title=f"다이제스트 {day}"),
        )
        calendar_gate.write_json(path, {
            "approval_guild_id": binding.guild_id, "channel_id": binding.channel_id, "kind": str(binding.kind),
            "policy_version": binding.policy_version, "surface": str(binding.surface),
        })
        return binding


def _mail_digest_binding(day: str) -> calendar_binding.ApprovalBindingLike | None:
    """The mail digest's thread for that day, so its event cards stand under their mail (Q187).

    Absent (the digest posted flat, or an older mail skill) means this skill's own daily
    thread as before; a stored thread that no longer validates is a refusal, not a guess.
    """
    try:
        shared = calendar_binding._repo_module("digest_thread")
    except calendar_gate.GateError:
        return None
    thread = shared.load(day)
    if thread is None:
        return None
    surface = calendar_binding._surface()
    try:
        return shared.binding(surface, surface.ApprovalKind.CALENDAR, thread,
                              calendar_binding.approval_directory(), calendar_confirm.owner_id())
    except (surface.ApprovalSurfaceError, TypeError, ValueError) as error:
        raise calendar_gate.GateError(f"다이제스트 승인 스레드 검증 실패 — 게시 거부: {error}", 1) from error


def _reply_target() -> dict[str, str]:
    """The mail card this event card replies under (env from the mail digest; never hashed)."""
    reply_to = os.environ.get("CALENDAR_DIGEST_REPLY_TO", "")
    channel = os.environ.get("CALENDAR_DIGEST_REPLY_CHANNEL", "")
    return {"digest_reply_to": reply_to, "digest_reply_channel": channel} if reply_to and channel else {}


def pending_by_thread(thread_ids: list[str]) -> dict[str, int]:
    """Undecided digest event cards per thread — read only, for the digest thread's mark."""
    from calendar_pending import PendingConfirmStore

    counts = dict.fromkeys(thread_ids, 0)
    for entry in PendingConfirmStore().load():
        if entry.channel_id in counts:
            counts[entry.channel_id] += 1
    return counts


def submit(draft: DraftRecord, day: str) -> DraftRecord:
    """초안을 저장하고, 다이제스트 건(``day`` 지정)이면 그날 스레드에 카드까지 올린 뒤 돌려준다.

    일정 동일성 키가 초안 id 다: 이미 결정된(executed/cancelled) 일정은 그 레코드를 그대로
    돌려주고 아무것도 게시하지 않으며, 미결 일정은 최신 내용으로 교체 게시된다.
    게시가 끝나지 않으면 소유자에게 즉시 알리고 GateError 로 끝난다 — 저장된 초안은
    감시 워처가 다음 틱에 다시 게시한다(`retry`).
    """
    if not day:
        calendar_gate.persist_draft(draft)
        return draft
    import calendar_approval

    key = event_key(draft)
    candidate: DraftRecord = {**draft, "id": key, "digest_key": key, "digest_day": date.fromisoformat(day).isoformat(),
                              **_reply_target()}
    with calendar_approval.confirm_lease().hold(f"digest-submit:{key}") as owned:
        if not owned:
            raise calendar_gate.GateError("같은 일정을 게시하는 중 — 다음 틱에 재시도", 1)
        path = calendar_gate._draft_path(key)
        if path.exists():
            previous: DraftRecord = json.loads(path.read_text(encoding="utf-8"))
            if previous.get("status") != "pending":
                print(f"DIGEST-DECIDED id={key} status={previous.get('status')}")
                print(card_line(previous, None))
                return previous
            candidate = {**candidate, "created": previous["created"], "digest_day": previous.get("digest_day", day)}
        else:
            # Save before directory I/O so even a failed first binding is retryable.
            calendar_gate.persist_draft(candidate)
        try:
            return _publish(candidate)
        except _failures() as error:
            _notify_publication_failure(candidate, error)
            raise calendar_gate.GateError("다이제스트 승인 게시 실패 — 다음 틱에 재시도", 1) from error


def retry(record: DraftRecord) -> None:
    """감시 워처의 재게시 — 실패는 이미 통지됐으므로 여기서는 로그만 남기고 다음 틱을 기다린다."""
    try:
        _ = _publish(record)
    except _failures() as error:
        print(f"DIGEST-RETRY-FAIL draft={record['id']} error={type(error).__name__}", file=sys.stderr)


def _publish(candidate: DraftRecord) -> DraftRecord:
    """일별 스레드에 묶어 저장한 뒤, 중단됐던 게시를 먼저 수습하고 공유 라이프사이클로 게시한다."""
    import calendar_approval
    import calendar_digest_recovery

    # Reconcile the saved payload before a newer correction may supersede it.
    # The shared lifecycle owns replacement: it probes decisions before persisting
    # the candidate, so a reaction already bound to the old hash remains valid.
    calendar_digest_recovery.recover(calendar_gate.load_draft(candidate["id"]))
    entry = calendar_approval.request_confirmation(candidate)
    print(f"PENDING-OWNER draft={candidate['id']} message={entry.dm_message_id}")
    record = calendar_gate.load_draft(candidate["id"])
    print(card_line(record, entry.dm_message_id))
    return record


def card_line(record: Mapping[str, object], message_id: str | None) -> str:
    """``DIGEST-CARD`` — 메일 다이제스트가 일정 줄을 이 승인 카드로 잇게 하는 한 줄.

    카드는 다이제스트 메일 카드보다 먼저 게시되므로 링크를 여기서 알 수 있다. 카드 메시지
    id 를 모르면(이미 결정된 일정) 스레드 링크로, 좌표가 없으면 ``unavailable`` 로 내려간다 —
    링크를 추측하지 않는다. 제목은 마지막 필드라 공백을 담을 수 있고 줄바꿈은 접는다.
    """
    url = "unavailable"
    try:
        from automation.interop.owner_message import LinkStatus, discord_link
    except ImportError as error:
        print(f"DIGEST-CARD-LINK-UNAVAILABLE draft={record.get('id')} err={type(error).__name__}", file=sys.stderr)
    else:
        def text(field: str) -> str | None:
            value = record.get(field)
            return value if isinstance(value, str) and value else None

        link = discord_link(space="guild", channel_id=text("approval_thread_id"),
                            message_id=message_id or None, guild_id=text("approval_guild_id"))
        if link.status is LinkStatus.AVAILABLE:
            url = str(link.url)
    title = " ".join(str(record.get("summary") or "").split())
    return (f"DIGEST-CARD draft={record.get('id')} url={url} start={record.get('start') or '-'} "
            f"end={record.get('end') or '-'} summary={title}")


def _failures() -> tuple[type[BaseException], ...]:
    import calendar_approval

    return (
        *calendar_approval._TRANSPORT_ERRORS, calendar_approval.lifecycle().ApprovalSurfaceError,
        calendar_binding._surface().ApprovalSurfaceError, ValueError,
    )


def _notify_publication_failure(draft: DraftRecord, error: BaseException) -> None:
    """게시가 끝나지 않았다는 사실만 즉시 알린다 — 일정 내용은 싣지 않는다(SKILL.md 반출 금지)."""
    from automation import owner_notice
    from automation.interop.owner_message import Action, Approval, OwnerMessage, Ref

    fact = f"캘린더 승인 카드를 게시하지 못했습니다 (draft {draft['id']}) — 다음 감시 틱에 다시 시도합니다."
    message = OwnerMessage(
        subject_key=str(draft["id"]), subject="다이제스트 일정 승인", fact=fact,
        location=Ref(scope="none"), owner=Action(verb="none"),
        agent_next="카드 게시에 성공한 뒤에만 승인 대기로 넘어갑니다", recovery="not_applicable",
        detail=Approval(expires_at=None, cancel_effect="캘린더를 변경하지 않음"),
    )
    notified = owner_notice.notify_owner(fact, message=message)
    print(f"DIGEST-POST-FAIL draft={draft['id']} error={type(error).__name__} notified={notified}", file=sys.stderr)


@dataclass(frozen=True, slots=True)
class DigestReply:
    """origin_notice 가 라우팅·렌더를 소유하고, 이 전송기는 자기 카드에 다는 답글만 더한다."""

    channel_id: str
    message_id: str

    def send(self, body: str) -> tuple[SentMessage, ...]:
        from automation.interop.chunker import chunk_message
        from automation.interop.discord_transport import SentMessage

        return tuple(
            SentMessage(message_id=calendar_confirm.post_message(self.channel_id, chunk, reply_to=self.message_id))
            for chunk in chunk_message(body)
        )


def reply_transport(record: Mapping[str, object]) -> Callable[[str], DigestReply] | None:
    """다이제스트 항목의 결과는 같은 일별 스레드 안, 자기 카드 아래에 답글로 붙는다.

    카드가 없는 레코드(게시 전 폐기)나 다이제스트가 아닌 초안은 None — 공유 스레드 전송기.
    """
    card = str(record.get("dm_message_id") or "")
    if not record.get("digest_day") or not card:
        return None
    return partial(DigestReply, message_id=card)
