"""Mail lifecycle adapter, retaining facade injection seams."""
from __future__ import annotations
import calendar
import contextlib
import fcntl
import os
import sys
import time
import uuid
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
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
        self._bind(intent, message_id)
        _add_reactions(message_id, intent.channel_id)
        if self.draft.get("approval_thread_id") == intent.channel_id:
            resume_pending_mark(intent.channel_id)
        return approval.lifecycle().PostedApproval(message_id=message_id, channel_id=intent.channel_id)

    def _bind(self, intent: ApprovalIntent, message_id: str) -> None:
        """Bind the posted card before anything else can hang, as the other producers do.

        2026-11-04: compose died at the node gate's 150 s after the POST, so the draft kept no
        message id and the reserved posting journal wedged the key (posting-journal-stale).
        The lifecycle's later ``commit`` writes the same id again, which ``set_message_id`` allows.
        """
        approval = import_module("triage_approval")
        journal = approval.posting_journal()
        reservation = journal.outstanding(intent.key) or {}
        triage_gate.set_message_id(
            self.draft, message_id, intent.channel_id,
            approval_created_at=reservation.get("at", ""),
        )
        journal.clear(intent.key)

    def commit(self, intent: ApprovalIntent, posted: PostedApproval, created_at: str) -> None:
        """The ONLY writer of the Discord ``message_id`` and its timer anchor."""
        triage_gate.set_message_id(
            self.draft,
            posted.message_id,
            intent.channel_id,
            approval_created_at=created_at,
        )


THREAD_MARKS_DIRNAME = "thread-marks"
_DISCORD_EPOCH_MS = 1420070400000
_ADOPT_WINDOW_MS = 60_000


def _add_reactions(message_id: str, channel_id: str, present: frozenset[str] = frozenset()) -> frozenset[str]:
    """Add the card's missing ✅⛔ and return the ones still missing.

    Discord refuses a reaction inside an archived thread (400, code 50083; 2026-11-04 draft 36b605
    was adopted into a thread its withdrawn sibling had archived). The thread is then reopened on
    its own - ``archived: false`` without a name, so the two-renames-per-ten-minutes limit never
    applies, and without waiting on a 429 - and the reaction is tried again.
    """
    missing: set[str] = set()
    for emoji in (triage_confirm.APPROVE_EMOJI, triage_confirm.CANCEL_EMOJI):
        if emoji in present:
            continue
        try:
            try:
                triage_confirm.add_reaction(message_id, emoji, channel_id)
            except HTTPError as error:
                if not _archived_refusal(error):
                    raise
                with triage_confirm.no_rate_limit_wait():
                    triage_confirm._api("PATCH", f"/channels/{channel_id}", {"archived": False})
                triage_confirm.add_reaction(message_id, emoji, channel_id)
        except (HTTPError, OSError) as error:
            missing.add(emoji)
            reason = getattr(error, "code", None) or str(error)
            print(
                f"APPROVAL-REACTION-FAIL {emoji} message={message_id} "
                f"reason={type(error).__name__}:{reason}",
                file=sys.stderr,
            )
    return frozenset(missing)


def _archived_refusal(error: HTTPError) -> bool:
    triage_supersede = import_module("triage_supersede")
    return error.code == 400 and triage_supersede._discord_code(error) == triage_supersede.ARCHIVED_THREAD


def _own_reactions(message: Mapping[str, JsonValue]) -> frozenset[str]:
    return frozenset(
        str((reaction.get("emoji") or {}).get("name")) for reaction in message.get("reactions") or ()
        if isinstance(reaction, dict) and reaction.get("me")
    )


def fill_missing_reactions(drafts: Iterable[Mapping[str, JsonValue]]) -> int:
    """Watch tick: give the one decidable card of each channel the ✅⛔ it lacks; count failures.

    Only a pending, bound card that is the newest card of its channel and whose message still
    holds exactly its card bytes is touched. A superseded, withdrawn or decided card, an older
    card under a newer one (36b605 under c995a3, 2026-11-04) or a card whose text was replaced
    never gets buttons back, so the owner cannot approve the same mail twice.
    """
    drafts = tuple(drafts)
    newest: dict[str, int] = {}
    for draft in drafts:
        message, channel = str(draft.get("message_id") or ""), str(draft.get("channel_id") or "")
        if message.isdigit() and channel:
            newest[channel] = max(newest.get(channel, 0), int(message))
    approval = import_module("triage_approval")
    failed = 0
    for draft in drafts:
        message_id, channel = str(draft.get("message_id") or ""), str(draft.get("channel_id") or "")
        if draft.get("status") != "pending" or not message_id.isdigit() or newest.get(channel) != int(message_id):
            continue
        with approval.confirm_lease().hold(approval.approval_key(draft)) as owned:
            if not owned:
                continue
            try:
                current = triage_gate.load_draft(str(draft["id"]))
            except triage_gate.GateError:
                continue
            if current.get("message_id") != message_id or str(current.get("channel_id")) != channel:
                continue
            try:
                message = triage_confirm._api("GET", f"/channels/{channel}/messages/{message_id}")
            except (HTTPError, OSError, ValueError) as error:
                failed += 1
                print(f"APPROVAL-REACTIONS-MISSING draft={current['id']} message={message_id} "
                      f"err={type(error).__name__}", file=sys.stderr)
                continue
            if not (
                isinstance(message, dict) and (message.get("author") or {}).get("bot") is True
                and mail_approval_attachment.matches(current, message)
                and mail_card_compact.references(
                    current, str(message.get("content", "")), approval._approval_action_hash(current))
            ):
                continue
            present = _own_reactions(message)
            if {triage_confirm.APPROVE_EMOJI, triage_confirm.CANCEL_EMOJI} <= present:
                continue
            if _add_reactions(message_id, channel, present):
                failed += 1
                print(f"APPROVAL-REACTIONS-MISSING draft={current['id']} message={message_id}", file=sys.stderr)
            else:
                print(f"APPROVAL-REACTIONS-FILLED draft={current['id']} message={message_id}")
    return failed


def _thread_marks() -> Path:
    return triage_gate.gate_dir() / THREAD_MARKS_DIRNAME


PENDING_MARK = "🔔"
#: How long a mark waits for another process marking the same thread (one GET and one PATCH, no 429 wait).
_THREAD_LOCK_SECONDS = 20.0
SENT_MARK = "✅"
#: End marks that close (archive) the thread; ❗ failed stays open for the owner, like 🔔.
_CLOSING_MARKS = frozenset({SENT_MARK, "⛔", "💤"})
#: End marks of a card that sent nothing; a mail already sent in the same thread outranks them.
_ENDED_UNSENT = frozenset({"⛔", "💤"})


def waiting_threads(drafts: Iterable[Mapping[str, JsonValue]]) -> set[str]:
    """Approval threads holding a bound card that still waits for the owner."""
    return {
        str(draft.get("channel_id")) for draft in drafts
        if draft.get("status") == "pending" and draft.get("message_id")
        and draft.get("approval_thread_id") == draft.get("channel_id")
        and not draft.get("last_error")  # approved, its send waits for a retry: nothing left to press
    }


def mark_thread(thread_id: str, mark: str | None = None, *, exclude: str = "", card: str = "") -> bool:
    """The one place a mail approval thread's state mark is set; a miss is kept for the next tick.

    ``mark`` (when given) is recorded with its card's message id as its own file in
    ``thread-marks/`` (written aside, then renamed in, at once and without waiting), so no writer
    ever overwrites, half-shows or removes another's wish; without ``mark`` the recorded ones are
    settled (the watch tick). Settling runs under the thread's lock: it lists the recorded marks
    first, then reads the draft store, where a card still waiting in the thread wins with 🔔 (lead
    2026-11-12: what the owner still has to press comes first; ``exclude`` is the draft whose
    result is being delivered). Otherwise the newest card's result wins, a final one (✅ ⛔ 💤)
    over ❗ for the same card, so no clock decides the order; but once a mail of the thread was
    sent, a later ⛔ or 💤 of another card there leaves it ✅ (owner 2026-11-18: an old draft
    cancelled after the mail went out must not show the thread cancelled). A card is bound before
    its 🔔 is recorded and a result's status before its end mark, so a wish recorded after the
    listing is never decided on a staler store, and only the listed files are removed. A name that
    already shows the mark costs no rename: Discord allows two name edits per ten minutes, and a
    429 is never waited on (2026-11-04: the wait killed compose; 2026-11-12: four cards and
    results in eleven minutes left a sent mail's thread 🔔).
    """
    if not thread_id.isdigit():
        return False
    root = _thread_marks()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if mark is not None:
        _record_mark(root, thread_id, mark, card)
    with _thread_lock(root / thread_id) as locked:
        if not locked:
            print(f"THREAD-MARK-BUSY thread={thread_id}", file=sys.stderr)
            return False
        wishes = _recorded_marks(root, thread_id)
        if not wishes:
            return True
        drafts = triage_gate.list_drafts()
        waiting = thread_id in waiting_threads(
            draft for draft in drafts if not exclude or draft.get("id") != exclude)
        ended = [wish for _, wish in wishes if wish[0] != PENDING_MARK]
        shown = _settled_mark(root, thread_id)
        if waiting or ended:
            newest = max([*ended, *([shown] if shown else [])], key=lambda wish: (wish[1], wish[0] in _CLOSING_MARKS), default=(PENDING_MARK, 0))
            sent = _sent_card(drafts, thread_id)
            if newest[0] in _ENDED_UNSENT and sent is not None:
                newest = (SENT_MARK, sent)
            done = _apply_mark(thread_id, PENDING_MARK if waiting else newest[0])
            if done and not waiting:
                _settle_mark(root, thread_id, newest)
        else:
            done = True  # a 🔔 whose card was decided: that result records its own end mark
        if done:
            for path, _ in wishes:
                path.unlink(missing_ok=True)
        return done


def _sent_card(drafts: Iterable[Mapping[str, JsonValue]], thread_id: str) -> int | None:
    """The newest card of the thread whose mail was sent: its draft is executed, and an executed draft is kept."""
    return max((
        int(str(draft.get("message_id"))) for draft in drafts
        if draft.get("status") == "executed" and str(draft.get("approval_thread_id") or "") == thread_id
        and str(draft.get("message_id") or "").isdigit()
    ), default=None)


def _settled_mark(root: Path, thread_id: str) -> tuple[str, int] | None:
    """The end mark last shown on the thread, so a late record of an older result never beats it."""
    try:
        mark, _, card = (root.parent / f"{THREAD_MARKS_DIRNAME}.settled" / thread_id).read_text(encoding="utf-8").partition(" ")
    except FileNotFoundError:
        return None
    return (mark, int(card)) if mark and card.isdigit() else None


def _settle_mark(root: Path, thread_id: str, wish: tuple[str, int]) -> None:
    settled = root.parent / f"{THREAD_MARKS_DIRNAME}.settled"
    settled.mkdir(mode=0o700, exist_ok=True)
    aside = settled / f".{thread_id}.{uuid.uuid4().hex[:8]}"
    aside.write_text(f"{wish[0]} {wish[1]}", encoding="utf-8")
    os.replace(aside, settled / thread_id)


def _record_mark(root: Path, thread_id: str, mark: str, card: str = "") -> None:
    token = f"{time.time_ns()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    aside = root / f".{thread_id}.{token}"
    aside.write_text(f"{mark} {card if card.isdigit() else 0}", encoding="utf-8")
    os.replace(aside, root / f"{thread_id}.{token}")


def _recorded_marks(root: Path, thread_id: str) -> list[tuple[Path, tuple[str, int]]]:
    """The thread's recorded marks as (mark, card message id); a bare ``<thread>`` file is an older runtime's 🔔."""
    paths = [root / thread_id] if (root / thread_id).is_file() else []
    paths += sorted(root.glob(f"{thread_id}.*"))
    wishes: list[tuple[Path, tuple[str, int]]] = []
    for path in paths:
        try:
            mark, _, card = path.read_text(encoding="utf-8").strip().partition(" ")
        except FileNotFoundError:
            continue
        wishes.append((path, (mark or PENDING_MARK, int(card) if card.isdigit() else 0)))
    return wishes


@contextlib.contextmanager
def _thread_lock(path: Path) -> Iterator[bool]:
    """Hold ``thread-marks.locks/<thread>``; give up after a while rather than outlive a gate."""
    locks = path.parent.with_name(f"{THREAD_MARKS_DIRNAME}.locks")
    locks.mkdir(mode=0o700, exist_ok=True)
    with open(locks / path.name, "a", encoding="utf-8") as handle:
        deadline = time.monotonic() + _THREAD_LOCK_SECONDS
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    yield False
                    return
                time.sleep(0.1)
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _apply_mark(thread_id: str, mark: str) -> bool:
    closed = mark in _CLOSING_MARKS
    try:
        with triage_confirm.no_rate_limit_wait():
            current = triage_confirm._api("GET", f"/channels/{thread_id}")
            if not isinstance(current, dict):
                raise ValueError("thread is not an object")
            name = str(current.get("name") or "")
            archived = bool((current.get("thread_metadata") or {}).get("archived"))
            wanted = triage_confirm._origin_notice().state_thread_name(name, mark) if name else name
            if wanted == name and archived == closed:
                return True
            if archived and wanted != name:  # Discord renames an archived thread only while reopening it
                triage_confirm._api("PATCH", f"/channels/{thread_id}", {"name": wanted, "archived": False})
                if closed:
                    triage_confirm._api("PATCH", f"/channels/{thread_id}", {"archived": True})
            else:
                payload: dict[str, JsonValue] = {"archived": closed}
                if wanted != name:
                    payload["name"] = wanted
                triage_confirm._api("PATCH", f"/channels/{thread_id}", payload)
    except HTTPError as error:
        if error.code == 404:
            return True  # the thread is gone: nothing left to mark
        print(f"THREAD-RENAME-FAIL thread={thread_id} err=HTTPError:{error.code}", file=sys.stderr)
        return False
    except Exception as error:  # noqa: BLE001 — a mark never costs the card or the result behind it
        print(f"THREAD-RENAME-FAIL thread={thread_id} err={type(error).__name__}", file=sys.stderr)
        return False
    return True


def resume_pending_mark(thread_id: str) -> bool:
    """Give a reused thread 🔔 once its new card is bound; a miss is retried by the watch tick."""
    return mark_thread(thread_id, PENDING_MARK)


def retry_thread_marks(drafts: Iterable[Mapping[str, JsonValue]] = ()) -> None:
    """Watch tick: settle each remembered thread's last wanted mark, 🔔 while a card there still waits.

    A remembered 🔔 whose card no longer waits is forgotten: that card's own result asked for its
    end mark, so the tick never renames a decided thread back. The draft store is read again under
    each thread's lock, so ``drafts`` (the tick's own list) is not needed and kept for callers.
    """
    del drafts
    root = _thread_marks()
    if not root.is_dir():
        return
    for thread_id in sorted({path.name.partition(".")[0] for path in root.iterdir() if not path.name.startswith(".")}):
        if thread_id.isdigit():
            mark_thread(thread_id)


def _snowflake_before(stamp: str) -> int | None:
    try:
        posted_ms = calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")) * 1000
    except (TypeError, ValueError):
        return None
    return max(posted_ms - _ADOPT_WINDOW_MS - _DISCORD_EPOCH_MS, 0) << 22


def adopt_unrecorded_post(draft: dict[str, JsonValue], notice: str = "") -> bool:
    """Bind a card whose compose died between its POST and its binding.

    The posting journal then holds a reservation without a message id, which the lifecycle
    refuses for good (posting-journal-stale; 2026-11-04 draft 36b605: card in the thread, no
    reactions, every watch tick failed). One read of the thread's messages after the
    reservation finds the bot message with exactly this card's bytes; it is bound, its
    missing reactions are added and the journal is cleared. Anything else (no match, two
    matches, a changed card, an unreadable thread) leaves the refusal as it was.
    """
    if draft.get("message_id"):
        return False
    approval = import_module("triage_approval")
    key = approval.approval_key(draft)
    journal = approval.posting_journal()
    reservation = journal.outstanding(key)
    if reservation is None or reservation.get("message_id"):
        return False
    channel = str(draft.get("channel_id") or draft.get("approval_thread_id") or "")
    after = _snowflake_before(reservation.get("at", ""))
    if not channel.isdigit() or after is None:
        return False
    with approval.confirm_lease().hold(key) as owned:
        if not owned or journal.outstanding(key) != reservation:
            return False
        intent, gate = prepare_request(draft, notice)
        if intent.action_hash != reservation.get("action_hash"):
            return False
        try:
            messages = triage_confirm._api("GET", f"/channels/{channel}/messages?after={after}&limit=50")
        except (HTTPError, OSError, ValueError, triage_gate.GateError) as error:
            print(f"APPROVAL-ADOPT-FAIL draft={draft.get('id')} err={type(error).__name__}", file=sys.stderr)
            return False
        found = [
            message for message in (messages if isinstance(messages, list) else ())
            if isinstance(message, dict) and (message.get("author") or {}).get("bot") is True
            and message.get("content") == gate.content and mail_approval_attachment.matches(gate.draft, message)
        ]
        if len(found) != 1:
            print(f"APPROVAL-ADOPT-MISS draft={draft.get('id')} found={len(found)}", file=sys.stderr)
            return False
        message_id = str(found[0]["id"])
        triage_gate.set_message_id(gate.draft, message_id, channel, approval_created_at=reservation.get("at", ""))
        journal.clear(key)
        _add_reactions(message_id, channel, _own_reactions(found[0]))
        print(f"APPROVAL-ADOPTED draft={draft.get('id')} message={message_id}")
    if draft.get("approval_thread_id") == channel:
        resume_pending_mark(channel)
    return True


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
    if version == "6" and not draft.get("message_id"):
        version = None  # An unposted format-test still needs the standard long-body fallback.
    selected_version = version or ("6" if draft.get("reply_format") == "html-test" else mail_card_compact.VERSION)
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
