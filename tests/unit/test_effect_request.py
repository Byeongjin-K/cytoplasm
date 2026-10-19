"""Generic outbound effect cards execute once on the owner's approval.

Regression for 2026-10-17: a result file's Drive save and other actions without a
dedicated CLI ended with "이 작업은 승인 경로가 없다". These tests pin the replacement:
the card names what will happen, only the owner's ✅ on that card runs it, the frozen
payload is re-hashed before it runs, and owner-only actions are refused with a reason.
Owner-account-internal writes are not card requests: they use dori-gate, initiated
only by an owner Discord command. The legacy Drive-card fixtures exercise stored
approval compatibility, not the routing policy for new owner-internal writes.
"""

from __future__ import annotations

import argparse
import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from automation.effect_request import cli, gate, policy
from automation.effect_request.model import EffectRequest, EffectRequestStore
from automation.effect_request.render import render_effect_approval
from automation.effect_request.watch import run_tick
from automation.interop.approval_lifecycle import Outcome
from automation.interop.approval_surface import (
    POLICY_VERSION,
    ApprovalBinding,
    ApprovalKind,
    ApprovalSurface,
    required_surface,
)

OWNER = "100"
NOW = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)


class FakeTransport:
    def __init__(self) -> None:
        self.owner_id = OWNER
        self.messages: dict[str, tuple[str, str]] = {}
        self.reactions: dict[tuple[str, str], list[tuple[str, bool]]] = {}

    def api(self, method: str, path: str, payload: object = None) -> object:
        return {"id": "9"}

    def post_message(self, channel_id: str, content: str) -> str:
        message_id = str(500 + len(self.messages))
        self.messages[message_id] = (channel_id, content)
        return message_id

    def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None:
        self.reactions.setdefault((message_id, emoji), []).append(("777", True))

    def get_message(self, channel_id: str, message_id: str) -> str | None:
        found = self.messages.get(message_id)
        return None if found is None else found[1]

    def get_reaction_users(self, channel_id: str, message_id: str, emoji: str) -> tuple[tuple[str, bool], ...]:
        return tuple(self.reactions.get((message_id, emoji), ()))

    def delete_message(self, channel_id: str, message_id: str) -> None:
        self.messages.pop(message_id, None)


def _drive_args(file: Path, **extra: str) -> argparse.Namespace:
    values = {"effect": "drive-save", "summary": "세미나 신청자 조회 결과 저장", "file": str(file),
              "title": None, "kind": "doctype", "project": "", "on": "2026-10-17",
              "origin_channel_id": "", "origin_message_id": ""}
    values.update(extra)
    return argparse.Namespace(**values)


def _command_args(argv: list[str], tmp_path: Path) -> argparse.Namespace:
    return argparse.Namespace(
        effect="command", summary="실험 서버에 알림 보내기", target="실험 서버 웹훅",
        cwd=str(tmp_path), timeout=30, argv=["--", *argv], origin_channel_id="", origin_message_id="",
    )


def _post(monkeypatch: pytest.MonkeyPatch, store: EffectRequestStore, record: EffectRequest) -> tuple[FakeTransport, EffectRequest]:
    binding = ApprovalBinding(
        ApprovalKind.EXTERNAL_EFFECT, required_surface(ApprovalKind.EXTERNAL_EFFECT), "4242", POLICY_VERSION, "77"
    )
    monkeypatch.setattr(gate, "reuse_request_thread", lambda *args: None)
    monkeypatch.setattr(gate, "resolve_new_binding", lambda *args, **kwargs: binding)
    transport = FakeTransport()
    runtime = gate.DiscordRuntime(transport, object(), OWNER)  # type: ignore[arg-type]
    assert gate.request_approval(record, store, runtime).outcome is Outcome.POSTED
    return transport, store.get(record.request_id) or record


def _tick(store: EffectRequestStore, transport: FakeTransport, publisher=None, runner=None):  # noqa: ANN001, ANN202
    runtime = gate.DiscordRuntime(transport, object(), OWNER)  # type: ignore[arg-type]
    return run_tick(store, runtime, token="", now=NOW,
                    publisher=publisher or (lambda *a, **k: pytest.fail("publisher must not run")),
                    runner=runner)


def _approve(transport: FakeTransport, record: EffectRequest, *, by: str = OWNER, bot: bool = False) -> None:
    transport.reactions.setdefault((record.message_id or "", "\u2705"), []).append((by, bot))


def test_new_kind_posts_in_the_owner_request_thread() -> None:
    assert required_surface(ApprovalKind.EXTERNAL_EFFECT) is ApprovalSurface.AGENT_CHAT_THREAD


def test_drive_save_card_names_file_location_and_private_sharing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "조회결과.csv"
    source.write_text("a,b\n", encoding="utf-8")
    store = EffectRequestStore(tmp_path / "state")
    record = cli._new_record(_drive_args(source), store)
    card = render_effect_approval(record)
    assert "파일 이름: 2026-10-17_조회결과.csv" in card
    assert "Drive 위치: autophagy/문서/2026/2026-10-17_조회결과.csv" in card
    assert "공유 범위: 비공개" in card
    assert record.action_hash in card
    transport, posted = _post(monkeypatch, store, record)
    assert transport.messages[posted.message_id or ""][1] == card


def test_drive_save_runs_only_after_the_owner_check_and_saves_the_frozen_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "조회결과.csv"
    source.write_text("a,b\n", encoding="utf-8")
    store = EffectRequestStore(tmp_path / "state")
    transport, record = _post(monkeypatch, store, cli._new_record(_drive_args(source), store))
    source.write_text("changed after the card\n", encoding="utf-8")
    calls: list[tuple[str, str, Path]] = []

    def publisher(kind, title, artifacts, *, on, project):  # noqa: ANN001, ANN202
        calls.append((kind, title, artifacts[0][0]))
        return type("R", (), {"links": ("https://drive.example/f",), "action": "created"})()

    _approve(transport, record, by="777", bot=True)
    _tick(store, transport)
    assert (store.get(record.request_id) or record).status == "posted"
    _approve(transport, record)
    result = _tick(store, transport, publisher)
    done = store.get(record.request_id) or record
    assert result.done == (record.request_id,) and done.status == "done"
    assert calls == [("doctype", "조회결과", Path(str(record.payload["frozen_path"])))]
    assert Path(str(record.payload["frozen_path"])).read_text(encoding="utf-8") == "a,b\n"
    assert done.receipt and done.receipt["links"] == ["https://drive.example/f"]


def test_tampered_frozen_file_is_not_saved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "r.csv"
    source.write_text("a\n", encoding="utf-8")
    store = EffectRequestStore(tmp_path / "state")
    transport, record = _post(monkeypatch, store, cli._new_record(_drive_args(source), store))
    Path(str(record.payload["frozen_path"])).write_text("tampered\n", encoding="utf-8")
    _approve(transport, record)
    result = _tick(store, transport)
    assert result.failed == (record.request_id,)
    assert "바뀌었다" in (store.get(record.request_id) or record).last_error


def test_cancel_wins_and_runs_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = EffectRequestStore(tmp_path / "state")
    record = cli._new_record(_command_args(["true"], tmp_path), store)
    transport, record = _post(monkeypatch, store, record)
    _approve(transport, record)
    transport.reactions.setdefault((record.message_id or "", "\u26d4"), []).append((OWNER, False))
    result = _tick(store, transport, runner=lambda *a, **k: pytest.fail("cancelled request ran"))
    assert result.cancelled == (record.request_id,)


def test_command_runs_once_and_reports_masked_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = EffectRequestStore(tmp_path / "state")
    transport, record = _post(monkeypatch, store, cli._new_record(_command_args(["notify", "--to", "x"], tmp_path), store))
    runs: list[list[str]] = []

    def runner(argv, **kwargs):  # noqa: ANN001, ANN003, ANN202
        runs.append(argv)
        return subprocess.CompletedProcess(argv, 0, "sent to person@example.org\n", "")

    _approve(transport, record)
    _tick(store, transport, runner=runner)
    _tick(store, transport, runner=runner)
    done = store.get(record.request_id) or record
    assert runs == [["notify", "--to", "x"]]
    assert done.status == "done" and "person@example.org" not in str(done.receipt)


def test_failed_command_is_final_and_a_dead_tick_is_never_rerun(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = EffectRequestStore(tmp_path / "state")
    transport, record = _post(monkeypatch, store, cli._new_record(_command_args(["notify"], tmp_path), store))
    _approve(transport, record)
    _tick(store, transport, runner=lambda argv, **k: subprocess.CompletedProcess(argv, 3, "", "boom"))
    failed = store.get(record.request_id) or record
    assert failed.status == "failed" and "종료 코드 3" in failed.last_error
    store.update(replace(failed, status="running"))
    result = _tick(store, transport, runner=lambda *a, **k: pytest.fail("a claimed command ran twice"))
    assert result.failed == (record.request_id,)


def test_changed_payload_after_the_card_runs_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = EffectRequestStore(tmp_path / "state")
    transport, record = _post(monkeypatch, store, cli._new_record(_command_args(["notify"], tmp_path), store))
    store.update(replace(record, payload={**record.payload, "argv": ["rm", "-rf", "x"]}))
    _approve(transport, record)
    _tick(store, transport, runner=lambda *a, **k: pytest.fail("changed argv ran"))
    assert (store.get(record.request_id) or record).status == "failed"


@pytest.mark.parametrize(("argv", "code"), [
    (["passwd", "cha"], "owner-only:credential"),
    (["sudo", "systemctl", "restart", "x"], "owner-only:privilege"),
    (["curl", "-X", "PUT", "https://discord.com/api/channels/1/messages/2/reactions/%E2%9C%85/@me"], "owner-only:self-approval"),
    (["python3", "-m", "mailon.main", "send", "--to", "a"], "dedicated:mail"),
    (["gws", "calendar", "events", "insert"], "dedicated:calendar"),
    (["gws", "drive", "permissions", "create"], "dedicated:drive-share"),
    (["gws", "drive", "files", "create"], "dedicated:drive-upload"),
])
def test_owner_only_and_dedicated_effects_are_refused_with_a_reason(argv: list[str], code: str) -> None:
    refused = policy.refusal("작업", tuple(argv))
    assert refused is not None and refused.code == code and refused.reason


def test_a_plain_remote_post_gets_a_card() -> None:
    assert policy.refusal("실험 서버 웹훅 알림", ("curl", "-X", "POST", "https://hooks.example/x")) is None


def test_cli_refuses_with_exit_4_before_any_network(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["request", "command", "--summary", "비밀번호 바꾸기", "--target", "계정",
                     "--cwd", str(tmp_path), "--", "passwd"])
    assert code == cli.EXIT_REFUSED
    assert "owner-only:credential" in capsys.readouterr().err
