"""W4-3 budget skill — schema validation / diff / idempotency / masking /
retry queue / gate parity with the deployed external-effect gate."""

from __future__ import annotations

import json
import shlex
import sys
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "budget" / "scripts"))

import budget_core  # noqa: E402
import budget_gate  # noqa: E402
import budget_store  # noqa: E402
from automation.interop import effect_approval  # noqa: E402

budget_cli = import_module("budget_cli")

NOW = datetime(2026, 7, 16, 3, 0, tzinfo=UTC)
BASE = [
    ("인건비", "100", "10", "90", "2026-07-14"),
    ("재료비", "200", "0", "200", "2026-07-14"),
]


def test_sheet_id_requires_external_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: no private runtime setting supplies the spreadsheet identifier.
    monkeypatch.delenv("BUDGET_SHEET_ID", raising=False)

    # When / Then: lookup fails closed instead of using a tracked production identifier.
    with pytest.raises(budget_gate.GateError, match="BUDGET_SHEET_ID"):
        budget_cli._sheet_id()


def _payload(rows: list[list[str]]) -> str:
    values = [["[규칙]"], ["1"], ["2"], ["3"], [], list(budget_core.HEADER_EXPECTED), *rows]
    return json.dumps({"majorDimension": "ROWS", "values": values}, ensure_ascii=False)


# --- payload parsing / header contract -----------------------------------------

def test_parse_payload_skips_gws_banner_lines() -> None:
    raw = "Using keyring backend: keyring\n" + _payload([["인건비", "1", "2", "3", "d"]])
    values = budget_core.parse_balance_payload(raw)
    budget_core.validate_header(values)
    assert budget_core.data_rows(values) == [("인건비", "1", "2", "3", "d")]


def test_header_mismatch_raises_schema_error() -> None:
    values = [[], [], [], [], [], ["잘못된", "헤더", "행", "임", "다"]]
    with pytest.raises(budget_core.SheetSchemaError, match="헤더 불일치"):
        budget_core.validate_header(values)


def test_missing_header_row_raises() -> None:
    with pytest.raises(budget_core.SheetSchemaError, match="6행"):
        budget_core.validate_header([["only"], ["four"], ["rows"], ["here"]])


def test_data_rows_pad_and_drop_empty() -> None:
    values = [[], [], [], [], [], list(budget_core.HEADER_EXPECTED), ["인건비", "1"], [""], []]
    assert budget_core.data_rows(values) == [("인건비", "1", "", "", "")]


# --- diff -----------------------------------------------------------------------

def test_diff_detects_changed_field_only() -> None:
    new = [BASE[0], ("재료비", "200", "50", "150", "2026-07-16")]
    changes = budget_core.diff_rows(list(BASE), new)
    assert [(c.item, c.field, c.old, c.new) for c in changes] == [
        ("재료비", "집행액", "0", "50"),
        ("재료비", "잔액", "200", "150"),
        ("재료비", "최종수정", "2026-07-14", "2026-07-16"),
    ]


def test_diff_detects_added_and_removed_items() -> None:
    changes = budget_core.diff_rows(list(BASE), [BASE[0], ("출장비", "9", "0", "9", "d")])
    kinds = {(c.item, c.field) for c in changes}
    assert ("출장비", "(신규 항목)") in kinds
    assert ("재료비", "(항목 삭제)") in kinds


def test_snapshot_hash_is_stable_and_order_sensitive() -> None:
    assert budget_core.snapshot_hash(list(BASE)) == budget_core.snapshot_hash(list(BASE))
    assert budget_core.snapshot_hash(list(BASE)) != budget_core.snapshot_hash(list(reversed(BASE)))


# --- store: claim idempotency + retry queue -------------------------------------

def test_claim_change_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "budget.db"
    key = budget_core.claim_key("a" * 64, "b" * 64)
    assert budget_store.claim_change(db, key, "t1") is True
    assert budget_store.claim_change(db, key, "t2") is False
    budget_store.release_change(db, key)
    assert budget_store.claim_change(db, key, "t3") is True


def test_snapshot_roundtrip_keeps_latest(tmp_path: Path) -> None:
    db = tmp_path / "budget.db"
    assert budget_store.latest_snapshot(db) is None
    budget_store.store_snapshot(db, "h1", list(BASE), "t1")
    budget_store.store_snapshot(db, "h2", [BASE[0]], "t2")
    latest = budget_store.latest_snapshot(db)
    assert latest is not None
    assert latest[0] == "h2"
    assert latest[1] == [BASE[0]]


def test_retry_queue_roundtrip(tmp_path: Path) -> None:
    db = tmp_path / "budget.db"
    assert budget_store.pending_retries(db) == []
    retry_id = budget_store.queue_retry(db, "gws sheets read 실패 rc=1", "t1")
    assert retry_id >= 1
    assert [row[0] for row in budget_store.pending_retries(db)] == [retry_id]
    assert budget_store.resolve_retries(db, "t2") == 1
    assert budget_store.pending_retries(db) == []


# --- masking --------------------------------------------------------------------

def _approvals_draft() -> dict:
    return {
        "id": "abc123",
        "sha256": "f" * 64,
        "changes": [["재료비", "집행액", "1234567", "7654321"]],
        "prev_hash": "p" * 64,
        "new_hash": "n" * 64,
    }


def test_approvals_message_masks_every_value() -> None:
    message = budget_core.render_approvals_message(_approvals_draft())
    assert "1234567" not in message
    assert "7654321" not in message
    assert "MASKED-" in message
    assert "재료비" in message
    assert "abc123" in message
    assert "f" * 64 in message


def test_approvals_message_carries_only_the_instruction_it_is_handed() -> None:
    # Given: the draft-only render path, which knows no approval surface yet
    draft = _approvals_draft()
    # When / Then: it invents no reaction line, and renders the one it is given verbatim
    assert "반응(기본)" not in budget_core.render_approvals_message(draft)
    handed = budget_core.render_approvals_message(draft, instruction="이 메시지에 ✅ 실행 / ⛔ 취소")
    assert "- 반응(기본): 이 메시지에 ✅ 실행 / ⛔ 취소" in handed


def test_mask_value_deterministic_opaque() -> None:
    assert budget_core.mask_value("12345") == budget_core.mask_value("12345")
    assert "12345" not in budget_core.mask_value("12345")
    assert budget_core.mask_value("") == "[없음]"


def test_redact_hides_emails_and_long_numbers() -> None:
    masked = budget_core.redact("mail me at someone@example.com id 152648282079")
    assert "someone@example.com" not in masked
    assert "152648282079" not in masked


def test_request_mail_contains_changes_and_regulation() -> None:
    changes = [budget_core.Change("재료비", "집행액", "0", "50")]
    subject, body = budget_core.render_mail(changes, prev_hash="p" * 64, new_hash="n" * 64, now=NOW)
    assert subject.startswith("[과제비] 원장 변경 통지 및 처리 요청")
    assert "재료비 / 집행액: 0 → 50" in body
    assert "운영 규칙" in body
    assert "소유자의 명시적 승인(✅) 이후에만 발송되었습니다" in body


# --- gate parity with the deployed pre_tool_call external-effect gate -----------

def test_action_hash_parity_with_effect_approval() -> None:
    argv = budget_core.build_gmail_argv("self@example.invalid", "제목", "본문 줄1\n줄2")
    call = effect_approval.ToolCall(
        tool_name="gws", arguments={"command": shlex.join(argv)}
    )
    expected = effect_approval.action_hash(call, budget_core.EXTERNAL_EFFECT_TARGET_ID)  # noqa: SLF001
    assert budget_core.external_effect_action_hash(argv) == expected


def test_gmail_argv_matches_denylist_rule() -> None:
    argv = budget_core.build_gmail_argv("self@example.invalid", "s", "b")
    call = effect_approval.ToolCall(tool_name="gws", arguments={"command": shlex.join(argv)})
    decision = effect_approval.validate_approval(
        call, budget_core.EXTERNAL_EFFECT_TARGET_ID,
        effect_approval.ApprovalContext(approval_log=None, owner_id="1", e2e_test_mode=False),
    )
    assert decision.external_effect is True
    assert decision.allowed is False
    assert decision.target_id == budget_core.EXTERNAL_EFFECT_TARGET_ID


def test_gate_approval_record_unlocks_effect_approval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The record execute_draft appends is exactly what the deployed gate reads."""
    monkeypatch.setenv("BUDGET_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("BUDGET_APPROVAL_LOG", str(tmp_path / "approvals.jsonl"))
    stub = tmp_path / "gws-stub"
    stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stub.chmod(0o700)
    monkeypatch.setenv("BUDGET_GWS_BIN", str(stub))
    draft = budget_gate.create_draft(
        changes=[budget_core.Change("재료비", "집행액", "0", "50")],
        subject="s", body="b", recipient="self@example.invalid",
        prev_hash="p" * 64, new_hash="n" * 64, claim_key="k",
    )
    budget_gate.execute_draft(
        draft, budget_gate.Approval(ref="reaction:123", method="manual_reaction", owner="42"),
    )
    call = effect_approval.ToolCall(
        tool_name="gws", arguments={"command": shlex.join(tuple(draft["argv"]))}
    )
    decision = effect_approval.validate_approval(
        call, budget_core.EXTERNAL_EFFECT_TARGET_ID,
        effect_approval.ApprovalContext(
            approval_log=tmp_path / "approvals.jsonl", owner_id="42", e2e_test_mode=False,
        ),
    )
    assert decision.allowed is True


# --- draft binding ---------------------------------------------------------------

def test_execute_refuses_tampered_draft(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUDGET_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("BUDGET_APPROVAL_LOG", str(tmp_path / "approvals.jsonl"))
    draft = budget_gate.create_draft(
        changes=[budget_core.Change("재료비", "집행액", "0", "50")],
        subject="s", body="b", recipient="self@example.invalid",
        prev_hash="p" * 64, new_hash="n" * 64, claim_key="k",
    )
    tampered = {**draft, "argv": [*draft["argv"][:4], "attacker@example.invalid", *draft["argv"][5:]]}
    with pytest.raises(budget_gate.GateError, match="해시 불일치"):
        budget_gate.execute_draft(
            tampered, budget_gate.Approval(ref="r", method="manual_reaction", owner="42"),
        )
    assert not (tmp_path / "approvals.jsonl").exists()


def test_send_log_masks_recipient(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUDGET_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("BUDGET_APPROVAL_LOG", str(tmp_path / "approvals.jsonl"))
    stub = tmp_path / "gws-stub"
    stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stub.chmod(0o700)
    monkeypatch.setenv("BUDGET_GWS_BIN", str(stub))
    draft = budget_gate.create_draft(
        changes=[budget_core.Change("재료비", "잔액", "1", "2")],
        subject="s", body="b", recipient="self@example.invalid",
        prev_hash="p" * 64, new_hash="n" * 64, claim_key="k2",
    )
    budget_gate.execute_draft(
        draft, budget_gate.Approval(ref="reaction:1", method="manual_reaction", owner="42"),
    )
    send_log = (tmp_path / "gate" / "send-log.jsonl").read_text(encoding="utf-8")
    assert "self@example.invalid" not in send_log
    assert '"status":"sent"' in send_log
