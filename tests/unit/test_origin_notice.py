"""RED-first contract for the shared origin-thread result-notice helper.

Owner instruction 2026-08-23: EVERY approval-gated skill routes its RESULT
notices (sent/cancelled/executed) back to the channel the instruction came
from, in a thread; the approval surface (owner DM) stays approval-only.
mail shipped the first implementation privately (triage_confirm.notify_result);
this module generalizes it so the other skills reuse ONE implementation
instead of growing per-skill copies.

The helper is injection-only: callers pass their own Discord api callable and
chunking transport factory, so each skill's existing test monkeypatch points
(`_api`, `_dm_transport`, `dm_owner`) keep working unchanged.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from automation.interop.origin_notice import (  # noqa: E402
    OriginRef,
    deliver,
    resolve_thread_id,
)

ORIGIN_CHANNEL = "200000000000000001"
ORIGIN_MESSAGE = "origin-message-1"
THREAD_ID = "300000000000000001"


def _http_error(code: int) -> HTTPError:
    return HTTPError("https://discord.test", code, "boom", None, None)


class _Transport:
    def __init__(self, channel_id: str, log: list[tuple[str, str]]) -> None:
        self.channel_id = channel_id
        self.log = log

    def send(self, body: str) -> tuple[SimpleNamespace, ...]:
        self.log.append((self.channel_id, body))
        return (SimpleNamespace(message_id="thread-post-1"),)


# ------------------------------------------------------------------ OriginRef

def test_of_record_reads_origin_fields_and_truthiness() -> None:
    # Given: records with and without the origin binding
    bound = OriginRef.of_record(
        {"origin_channel_id": ORIGIN_CHANNEL, "origin_message_id": ORIGIN_MESSAGE}
    )
    channel_only = OriginRef.of_record({"origin_channel_id": ORIGIN_CHANNEL})
    legacy = OriginRef.of_record({"id": "abc123"})
    null_fields = OriginRef.of_record({"origin_channel_id": None, "origin_message_id": None})
    # Then: the ref is truthy exactly when a channel is bound
    assert (bound.channel_id, bound.message_id) == (ORIGIN_CHANNEL, ORIGIN_MESSAGE)
    assert bool(bound) and bool(channel_only)
    assert not legacy and not null_fields


# ------------------------------------------------------------ resolve_thread_id

def test_resolve_creates_message_anchored_thread() -> None:
    # Given: an origin with the instruction message id
    calls: list[tuple[str, str, dict | None]] = []

    def api(method: str, path: str, payload: dict | None = None):
        calls.append((method, path, payload))
        return {"id": THREAD_ID}

    # When: the thread is resolved
    thread = resolve_thread_id(api, OriginRef(ORIGIN_CHANNEL, ORIGIN_MESSAGE), "스레드 이름")
    # Then: it is anchored on the instruction message
    assert thread == THREAD_ID
    assert calls == [
        (
            "POST",
            f"/channels/{ORIGIN_CHANNEL}/messages/{ORIGIN_MESSAGE}/threads",
            {"name": "🧵 스레드 이름"},
        ),
        # The new thread opens with its big title line (owner 2026-10-16).
        ("POST", f"/channels/{THREAD_ID}/messages", {"content": "## 🧵 스레드 이름"}),
    ]


def test_resolve_reuses_existing_thread_on_400() -> None:
    # Given: Discord refuses because the message already carries a thread
    def api(method: str, path: str, payload: dict | None = None):
        raise _http_error(400)

    # When/Then: the existing thread is reused — its id equals the message id
    thread = resolve_thread_id(api, OriginRef(ORIGIN_CHANNEL, ORIGIN_MESSAGE), "이름")
    assert thread == ORIGIN_MESSAGE


def test_resolve_raises_on_other_http_errors() -> None:
    # Given: a non-400 Discord failure (e.g. missing permission)
    def api(method: str, path: str, payload: dict | None = None):
        raise _http_error(403)

    # When/Then: the failure surfaces to the caller (deliver handles fallback)
    with pytest.raises(HTTPError):
        resolve_thread_id(api, OriginRef(ORIGIN_CHANNEL, ORIGIN_MESSAGE), "이름")


def test_resolve_creates_channel_thread_without_message() -> None:
    # Given: only the origin channel is known
    calls: list[tuple[str, str, dict | None]] = []

    def api(method: str, path: str, payload: dict | None = None):
        calls.append((method, path, payload))
        return {"id": THREAD_ID}

    # When: the thread is resolved
    thread = resolve_thread_id(api, OriginRef(ORIGIN_CHANNEL), "이름")
    # Then: a public thread is created in the channel itself
    assert thread == THREAD_ID
    [(method, path, payload), title] = calls
    assert (method, path) == ("POST", f"/channels/{ORIGIN_CHANNEL}/threads")
    assert payload is not None and payload["name"] == "🧵 이름" and payload["type"] == 11
    assert title == ("POST", f"/channels/{THREAD_ID}/messages", {"content": "## 🧵 이름"})


# ------------------------------------------------------------------- deliver

def test_deliver_falls_back_to_owner_without_origin() -> None:
    # Given: a legacy record with no origin binding
    fallback_log: list[str] = []

    def deny_api(method: str, path: str, payload: dict | None = None):
        raise AssertionError(f"network call: {method} {path}")

    # When: the result is delivered
    result = deliver(
        api=deny_api,
        transport_factory=lambda channel_id: pytest.fail("no thread transport expected"),
        record={"id": "abc123"},
        thread_name="이름",
        content="결과",
        fallback=lambda content: fallback_log.append(content) or "dm-1",
    )
    # Then: only the owner fallback fires, with the unchanged content
    assert result == "dm-1"
    assert fallback_log == ["결과"]


def test_deliver_posts_content_to_origin_thread() -> None:
    # Given: an origin-bound record and a healthy thread path
    posts: list[tuple[str, str]] = []

    def api(method: str, path: str, payload: dict | None = None):
        return {"id": THREAD_ID}

    # When: the result is delivered
    result = deliver(
        api=api,
        transport_factory=lambda channel_id: _Transport(channel_id, posts),
        record={
            "id": "abc123",
            "origin_channel_id": ORIGIN_CHANNEL,
            "origin_message_id": ORIGIN_MESSAGE,
        },
        thread_name="이름",
        content="✉️ 발송 완료",
        fallback=lambda content: pytest.fail("fallback must not fire on success"),
    )
    # Then: the content lands in the resolved thread through the chunking transport
    assert result == "thread-post-1"
    assert posts == [(THREAD_ID, "✉️ 발송 완료")]


def test_deliver_marks_and_falls_back_on_thread_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: an origin-bound record whose thread creation blows up
    fallback_log: list[str] = []

    def api(method: str, path: str, payload: dict | None = None):
        raise RuntimeError("thread API down")

    # When: the result is delivered
    result = deliver(
        api=api,
        transport_factory=lambda channel_id: pytest.fail("no thread transport expected"),
        record={"id": "abc123", "origin_channel_id": ORIGIN_CHANNEL},
        thread_name="이름",
        content="결과",
        fallback=lambda content: fallback_log.append(content) or "dm-1",
    )
    # Then: the owner still gets the result and the failure leaves a marker
    assert result == "dm-1"
    assert fallback_log == ["결과"]
    err = capsys.readouterr().err
    assert "NOTIFY-THREAD-FAIL" in err and "abc123" in err


# -------------------------------------------------- approval thread (2026-09-01)
# 소유자 결정: 승인 요청·리마인더·결과 통지가 요청별 스레드 하나에서 완결된다.
# 레코드의 `approval_thread_id` 가 있으면 결과는 스레드를 새로 만들지 않고 거기로 간다.

APPROVAL_THREAD = "400000000000000001"


class _RecordingApi:
    def __init__(self, responses: dict[tuple[str, str], object] | None = None) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []
        self.responses = responses or {}

    def __call__(self, method: str, path: str, payload: dict | None = None):
        self.calls.append((method, path, payload))
        response = self.responses.get((method, path))
        if isinstance(response, BaseException):
            raise response
        return response


def test_of_record_prefers_the_approval_thread() -> None:
    # Given: a record bound to a per-request approval thread (origin also present)
    ref = OriginRef.of_record({
        "approval_thread_id": APPROVAL_THREAD,
        "origin_channel_id": ORIGIN_CHANNEL,
        "origin_message_id": ORIGIN_MESSAGE,
    })
    # Then: the thread is known up front and the ref is truthy
    assert ref.thread_id == APPROVAL_THREAD
    assert bool(ref)
    assert OriginRef.of_record({"approval_thread_id": None}).thread_id == ""


def test_resolve_returns_the_approval_thread_without_any_api_call() -> None:
    # Given: a ref that already names the approval thread
    api = _RecordingApi()
    ref = OriginRef(channel_id=ORIGIN_CHANNEL, message_id=ORIGIN_MESSAGE, thread_id=APPROVAL_THREAD)
    # When / Then: no thread is created — the approval thread is the result thread
    assert resolve_thread_id(api, ref, "이름") == APPROVAL_THREAD
    assert api.calls == []


def test_deliver_posts_into_the_approval_thread_and_closes_it() -> None:
    # Given: a record bound to an approval thread and a healthy Discord api
    from automation.interop.origin_notice import ThreadOutcome

    posts: list[tuple[str, str]] = []
    api = _RecordingApi({
        ("GET", f"/channels/{APPROVAL_THREAD}"): {"name": "메일 발신 · 세미나 안내"},
        ("PATCH", f"/channels/{APPROVAL_THREAD}"): {"id": APPROVAL_THREAD},
    })
    # When: the terminal result is delivered
    result = deliver(
        api=api,
        transport_factory=lambda channel_id: _Transport(channel_id, posts),
        record={"id": "abc123", "approval_thread_id": APPROVAL_THREAD,
                "origin_channel_id": ORIGIN_CHANNEL, "origin_message_id": ORIGIN_MESSAGE},
        thread_name="이름",
        content="✉️ 발송 완료",
        fallback=lambda content: pytest.fail("fallback must not fire on success"),
        outcome=ThreadOutcome.DONE,
    )
    # Then: the content lands in the approval thread, no thread is created, and the
    # thread is renamed with the status prefix and archived
    assert result == "thread-post-1"
    assert posts == [(APPROVAL_THREAD, "✉️ 발송 완료")]
    assert not [call for call in api.calls if call[1].endswith("/threads")]
    assert api.calls[-1] == (
        "PATCH", f"/channels/{APPROVAL_THREAD}",
        {"name": "✅ 완료 · 메일 발신 · 세미나 안내", "archived": True},
    )


def test_close_replaces_an_earlier_status_prefix_and_respects_the_limit() -> None:
    # Given: a thread already closed once (cancelled) and a long name
    from automation.interop.origin_notice import ThreadOutcome, close_thread

    long_name = "⛔ 취소 · " + "가" * 95
    api = _RecordingApi({
        ("GET", f"/channels/{APPROVAL_THREAD}"): {"name": long_name},
        ("PATCH", f"/channels/{APPROVAL_THREAD}"): {},
    })
    # When: it is closed again as expired
    assert close_thread(api, APPROVAL_THREAD, ThreadOutcome.EXPIRED) is True
    # Then: the old prefix is replaced, not stacked, and the name fits Discord's limit
    payload = api.calls[-1][2]
    assert payload is not None
    assert payload["archived"] is True
    assert payload["name"].startswith("⌛ 만료 · 가")
    assert "⛔" not in payload["name"]
    assert len(payload["name"]) <= 100


def test_deliver_close_failure_leaves_a_marker_but_keeps_the_result(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: the rename/archive call is refused
    from automation.interop.origin_notice import ThreadOutcome

    posts: list[tuple[str, str]] = []
    api = _RecordingApi({
        ("GET", f"/channels/{APPROVAL_THREAD}"): {"name": "할 일 · 보고서"},
        ("PATCH", f"/channels/{APPROVAL_THREAD}"): _http_error(403),
    })
    # When: the result is delivered with a terminal outcome
    result = deliver(
        api=api,
        transport_factory=lambda channel_id: _Transport(channel_id, posts),
        record={"id": "abc123", "approval_thread_id": APPROVAL_THREAD},
        thread_name="이름",
        content="등록 완료",
        fallback=lambda content: pytest.fail("fallback must not fire on success"),
        outcome=ThreadOutcome.CANCELLED,
    )
    # Then: the notice itself is unaffected and the failure is a marker, not an exception
    assert result == "thread-post-1"
    assert posts == [(APPROVAL_THREAD, "등록 완료")]
    err = capsys.readouterr().err
    assert "THREAD-CLOSE-FAIL" in err and "abc123" in err and "NOTIFY-THREAD-FAIL" not in err


def test_deliver_without_any_thread_never_closes() -> None:
    # Given: a legacy record with neither approval thread nor origin
    from automation.interop.origin_notice import ThreadOutcome

    api = _RecordingApi()
    fallback_log: list[str] = []
    # When: a terminal result is delivered
    result = deliver(
        api=api,
        transport_factory=lambda channel_id: pytest.fail("no thread expected"),
        record={"id": "abc123"},
        thread_name="이름",
        content="결과",
        fallback=lambda content: fallback_log.append(content) or "dm-1",
        outcome=ThreadOutcome.DONE,
    )
    # Then: only the fallback ran — nothing to rename or archive
    assert result == "dm-1" and fallback_log == ["결과"]
    assert api.calls == []


def test_thread_outcome_prefixes_are_the_owner_facing_status_words() -> None:
    from automation.interop.origin_notice import ThreadOutcome

    assert {outcome.value for outcome in ThreadOutcome} == {"✅ 완료", "⛔ 취소", "⌛ 만료"}
