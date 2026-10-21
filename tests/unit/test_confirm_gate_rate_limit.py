"""소유자의 결정을 실제로 소비하는 confirm 게이트도 429 를 존중해야 한다.

`tests/unit/test_skill_gate_rate_limit.py` 가 같은 결함을 배포 게이트에서 이미 고정했다.
그런데 그 수정은 `automation/skill_gate.py` 한 곳에만 적용됐고, **소유자의 ✅/⛔ 를 읽어
실제로 반영하는** 스킬 쪽 confirm 게이트 둘(wiki·mail)은 맨 `urlopen` 그대로였다.

2026-08-03 실측으로 그 대가가 드러났다. `wiki-confirm-watch` 는 1분 주기로 도는데,
매 tick 이 대기 중인 초안마다 메시지 1회 + 리액션 2회를 조회한다. per-route 버킷이
소진되자 워처가 통째로 죽었다:

    wiki-confirm-reaction-watch error: HTTP Error 429: Too Many Requests   (exit 1)

그 사이 소유자는 승격 확인 4건에 ⛔ 를 눌렀지만 아무것도 반영되지 않았다. 초안은
`pending` 으로, 승격 레코드는 `posted`/`note_missing` 으로 남았고, 상태만 보면
"소유자가 아직 안 눌렀다" 와 구분되지 않는다. 즉 **사람은 결정했는데 시스템은 그 결정을
잃어버렸고, 그 사실조차 조용했다**.

여기서 고정하는 것은 배포 게이트와 같은 세 가지다: 429 는 재시도한다, 429 가 아닌
실패는 재시도하지 않는다(오류를 숨기면 안 된다), 재시도는 유한하다(무한 대기는 그것대로
침묵하는 고장이다).
"""
from __future__ import annotations

import io
import json
import sys
from email.message import Message
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.error import HTTPError

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
for _skill in ("wiki", "mail"):
    sys.path.insert(0, str(_REPO / "skills" / _skill / "scripts"))

import triage_confirm  # noqa: E402
import wiki_gate  # noqa: E402

#: 소유자 결정을 읽는 게이트 전부. 하나라도 빠지면 그 표면만 조용히 결정을 잃는다.
_GATES = (wiki_gate, triage_confirm)
_IDS = ("wiki", "mail")


class _Response:
    def __init__(self, payload: object) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _rate_limited(retry_after: str | None = "1.5") -> HTTPError:
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return HTTPError(
        "https://discord.com/api/v10/x", 429, "Too Many Requests", headers, io.BytesIO(b"{}")
    )


def _install(
    gate: ModuleType, monkeypatch: pytest.MonkeyPatch, responses: list[object]
) -> list[float]:
    slept: list[float] = []
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setattr(gate.time, "sleep", lambda seconds: slept.append(seconds))

    def fake_urlopen(_request: Any, timeout: int = 0) -> _Response:  # noqa: ARG001
        item = responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return _Response(item)

    monkeypatch.setattr(gate, "urlopen", fake_urlopen)
    return slept


@pytest.mark.parametrize("gate", _GATES, ids=_IDS)
def test_a_rate_limited_call_is_retried_instead_of_raised(
    gate: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """소유자의 ⛔ 4건이 반영되지 않고 사라지던 바로 그 지점."""
    slept = _install(gate, monkeypatch, [_rate_limited(), {"ok": True}])
    assert gate._api("GET", "/x") == {"ok": True}
    assert slept, "Retry-After 를 기다리지 않았다"


@pytest.mark.parametrize("gate", _GATES, ids=_IDS)
def test_the_retry_honours_the_servers_retry_after(
    gate: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    slept = _install(gate, monkeypatch, [_rate_limited("2.5"), {"ok": True}])
    _ = gate._api("GET", "/x")
    assert slept[0] >= 2.5


@pytest.mark.parametrize("gate", _GATES, ids=_IDS)
def test_a_missing_retry_after_still_backs_off(
    gate: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    slept = _install(gate, monkeypatch, [_rate_limited(None), {"ok": True}])
    _ = gate._api("GET", "/x")
    assert slept and slept[0] > 0


@pytest.mark.parametrize("gate", _GATES, ids=_IDS)
def test_a_non_rate_limit_error_is_not_retried(
    gate: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """404 를 재시도로 숨기면 삭제된 승인 메시지가 정상처럼 보인다."""
    not_found = HTTPError("https://discord.com/api/v10/x", 404, "Not Found", Message(), None)
    responses: list[object] = [not_found, {"ok": True}]
    _ = _install(gate, monkeypatch, responses)
    with pytest.raises(HTTPError) as raised:
        _ = gate._api("GET", "/x")
    assert raised.value.code == 404
    assert len(responses) == 1, "404 인데도 다시 호출했다"


@pytest.mark.parametrize("gate", _GATES, ids=_IDS)
def test_a_permanent_rate_limit_gives_up_rather_than_hanging(
    gate: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """무한 재시도는 워처를 영원히 매달아 그것대로 침묵하는 고장이 된다."""
    _ = _install(gate, monkeypatch, [_rate_limited() for _ in range(50)])
    with pytest.raises(HTTPError) as raised:
        _ = gate._api("GET", "/x")
    assert raised.value.code == 429


@pytest.mark.parametrize("gate", _GATES, ids=_IDS)
def test_a_successful_call_never_sleeps(
    gate: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    slept = _install(gate, monkeypatch, [{"ok": True}])
    assert gate._api("GET", "/x") == {"ok": True}
    assert slept == []
