"""대리 요청 안전망 — 대리 글이 ✅ 영수증만 받고 조용히 사라지지 않는다.

2026-10-10 실측: 출처 검증에 떨어진 일정 등록 대리 요청이 게이트웨이 ✅ 만 받고 아무 답도 없었다.
거부되면 즉시, 받았는데 기한 안에 이 봇의 글이 없으면 기한 뒤에 그 대리 글의 스레드에 글이 남아야 한다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import HTTPError

import pytest

from automation.interop import owner_proxy, owner_proxy_watch
from tests.unit.test_owner_proxy import (
    AGENT_CHAT, GUILD, ORIGIN_CHANNEL, OTHER_BOT, OWNER, PROXY_BOT,
    proxy_text, reader_with, snowflake,
)

ME = "100000000000000077"
PROXY_MESSAGE = "100000000000000099"


@dataclass
class FakeDiscord:
    """REST 호출과 전송을 기록한다. GET 응답은 경로 앞부분으로 고른다(없으면 404)."""

    pages: dict[str, list[dict[str, object]]] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)
    sent: list[tuple[str, str]] = field(default_factory=list)

    def api(self, method: str, path: str, payload: object = None) -> object:
        del payload
        self.calls.append((method, path))
        if method == "POST" and path.endswith("/threads"):
            return {"id": path.split("/")[-2]}
        for prefix, page in self.pages.items():
            if path.startswith(prefix):
                return page
        raise HTTPError(path, 404, "Not Found", None, None)  # type: ignore[arg-type]

    def send(self, channel_id: str, body: str) -> None:
        self.sent.append((channel_id, body))

    def runtime(self) -> owner_proxy_watch.Runtime:
        return owner_proxy_watch.Runtime(api=self.api, send=self.send, me=lambda: ME)


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, object]:
    """게이트웨이 주 프로세스처럼 무장하되 타이머·스레드는 즉시·동기로 돌린다."""
    payload = {
        "agent_id": "agent-under-test", "agents_log_channel_id": "1", "owner_id": OWNER,
        "agent_chat_channel_id": AGENT_CHAT, "owner_proxy_bot_id": PROXY_BOT,
        "owner_proxy_origin_channel_id": ORIGIN_CHANNEL,
    }
    discord = FakeDiscord()
    armed: list[owner_proxy_watch.Pending] = []
    state: dict[str, object] = {"discord": discord, "armed": armed}
    state["payload"] = payload
    state["ledger"] = tmp_path / "ledger.json"
    monkeypatch.setattr(owner_proxy, "DiscordOriginReader", lambda token: state["reader"])
    monkeypatch.setattr(owner_proxy_watch, "PENDING_PATH", tmp_path / "pending.json")
    monkeypatch.setattr(owner_proxy_watch, "active", lambda: True)
    monkeypatch.setattr(owner_proxy_watch, "live_runtime", discord.runtime)
    monkeypatch.setattr(owner_proxy_watch, "_spawn", lambda work: work())
    monkeypatch.setattr(owner_proxy_watch, "_arm", armed.append)
    return state


def _dispatch(gateway: dict[str, object], text: str, author: str = PROXY_BOT) -> object:
    from types import SimpleNamespace

    event = SimpleNamespace(text=text, message_id=PROXY_MESSAGE, source=SimpleNamespace(
        user_id=author, chat_id=AGENT_CHAT, thread_id=None, guild_id=GUILD, is_bot=True,
    ))
    result = owner_proxy.gateway_text(event, text, author, lambda: gateway["payload"], gateway["ledger"])
    return {"action": "rewrite", "text": result} if result is not None else {
        "action": "skip", "reason": "interop_bot_prose",
    }


def _settle(gateway: dict[str, object]) -> list[str]:
    discord: FakeDiscord = gateway["discord"]  # type: ignore[assignment]
    store = owner_proxy_watch.PendingStore(owner_proxy_watch.PENDING_PATH)
    return [
        owner_proxy_watch.settle(store, item.message_id, api=discord.api, me=lambda: ME, send=discord.send)
        for item in gateway["armed"]  # type: ignore[union-attr]
    ]


def test_a_rejected_proxy_request_leaves_a_reason_in_its_thread(gateway: dict[str, object]) -> None:
    origin = snowflake(time.time() - 60)
    gateway["reader"] = reader_with(origin)

    assert _dispatch(gateway, proxy_text(GUILD, ORIGIN_CHANNEL, origin, body="")) == {"action": "skip", "reason": "interop_bot_prose"}

    discord: FakeDiscord = gateway["discord"]  # type: ignore[assignment]
    assert ("POST", f"/channels/{AGENT_CHAT}/messages/{PROXY_MESSAGE}/threads") in discord.calls
    assert len(discord.sent) == 1
    target, body = discord.sent[0]
    assert target == PROXY_MESSAGE
    assert "처리하지 않았습니다" in body and "`empty_body`" in body


def test_a_proxy_bot_request_with_a_failed_origin_is_watched_too(gateway: dict[str, object]) -> None:
    origin = snowflake(time.time() - 60)
    gateway["reader"] = reader_with(origin, author=OTHER_BOT, bot=True)

    assert _dispatch(gateway, proxy_text(GUILD, ORIGIN_CHANNEL, origin))["action"] == "rewrite"  # type: ignore[index]

    assert [item.via for item in gateway["armed"]] == ["dori"]  # type: ignore[union-attr]
    assert gateway["discord"].sent == []  # type: ignore[union-attr]
    assert _settle(gateway) == ["notified"]


def test_prose_that_is_not_a_proxy_request_gets_no_notice(gateway: dict[str, object]) -> None:
    gateway["reader"] = reader_with(snowflake(time.time() - 60))

    assert _dispatch(gateway, "안녕하세요") == {"action": "skip", "reason": "interop_bot_prose"}
    assert _dispatch(gateway, "안녕하세요", author=OTHER_BOT) == {"action": "skip", "reason": "interop_bot_prose"}
    assert gateway["discord"].sent == []  # type: ignore[union-attr]


def test_an_accepted_request_with_no_answer_is_reported_after_the_deadline(gateway: dict[str, object]) -> None:
    origin = snowflake(time.time() - 60)
    gateway["reader"] = reader_with(origin)

    assert _dispatch(gateway, proxy_text(GUILD, ORIGIN_CHANNEL, origin))["action"] == "rewrite"  # type: ignore[index]
    armed: list[owner_proxy_watch.Pending] = gateway["armed"]  # type: ignore[assignment]
    assert [(item.message_id, item.via) for item in armed] == [(PROXY_MESSAGE, "owner")]
    assert armed[0].due_at - time.time() == pytest.approx(owner_proxy_watch.DEADLINE_SECONDS, abs=5)

    assert _settle(gateway) == ["notified"]
    target, body = gateway["discord"].sent[0]  # type: ignore[union-attr]
    assert target == PROXY_MESSAGE and "응답(답·승인 카드·질문)이 없습니다" in body
    assert _settle(gateway) == ["claimed"], "같은 대기는 한 번만 통지한다"


@pytest.mark.parametrize(
    "pages",
    [
        {f"/channels/{PROXY_MESSAGE}/messages": [{"author": {"id": ME}}]},
        {f"/channels/{AGENT_CHAT}/messages?after={PROXY_MESSAGE}": [
            {"author": {"id": ME}, "message_reference": {"message_id": PROXY_MESSAGE}}]},
    ],
)
def test_an_answered_request_is_not_reported(gateway: dict[str, object], pages: dict[str, list[dict[str, object]]]) -> None:
    origin = snowflake(time.time() - 60)
    gateway["reader"] = reader_with(origin)
    gateway["discord"].pages.update(pages)  # type: ignore[union-attr]

    assert _dispatch(gateway, proxy_text(GUILD, ORIGIN_CHANNEL, origin))["action"] == "rewrite"  # type: ignore[index]

    assert _settle(gateway) == ["answered"]
    assert gateway["discord"].sent == []  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "pages",
    [
        {f"/channels/{PROXY_MESSAGE}/messages": [{"author": {"id": PROXY_BOT}}]},
        {f"/channels/{AGENT_CHAT}/messages?after={PROXY_MESSAGE}": [
            {"author": {"id": ME}, "message_reference": {"message_id": "100000000000000098"}}]},
    ],
)
def test_other_messages_are_not_an_answer(gateway: dict[str, object], pages: dict[str, list[dict[str, object]]]) -> None:
    origin = snowflake(time.time() - 60)
    gateway["reader"] = reader_with(origin)
    gateway["discord"].pages.update(pages)  # type: ignore[union-attr]

    assert _dispatch(gateway, proxy_text(GUILD, ORIGIN_CHANNEL, origin))["action"] == "rewrite"  # type: ignore[index]

    assert _settle(gateway) == ["notified"]


def test_pending_requests_survive_a_restart(gateway: dict[str, object]) -> None:
    origin = snowflake(time.time() - 60)
    gateway["reader"] = reader_with(origin)
    assert _dispatch(gateway, proxy_text(GUILD, ORIGIN_CHANNEL, origin))["action"] == "rewrite"  # type: ignore[index]
    armed: list[owner_proxy_watch.Pending] = gateway["armed"]  # type: ignore[assignment]
    armed.clear()  # 재시동으로 메모리 안의 타이머가 사라졌다

    assert owner_proxy_watch.resume() == 1
    assert [item.message_id for item in armed] == [PROXY_MESSAGE]


def test_nothing_is_armed_outside_the_gateway_process(gateway: dict[str, object], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(owner_proxy_watch, "active", lambda: False)
    origin = snowflake(time.time() - 60)
    gateway["reader"] = reader_with(origin)

    assert _dispatch(gateway, proxy_text(GUILD, ORIGIN_CHANNEL, origin))["action"] == "rewrite"  # type: ignore[index]

    assert gateway["armed"] == []
    assert owner_proxy_watch.resume() == 0
    assert not owner_proxy_watch.PENDING_PATH.exists()


def test_the_gateway_process_check_matches_the_generation_record() -> None:
    from automation.gateway_generation import _is_gateway_process

    assert _is_gateway_process(["hermes", "gateway", "run"], {"SYSTEMD_EXEC_PID": "7"}, 7)
    assert not _is_gateway_process(["python", "-m", "pytest"], {}, 7)
    assert owner_proxy_watch.active() is False
