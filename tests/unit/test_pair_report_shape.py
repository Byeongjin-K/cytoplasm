"""Pair-channel notices use the report shape (lead Q304, owner 2026-10-20 "이것도 안 나오게 했나?").

The dori pair channel reads like a person: one or two sentences — what happened, one thing to check,
what is left — with no bold header, no field rows (대상/사실/위치/인계/되돌리기, 결과/위치/다음) and no
`-# 참조` footer. The owner channel keeps its own rendering.
"""
from __future__ import annotations

import json
import re

import pytest

from automation import healthcheck_notify, notice_bot, owner_notice
from automation.doctor import alarm

OWNER = "444444444444444444"
PAIR = "555555555555555555"
TOKEN = "synthetic pair report credential"
_FIELD_ROW = re.compile(r"^(\*\*)?(대상|사실|위치|인계|되돌리기|결과|다음)(:\*\*|:)", re.MULTILINE)


@pytest.fixture
def sent(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = tmp_path / notice_bot.OVERRIDE
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"token": TOKEN, "channel_id": OWNER, "fault_channel_id": PAIR}))
    path.chmod(0o600)
    posts: list[tuple[str, str, str]] = []
    monkeypatch.setattr(owner_notice, "send_notice", lambda *args: posts.append(args))
    monkeypatch.setenv("HEALTHCHECK_NOTIFY_STATE", str(tmp_path / "health/state.json"))
    return posts


def _assert_report_shape(body: str) -> None:
    assert "\n" not in body, body
    assert not body.startswith(("**", "#", ">")), body
    assert "-# " not in body and not _FIELD_ROW.search(body), body
    assert "남은 일" in body, body


def _healthcheck() -> None:
    healthcheck_notify.SUSTAIN_SECONDS = 0.0
    healthcheck_notify.main(["node release fully deployed|local|node|account|target"])


def _doctor() -> None:
    from datetime import UTC, datetime

    body = "[doctor] agent — 연결·동작 점검 결과가 바뀌었다\n새로 확인된 문제 1건:\n- 실패 · 모델: 응답 없음"
    now = datetime(2026, 10, 20, tzinfo=UTC)
    assert alarm._send(body, alarm.envelope("agent", body, now, now))


def _meeting() -> None:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/meeting/scripts"))
    import meeting_deploy_notice

    assert meeting_deploy_notice.notify_restart("a" * 64, "b" * 64)


def _stale_key() -> None:
    from automation.deploy_reconcile_trust import StaleTrustKey, notify_stale_key

    fields = StaleTrustKey.__dataclass_fields__
    values = {name: "v9.9.9" if "tag" in name else "SHA256:synthetic" for name in fields}
    assert notify_stale_key(StaleTrustKey(**values), "신뢰 키 경고")


@pytest.mark.parametrize("producer", [_healthcheck, _doctor, _meeting, _stale_key],
                         ids=["healthcheck", "doctor", "meeting", "trust-key"])
def test_fault_notice_reaches_the_pair_channel_in_report_shape(sent, monkeypatch, producer) -> None:
    monkeypatch.setattr(healthcheck_notify, "SUSTAIN_SECONDS", healthcheck_notify.SUSTAIN_SECONDS)
    producer()
    assert [post[1] for post in sent] == [PAIR]
    _assert_report_shape(sent[0][2])


def test_healthcheck_names_the_failing_check_in_a_sentence(sent, monkeypatch) -> None:
    monkeypatch.setattr(healthcheck_notify, "SUSTAIN_SECONDS", healthcheck_notify.SUSTAIN_SECONDS)
    _healthcheck()
    body = sent[0][2]
    assert "node release fully deployed" in body and "신규 실패:" not in body


def test_meeting_notice_carries_no_warning_code(sent) -> None:
    _meeting()
    assert "PLUGIN-CHANGED" not in sent[0][2]


def test_owner_notice_moved_to_pair_for_a_warning_code_uses_report_shape(sent) -> None:
    from automation.interop.owner_message import Action, OwnerMessage, Ref, Result

    message = OwnerMessage(
        subject_key="sample", subject="정기 점검", fact="DEPLOY-BLOCK: 배포가 멈췄습니다",
        location=Ref(scope="resource", search=("릴리스", "v9.9.9")), owner=Action("none"),
        agent_next="다음 틱에서 다시 확인", recovery="not_applicable", detail=Result("executed"),
        render_version="owner-ko-v2",
    )
    assert owner_notice.notify_owner("", message=message)
    assert [post[1] for post in sent] == [PAIR]
    _assert_report_shape(sent[0][2])


def test_owner_channel_keeps_its_rendering(sent) -> None:
    from automation.interop.owner_message import Action, OwnerMessage, Ref, Result, render

    message = OwnerMessage(
        subject_key="sample", subject="정기 점검", fact="오늘 점검을 마쳤습니다",
        location=Ref(scope="none"), owner=Action("none"), agent_next=None,
        recovery="not_applicable", detail=Result("executed"), render_version="owner-ko-v2",
    )
    assert owner_notice.notify_owner("", message=message)
    assert sent[0][1] == OWNER
    assert sent[0][2] == render(message, destination=Ref(scope="channel", space="unknown", channel_id=OWNER))
