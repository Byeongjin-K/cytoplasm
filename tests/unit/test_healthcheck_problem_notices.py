"""Problem-only notices; new cases live here to preserve FS3-pinned case counts."""
from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from automation import deploy_reconcile, healthcheck_notify, owner_notice
from automation.doctor import alarm
from automation.doctor.capabilities import Finding
from automation.install.checks import Status
from automation.interop.owner_message import OwnerMessage


@pytest.mark.parametrize("remaining", [(), ("still-broken",)])
def test_healthcheck_recovery_advances_state_without_posting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, remaining: tuple[str, ...],
) -> None:
    # Given: one previously reported incident recovers, possibly beside an old failure.
    path = tmp_path / "state.json"
    healthcheck_notify.save_state(path, healthcheck_notify.NotifyState(("recovered", *remaining)))
    monkeypatch.setenv("HEALTHCHECK_NOTIFY_STATE", str(path))
    sent: list[str] = []
    monkeypatch.setattr(owner_notice, "notify_owner", lambda body, **kwargs: sent.append(body))
    # When: the sweep has no newly failing check.
    assert healthcheck_notify.main(remaining) == 0
    # Then: the recovery is remembered without calling the owner transport.
    assert sent == []
    assert healthcheck_notify.load_state(path).open_incidents == remaining
    assert path.stat().st_mode & 0o777 == 0o600
    following, notice = healthcheck_notify.plan_notice(
        healthcheck_notify.load_state(path), failing=(*remaining, "recovered"),
    )
    assert notice is not None
    assert "recovered" in following.open_incidents


def test_healthcheck_new_failure_uses_discord_header_with_sender_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the real YooDori credential/channel override and a recovered incident.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "legacy synthetic credential")
    monkeypatch.setenv("OWNER_NOTICE_CHANNEL_ID", "111111111111111111")
    override = tmp_path / ".hermes/notice-bot.json"
    override.parent.mkdir(parents=True)
    override.write_text(json.dumps({
        "token": "sender override synthetic credential",
        "channel_id": "222222222222222222",
    }), encoding="utf-8")
    override.chmod(0o600)
    path = tmp_path / "state.json"
    monkeypatch.setenv("HEALTHCHECK_NOTIFY_STATE", str(path))
    healthcheck_notify.save_state(path, healthcheck_notify.NotifyState(("recovered",)))
    monkeypatch.setattr(healthcheck_notify, "SUSTAIN_SECONDS", 0.0)  # wait pinned in test_q281_alerts.py
    requests = []

    def post(request, **kwargs):
        requests.append(request)
        return io.BytesIO(b'{"id":"333333333333333333"}')

    monkeypatch.setattr("automation.interop.discord_transport.urlopen", post)
    # When: a new failure reaches the real facade, renderer, and REST transport.
    assert healthcheck_notify.main(["new-failure|local|node|ops|probe"]) == 0
    # Then: identity is only in transport auth; the header is the producer's v2 header.
    assert len(requests) == 1
    request = requests[0]
    assert request.get_header("Authorization") == "Bot sender override synthetic credential"
    assert request.full_url.endswith("/channels/222222222222222222/messages")
    body = json.loads(request.data)["content"]
    assert body.splitlines()[0] == "**✅ 헬스체크 관측**"
    assert "new-failure" in body and "recovered" in body
    assert healthcheck_notify.load_state(path).open_incidents == ("new-failure",)


def test_doctor_recovery_advances_timestamp_without_posting(tmp_path: Path) -> None:
    # Given: a previously reported doctor failure.
    before = datetime(2026, 10, 18, tzinfo=UTC)
    now = before + timedelta(hours=1)
    path = alarm.state_path(tmp_path)
    problem = Finding("synthetic", "synthetic", Status.FAIL, "detail", "impact", (), ())
    alarm._save(path, alarm.signatures((problem,)), before)
    sent: list[str] = []

    def send(body: str, message: OwnerMessage | None) -> bool:
        sent.append(body)
        return True

    # When: the check recovers.
    alarm.run_alarm("agent", (), home=tmp_path, now=now, send=send)
    # Then: no all-OK post, but the saved observation advances.
    assert sent == []
    assert json.loads(path.read_text()) == {"checked_at": now.isoformat(), "problems": {}}


@pytest.mark.parametrize("pending", [False, True])
def test_reconcile_owner_recovery_is_consumed_without_posting(
    monkeypatch: pytest.MonkeyPatch, pending: bool,
) -> None:
    # Given: an open incident or a recovery queued before this policy change.
    sent: list[str] = []
    monkeypatch.setattr(owner_notice, "send_notice", lambda token, channel, body: sent.append(body))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "synthetic credential")
    monkeypatch.setenv("OWNER_NOTICE_CHANNEL_ID", "111111111111111111")
    state = deploy_reconcile.ReconcileState(
        incident_open=not pending,
        pending_notice="prod가 origin/main에 다시 도달했습니다: same" if pending else None,
    )
    # When: the real owner-notice adapter observes a converged release.
    result = deploy_reconcile.reconcile_tick(
        state, origin_sha="same", current_sha="same", now=100,
        converge=lambda: pytest.fail("already converged"), deliver=owner_notice.notify_owner,
    )
    # Then: the incident and old recovery queue close without a channel post.
    assert sent == []
    assert result == deploy_reconcile.ReconcileState()
