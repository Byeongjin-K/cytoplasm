"""W3-1 calendar skill — KST parsing / ambiguity re-ask / gws argv / gate parity."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from importlib import import_module
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "calendar" / "scripts"))

calendar_core = import_module("calendar_core")
calendar_cli = import_module("calendar_cli")
calendar_gate = import_module("calendar_gate")
from automation.interop import effect_approval  # noqa: E402

NOW = datetime(2026, 7, 15, 12, 0, tzinfo=calendar_core.KST)  # Wednesday


# --- parsing: happy paths -----------------------------------------------------

def test_parse_tomorrow_afternoon_default_duration() -> None:
    parsed = calendar_core.parse_request("내일 오후 3시 실험 미팅", NOW)
    assert parsed.summary == "실험 미팅"
    assert parsed.start.isoformat() == "2026-07-16T15:00:00+09:00"
    assert parsed.end.isoformat() == "2026-07-16T16:00:00+09:00"


def test_parse_hhmm_and_day_after_tomorrow() -> None:
    parsed = calendar_core.parse_request("모레 14:30 치과", NOW)
    assert parsed.start.isoformat() == "2026-07-17T14:30:00+09:00"
    assert parsed.summary == "치과"


def test_parse_next_week_weekday_with_half_hour() -> None:
    parsed = calendar_core.parse_request("다음주 화요일 오전 10시 반 세미나", NOW)
    assert parsed.start.isoformat() == "2026-07-21T10:30:00+09:00"
    assert parsed.summary == "세미나"


def test_parse_month_day_with_range() -> None:
    parsed = calendar_core.parse_request("7월 20일 오후 2시부터 오후 4시까지 워크숍", NOW)
    assert parsed.start.isoformat() == "2026-07-20T14:00:00+09:00"
    assert parsed.end.isoformat() == "2026-07-20T16:00:00+09:00"


def test_parse_duration_hours() -> None:
    parsed = calendar_core.parse_request("내일 오후 2시 미팅 2시간", NOW)
    assert parsed.end.isoformat() == "2026-07-16T16:00:00+09:00"
    assert parsed.summary == "미팅"


def test_parse_strips_particles_and_verbs() -> None:
    parsed = calendar_core.parse_request("내일 오후 3시에 실험 미팅 잡아줘", NOW)
    assert parsed.summary == "실험 미팅"


@pytest.mark.parametrize(
    ("text", "summary", "start", "end"),
    [
        ("내일 오후 3시 30분 peer-test 미팅", "peer-test 미팅", "15:30", "16:30"),
        ("내일 peer-test 오후 3시부터 30분 미팅", "peer-test 미팅", "15:00", "15:30"),
    ],
)
def test_parse_korean_time_forms_keep_title_free_of_time_grammar(
    text: str, summary: str, start: str, end: str
) -> None:
    # Given / When
    parsed = calendar_core.parse_request(text, NOW)

    # Then
    assert parsed.summary == summary
    assert parsed.start.strftime("%H:%M") == start
    assert parsed.end.strftime("%H:%M") == end


# --- parsing: ambiguity → re-ask (never guess) --------------------------------

@pytest.mark.parametrize(
    "text",
    ["다음주쯤 미팅", "내일 미팅", "내일 3시 미팅", "다음주 회의 오후 3시", "나중에 회식"],
)
def test_ambiguous_inputs_raise_reask(text: str) -> None:
    with pytest.raises(calendar_core.AmbiguousTime) as excinfo:
        calendar_core.parse_request(text, NOW)
    assert excinfo.value.question


def test_past_time_rejected() -> None:
    with pytest.raises(calendar_core.ParseRejected):
        calendar_core.parse_request("오늘 오전 9시 회고", NOW)


# --- gws argv + change summary -------------------------------------------------

def _create_argv() -> tuple[str, ...]:
    parsed = calendar_core.parse_request("내일 오후 3시 실험 미팅", NOW)
    return calendar_core.build_create_argv("primary", parsed)


def test_build_create_argv_shape() -> None:
    argv = _create_argv()
    assert argv[:4] == ("gws", "calendar", "events", "insert")
    body = json.loads(argv[argv.index("--json") + 1])
    assert body["summary"] == "실험 미팅"
    assert body["start"] == {"dateTime": "2026-07-16T15:00:00+09:00", "timeZone": "Asia/Seoul"}


def test_build_delete_argv_shape() -> None:
    argv = calendar_core.build_delete_argv("primary", "evt123")
    assert argv[:4] == ("gws", "calendar", "events", "delete")
    assert json.loads(argv[-1]) == {"calendarId": "primary", "eventId": "evt123"}


def test_change_summary_renders_korean_fields() -> None:
    text = calendar_core.render_change_summary(
        action="create", summary="실험 미팅", start="2026-07-16T15:00:00+09:00",
        end="2026-07-16T16:00:00+09:00", calendar_id="primary", event_id="",
    )
    assert "동작: 생성" in text
    assert "2026-07-16 (목) 15:00 ~ 16:00 KST" in text
    assert "(신규)" in text


# --- gate parity: action hash + approval record contract -----------------------

def test_action_hash_parity_with_effect_approval() -> None:
    import shlex

    argv = _create_argv()
    call = effect_approval.ToolCall(tool_name="gws", arguments={"command": shlex.join(argv)})
    expected = effect_approval.action_hash(call, calendar_core.EXTERNAL_EFFECT_TARGET_ID)
    assert calendar_core.external_effect_action_hash(argv) == expected


def _approved_context(tmp_path: Path, monkeypatch, argv: tuple[str, ...]) -> Path:
    log = tmp_path / "approvals.jsonl"
    monkeypatch.setenv("CALENDAR_APPROVAL_LOG", str(log))
    approval = calendar_gate.Approval(ref="msg-1", method="signed_injection_e2e", owner="owner-1")
    calendar_gate._append_record(calendar_gate._approval_record(argv, approval))
    return log


def test_confirmed_mutation_passes_deployed_gate_in_e2e_mode(tmp_path, monkeypatch) -> None:
    import shlex

    argv = _create_argv()
    log = _approved_context(tmp_path, monkeypatch, argv)
    call = effect_approval.ToolCall(tool_name="gws", arguments={"command": shlex.join(argv)})
    decision = effect_approval.validate_approval(
        call, calendar_core.EXTERNAL_EFFECT_TARGET_ID,
        effect_approval.ApprovalContext(approval_log=log, owner_id="owner-1", e2e_test_mode=True),
    )
    assert decision.external_effect and decision.allowed


def test_unconfirmed_or_production_e2e_record_stays_blocked(tmp_path, monkeypatch) -> None:
    import shlex

    argv = _create_argv()
    log = _approved_context(tmp_path, monkeypatch, argv)
    call = effect_approval.ToolCall(tool_name="gws", arguments={"command": shlex.join(argv)})
    production = effect_approval.validate_approval(
        call, calendar_core.EXTERNAL_EFFECT_TARGET_ID,
        effect_approval.ApprovalContext(approval_log=log, owner_id="owner-1", e2e_test_mode=False),
    )
    assert production.external_effect and not production.allowed  # e2e record refused in production
    other = calendar_core.build_delete_argv("primary", "unapproved-evt")
    unapproved = effect_approval.validate_approval(
        effect_approval.ToolCall(tool_name="gws", arguments={"command": shlex.join(other)}),
        calendar_core.EXTERNAL_EFFECT_TARGET_ID,
        effect_approval.ApprovalContext(approval_log=log, owner_id="owner-1", e2e_test_mode=True),
    )
    assert unapproved.external_effect and not unapproved.allowed


# --- draft store: no mutation before confirm -----------------------------------


@pytest.fixture
def draft_gate_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    gate_dir = tmp_path / "gate"
    peers = tmp_path / "peers.yaml"
    peers.write_text(
        'version: 1\npeers:\n  agent-cha:\n    bot_user_id: "111111111111111111"\n'
        '    bot_name: Owner-Agent\n  peer-test:\n    bot_user_id: "222222222222222222"\n'
        "    bot_name: Test-Peer\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CALENDAR_GATE_DIR", str(gate_dir))
    monkeypatch.setenv("CALENDAR_PEERS_CONFIG", str(peers))
    return gate_dir


def test_draft_create_peer_named_window_with_cue_refuses_to_coordination(
    draft_gate_dir: Path,
) -> None:
    # Given: a peer is named AND the owner asks to negotiate a time window.
    args = argparse.Namespace(
        text="peer-test와 다음주 오전에 가능한 시간 조율해줘", summary="",
        calendar="primary", channel_id="dm",
    )

    # When: calendar draft creation is requested.
    with pytest.raises(calendar_gate.GateError) as excinfo:
        calendar_cli.cmd_draft_create(args)

    # Then: routing is refused before any draft is written.
    assert excinfo.value.exit_code == 4
    assert "ROUTING-REJECT" in str(excinfo.value)
    assert "coordination" in str(excinfo.value)
    assert not (draft_gate_dir / "drafts").exists()


def test_draft_create_peer_named_with_exact_time_creates_solo_draft(
    draft_gate_dir: Path,
) -> None:
    # Given: a peer NAME appears but the owner fixed an exact single time.
    # Post-incident (2026-07-20): the peer name is a title token here, NOT a
    # negotiation trigger — this must produce one solo calendar draft and must
    # NOT fan out to coordination (which drifted to a different day/time).
    args = argparse.Namespace(
        text="peer-test랑 다음주 수요일 오전 10시 30분 미팅", summary="",
        calendar="primary", channel_id="dm", digest_day="",
    )

    # When: calendar draft creation is requested.
    record = calendar_cli.cmd_draft_create(args)

    # Then: exactly one solo draft is written; no routing rejection.
    assert record == 0
    assert len(tuple((draft_gate_dir / "drafts").glob("*.json"))) == 1


def test_draft_create_bare_peer_name_without_cue_clarifies_without_draft(
    draft_gate_dir: Path,
) -> None:
    # Given: a bare peer name in vague free text (no exact time, no cue).
    args = argparse.Namespace(
        text="peer-test 다음주 미팅", summary="", calendar="primary", channel_id="dm"
    )

    # When: calendar draft creation is requested.
    with pytest.raises(calendar_gate.GateError) as excinfo:
        calendar_cli.cmd_draft_create(args)

    # Then: fail-closed — clarify, no draft written.
    assert excinfo.value.exit_code == 4
    assert "ROUTING-CLARIFY" in str(excinfo.value)
    assert not (draft_gate_dir / "drafts").exists()


def test_draft_create_when_request_is_solo_creates_draft(draft_gate_dir: Path) -> None:
    # Given: a request that names no registered peer.
    args = argparse.Namespace(
        text="내일 오후 3시 실험 미팅", summary="", calendar="primary", channel_id="dm", digest_day=""
    )

    # When: calendar draft creation is requested.
    record = calendar_cli.cmd_draft_create(args)

    # Then: the normal pending draft is retained.
    assert record == 0
    assert len(tuple((draft_gate_dir / "drafts").glob("*.json"))) == 1


def test_draft_create_when_request_names_owner_agent_creates_draft(draft_gate_dir: Path) -> None:
    # Given: the configured owner agent id, not a peer, is present in the request.
    args = argparse.Namespace(
        text="agent-cha와 내일 오후 3시 실험 미팅", summary="", calendar="primary", channel_id="dm", digest_day=""
    )

    # When: calendar draft creation is requested.
    record = calendar_cli.cmd_draft_create(args)

    # Then: the owner id is not misclassified as a peer.
    assert record == 0
    assert len(tuple((draft_gate_dir / "drafts").glob("*.json"))) == 1

def test_draft_hash_binds_mutation_and_tamper_is_refused(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CALENDAR_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("CALENDAR_APPROVAL_LOG", str(tmp_path / "approvals.jsonl"))
    record = calendar_gate.create_draft(
        action="create", argv=_create_argv(), calendar_id="primary", event_id="",
        summary="실험 미팅", start="2026-07-16T15:00:00+09:00",
        end="2026-07-16T16:00:00+09:00", channel_id="dm",
    )
    assert record["status"] == "pending"
    assert not (tmp_path / "approvals.jsonl").exists()  # drafting writes no approval
    tampered = {**record, "argv": [*record["argv"][:-1], '{"summary":"변조"}']}
    approval = calendar_gate.Approval(ref="msg-1", method="signed_injection_e2e", owner="o")
    with pytest.raises(calendar_gate.GateError) as excinfo:
        calendar_gate.execute_draft(tampered, approval)
    assert excinfo.value.exit_code == 1
    assert not (tmp_path / "approvals.jsonl").exists()  # refused before any record/exec


def test_audit_record_has_w06_required_fields() -> None:
    draft = {
        "action": "create", "argv": list(_create_argv()), "calendar_id": "primary",
        "event_id": "", "id": "abc123", "sha256": "x", "start": "", "end": "",
        "summary": "실험 미팅", "channel_id": "dm", "created": "2026-07-15T00:00:00Z",
        "status": "pending",
    }
    approval = calendar_gate.Approval(ref="dm:1", method="owner_dm_reply", owner="o")
    record = calendar_gate._audit_record(draft, approval, event_id="evt9", status="executed")
    assert record["action"] == "calendar.create"
    assert record["target_id"] == "event:primary:evt9"
    assert record["hash"].startswith("sha256:") and len(record["hash"]) == 71
    assert record["timestamp"].endswith("Z")
    assert "실험" not in json.dumps(record, ensure_ascii=False)  # no calendar content leaks
