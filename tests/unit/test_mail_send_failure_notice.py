"""A failed approved send is counted, backed off, capped and told to the owner.

2026-10-13: an owner-approved forward failed at the webmail compose step every watch
tick for over an hour. ``external_service_error`` was not counted as a failure, so the
draft stayed ``pending``, was retried every tick without end, the tick's failure never
reached the owner's approval thread, and the record said only
``browser_command_failed``.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))
sys.path.insert(0, str(Path(__file__).parent))

import triage_cli  # noqa: E402
import triage_confirm  # noqa: E402
import triage_gate  # noqa: E402
import triage_mode  # noqa: E402
import triage_store  # noqa: E402
from test_mail_compose_gate import (  # noqa: E402
    MESSAGE_ID,
    OWNER_ID,
    _compose_draft,
    _compose_env,
)

_COMPOSE_FAILURE = json.dumps({
    "status": "error", "error_code": "external_service_error", "stage": "provider",
    "retryable": True, "cause": "compose_not_ready", "step": "compose_open",
    "browser_command": "eval", "error_kind": "js_type_error",
    "message": "free text that must stay out of every record",
})


def _approval() -> triage_gate.Approval:
    return triage_gate.Approval(ref=f"reaction:{MESSAGE_ID}", method="manual_reaction",
                                owner=OWNER_ID)


def _stored(tmp_path: Path, draft_id: str) -> dict:
    return json.loads((tmp_path / "gate" / "drafts" / f"{draft_id}.json").read_text())


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setenv("TRIAGE_APPROVAL_LOG", str(tmp_path / "approvals.jsonl"))
    _compose_env(monkeypatch, tmp_path)
    monkeypatch.setattr(triage_mode, "effective_mode", lambda: "full-go")
    monkeypatch.setattr(triage_gate, "_run_send", lambda argv: (2, _COMPOSE_FAILURE, ""))
    draft = _compose_draft()
    triage_mode.write_json(tmp_path / "gate" / "drafts" / f"{draft['id']}.json", draft)
    return draft


def test_a_browser_failure_is_counted_and_records_where_the_send_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an approved draft whose send stops at the compose step
    draft = _setup(tmp_path, monkeypatch)
    before = datetime.now(UTC)

    # When: the gate executes it
    with pytest.raises(triage_gate.GateError) as caught:
        triage_gate.execute_draft(draft, _approval())

    # Then: the record names the step, command and kind, and schedules a later retry
    stored = _stored(tmp_path, draft["id"])
    assert stored["status"] == "pending"
    assert stored["failed_attempts"] == 1
    assert stored["last_error"] == {
        "error_code": "external_service_error", "stage": "provider", "retryable": True,
        "cause": "compose_not_ready", "step": "compose_open", "browser_command": "eval",
        "error_kind": "js_type_error",
    }
    due = datetime.strptime(stored["next_attempt_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    assert timedelta(minutes=9) < due - before < timedelta(minutes=11)
    assert "step=compose_open" in str(caught.value)
    assert "free text" not in json.dumps(stored)
    assert triage_store.consecutive_send_failures(tmp_path / "triage.db") == 0


def test_retries_stop_after_the_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: an approved draft that keeps failing the same way
    draft = _setup(tmp_path, monkeypatch)

    # When: it is executed until it stops being pending
    for _ in range(10):
        current = _stored(tmp_path, draft["id"])
        if current["status"] != "pending":
            break
        with pytest.raises(triage_gate.GateError):
            triage_gate.execute_draft(current, _approval())

    # Then: it gave up after six attempts instead of retrying forever
    stored = _stored(tmp_path, draft["id"])
    assert stored["status"] == "blocked"
    assert stored["failed_attempts"] == 6
    assert stored["retry_exhausted"] is True
    assert "next_attempt_at" not in stored


def test_the_watch_tick_tells_the_owner_and_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: an approved failing draft and an approved draft that sends
    failing = _setup(tmp_path, monkeypatch)
    working = {**_compose_draft(), "id": "def456"}
    notices: list[tuple[str, str, str]] = []
    sent: list[str] = []

    def execute(draft: dict, approval: triage_gate.Approval) -> None:
        if draft["id"] == working["id"]:
            sent.append(draft["id"])
            return
        triage_gate.execute_draft(draft, approval)

    monkeypatch.delenv("E2E_TEST_MODE", raising=False)
    monkeypatch.setattr(triage_gate, "list_drafts", lambda: [failing, working])
    monkeypatch.setattr(triage_cli.mail_preflight, "guarded_execute_draft", execute)
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER_ID)
    monkeypatch.setattr(triage_confirm, "resolve_reaction", lambda _d: triage_confirm.APPROVE_EMOJI)
    monkeypatch.setattr(triage_cli, "_remind_pending", lambda *_a: None)
    monkeypatch.setattr(triage_cli.triage_approval, "expire_retired_approval", lambda _d: False)
    monkeypatch.setattr(triage_confirm, "notify_result",
                        lambda d, content, outcome="": notices.append((d["id"], content, outcome)))

    # When: the watch tick runs
    rc = triage_cli.cmd_watch(argparse.Namespace())

    # Then: the tick fails, the owner's thread hears why, and the other draft still sends
    assert rc == 6
    assert sent == [working["id"]]
    failure = next(content for draft_id, content, _ in notices if draft_id == failing["id"])
    assert failure.startswith("⚠️ 발송 실패")
    assert "compose_open" in failure and "compose_not_ready" in failure and "eval" in failure
    assert "다음 자동 재시도" in failure and "1/6" in failure
    assert f"SEND-FAILED draft={failing['id']}" in capsys.readouterr().err

    # And when: the next tick comes before the retry time
    notices.clear()
    monkeypatch.setattr(triage_gate, "list_drafts", lambda: [_stored(tmp_path, failing["id"])])
    rc = triage_cli.cmd_watch(argparse.Namespace())

    # Then: it waits instead of trying again
    assert rc == 0
    assert notices == []
    assert f"RETRY-WAIT draft={failing['id']}" in capsys.readouterr().out
