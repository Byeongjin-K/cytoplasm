"""메일 승인 요청 직후 CLI 가 승인 스레드 링크를 한 줄로 내는지 고정한다.

2026-09-23 소유자 지적: compose 후 에이전트 답장이 "승인 스레드에 게시했습니다" 라고만 말해
소유자가 스레드를 직접 찾아야 했다. CLI 출력(`COMPOSED draft=… posted=…`)에 좌표가 없어서
에이전트가 인용할 링크 자체가 없었다. 여기서는 그 좌표 줄(`APPROVAL-THREAD`)만 본다 —
compose 게이트 본체의 회귀는 `test_mail_compose_gate.py` 가 소유한다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "mail" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import triage_cli  # noqa: E402
import triage_gate  # noqa: E402
import triage_mode  # noqa: E402
import triage_pipeline  # noqa: E402
import triage_store  # noqa: E402

GUILD = "300000000000000001"
THREAD = "300000000000000002"
CARD = "300000000000000003"


def _posted(**extra: str) -> dict[str, str]:
    return {"id": "abc123", "subject": "TechLibrary 소개 자료 공유", "message_id": CARD, **extra}


def _compose(monkeypatch: pytest.MonkeyPatch, draft: dict[str, str]) -> None:
    monkeypatch.setattr(triage_mode, "effective_mode", lambda: "full-go")
    monkeypatch.setattr(triage_pipeline, "compose_and_post", lambda *_a, **_k: draft)
    args = triage_cli.build_parser().parse_args(
        ["compose", "--to", "x@y.z", "--subject", "s", "--body", "b"]
    )
    assert triage_cli.cmd_compose(args) == 0


def test_compose_prints_the_approval_thread_link(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: a posted compose draft whose binding stored guild and thread coordinates
    draft = _posted(approval_guild_id=GUILD, approval_thread_id=THREAD)
    # When: the owner-instructed compose runs
    _compose(monkeypatch, draft)
    # Then: stdout names the exact thread the agent must cite
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "COMPOSED draft=abc123 posted=1"
    assert out[1] == (
        f"APPROVAL-THREAD draft=abc123 url=[승인스레드](<https://discord.com/channels/{GUILD}/{THREAD}/{CARD}>)"
    )


def test_compose_without_coordinates_does_not_guess_a_link(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: a posted draft whose guild is unknown (legacy/owner-DM binding)
    draft = _posted(approval_thread_id=THREAD)
    # When: compose runs
    _compose(monkeypatch, draft)
    # Then: no fabricated URL — the draft id is the search key instead
    out = capsys.readouterr().out.splitlines()
    assert out[1] == "APPROVAL-THREAD draft=abc123 url=unavailable search=abc123"


def test_unposted_compose_prints_no_thread_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: --no-post style draft (nothing was posted to Discord)
    draft = {"id": "abc123", "subject": "s"}
    # When: compose runs
    _compose(monkeypatch, draft)
    # Then: only the COMPOSED marker — there is no thread to cite
    assert capsys.readouterr().out.splitlines() == ["COMPOSED draft=abc123 posted=0"]


def test_instruction_draft_prints_the_approval_thread_link(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: an owner-instructed reply draft that was posted and bound to a thread
    stored = _posted(approval_guild_id=GUILD, approval_thread_id=THREAD)
    monkeypatch.setattr(triage_mode, "effective_mode", lambda: "full-go")
    monkeypatch.setattr(triage_gate, "has_draft_for", lambda _uid: False)
    monkeypatch.setattr(triage_cli, "_get_mail", lambda _uid: {"sender": "a@b.c"})
    monkeypatch.setattr(
        triage_pipeline, "_classify",
        lambda *_a, **_k: argparse.Namespace(category="x"),
    )
    monkeypatch.setattr(
        triage_pipeline, "_draft_and_post",
        lambda *_a, **_k: ["draft:abc123", f"posted:{CARD}"],
    )
    monkeypatch.setattr(triage_gate, "load_draft", lambda draft_id: dict(stored, id=draft_id))
    monkeypatch.setattr(triage_store, "record_processed", lambda *_a, **_k: None)
    args = triage_cli.build_parser().parse_args(["draft", "--uid", "u-1", "--instruction", "i"])
    # When: the draft command runs
    assert triage_cli.cmd_draft(args) == 0
    # Then: the thread link follows the DRAFTED marker
    assert capsys.readouterr().out.splitlines()[-1] == (
        f"APPROVAL-THREAD draft=abc123 url=[승인스레드](<https://discord.com/channels/{GUILD}/{THREAD}/{CARD}>)"
    )
