"""대리 요청 안전망 — 대리 봇이 옮겨 쓴 소유자 요청이 조용히 사라지지 않게 한다.

게이트웨이는 대리 글을 받든 버리든 처리 완료 콜백에서 그 글에 ✅ 영수증을 단다. 그래서
✅ 만으로는 요청이 처리됐는지 알 수 없다(2026-10-10: 출처 검증에 떨어진 일정 등록 요청이
✅ 만 받고 아무 답 없이 사라졌다). 이 모듈은 두 경우에 그 대리 글의 스레드에 글 하나를 남긴다.

- **거부**: 형식은 대리 요청인데 `owner_proxy.verify` 가 받지 않았으면 즉시 사유를 남긴다.
- **무응답**: 받은 요청에 `DEADLINE_SECONDS` 안에 이 봇의 글(답·승인 카드·질문)이 그 스레드에
  없으면 '처리 확인 실패'를 남긴다. 대기 목록은 파일에 남으므로 재시동이 턴을 끊어도
  (2026-10-02 사례) 다음 기동에 다시 무장된다.

아무것도 실행하지 않는다 — 승인 게이트와 무관하게 글 하나를 남길 뿐이다. 무장은 게이트웨이
주 프로세스에서만 한다(CLI·웹 프로세스가 플러그인을 불러도 중복 통지가 생기지 않게).
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final
from urllib.error import HTTPError

from automation.interop import origin_notice

if TYPE_CHECKING:
    from automation.interop.owner_proxy import Accepted, ProxyMessage

LOGGER: Final = logging.getLogger("autophagy.interop")
DEADLINE_SECONDS: Final = 180
PENDING_PATH: Final = Path("~/.hermes/interop/owner-proxy-pending.json").expanduser()
THREAD_NAME: Final = "대리 요청 처리 확인"
#: 이 사유들은 대리 요청이 아니다(다른 봇·다른 채널·형식 없음) — 답하면 봇끼리 대화가 열린다.
NOT_A_PROXY_REQUEST: Final = frozenset({"not_proxy_bot", "outside_agent_chat", "no_prefix"})
_REASONS: Final = {
    "origin_author": "원문 작성자가 소유자(또는 등록된 소유자 웹훅)로 확인되지 않음",
    "origin_reused": "이미 한 번 받은 원문이라 다시 처리하지 않음(정정은 새 원문으로)",
    "origin_stale": "원문이 24시간보다 오래됨",
    "origin_channel": "원문이 소유자 원문 채널에 있지 않음",
    "origin_unreadable": "원문을 읽지 못함",
    "origin_mismatch": "링크와 원문 정보가 맞지 않음",
    "guild_mismatch": "원문이 다른 서버에 있음",
    "link_count": "원문 링크가 정확히 하나가 아님",
    "empty_body": "본문이 비어 있음",
    "ledger_unavailable": "중복 확인 원장을 읽지 못함",
}

Api = Callable[..., object]
Send = Callable[[str, str], object]


@dataclass(frozen=True, slots=True)
class Pending:
    """받은 대리 글 하나. ``in_thread`` 면 대리 글이 이미 스레드 안에 있어 그 스레드가 답 자리다."""

    message_id: str
    channel_id: str
    in_thread: bool
    via: str
    due_at: float


def rejected_text(reason: str) -> str:
    why = _REASONS.get(reason, "출처 검증 실패")
    return (
        f"⚠️ 이 대리 요청은 처리하지 않았습니다 — {why} (사유 코드 `{reason}`).\n"
        "이 요청으로 실행된 것은 없습니다. 소유자가 직접 요청하거나 대리 봇이 확인해 다시 보내 주세요."
    )


def silent_text() -> str:
    return (
        f"⚠️ 이 대리 요청에 {DEADLINE_SECONDS // 60}분 동안 응답(답·승인 카드·질문)이 없습니다 — "
        "처리가 끊겼을 수 있습니다.\n"
        "승인 카드가 없었으므로 실행된 외부효과는 없습니다. 다시 보내거나 소유자가 직접 요청해 주세요."
    )


@dataclass(frozen=True, slots=True)
class PendingStore:
    """``{message_id: Pending}`` JSON — flock 아래 원자 교체한다. 꺼내는 쪽만 통지한다."""

    path: Path

    def add(self, item: Pending) -> None:
        with self._locked() as data:
            data[item.message_id] = asdict(item)

    def claim(self, message_id: str) -> Pending | None:
        with self._locked() as data:
            raw = data.pop(message_id, None)
        return _pending(raw)

    def items(self) -> list[Pending]:
        with self._locked() as data:
            return [item for item in map(_pending, data.values()) if item is not None]

    @contextmanager
    def _locked(self) -> Iterator[dict[str, object]]:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.path.with_suffix(".lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                loaded = {}
            data: dict[str, object] = dict(loaded) if isinstance(loaded, dict) else {}
            before = json.dumps(data, sort_keys=True)
            yield data
            if json.dumps(data, sort_keys=True) != before:
                temporary = self.path.with_suffix(".tmp")
                temporary.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
                os.chmod(temporary, 0o600)
                os.replace(temporary, self.path)


def _pending(raw: object) -> Pending | None:
    if not isinstance(raw, Mapping):
        return None
    try:
        return Pending(
            message_id=str(raw["message_id"]), channel_id=str(raw["channel_id"]),
            in_thread=bool(raw["in_thread"]), via=str(raw["via"]), due_at=float(raw["due_at"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def answered(api: Api, me: str, item: Pending) -> bool:
    """이 봇이 그 대리 글의 스레드(또는 그 글에 단 답글)에 글을 남겼는가."""
    if item.in_thread:
        probes = ((f"/channels/{item.channel_id}/messages?after={item.message_id}&limit=50", False),)
    else:
        probes = (
            (f"/channels/{item.message_id}/messages?limit=50", False),
            (f"/channels/{item.channel_id}/messages?after={item.message_id}&limit=50", True),
        )
    for path, needs_reference in probes:
        try:
            messages = api("GET", path)
        except HTTPError as error:
            if error.code == 404:  # 스레드가 아직 없다 = 그 스레드에 글도 없다
                continue
            raise
        for message in messages if isinstance(messages, list) else ():
            if not isinstance(message, Mapping) or str((message.get("author") or {}).get("id")) != me:
                continue
            reference = message.get("message_reference") or {}
            if not needs_reference or str(reference.get("message_id")) == item.message_id:
                return True
    return False


def post(api: Api, send: Send, *, channel_id: str, message_id: str, in_thread: bool, text: str) -> None:
    """대리 글의 스레드에 남긴다 — 스레드 해석은 결과 통지와 같은 공용 구현을 쓴다."""
    target = channel_id if in_thread else origin_notice.resolve_thread_id(
        api, origin_notice.OriginRef(channel_id=channel_id, message_id=message_id), THREAD_NAME
    )
    send(target, text)


def settle(store: PendingStore, message_id: str, *, api: Api, me: Callable[[], str], send: Send) -> str:
    """기한이 된 대기 하나를 꺼내 답이 없으면 통지한다. 확인할 수 없으면 알리는 쪽으로 닫는다."""
    item = store.claim(message_id)
    if item is None:
        return "claimed"
    try:
        if answered(api, me(), item):
            return "answered"
    except Exception as error:  # noqa: BLE001 — 확인 실패는 무응답과 같이 다룬다(조용한 누락 금지)
        LOGGER.warning("interop owner proxy watch check failed error=%s", type(error).__name__)
    post(api, send, channel_id=item.channel_id, message_id=item.message_id, in_thread=item.in_thread, text=silent_text())
    return "notified"


@dataclass(frozen=True, slots=True)
class Runtime:
    api: Api
    send: Send
    me: Callable[[], str]


def live_runtime() -> Runtime:
    from automation.notice_bot import resolve_token

    from automation.interop.discord_transport import DiscordTransport
    from automation.interop.reaction_approval import DiscordTransport as RestApi

    token = resolve_token()
    rest = RestApi(token, "")
    return Runtime(
        api=rest.api,
        send=lambda channel, body: DiscordTransport(token=token, channel_id=channel).send(body),
        me=lambda: str(rest.api("GET", "/users/@me")["id"]),  # type: ignore[index]
    )


def active() -> bool:
    """게이트웨이 주 프로세스에서만 무장한다(세대 기록과 같은 판정)."""
    from automation.gateway_generation import _is_gateway_process

    return _is_gateway_process(sys.argv, os.environ, os.getpid())


def on_rejected(message: ProxyMessage, reason: str) -> None:
    """거부된 대리 요청 — 즉시 사유를 남긴다. 예외를 올리지 않는다."""
    if reason in NOT_A_PROXY_REQUEST or not message.message_id or not active():
        return
    message_id = message.message_id

    def notify() -> None:
        try:
            runtime = live_runtime()
            post(runtime.api, runtime.send, channel_id=message.channel_id, message_id=message_id,
                 in_thread=message.parent_channel_id is not None, text=rejected_text(reason))
            LOGGER.warning("interop owner proxy watch outcome=rejected-notified reason=%s", reason)
        except Exception as error:  # noqa: BLE001 — 통지 실패가 게이트웨이를 멈추지 않는다
            LOGGER.warning("interop owner proxy watch notice failed error=%s", type(error).__name__)

    _spawn(notify)


def on_accepted(message: ProxyMessage, accepted: Accepted, now: float | None = None) -> None:
    """받은 대리 요청 — 기한을 기록하고 타이머를 건다. 예외를 올리지 않는다."""
    if not message.message_id or not active():
        return
    item = Pending(
        message_id=message.message_id, channel_id=message.channel_id,
        in_thread=message.parent_channel_id is not None, via=accepted.via,
        due_at=(time.time() if now is None else now) + DEADLINE_SECONDS,
    )
    try:
        PendingStore(PENDING_PATH).add(item)
    except Exception as error:  # noqa: BLE001
        LOGGER.warning("interop owner proxy watch store failed error=%s", type(error).__name__)
        return
    _arm(item)


def resume() -> int:
    """기동 때 남은 대기를 다시 무장한다(이미 지난 기한은 곧바로 확인). 무장한 수를 돌려준다."""
    try:
        if not active():
            return 0
        items = PendingStore(PENDING_PATH).items()
    except Exception as error:  # noqa: BLE001
        LOGGER.warning("interop owner proxy watch resume failed error=%s", type(error).__name__)
        return 0
    for item in items:
        _arm(item)
    return len(items)


def _spawn(work: Callable[[], None]) -> None:
    threading.Thread(target=work, daemon=True).start()


def _arm(item: Pending) -> None:
    timer = threading.Timer(max(0.0, item.due_at - time.time()), _fire, args=(item.message_id,))
    timer.daemon = True
    timer.start()


def _fire(message_id: str) -> None:
    try:
        runtime = live_runtime()
        outcome = settle(PendingStore(PENDING_PATH), message_id, api=runtime.api, me=runtime.me, send=runtime.send)
        LOGGER.warning("interop owner proxy watch outcome=%s", outcome)
    except Exception as error:  # noqa: BLE001
        LOGGER.warning("interop owner proxy watch failed error=%s", type(error).__name__)
