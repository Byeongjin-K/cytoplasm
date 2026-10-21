"""승인 요청 CLI 마다 게시 직후 `APPROVAL-THREAD` 줄을 내는지 고정한다(todo·calendar·coordination).

mail 은 `test_mail_approval_thread_link.py` 가 소유한다. 여기 세 스킬은 2026-09-23 todo 답장이
"생성된 승인 스레드에서 ✅" 로만 끝나 소유자가 스레드를 찾아야 했던 사건과 같은 결함을 닫는다.
"""
from __future__ import annotations

import argparse
import sys
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
for _skill in ("todo", "calendar", "coordination"):
    sys.path.insert(0, str(_REPO / "skills" / _skill / "scripts"))

GUILD = "300000000000000001"
THREAD = "300000000000000002"
URL = f"https://discord.com/channels/{GUILD}/{THREAD}"
COORDS = {"approval_guild_id": GUILD, "approval_thread_id": THREAD}


def test_todo_request_prints_the_request_thread(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: a todo request whose approval was posted into a bound thread
    todo_cli = import_module("todo_cli")
    todo_approval = import_module("todo_approval")
    runtime = import_module("todo_approval_runtime")
    monkeypatch.setenv("TODO_OWNER_ID", "owner-1")
    monkeypatch.setattr(
        todo_cli, "evaluate",
        lambda argv, *, context: SimpleNamespace(action_hash="sha256:h", target_id="t"),
    )
    monkeypatch.setattr(todo_approval, "request_cli_approval", lambda intent, owner: None)
    monkeypatch.setattr(runtime, "origin_record", lambda action_hash: dict(COORDS, id=action_hash))
    # When: the agent runs `request`
    assert todo_cli.main(["request", "--title", "장보기"]) == 0
    # Then: the thread link follows the REQUESTED marker
    assert capsys.readouterr().out.splitlines()[-1] == f"APPROVAL-THREAD hash=sha256:h url={URL}"


def test_calendar_post_confirm_prints_the_request_thread(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: a calendar draft that the approval binding stored coordinates into
    calendar_cli = import_module("calendar_cli")
    calendar_approval = import_module("calendar_approval")
    monkeypatch.setattr(calendar_cli.calendar_gate, "load_draft", lambda draft_id: dict(COORDS, id=draft_id))
    monkeypatch.setattr(
        calendar_approval, "request_confirmation", lambda draft: SimpleNamespace(dm_message_id="9"),
    )
    # When: the agent runs `post-confirm`
    assert calendar_cli.cmd_post_confirm(argparse.Namespace(draft="c1")) == 0
    # Then: the thread link follows the PENDING-OWNER marker
    assert capsys.readouterr().out.splitlines()[-1] == f"APPROVAL-THREAD draft=c1 url={URL}"


def test_coordination_pending_owner_prints_the_request_thread(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a pending owner confirmation bound to a thread
    lifecycle = import_module("coordination_lifecycle")
    entry = SimpleNamespace(draft_id="k1", approval_thread_id=THREAD, approval_guild_id=GUILD)
    # When: the owner leg reports the pending request
    lifecycle.report_pending(entry, "2026-09-24T10:00:00+09:00", "coord-1")
    # Then: PENDING-OWNER is followed by the thread link
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "PENDING-OWNER draft=k1 slot=2026-09-24T10:00:00+09:00 correlation=coord-1"
    assert out[1] == f"APPROVAL-THREAD draft=k1 url={URL}"
