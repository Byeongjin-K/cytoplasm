"""Owner-approval transports for the mail triage gate (W4-2, 제약 1)."""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
import time
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from triage_gate import GateError, write_json

API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://github.com/orientpine/autophagy-agents, 0)"
APPROVE_EMOJI = "\u2705"  # ✅
CANCEL_EMOJI = "\u26d4"
ENV_SECRETS = Path.home() / ".env.secrets"


def confirm_text(draft: dict) -> str:
    return f"APPROVE mail-triage:{draft['id']} sha256:{draft['sha256']} msg:{draft['message_id']}"


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
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if token:
        return token
    try:
        for line in ENV_SECRETS.read_text(encoding="utf-8").splitlines():
            if line.startswith("DISCORD_BOT_TOKEN="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    raise GateError("DISCORD_BOT_TOKEN 없음 — 프로덕션 확인 경로 사용 불가", 3)


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


def _send(request: Request) -> object:
    with urlopen(request, timeout=30) as response:  # noqa: S310
        body = response.read().decode("utf-8")
    return json.loads(body) if body else None


def _api(method: str, path: str, payload: dict | None = None) -> object:
    request = Request(
        f"{API}{path}",
        data=None if payload is None else json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bot {bot_token()}",
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


def fetch_channel(channel_id: str) -> object:
    return _api("GET", f"/channels/{channel_id}")


def post_approval_request(content: str, channel_id: str, *, reply_to: str = "") -> str:
    payload: dict[str, object] = {"content": content}
    if reply_to:
        payload["message_reference"] = {"message_id": reply_to, "fail_if_not_exists": False}
    return str(_api("POST", f"/channels/{channel_id}/messages", payload)["id"])


def edit_message(channel_id: str, message_id: str, content: str) -> None:
    _api("PATCH", f"/channels/{channel_id}/messages/{message_id}", {"content": content})


class _CardReply:
    """A result posted as a reply under its own approval card (digest thread, owner Q187)."""

    def __init__(self, channel_id: str, card_id: str) -> None:
        self.channel_id, self.card_id = channel_id, card_id

    def send(self, body: str) -> tuple[object, ...]:
        from automation.interop.chunker import chunk_message

        return tuple(
            SimpleNamespace(message_id=post_approval_request(chunk, self.channel_id, reply_to=self.card_id))
            for chunk in chunk_message(body)
        )


def add_reaction(message_id: str, emoji: str, channel_id: str) -> None:
    _api(
        "PUT",
        f"/channels/{channel_id}/messages/{message_id}/reactions/{quote(emoji, safe='')}/@me",
    )


def delete_message(message_id: str, channel_id: str) -> None:
    _api("DELETE", f"/channels/{channel_id}/messages/{message_id}")


def _interop_runtime() -> Path:
    configured = os.environ.get("INTEROP_RUNTIME")
    if configured:
        return Path(configured).expanduser()
    root = Path("~/.hermes/autophagy-import").expanduser()
    if (root / "automation" / "interop").is_dir():
        return root
    return Path("~/.hermes/interop_runtime").expanduser()


def _dm_transport(channel_id: str):
    runtime = _interop_runtime()
    sys.path.insert(0, str(runtime))
    from automation.interop.discord_transport import DiscordTransport  # noqa: PLC0415

    return DiscordTransport(token=bot_token(), channel_id=channel_id)


def dm_owner(content: str) -> str:
    """오너 통지 1건 — 대화 채널 우선(2026-08-24 소유자 지시), 미설정 시 기존 폴백 경로.

    다이제스트 본문과 결과 통지 폴백이 이 경로를 탄다. 통지는 도달이 우선이므로
    (승인 게이트와 달리) 채널 해석 실패는 조용히 기존 폴백 경로로 내려간다.
    """
    sent = _dm_transport(_owner_channel()).send(content)
    return sent[-1].message_id


def _owner_channel() -> str:
    import triage_approval

    directory = triage_approval.approval_directory()
    try:
        return directory.agent_chat()
    except Exception:  # noqa: BLE001 — 통지는 도달 우선: 미설정/해석 실패는 DM 폴백
        return directory.owner_dm()


def digest_thread(header_message_id: str, name: str) -> str:
    """Thread hung on the digest header (shared origin_notice helper), or '' to post flat.

    Best-effort: a DM channel cannot hold threads and an old interop runtime may lack
    the helper — the mail messages then follow the header in the same channel.
    """
    if not header_message_id:
        return ""
    try:
        origin_notice = _origin_notice()
        origin = origin_notice.OriginRef(channel_id=_owner_channel(), message_id=header_message_id)
        return origin_notice.resolve_thread_id(_api, origin, name)
    except Exception as error:  # noqa: BLE001 — 스레드는 편의, 도달이 우선
        print(f"DIGEST-THREAD-FAIL err={type(error).__name__}", file=sys.stderr)
        return ""


def post_in(channel_id: str, content: str) -> str:
    return _dm_transport(channel_id).send(content)[-1].message_id


def _origin_notice():
    runtime = _interop_runtime()
    sys.path.insert(0, str(runtime))
    from automation.interop import origin_notice  # noqa: PLC0415

    return origin_notice


#: ``origin_notice.ThreadOutcome`` 멤버 이름 — 종결 통지만 스레드를 닫는다.
SENT_OUTCOME = "DONE"
CANCELLED_OUTCOME = "CANCELLED"
EXPIRED_OUTCOME = "EXPIRED"


def notify_result(draft: dict, content: str, *, outcome: str = "") -> str:
    """실행 후 종결 봉투(outcome 없는 중간 통지는 문자열). 라우팅·재렌더·종결·폴백은 deliver 소유."""
    from triage_result_notice import result_message

    try:
        origin_notice = _origin_notice()
    except ImportError as error:  # 낡은 interop 런타임/샌드박스 — 결과는 그래도 소유자에게 닿아야 한다
        print(
            f"NOTIFY-HELPER-MISSING draft={draft['id']} err={type(error).__name__}",
            file=sys.stderr,
        )
        return dm_owner(content)
    message = result_message(draft, content, outcome)
    transport = _dm_transport
    if _in_digest_thread(draft):  # other cards in the thread still wait — never close it
        outcome = ""
        card = str(draft.get("message_id") or "")
        transport = lambda channel: _CardReply(channel, card)  # noqa: E731
    if message is not None and getattr(origin_notice, "ACCEPTS_OWNER_MESSAGE", False):
        return str(origin_notice.deliver(
            api=_api, transport_factory=transport, record=draft,
            thread_name=f"메일: {draft['subject']} (draft {draft['id']})",
            content=content, fallback=dm_owner,
            outcome=origin_notice.ThreadOutcome[outcome] if outcome else None,
            message=message, fallback_destination=None,
        ))
    return str(origin_notice.deliver(
        api=_api,
        transport_factory=transport,
        record=draft,
        thread_name=f"메일: {draft['subject']} (draft {draft['id']})",
        content=content,
        fallback=dm_owner,
        outcome=origin_notice.ThreadOutcome[outcome] if outcome else None,
    ))


def _in_digest_thread(draft: dict) -> bool:
    try:
        import triage_digest_thread

        return triage_digest_thread.is_digest_draft(draft)
    except Exception as error:  # noqa: BLE001 — the result notice still goes out the usual way
        print(f"DIGEST-THREAD-CHECK-FAIL draft={draft.get('id')} err={type(error).__name__}", file=sys.stderr)
        return False


def _owner_reacted(users: list[dict], owner: str) -> bool:
    return any(
        str(user.get("id", "")) == owner and not bool(user.get("bot", False))
        for user in users
    )


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


def resolve_reaction(draft: dict) -> str | None:
    """Return the bound owner decision, with ⛔ taking precedence over ✅."""
    from triage_approval_reaction import resolve_reaction as resolve

    return resolve(draft)


def confirm_via_reaction(draft: dict) -> str:
    """Return a bound owner-only ✅ approval reference or reject the draft."""
    action = resolve_reaction(draft)
    if action == APPROVE_EMOJI:
        return f"reaction:{draft['message_id']}"
    if action == CANCEL_EMOJI:
        raise GateError(f"소유자의 {CANCEL_EMOJI} 리액션으로 취소됨 — 발송하지 않습니다", 1)
    raise GateError(f"소유자의 {APPROVE_EMOJI} 리액션 없음 — 발송하지 않습니다", 1)


def _adapter() -> object:
    runtime = _interop_runtime()
    sys.path.insert(0, str(runtime))
    try:
        from automation.interop import injection_adapter
    except ImportError:
        raise GateError(f"injection adapter 불가 (INTEROP_RUNTIME={runtime})", 3) from None
    return injection_adapter


def _require_e2e_secret() -> str:
    if os.environ.get("E2E_TEST_MODE") != "1":
        raise GateError("주입 경로는 E2E_TEST_MODE=1 전용입니다 (프로덕션 게이트웨이 금지)", 1)
    secret = os.environ.get("INTEROP_E2E_SECRET", "")
    if not secret:
        raise GateError("INTEROP_E2E_SECRET 누락", 1)
    return secret


def _persisted_injection_channel_id(draft: dict) -> str:
    import triage_binding

    channel_id = triage_binding.persisted_channel_id(draft)
    if channel_id is None:
        raise GateError("주입 승인에는 저장된 승인 바인딩이 필요함 — 거부", 1)
    return channel_id


def confirm_via_injection(draft: dict, injection_path: Path) -> str:
    secret = _require_e2e_secret()
    adapter = _adapter()
    envelope = json.loads(injection_path.read_text(encoding="utf-8"))
    event = adapter.InboundEvent(
        event_id=str(envelope["event"]["event_id"]),
        user_id=str(envelope["event"]["user_id"]),
        channel_id=str(envelope["event"]["channel_id"]),
        text=str(envelope["event"]["text"]),
    )
    if not adapter.accept_test_event(
        event, str(envelope["signature"]), secret.encode("utf-8"), e2e_test_mode=True
    ):
        raise GateError("주입 승인 서명 불일치 — 거부", 1)
    if event.user_id != owner_id():
        raise GateError("주입 승인 발신자가 소유자가 아님 — 거부", 1)
    if event.channel_id != _persisted_injection_channel_id(draft):
        raise GateError("주입 승인 대상이 저장된 바인딩과 다름 — 거부", 1)
    if event.text != confirm_text(draft):
        raise GateError("주입 승인 텍스트/해시 불일치 — 거부", 1)
    return f"injected:{event.event_id}"


def sign_injection(
    draft: dict, out_path: Path, user_id: str | None, channel_id: str | None, forge_signature: bool
) -> None:
    secret = _require_e2e_secret()
    adapter = _adapter()
    persisted_channel_id = _persisted_injection_channel_id(draft)
    if channel_id not in (None, "", persisted_channel_id):
        raise GateError("주입 승인 대상이 저장된 바인딩과 다름 — 거부", 1)
    event = adapter.InboundEvent(
        event_id=str(uuid.uuid4()),
        user_id=user_id or owner_id(),
        channel_id=persisted_channel_id,
        text=confirm_text(draft),
    )
    signature = "0" * 64 if forge_signature else adapter.sign_event(event, secret.encode("utf-8"))
    write_json(
        out_path,
        {
            "event": {
                "event_id": event.event_id,
                "user_id": event.user_id,
                "channel_id": event.channel_id,
                "text": event.text,
            },
            "signature": signature,
        },
    )
