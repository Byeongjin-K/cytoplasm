"""Draft → owner-confirm → save gate for the personal wiki (W2-2, constraint 1).

NOTHING is ever written into WIKI_ROOT before an owner confirmation:
- production: cha's own DM reply ``저장 <draft-id>`` is independently verified
  through Discord REST (author must equal the interop owner_id, bots rejected);
- unattended E2E: an HMAC-signed injected event (W1-6 injection adapter) under
  ``E2E_TEST_MODE=1``. The production gateway refuses that env at boot (W1-6).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
import time
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen
from wiki_confirm_legacy import _owner_dm_surface as _owner_dm_surface, confirm_v1 as _confirm_v1


GATE_DIR = Path(os.environ.get("WIKI_GATE_DIR", "~/.hermes/wiki-gate")).expanduser()
INTEROP_CONFIG = Path(
    os.environ.get("INTEROP_CONFIG", "~/.hermes/interop/config.json")
).expanduser()
INTEROP_RUNTIME = Path(
    os.environ.get("INTEROP_RUNTIME", "~/.hermes/interop_runtime")
).expanduser()
API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://github.com/orientpine/autophagy-agents, 0)"
DM_SCAN_LIMIT = 50
APPROVE_EMOJI = "\u2705"
CANCEL_EMOJI = "\u26d4"
CONFIRM_MAX_CHARS = 1900  # Discord 2000자 상한 아래의 안전 여유


def _wiki_root() -> Path:
    return Path(os.environ.get("WIKI_ROOT", "~/wiki")).expanduser()


class GateError(RuntimeError):
    """Gate refusal with a CLI exit code (1 unconfirmed, 2 schema, 3 config)."""

    def __init__(self, message: str, exit_code: int = 3) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def confirm_text(draft: dict, *, surface: str | None = None) -> str:
    """저장된 판본만 재생한다. 누락 판본은 동결된 레거시다."""
    version = draft.get("render_version", 1)
    match version:
        case 1:
            return _confirm_v1(draft, surface=surface)
        case 2:
            pass
        case _:  # 저장 레코드 경계: 알 수 없는 판본을 해석하지 않는다.
            raise GateError("알 수 없는 승인 렌더 판본 — 거부", 3)
    try:
        from automation.interop.owner_message import Action, Approval, OwnerMessage, OwnerMessageError, Ref, render
    except ImportError as error:
        raise GateError("승인 봉투 모듈 불가 — 거부", 3) from error
    here = Ref(scope="self")
    message = OwnerMessage(
        subject_key=draft["id"], subject=draft["id"], fact=f"sha256:{draft['sha256']}",
        location=here, owner=Action("react", here, "✅ 실행 / ⛔ 취소"),
        agent_next="승인 시 저장", recovery="not_applicable",
        detail=Approval(None, "저장하지 않음"),
    )
    try:
        return render(message, destination=here)
    except OwnerMessageError as error:
        raise GateError("승인 봉투 렌더 불가 — 거부", 3) from error


def post_confirm_message(draft: dict) -> dict:
    """Keep EXACTLY ONE live confirm message per approval key — a stored
    ``confirm_message_id`` is never replaced, only superseded (delete BEFORE drop)."""
    import wiki_approval  # deferred: wiki_approval imports this module
    import wiki_binding

    bound = bool(_confirm_message_id(draft))
    prepared = draft if bound else {**draft, "render_version": 2}
    content = None
    if not bound:
        try:
            content = confirm_text(prepared)
        except GateError:
            prepared = {**draft, "render_version": 1}
            content = confirm_text(prepared)
    facade = wiki_approval.lifecycle()
    binding = wiki_binding.stored_binding(prepared)
    verdict = facade.request_owner_approval(
        wiki_approval.confirm_intent(prepared, binding),
        wiki_approval.WikiApprovalGate(draft=prepared, binding=binding, content=content),
        wiki_approval.confirm_lease(),
        wiki_approval.posting_journal(),
    )
    return wiki_approval.apply_verdict(verdict, draft)


def _add_reaction(channel_id: str, message_id: str, emoji: str) -> None:
    _api(
        "PUT",
        f"/channels/{channel_id}/messages/{message_id}/reactions/{quote(emoji, safe='')}/@me",
    )


def owner_id() -> str:
    try:
        owner = json.loads(INTEROP_CONFIG.read_text(encoding="utf-8")).get("owner_id")
    except OSError:
        raise GateError(f"interop config 읽기 실패: {INTEROP_CONFIG}", 3) from None
    if not isinstance(owner, str) or not owner:
        raise GateError("interop config에 owner_id가 없습니다", 3)
    return owner


def _adapter() -> Any:
    sys.path.insert(0, str(INTEROP_RUNTIME))
    try:
        from automation.interop import injection_adapter
    except ImportError:
        raise GateError(f"injection adapter 불가 (INTEROP_RUNTIME={INTEROP_RUNTIME})", 3) from None
    return injection_adapter


#: 배포 게이트(`automation/skill_gate.py`)와 같은 값. 소유자의 결정을 읽는 경로는
#: 429 를 존중해야 한다 — 2026-08-03 실측으로 이 게이트가 429 에 죽으면서 소유자가
#: 이미 누른 ⛔ 4건이 반영되지 않은 채 "아직 안 누름"과 구분되지 않았다.
_RATE_LIMIT_ATTEMPTS = 5
_RATE_LIMIT_FALLBACK_SECONDS = 1.0


def _retry_after(error: HTTPError) -> float:
    """Discord 가 요청한 대기 시간. 값이 없거나 읽을 수 없어도 백오프는 한다."""
    value = error.headers.get("Retry-After") if error.headers is not None else None
    try:
        return max(float(value), 0.0) if value is not None else _RATE_LIMIT_FALLBACK_SECONDS
    except (TypeError, ValueError):
        return _RATE_LIMIT_FALLBACK_SECONDS


def _send(request: Request) -> Any:
    with urlopen(request, timeout=30) as response:  # noqa: S310
        body = response.read().decode("utf-8")
    return json.loads(body) if body else None


def _api(method: str, path: str, payload: dict | None = None) -> Any:
    import wiki_binding

    wiki_binding._repo_module("approval_directory")
    from automation.notice_bot import resolve_token

    token = resolve_token()
    if not token:
        raise GateError("DISCORD_BOT_TOKEN 누락 — 프로덕션 확인 경로 사용 불가", 3)
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
    for _ in range(_RATE_LIMIT_ATTEMPTS - 1):
        try:
            return _send(request)
        except HTTPError as error:
            if error.code != 429:
                raise
            time.sleep(_retry_after(error))
    return _send(request)  # 마지막 시도는 실패해도 그대로 올린다


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def confirm_via_owner_scan(draft: dict) -> str:
    owner = owner_id()
    import wiki_binding

    channel_id = wiki_binding.approval_directory().owner_dm()
    messages = _api("GET", f"/channels/{channel_id}/messages?limit={DM_SCAN_LIMIT}")
    accepted = {f"저장 {draft['id']}", confirm_text(draft)}
    for message in messages:
        author = message.get("author", {})
        if str(author.get("id", "")) != owner or bool(author.get("bot", False)):
            continue
        if str(message.get("content", "")).strip() not in accepted:
            continue
        if _parse_ts(str(message["timestamp"])) < _parse_ts(draft["created"]):
            continue
        return f"dm:{message['id']}"
    raise GateError(
        f"소유자의 '저장 {draft['id']}' DM 확인을 찾지 못함 — 저장하지 않습니다", 1
    )


def resolve_reaction(draft: dict) -> Path | None:
    """Resolve one bound owner reaction; missing/unreacted messages stay pending."""
    channel_id = _draft_channel_id(draft)
    message_id = _confirm_message_id(draft)
    if not message_id:
        return None
    _verify_draft_hash(draft)
    message = _confirm_message(channel_id, message_id)
    if message is None:
        return None
    _verify_message_binding(message, channel_id, draft["sha256"])
    owner = owner_id()
    if _owner_reacted(_reaction_users(channel_id, message_id, CANCEL_EMOJI), owner):
        discard_draft(draft["id"])
        raise GateError(f"소유자의 {CANCEL_EMOJI} 리액션으로 취소됨 — 저장하지 않습니다", 1)
    if _owner_reacted(_reaction_users(channel_id, message_id, APPROVE_EMOJI), owner):
        return apply_draft(_wiki_root(), draft, f"reaction:{message_id}", "reaction")
    return None


def _draft_channel_id(draft: dict) -> str:
    import wiki_binding

    return wiki_binding.stored_binding(draft).channel_id


def _confirm_message_id(draft: dict) -> str:
    message_id = draft.get("confirm_message_id")
    if message_id is None:
        message_id = draft.get("message_id", "")
    if not isinstance(message_id, str):
        raise GateError("승인 메시지 id가 유효하지 않음 — 거부", 1)
    return message_id


def _verify_draft_hash(draft: dict) -> None:
    note_text = draft.get("note_text")
    digest = draft.get("sha256")
    if not isinstance(note_text, str) or not isinstance(digest, str):
        raise GateError("드래프트 해시 필드가 유효하지 않음 — 거부", 1)
    if hashlib.sha256(note_text.encode("utf-8")).hexdigest() != digest:
        raise GateError("드래프트 내용 해시 불일치 — 저장 중단", 1)


def _confirm_message(channel_id: str, message_id: str) -> dict | None:
    try:
        message = _api("GET", f"/channels/{channel_id}/messages/{message_id}")
    except HTTPError as error:
        if error.code == 404:
            return None
        raise
    if not isinstance(message, dict):
        raise GateError("승인 메시지 응답이 유효하지 않음 — 거부", 1)
    return message


def _verify_message_binding(message: dict, channel_id: str, digest: str) -> None:
    message_channel = message.get("channel_id")
    if message_channel is not None and str(message_channel) != channel_id:
        raise GateError("승인 메시지 채널 바인딩 불일치 — 거부", 1)
    if digest not in str(message.get("content", "")):
        raise GateError("승인 메시지가 이 드래프트 해시를 참조하지 않음 — 거부", 1)


def _owner_reacted(users: list[dict], owner: str) -> bool:
    return any(str(user.get("id", "")) == owner and not bool(user.get("bot", False)) for user in users)


def _reaction_users(channel_id: str, message_id: str, emoji: str) -> list[dict]:
    try:
        users = _api(
            "GET",
            f"/channels/{channel_id}/messages/{message_id}/reactions/{quote(emoji, safe='')}?limit=100",
        )
    except HTTPError as error:
        if error.code == 404:
            return []
        raise
    if not isinstance(users, list) or not all(isinstance(user, dict) for user in users):
        raise GateError("승인 리액션 응답이 유효하지 않음 — 거부", 1)
    return users


from wiki_drafts import (  # noqa: E402 - 기존 공개 파사드 보존
    _drafts_dir as _drafts_dir,
    _draft_path as _draft_path,
    create_draft as create_draft,
    load_draft as load_draft,
    discard_draft as discard_draft,
    list_drafts as list_drafts,
    apply_draft as apply_draft,
    _append_audit as _append_audit,
    _write_json as _write_json,
)

from wiki_injection import (  # noqa: E402 - 기존 공개 파사드 보존
    _require_e2e_secret as _require_e2e_secret,
    _persisted_injection_channel_id as _persisted_injection_channel_id,
    confirm_via_injection as confirm_via_injection,
    sign_injection as sign_injection,
)
