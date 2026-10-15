"""doctor 주 모델: Hermes 의 일시 쿨다운(`exhausted`)은 로그인 고장이 아니다.

2026-10-15 05:18 UTC agent 의 매시 점검이 auth.json 의 `exhausted` 를 보고 「고장 · 다시 로그인한다」를
보냈고, 그 자격은 05:33 에 이미 정상으로 돌아왔는데도 다음 점검(06:17)까지 고장으로 남았다. 영구 인증
실패는 Hermes 가 `dead` 로 따로 적으므로, 고장은 그것일 때만 낸다. 관측은 가짜 홈으로만 한다.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

from automation.doctor.capabilities import Finding, evaluate
from automation.doctor.facts import gather
from automation.install.checks import Status

_NOW: Final = datetime(2026, 10, 15, 5, 18, tzinfo=timezone.utc).timestamp()
_WITH_FALLBACK: Final = "model:\n  provider: openai-codex\nfallback_providers:\n  - provider: xai-oauth\n    model: g\n"
_NO_FALLBACK: Final = "model:\n  provider: openai-codex\n"


def _primary(tmp_path: Path, codex: dict[str, object], *, config: str = _WITH_FALLBACK) -> Finding:
    hermes = tmp_path / ".hermes"
    hermes.mkdir(parents=True)
    _ = (hermes / "auth.json").write_text(json.dumps({"credential_pool": {
        "openai-codex": [codex], "xai-oauth": [{"last_status": "ok"}],
    }}), encoding="utf-8")
    _ = (hermes / "config.yaml").write_text(config, encoding="utf-8")
    facts = gather(account="agent", role="agent", home=tmp_path, gateway_unit="hermes-gateway.service",
                   online=False, run=lambda _argv, _extra: (127, ""), uid=1000, now=_NOW)
    return next(f for f in evaluate(facts) if f.key == "primary-model")


def test_a_cooldown_that_already_ended_passes(tmp_path: Path) -> None:
    found = _primary(tmp_path, {"last_status": "exhausted", "last_status_at": _NOW - 2 * 3600,
                                "last_error_code": 429})

    assert found.status is Status.PASS


def test_a_short_cooldown_with_a_logged_in_fallback_does_not_alert(tmp_path: Path) -> None:
    found = _primary(tmp_path, {"last_status": "exhausted", "last_status_at": _NOW - 60,
                                "last_error_code": 429})

    assert found.status is Status.PASS
    assert "xai-oauth" in found.detail and "로그인" not in found.detail


def test_a_quota_reset_hours_away_is_a_warning_not_a_login_failure(tmp_path: Path) -> None:
    found = _primary(tmp_path, {"last_status": "exhausted", "last_status_at": _NOW - 60,
                                "last_error_code": 429, "last_error_reset_at": _NOW + 3 * 3600})

    assert found.status is Status.WARN
    assert "다시 로그인할 필요는 없다" in found.detail


def test_a_cooldown_without_a_fallback_is_a_warning(tmp_path: Path) -> None:
    found = _primary(tmp_path, {"last_status": "exhausted", "last_status_at": _NOW - 60},
                     config=_NO_FALLBACK)

    assert found.status is Status.WARN


def test_a_cooldown_with_no_time_is_a_warning(tmp_path: Path) -> None:
    found = _primary(tmp_path, {"last_status": "exhausted"})

    assert found.status is Status.WARN


def test_a_dead_credential_is_still_a_login_failure(tmp_path: Path) -> None:
    found = _primary(tmp_path, {"last_status": "dead", "last_status_at": _NOW - 60, "last_error_code": 401})

    assert found.status is Status.FAIL
    assert "다시 로그인한다" in found.detail


def test_an_iso_reset_time_is_read(tmp_path: Path) -> None:
    found = _primary(tmp_path, {"last_status": "exhausted", "last_error_reset_at": "2026-10-15T09:00:00Z"})

    assert found.status is Status.WARN and "분 남음" in found.detail
