"""ON-1 회귀: `owner_notice` 파사드의 통지 채널 지정 (`owner_notice_channel_id`).

고정하는 것:

* env(`OWNER_NOTICE_CHANNEL_ID`) → interop config → 미설정(DM) 순의 해석.
* 채널이 지정되면 **그 채널로만** 보낸다 — DM 폴백 없음, 실패는 False(호출자 큐잉).
* `notify_owner_dm` 은 그 반대 — 소유자가 DM 을 명시한 통지(릴리스 적용 완료)는 통지
  채널이 설정돼 있어도 **DM 으로만** 간다. DM 오픈은 여기서만 한다(ON-2).
* 설정을 읽을 수 없어도(EACCES 포함) 답은 ""(=DM) 이다 — ProtectHome 유닛에서
  홈을 찌르는 코드는 답해야지 던지면 안 된다(2026-08-21 repair 워처 5일 정지).
* 「절대 예외를 던지지 않는다」 계약 불변.
* 선택 통지 모듈 초기화 실패도 기존 문자열 배달·실패 마커를 유지한다.
"""
from __future__ import annotations

import builtins
import importlib
import json
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import cast
from unittest.mock import patch

import pytest

from automation import owner_notice
from automation.selfskill_audit import report
from automation.repair.repair_ops_reaction_watch import RepairApprovalWatcher
from tests.unit.test_repair_owner_message import GUILD_FALLBACK, LEGACY_FALLBACK, Wire, runtime as runtime


@pytest.fixture(autouse=True)
def _credentials(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "unit-test-token")
    monkeypatch.setenv("AUTOPHAGY_OWNER_ID", "42")
    monkeypatch.delenv("OWNER_NOTICE_CHANNEL_ID", raising=False)


def _config(home: Path, payload: object) -> None:
    directory = home / ".hermes" / "interop"
    directory.mkdir(parents=True)
    _ = (directory / "config.json").write_text(json.dumps(payload), encoding="utf-8")


class TestChannelResolution:
    def test_a_env_wins_over_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OWNER_NOTICE_CHANNEL_ID", "111")
        _config(tmp_path, {"owner_notice_channel_id": "222"})
        assert owner_notice.owner_notice_channel(tmp_path) == "111"

    def test_b_config_key_is_read_when_env_is_unset(self, tmp_path: Path) -> None:
        _config(tmp_path, {"owner_notice_channel_id": " 333 "})
        assert owner_notice.owner_notice_channel(tmp_path) == "333"

    def test_c_missing_config_means_unconfigured(self, tmp_path: Path) -> None:
        assert owner_notice.owner_notice_channel(tmp_path) == ""

    def test_d_unreadable_config_answers_instead_of_raising(self, tmp_path: Path) -> None:
        _config(tmp_path, {"owner_notice_channel_id": "444"})
        config = tmp_path / ".hermes" / "interop" / "config.json"
        config.chmod(0o000)
        try:
            assert owner_notice.owner_notice_channel(tmp_path) == ""
        finally:
            config.chmod(0o600)

    def test_e_non_string_value_is_unconfigured(self, tmp_path: Path) -> None:
        _config(tmp_path, {"owner_notice_channel_id": 555})
        assert owner_notice.owner_notice_channel(tmp_path) == ""


class TestNotifyOwnerTargeting:
    def test_a_configured_channel_is_the_only_target(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OWNER_NOTICE_CHANNEL_ID", "777")
        sent: list[tuple[str, str]] = []
        monkeypatch.setattr(
            owner_notice, "send_notice", lambda token, channel, body: sent.append((channel, body))
        )

        def no_dm(token: str, owner_id: str) -> str:
            raise AssertionError("채널이 지정되면 DM 해석을 시도하면 안 된다")

        monkeypatch.setattr(owner_notice, "owner_dm_channel", no_dm)
        assert owner_notice.notify_owner("hello") is True
        assert sent == [("777", "hello")]

    def test_b_unconfigured_falls_back_to_dm(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(owner_notice, "owner_notice_channel", lambda home=None: "")
        sent: list[str] = []
        monkeypatch.setattr(owner_notice, "owner_dm_channel", lambda token, owner_id: "dm-1")
        monkeypatch.setattr(
            owner_notice, "send_notice", lambda token, channel, body: sent.append(channel)
        )
        assert owner_notice.notify_owner("hello") is True
        assert sent == ["dm-1"]

    def test_c_channel_send_failure_is_false_not_dm_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OWNER_NOTICE_CHANNEL_ID", "777")
        attempts: list[str] = []

        def failing(token: str, channel: str, body: str) -> None:
            attempts.append(channel)
            raise RuntimeError("channel gone")

        monkeypatch.setattr(owner_notice, "send_notice", failing)
        monkeypatch.setattr(
            owner_notice,
            "owner_dm_channel",
            lambda token, owner_id: pytest.fail("실패 시 DM 으로 새지 않는다"),
        )
        assert owner_notice.notify_owner("hello") is False
        assert attempts == ["777"]  # 정확히 한 번, 지정 채널로만


class TestNotifyOwnerDm:
    """소유자가 DM 을 명시한 통지 — 릴리스 적용 완료가 첫 소비자다."""

    def test_a_dm_is_used_even_when_a_notice_channel_is_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Given: 정기 통지 채널이 설정된 설치
        monkeypatch.setenv("OWNER_NOTICE_CHANNEL_ID", "777")
        sent: list[tuple[str, str]] = []
        monkeypatch.setattr(owner_notice, "owner_dm_channel", lambda token, owner_id: "dm-9")
        monkeypatch.setattr(
            owner_notice,
            "send_notice",
            lambda token, channel, body: sent.append((channel, body)),
        )

        # When
        delivered = owner_notice.notify_owner_dm("릴리스 v1.2.4 가 적용되었습니다.")

        # Then: 채널이 아니라 DM 으로 간다
        assert delivered is True
        assert sent == [("dm-9", "릴리스 v1.2.4 가 적용되었습니다.")]

    def test_b_unconfigured_owner_is_false_without_sending(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Given: owner id 를 해석할 수 없는 계정
        monkeypatch.delenv("AUTOPHAGY_OWNER_ID", raising=False)
        monkeypatch.setattr(owner_notice, "_config_owner_id", lambda: "")
        monkeypatch.setattr(
            owner_notice,
            "send_notice",
            lambda token, channel, body: pytest.fail("자격 없으면 보내지 않는다"),
        )

        # When
        delivered = owner_notice.notify_owner_dm("hello")

        # Then
        assert delivered is False
        assert "NOTIFY-UNCONFIGURED" in capsys.readouterr().err

    def test_c_send_failure_is_false_not_an_exception(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Given: DM 채널은 열리지만 전송이 터지는 상황
        monkeypatch.setattr(owner_notice, "owner_dm_channel", lambda token, owner_id: "dm-9")

        def failing(token: str, channel: str, body: str) -> None:
            raise RuntimeError("discord down")

        monkeypatch.setattr(owner_notice, "send_notice", failing)

        # When
        delivered = owner_notice.notify_owner_dm("hello")

        # Then: 호출자가 다음 틱에 재시도할 수 있도록 False 로 답한다
        assert delivered is False
        assert "NOTIFY-FAILED: RuntimeError" in capsys.readouterr().err


# Import these producers before injecting failure into only the optional module.
_BUILDERS = (
    ("automation.selfskill_audit.notice", "audit_message", ("body", "fixture")),
    ("automation.supply_chain_shadow_notice", "shadow_message", ("body", ("skill",), Path("home"))),
    ("automation.release_applied_message", "applied_message", ("version", "head", "body")),
    ("automation.plaud_sync.result_message", "result_notice_message", (None, "body", "written")),
    ("automation.deploy_reconcile_notice", "build_notice", ("body",)),
    ("automation.deploy_reconcile_backlog", "backlog_message", ("body", None)),
    ("automation.release_retire", "stale_message", ({}, "tip")),
    ("automation.release_retire", "abandoned_message", ({}, "reason")),
    ("automation.interop.delegation", "result_message", ("key", None, None)),
    ("automation.memory_curator.notice", "build_notice", ("body",)),
    ("automation.managed_sync.cron.managed_sync_watch", "staged_message", ("body",)),
    ("automation.healthcheck_notify", "notice_message", (None, None)),
    ("skills.todo.scripts.todo_result_message", "result_message", ({}, "body", "DONE")),
    ("skills.budget.scripts.budget_result_message", "result_message", ({}, "body", "DONE")),
    ("skills.coordination.scripts.coordination_result_notice", "result_message", ({}, "body", "done")),
    ("skills.calendar.scripts.calendar_result_message", "build", ({}, "body", "done")),
    ("skills.mail.scripts.triage_result_notice", "result_message", ({}, "body", "DONE")),
    ("skills.meeting.scripts.meeting_result_notice", "result_message", ({}, "body", object())),
)


def _reject_optional_import(error: type[Exception]) -> Callable[..., ModuleType]:
    original = builtins.__import__

    def importing(
        name: str, globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None, fromlist: tuple[str, ...] = (), level: int = 0,
    ) -> ModuleType:
        if name == "automation.interop.owner_message":
            raise error("optional initialization failed")
        return original(name, globals, locals, fromlist, level)

    return importing


@pytest.mark.parametrize("error", [ImportError, RuntimeError, ValueError, OSError])
@pytest.mark.parametrize("module_name,function_name,arguments", _BUILDERS)
def test_builder_declines_unavailable_optional_module(
    module_name: str, function_name: str, arguments: tuple[object, ...], error: type[Exception],
) -> None:
    module = importlib.import_module(module_name)
    builder = cast(Callable[..., object], getattr(module, function_name))
    with patch("builtins.__import__", _reject_optional_import(error)):
        assert builder(*arguments) is None


@pytest.mark.parametrize("error", [ImportError, RuntimeError])
@pytest.mark.parametrize("delivery", ["sent", "failed", "unconfigured"])
def test_audit_import_failure_preserves_delivery_and_markers(
    error: type[Exception], delivery: str, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fixture" if delivery != "unconfigured" else "")
    monkeypatch.setenv("OWNER_NOTICE_CHANNEL_ID", "111")
    content = report.render_summary((), account_label="fixture")
    failure = OSError("delivery unavailable") if delivery == "failed" else None
    with patch.object(owner_notice, "send_notice", side_effect=failure) as sender:
        with patch("builtins.__import__", _reject_optional_import(error)):
            assert report.send_report((), account_label="fixture") is (delivery == "sent")
    if delivery == "unconfigured":
        sender.assert_not_called()
        assert "NOTIFY-UNCONFIGURED:" in capsys.readouterr().err
    else:
        sender.assert_called_once_with("fixture", "111", content)
        stderr = capsys.readouterr().err
        assert stderr == ("[owner-notice] NOTIFY-FAILED: OSError\n" if delivery == "failed" else "")


@pytest.mark.parametrize("error", [ImportError, RuntimeError])
@pytest.mark.parametrize("module_name", [
    "skills.doctype.scripts.doctype_review", "skills.proposal.scripts.proposal_dm",
])
def test_review_import_failure_still_sends(
    module_name: str, error: type[Exception], monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module(module_name)
    send = cast(Callable[[str, str, Path], None], module.send_review)
    body = "  body\t\n"
    monkeypatch.setenv("OWNER_NOTICE_CHANNEL_ID", "111")
    with patch.object(owner_notice, "send_notice") as transport:
        with patch("builtins.__import__", _reject_optional_import(error)):
            send("fixture", body, Path("fixture.md"))
    transport.assert_called_once_with("unit-test-token", "111", body)


@pytest.mark.parametrize("error", [ImportError, RuntimeError])
def test_procurement_import_failure_still_sends(
    error: type[Exception], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root / "skills" / "procurement" / "scripts"))
    module = importlib.import_module("skills.procurement.scripts.procure_review")
    file = tmp_path / "fixture.md"
    _ = file.write_text("body", encoding="utf-8")
    monkeypatch.setenv("PROCURE_DISCORD_STUB", str(tmp_path))
    monkeypatch.setenv("PROCURE_DM_MAX_BYTES", "1000000")
    send = cast(Callable[[Path, str], str], module.send_review)
    with patch("builtins.__import__", _reject_optional_import(error)):
        result = send(file, "review")
    assert result.startswith("REVIEW-DM-SENT ")
    receipts = list(tmp_path.glob("dm-*.json"))
    assert len(receipts) == 1
    expected = module.review_note(file, "attach", "review", "")
    assert json.loads(receipts[0].read_text(encoding="utf-8"))["content"] == expected


@pytest.mark.parametrize("error", [ImportError, RuntimeError])
def test_patent_import_failure_still_sends(error: type[Exception]) -> None:
    module = importlib.import_module("skills.patent-prep.scripts.patent_export_gate")
    send = cast(Callable[[str, str], str], module.dm_owner)
    with patch.object(module, "_api", return_value={"id": "222"}) as transport:
        with patch("builtins.__import__", _reject_optional_import(error)):
            assert send("111", "body") == "222"
    transport.assert_called_once_with("POST", "/channels/111/messages", {"content": "body"})


@pytest.mark.parametrize("error", [ImportError, RuntimeError])
def test_calendar_watch_existing_outer_guard_preserves_marker(
    error: type[Exception], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from automation import owner_notice
    from tests.unit.test_calendar_confirm_reactions import FakeDiscord, watch

    monkeypatch.setenv("DISCORD_BOT_TOKEN", "unit-token")
    monkeypatch.setenv("OWNER_NOTICE_CHANNEL_ID", "222")
    sent = []
    monkeypatch.setattr(owner_notice, "send_notice", lambda token, channel, body: sent.append(body))
    discord = FakeDiscord({})
    with patch("builtins.__import__", _reject_optional_import(error)):
        watch._notify_owner(discord, "body", {"id": "fixture"}, "done")
    assert discord.sent_messages == []
    # Builder initialization failure is an optional-runtime fallback, not a failed send.
    assert sent == ["body"]
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("error", [ImportError, RuntimeError])
def test_repair_reminder_import_failure_still_sends(
    runtime: tuple[RepairApprovalWatcher, Wire], error: type[Exception],
) -> None:
    watcher, wire = runtime
    expected = GUILD_FALLBACK if wire.pending.approval_guild_id else LEGACY_FALLBACK
    with patch("builtins.__import__", _reject_optional_import(error)):
        watcher.run_once()
    assert wire.posts == [("/channels/222/messages", expected)]
