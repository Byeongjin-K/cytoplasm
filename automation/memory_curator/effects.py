"""Node-runtime side effects for the memory-curator cron watcher.

``alert_owner`` is the near-cap notification (best-effort owner-directed) — NOT an
approval, so it stays here.  The security-critical ``promote`` effect posts a
twin draft for owner-DM ✅ through the wiki gate's sanctioned
``create_draft`` + ``post_confirm_message`` path; it never re-implements the
``approval_lifecycle`` boundary.

Both effects are exercised on the node (live Discord / wiki gate) and are
deploy-validated, like every watcher here.  ``alert_owner`` fails closed: a
missing token/config or an HTTP error is swallowed so a bad tick never
crashes the cron.
"""

from __future__ import annotations

import importlib
import json
import os
import stat
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from automation.interop.owner_message import Ref

from . import reminder, reporting
from .binding import PromotionReceipt
from .notice import build_notice
from .promotion import PromotionProposal

#: Injected owner-notice sender — ``(message) -> delivered`` (ON-2: 파사드가 목적지 소유).
NotifyOwner = Callable[[str], bool]


def alert_owner(content: str, *, notify: NotifyOwner | None = None) -> bool:
    """Best-effort state-check notice; never crash the tick."""
    if os.environ.get("MEMORY_CURATOR_DRY_RUN") == "1":
        print(f"DRY-RUN alert: {content}")
        return False
    try:
        if notify is not None:
            return bool(notify(content))
        from automation import owner_notice
        from automation.owner_notice import notify_owner

        message = build_notice(content)
        if message is not None and getattr(owner_notice, "ACCEPTS_OWNER_MESSAGE", False):
            ok = notify_owner(content, message=message, fault=True)
        else:
            ok = notify_owner(content, fault=True)
        return bool(ok)
    except Exception:  # noqa: BLE001 — best-effort notification, must not crash the cron
        return False



# --- promotion: thin reuse of the wiki gate's sanctioned approval flow ------ #
WikiPromoteRunner = Callable[[PromotionProposal], "PromotionReceipt | None"]


def _twin_meta_and_body(proposal: PromotionProposal, now: str) -> tuple[dict[str, object], str]:
    """Pure: the wiki frontmatter (SI-3 observed/advisory) + body for a promotion."""
    meta: dict[str, object] = {
        "title": proposal.title,
        "tags": ["twin", proposal.twin_kind],
        "created": now,
        "updated": now,
        "links": [],
        "kind": proposal.twin_kind,
        "authority": proposal.authority,  # advisory — proposer cap
        "provenance": proposal.provenance,  # observed — proposer cap
    }
    return meta, proposal.body


def _promotion_summary(proposal: PromotionProposal) -> str:
    """Pure: the owner-DM-only summary the wiki confirm message carries.

    The ✅ cha taps authorizes DELETING this entry from the agent's own memory, so
    the message they react to has to say that — same standard as
    ``memory_relocate.render`` — which shows the whole entry, because the owner is
    deciding about THIS one item and a 28-character listing preview cannot carry that
    (2026-08-03 실측: 소유자가 미리보기만 보고 무엇을 승인해야 할지 모르겠다고 했다).
    토큰 모양 문자열은 가리고, 전체 길이는 confirm_text 의 1900자 상한이 지킨다.
    The wiki gate treats this as an opaque string.
    """
    return (
        f"자체 메모리 {reporting.source_filename(proposal.source_kind)} → 판단 트윈 승격({proposal.twin_kind})\n"
        "원본 항목\n"
        "───\n"
        f"{reporting.redacted(proposal.entry_text)}\n"
        "───\n"
        "승인(✅) 시 이 항목은 자체 메모리에서 **삭제**되고 이후에는 "
        "**recall(검색)로만** 찾을 수 있게 됩니다 — 취소는 ⛔."
    )


def _wiki_gate_promote(proposal: PromotionProposal) -> PromotionReceipt | None:
    """Node-runtime: post the twin draft through the wiki gate's OWN sanctioned
    ``create_draft`` + ``post_confirm_message`` (owner-DM ✅ via approval_lifecycle).

    This reuses the wiki skill's approval boundary verbatim: it NEVER resolves a
    surface, writes confirm_message_id, or touches approval_lifecycle — the gate
    does all of that.  ``post_confirm_message`` persists the full owner-DM binding
    (via ``WikiApprovalGate.commit``) and its one-live-confirm-per-key invariant
    makes a repeated tick a no-op.  The deployed wiki scripts dir must be on
    ``sys.path`` (the cron wrapper adds it) with the wiki runtime env set.
    """
    if os.environ.get("MEMORY_CURATOR_DRY_RUN") == "1":
        return None

    wiki_gate = importlib.import_module("wiki_gate")
    wiki_store = importlib.import_module("wiki_store")

    meta = _twin_meta_and_body(proposal, wiki_store.utc_now())[0]
    note_text = wiki_store.compose_note(meta, proposal.body)
    draft = wiki_gate.create_draft(
        "create", proposal.slug, note_text, "dm", summary=_promotion_summary(proposal)
    )
    result = wiki_gate.post_confirm_message(draft)
    confirm_message_id = result.get("confirm_message_id")
    if not isinstance(confirm_message_id, str):
        return None
    return PromotionReceipt(
        draft_id=draft["id"],
        confirm_message_id=confirm_message_id,
        slug=proposal.slug,
        note_sha256=draft["sha256"],
    )


def post_promotion(
    proposal: PromotionProposal, *, runner: WikiPromoteRunner | None = None
) -> PromotionReceipt | None:
    """Propose one durable entry to the twin for owner-DM ✅.

    Returns the approval receipt when the confirm was posted.  Any failure
    returns None — retried next tick, and the wiki gate's one-live-confirm-per-key
    invariant prevents a double post.
    """
    run = runner or _wiki_gate_promote
    try:
        return run(proposal)
    except Exception:  # noqa: BLE001 — a failed post is retried next tick, never crashes
        return None


def draft_present(draft_id: str, *, gate_dir: Path | None = None) -> bool:
    """그 제안의 위키 초안이 아직 살아 있는가.

    소유자가 ⛔ 를 누르면 위키 게이트가 초안을 폐기한다. 그것이 "거절됐다"의 유일한
    지속 신호다 — 리액션은 소비되면 사라지고, 노트는 애초에 만들어지지 않는다.
    읽기만 하며, 판단할 수 없으면 살아 있다고 답한다(fail-closed).
    """
    if not draft_id:
        return True
    root = (
        gate_dir
        if gate_dir is not None
        else Path(os.environ.get("WIKI_GATE_DIR", "~/.hermes/wiki-gate")).expanduser()
    )
    try:
        return (root / "drafts" / f"{draft_id}.json").is_file()
    except OSError:
        return True


def read_twin(slug: str, *, wiki_root: Path | None = None) -> bytes | None:
    """Read one regular twin note without following a final-component symlink."""
    root = (
        wiki_root
        if wiki_root is not None
        else Path(os.environ.get("WIKI_ROOT", "~/wiki")).expanduser()
    )
    path = root / f"{slug}.md"
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return None
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            return handle.read()
    except OSError:
        return None
    finally:
        os.close(descriptor)


#: 리마인더 주기 표식. 승격 원장이 아니라 알림 이력이므로 상태 스키마에 넣지 않는다
#: (v3 키 집합이 고정돼 있어 필드 추가는 마이그레이션을 부른다).
REMINDER_MARKER_NAME = "last-approval-reminder"


def _read_marker(path: Path) -> datetime | None:
    try:
        return datetime.fromisoformat(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _draft_link(gate_dir: Path, draft_id: str) -> Ref | None:
    """저장된 승인 좌표만 읽는다 — 공간 미상은 DM 으로 추측하지 않는다."""
    from automation.interop.owner_message import Ref, Space
    try:
        record = json.loads((gate_dir / "drafts" / f"{draft_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    channel = record.get("channel_id")
    message = record.get("confirm_message_id")
    message = record.get("message_id") if message in (None, "") else message
    if any(value not in (None, "") and (not isinstance(value, str) or not
           (value.isascii() and value.isdecimal() and value.strip("0"))) for value in (channel, message)):
        return None
    if not channel or not message:
        return Ref(scope="resource", url=None, search=("Discord 검색", draft_id))
    guild = record.get("approval_guild_id")
    space: Space
    match record.get("surface"):
        case "owner-dm" | "dm":
            space = "dm"
        case "agent-chat-thread":
            space = "guild"
        case _:
            space = "unknown"
    return Ref(scope="message", space=space, channel_id=channel, message_id=message,
               guild_id=guild if isinstance(guild, str) else None, search=("Discord 검색", draft_id))


def pending_approvals(state: object, gate_dir: Path) -> tuple[reminder.PendingApproval, ...]:
    """대기 중인 승격 확인 — 좌표 오류는 제외하고 좌표·공간 부재는 검색으로 안내한다."""
    from automation.interop.owner_message import LinkStatus, discord_link

    promotions = getattr(state, "promotions", {})
    items: list[reminder.PendingApproval] = []
    for record in sorted(promotions.values(), key=lambda item: item.draft_id or ""):
        if record.status != "posted" or not record.draft_id:
            continue
        link = _draft_link(gate_dir, record.draft_id)
        if link is None:
            continue  # 초안이 없으면 리마인드할 대상도 없다(거절됐거나 이미 소비됨)
        jump = discord_link(space=link.space, channel_id=link.channel_id,
                            message_id=link.message_id, guild_id=link.guild_id)
        if jump.status is LinkStatus.INVALID:
            continue  # 저장된 좌표 오류는 잘못된 링크로 알리지 않는다.
        items.append(
            reminder.PendingApproval(
                draft_id=record.draft_id,
                source_file=reporting.source_filename(record.source_kind),
                preview=reporting.preview(_draft_entry_preview(gate_dir, record.draft_id)),
                ref=link,
            )
        )
    return tuple(items)


def _draft_entry_preview(gate_dir: Path, draft_id: str) -> str:
    """초안이 실은 요약의 첫 줄 — 없으면 빈 문자열(미리보기는 부가물이다)."""
    try:
        record = json.loads((gate_dir / "drafts" / f"{draft_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    summary = record.get("summary")
    if not isinstance(summary, str):
        return ""
    body = summary.split("───")
    return body[1].strip() if len(body) > 1 else summary.splitlines()[0]


def send_pending_reminder(
    state: object,
    *,
    gate_dir: Path,
    marker_path: Path,
    now: datetime,
    alert: Callable[[str], bool],
) -> bool:
    """3시간마다, 조용한 창 밖에서만, 대기 중인 승인을 다시 가리킨다.

    승인 메시지는 건드리지 않는다 — 지웠다 다시 올리면 그 사이 소유자가 누른 반응을
    잃을 수 있고, 승인 메시지 단일성 규칙도 위태롭다. 보냈을 때만 표식을 전진시키므로,
    조용한 창에 걸린 리마인더는 취소가 아니라 연기다.
    """
    pending = pending_approvals(state, gate_dir)
    if not reminder.due(now, last_sent=_read_marker(marker_path), pending=pending):
        return False
    if not alert(reminder.render(pending)):
        return False
    try:
        marker_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _ = marker_path.write_text(now.isoformat(), encoding="utf-8")
        marker_path.chmod(0o600)
    except OSError:
        return True  # 알림은 나갔다. 표식 실패는 다음 tick 에 한 번 더 보내게 할 뿐이다.
    return True
