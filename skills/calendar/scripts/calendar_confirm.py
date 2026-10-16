"""Owner-confirmation transports for the calendar gate (W3-1).

Three mutually exclusive paths, all fail-closed:
- production direct ``owner_dm_reply``/reaction: owner input verified through Discord REST;
- watcher ``owner_dm_reaction``: a short-lived HMAC authorization, bound to the exact
  draft/pending DM and consumed once without a duplicate Discord query;
- unattended ``signed_injection_e2e``: HMAC-signed injected event (W1-6 adapter)
  accepted only under ``E2E_TEST_MODE=1`` (production gateway refuses that env).
"""
from __future__ import annotations

import json
import os
import sys
from contextlib import suppress
from datetime import datetime
from importlib import import_module
from pathlib import Path
from typing import Any, Mapping
from calendar_confirm_input import DraftRecord as DraftRecord
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

calendar_core = import_module("calendar_core")
_gate = import_module("calendar_gate")
GateError = _gate.GateError
write_json = _gate.write_json
_pending = import_module("calendar_pending")
PendingConfirm = _pending.PendingConfirm
PendingConfirmError = _pending.PendingConfirmError
PendingConfirmStore = _pending.PendingConfirmStore
calendar_binding = import_module("calendar_binding")
_authz = import_module("calendar_confirm_authz")
create_watcher_authorization = _authz.create_watcher_authorization

API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://github.com/orientpine/autophagy-agents, 0)"
DM_SCAN_LIMIT = 50
APPROVE_EMOJI = "\u2705"
CANCEL_EMOJI = "\u26d4"
#: Terminal states a result notice may close its request thread with (origin_notice 소유).
OUTCOME_DONE = "done"
OUTCOME_CANCELLED = "cancelled"
OUTCOME_EXPIRED = "expired"
#: 재시도 가능한 실행 실패 — 종결이 아니므로 스레드를 닫지 않고 결과 봉투도 만들지 않는다.
OUTCOME_FAILED = "failed"

# Preserve the public transport injection seams while separating input validation.
from calendar_confirm_input import (  # noqa: E402
    _adapter as _adapter, _require_e2e_secret as _require_e2e_secret,
    confirm_via_injection as confirm_via_injection, sign_injection as sign_injection,
    confirm_via_owner_scan as confirm_via_owner_scan, _pending_entry as _pending_entry,
    _validate_pending_binding as _validate_pending_binding,
    _reaction_action as _reaction_action, _owner_reacted as _owner_reacted,
)


def confirm_text(draft: DraftRecord) -> str:
    return f"실행 {draft['id']} sha256:{draft['sha256']}"


def owner_id() -> str:
    config = Path(os.environ.get("INTEROP_CONFIG", "~/.hermes/interop/config.json")).expanduser()
    try:
        owner = json.loads(config.read_text(encoding="utf-8")).get("owner_id")
    except OSError:
        raise GateError(f"interop config 읽기 실패: {config}", 3) from None
    if not isinstance(owner, str) or not owner:
        raise GateError("interop config에 owner_id가 없습니다", 3)
    return owner


def bot_token() -> str:
    if token := os.environ.get("DISCORD_BOT_TOKEN", "").strip():
        return token
    with suppress(FileNotFoundError):
        for line in (Path.home() / ".env.secrets").read_text(encoding="utf-8").splitlines():
            key, delimiter, value = line.strip().partition("=")
            if key.strip() == "DISCORD_BOT_TOKEN" and delimiter and (token := value.strip().strip("'\"").strip()):
                return token
    raise GateError("DISCORD_BOT_TOKEN 누락 — 프로덕션 확인 경로 사용 불가", 3)


def consume_watcher_authorization(draft: DraftRecord, authorization_path: Path) -> str:
    """Atomically consume one watcher authorization bound to this exact draft/DM.

    서명·잠금·nonce 소진은 `calendar_confirm_authz` 가 소유한다. 소유자 판정과 pending
    조회는 이 모듈이 소유하므로 호출 시점에 그대로 넘긴다 — 사본을 만들지 않는다.
    """
    return _authz.consume_watcher_authorization(
        draft,
        authorization_path,
        _authz.WatcherAuthorizationBindings(owner_id=owner_id, pending_entry=_pending_entry),
    )


def _api(method: str, path: str, payload: Mapping[str, str | Mapping[str, str | bool]] | None = None) -> Any:
    token = bot_token()
    request = Request(
        f"{API}{path}",
        data=None if payload is None else json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method=method,
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310
        body = response.read().decode("utf-8")
    return json.loads(body) if body else None


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _render_v1(draft: DraftRecord) -> str:
    """Frozen calendar card; confirm_text remains the separate signed-input protocol."""
    return (
        f"{_change_summary(draft)}\n\n이 메시지에 ✅ 실행 / ⛔ 취소. "
        f"텍스트 fallback: `실행 {draft['id']}`/`취소 {draft['id']}`\n"
        f"sha256:{draft['sha256']}"
    )


def render_confirmation(draft: DraftRecord) -> str:
    if draft.get("render_version", "1") == "1":
        return _render_v1(draft)
    return import_module("calendar_card").render_envelope(draft)


def post_confirmation_message(draft: DraftRecord, channel_id: str) -> tuple[str, str]:
    """Post only the card prepared before surface resolution, or replay a stored version."""
    content = draft.get("approval_content") or render_confirmation(draft)
    payload: dict[str, object] = {"content": content}
    if draft.get("digest_reply_to") and draft.get("digest_reply_channel") == channel_id:
        # Under its mail card in the morning digest thread (owner Q187, 2026-10-16).
        payload["message_reference"] = {"message_id": draft["digest_reply_to"], "fail_if_not_exists": False}
    message = _api("POST", f"/channels/{channel_id}/messages", payload)
    message_id = _required_string(message, "id", "확정 DM 메시지 id가 없습니다")
    _api("PUT", f"/channels/{channel_id}/messages/{message_id}/reactions/{quote(APPROVE_EMOJI, safe='')}/@me")
    _api("PUT", f"/channels/{channel_id}/messages/{message_id}/reactions/{quote(CANCEL_EMOJI, safe='')}/@me")
    return channel_id, message_id


def owner_approval_channel(owner: str) -> str:
    """Resolve the configured owner's DM through the shared approval directory."""
    if owner != owner_id():
        raise GateError("승인 소유자 id가 현재 interop 설정과 다릅니다", 3)
    return calendar_binding.approval_directory().owner_dm()


def post_message(channel_id: str, content: str, *, reply_to: str = "") -> str:
    """Post an already-rendered reminder/result — optionally as a reply to its own card — never an approval card."""
    reference = {"message_reference": {"message_id": reply_to, "fail_if_not_exists": False}} if reply_to else {}
    return _required_string(_api("POST", f"/channels/{channel_id}/messages", {"content": content, **reference}), "id", "메시지 id가 없습니다")


def edit_message(channel_id: str, message_id: str, content: str) -> None:
    """Rewrite an already-posted reminder (one digest-thread reminder message, Q187)."""
    _api("PATCH", f"/channels/{channel_id}/messages/{message_id}", {"content": content})


def fetch_channel(channel_id: str) -> object:
    """Read channel metadata for the reminder's server-aware source link."""
    return _api("GET", f"/channels/{channel_id}")


def send_owner_dm(owner: str, content: str) -> None:
    """Send a private, terse result notification to the calendar owner."""
    if owner != owner_id():
        raise GateError("승인 소유자 id가 현재 interop 설정과 다릅니다", 3)
    channel_id = calendar_binding.approval_directory().owner_dm()
    _api("POST", f"/channels/{channel_id}/messages", {"content": content})


def _interop_runtime() -> Path:
    configured = os.environ.get("INTEROP_RUNTIME")
    if configured:
        return Path(configured).expanduser()
    root = Path("~/.hermes/autophagy-import").expanduser()
    if (root / "automation" / "interop").is_dir():
        return root
    return Path("~/.hermes/interop_runtime").expanduser()


def _origin_notice():
    runtime = _interop_runtime()
    sys.path.insert(0, str(runtime))
    from automation.interop import origin_notice  # noqa: PLC0415

    return origin_notice


def _thread_transport(channel_id: str):
    runtime = _interop_runtime()
    sys.path.insert(0, str(runtime))
    from automation.interop.discord_transport import DiscordTransport  # noqa: PLC0415

    return DiscordTransport(token=bot_token(), channel_id=channel_id)


def notify_result(draft: dict[str, str | list[str]], content: str, outcome: str = "", *, transport=None) -> object:
    """Route a result to the request's own approval thread, else the owner fallback.

    라우팅·폴백·NOTIFY-THREAD-FAIL 의미는 공유 구현
    `automation.interop.origin_notice.deliver`가 소유한다(2026-08-23 전 스킬 공통화).
    캘린더 내용(제목·시각·이벤트/캘린더 id)은 문구에도 스레드 이름에도 싣지 않는다 —
    SKILL.md 반출 금지 규칙에 따라 호출자가 draft id 만 담은 문구를 넘긴다.
    ``outcome``(OUTCOME_DONE/CANCELLED/EXPIRED)이 있으면 게시가 성공한 뒤 그 스레드를
    종결 표시한다. 비어 있거나 OUTCOME_FAILED 면, 또 다이제스트 항목(`digest_day`: 같은
    일별 스레드에 다른 카드가 남음)이면 열어 둔다. ``transport`` 는 호출자의 전송기 팩토리다.
    """
    try:
        origin_notice = _origin_notice()
    except ImportError as error:  # 낡은 interop 런타임/샌드박스 — 결과는 그래도 소유자에게 닿아야 한다
        print(
            f"NOTIFY-HELPER-MISSING draft={draft['id']} err={type(error).__name__}",
            file=sys.stderr,
        )
        return send_owner_dm(owner_id(), content)
    message = import_module("calendar_result_message").build(draft, content, outcome)
    closing = "" if draft.get("digest_day") else outcome
    terminal = getattr(origin_notice.ThreadOutcome, closing.upper(), None) if closing else None
    if message is not None and getattr(origin_notice, "ACCEPTS_OWNER_MESSAGE", False):
        return origin_notice.deliver(
            api=_api, transport_factory=transport or _thread_transport, record=draft,
            thread_name=f"캘린더 확정 (draft {draft['id']})", content=content,
            fallback=lambda body: send_owner_dm(owner_id(), body), outcome=terminal,
            message=message, fallback_destination=None,
        )
    return origin_notice.deliver(
        api=_api, transport_factory=transport or _thread_transport, record=draft,
        thread_name=f"캘린더 확정 (draft {draft['id']})", content=content,
        fallback=lambda body: send_owner_dm(owner_id(), body), outcome=terminal,
    )


def confirmation_message_content(entry: PendingConfirm) -> str:
    """Return the exact posted confirmation content for hash binding."""
    channel_id = calendar_binding.channel_for_entry(entry)
    message = _api("GET", f"/channels/{channel_id}/messages/{entry.dm_message_id}")
    return _required_string(message, "content", "확정 DM 내용이 없습니다")


def confirmation_reaction_users(
    entry: PendingConfirm, emoji: str
) -> tuple[dict[str, str | bool], ...]:
    """Read reaction users; Discord's absent-reaction response is empty."""
    channel_id = calendar_binding.channel_for_entry(entry)
    try:
        users = _api(
            "GET",
            f"/channels/{channel_id}/messages/{entry.dm_message_id}/reactions/"
            f"{quote(emoji, safe='')}?limit=100",
        )
    except HTTPError as error:
        if error.code == 404:
            return ()
        raise
    if not isinstance(users, list):
        raise GateError("확정 반응 응답이 올바르지 않습니다", 1)
    return tuple(user for user in users if isinstance(user, dict))


def reject_cancel_reaction(draft: DraftRecord) -> None:
    """Fail closed when the bound confirmation DM carries the owner's ⛔ reaction."""
    entry = _pending_entry(str(draft["id"]), required=False)
    if entry is None:
        return
    _validate_pending_binding(draft, entry)
    if _reaction_action(entry, owner_id()) == CANCEL_EMOJI:
        raise GateError("취소 반응이 있어 실행하지 않습니다", 1)


def confirm_via_reaction(draft: DraftRecord) -> str:
    """Authorize only the owner's ✅ on the exact pending confirmation DM."""
    entry = _pending_entry(str(draft["id"]))
    if entry is None:
        raise GateError("반응 확인용 pending confirm이 없습니다", 1)
    _validate_pending_binding(draft, entry)
    action = _reaction_action(entry, owner_id())
    if action == CANCEL_EMOJI:
        raise GateError("취소 반응이 있어 실행하지 않습니다", 1)
    if action != APPROVE_EMOJI:
        raise GateError("소유자 확정 반응이 없습니다", 1)
    return f"reaction:{entry.dm_message_id}"


def clear_pending(draft_id: str) -> None:
    """Remove the sole finished confirmation entry while preserving concurrent appends."""
    entry = _pending_entry(draft_id, required=False)
    if entry is not None:
        PendingConfirmStore().remove_completed((entry,), ())


def _change_summary(draft: DraftRecord) -> str:
    return calendar_core.render_change_summary(
        action=str(draft["action"]), summary=str(draft["summary"]), start=str(draft["start"]),
        end=str(draft["end"]), calendar_id=str(draft["calendar_id"]), event_id=str(draft["event_id"]),
    )


def _required_string(value: Any, key: str, message: str) -> str:
    if type(value) is not dict or not isinstance(result := value.get(key), str) or not result:
        raise GateError(message, 3)
    return result
