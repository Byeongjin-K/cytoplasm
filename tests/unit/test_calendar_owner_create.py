"""Q233 owner-create coverage in a new file, leaving historical test evidence intact."""
from __future__ import annotations

import json
import subprocess
import sys
from importlib import import_module
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills/calendar/scripts"))
calendar_cli = import_module("calendar_cli")
calendar_core = import_module("calendar_core")
calendar_gate = import_module("calendar_gate")
calendar_confirm = import_module("calendar_confirm")
calendar_owner = import_module("calendar_owner")
calendar_output = import_module("calendar_output")
calendar_governed = import_module("calendar_governed")

CHANNEL = "111111111111111111"
MESSAGE = "222222222222222222"
OWNER_EMAIL = "owner@example.invalid"


@pytest.fixture
def owner_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    # Given: isolated state and no possible real Google or Discord transport.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CALENDAR_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("CALENDAR_APPROVAL_LOG", str(tmp_path / "approvals.jsonl"))
    monkeypatch.setenv("CALENDAR_PEERS_CONFIG", str(tmp_path / "absent-peers.yaml"))
    monkeypatch.setenv("CALENDAR_GWS_BIN", "fake-gws")
    monkeypatch.setenv("AUTOPHAGY_SKILL_LIVE_ROOT", str(tmp_path / "no-live"))
    monkeypatch.setattr(calendar_confirm, "owner_id", lambda: "synthetic-owner")
    monkeypatch.setattr(calendar_confirm, "_api", lambda *_a, **_kw: pytest.fail("real Discord forbidden"))
    return [
        "calendar_cli.py", "owner-create", "--text", "내일 오후 3시 실험 미팅",
        "--summary", "소유자 일정", "--origin-channel-id", CHANNEL,
        "--origin-message-id", MESSAGE,
    ]


def _fake_gws(monkeypatch: pytest.MonkeyPatch, *, failure: str = "") -> list[list[str]]:
    calls: list[list[str]] = []
    saved: dict[str, str] = {}

    def run(argv: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if argv[1:4] == ["calendar", "calendars", "get"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps({"id": OWNER_EMAIL}), "")
        if argv[1:4] == ["calendar", "events", "insert"]:
            if failure == "write":
                return subprocess.CompletedProcess(argv, 7, "", "synthetic failure")
            saved.update(json.loads(argv[argv.index("--json") + 1]))
            return subprocess.CompletedProcess(argv, 0, json.dumps({"id": "event-1"}), "")
        assert argv[1:4] == ["calendar", "events", "get"]
        title = "different" if failure == "readback" else saved["summary"]
        return subprocess.CompletedProcess(argv, 0, json.dumps({"id": "event-1", "summary": title}), "")

    monkeypatch.setattr(calendar_gate.subprocess, "run", run)
    return calls


def _with_attendee(monkeypatch: pytest.MonkeyPatch, email: str) -> None:
    build = calendar_core.build_create_argv

    def invited(calendar_id, request):
        argv = list(build(calendar_id, request))
        body = json.loads(argv[-1])
        body["attendees"] = [{"email": email}]
        argv[-1] = json.dumps(body)
        return tuple(argv)

    monkeypatch.setattr(calendar_core, "build_create_argv", invited)


@pytest.mark.parametrize("invite_owner", [False, True])
def test_calendar_owner_create_writes_and_verifies_solo_event(
    owner_command, tmp_path, monkeypatch, capsys, invite_owner,
) -> None:
    # Given: normal draft builder, real executor and fake gws responses.
    calls = _fake_gws(monkeypatch)
    if invite_owner:
        _with_attendee(monkeypatch, OWNER_EMAIL.upper())
    monkeypatch.setattr(sys, "argv", owner_command)

    # When: the verified owner command enters the CLI.
    result = calendar_cli.main()

    # Then: one insert and read-back, consumed draft, origin-bound audit and marker.
    assert result == 0
    assert [call[3] for call in calls if call[2] == "events"] == ["insert", "get"]
    record, = calendar_gate.list_drafts()
    assert record["summary"] == "소유자 일정"
    assert record["status"] == "executed"
    assert record["method"] == "owner_command"
    with pytest.raises(calendar_gate.GateError, match="pending"):
        calendar_gate.load_draft(record["id"])
    audit = [json.loads(line) for line in (tmp_path / "approvals.jsonl").read_text().splitlines()]
    assert audit[-1]["result"]["status"] == "executed"
    assert audit[-1]["approval"]["method"] == "owner_command"
    assert audit[-1]["approval"]["origin_channel_id"] == CHANNEL
    assert audit[-1]["approval"]["origin_message_id"] == MESSAGE
    assert f"OWNER-COMMAND-WRITTEN calendar {record['id']} create" in capsys.readouterr().out.splitlines()


def test_calendar_owner_create_posts_existing_card_for_outside_attendee(
    owner_command, monkeypatch, capsys,
) -> None:
    # Given: an invitation in the frozen write body and the existing poster seam.
    calls = _fake_gws(monkeypatch)
    _with_attendee(monkeypatch, "guest@example.invalid")
    posted = []

    def post(args):
        posted.append(calendar_gate.load_draft(args.draft))
        return 0

    monkeypatch.setattr(calendar_output, "cmd_post_confirm", post)
    monkeypatch.setattr(sys, "argv", owner_command)

    # When
    result = calendar_cli.main()

    # Then: the same pending draft goes to post-confirm, with no write/ledger marker.
    assert result == 0
    assert len(posted) == 1
    assert posted[0]["origin_message_id"] == MESSAGE
    assert posted[0]["status"] == "pending"
    assert all(call[2] != "events" for call in calls)
    output = capsys.readouterr().out
    assert f"CARD-POSTED draft={posted[0]['id']}" in output.splitlines()
    assert "OWNER-COMMAND-WRITTEN " not in output


@pytest.mark.parametrize("failure", ["write", "readback"])
def test_calendar_owner_create_failure_has_no_success_marker(
    owner_command, monkeypatch, capsys, failure,
) -> None:
    # Given: the executor or read-back fails through the actual preflight path.
    _fake_gws(monkeypatch, failure=failure)
    monkeypatch.setattr(sys, "argv", owner_command)

    # When
    result = calendar_cli.main()

    # Then: no successful-write ledger signal, nor a pending orphan to auto-post.
    assert result == 6
    assert "OWNER-COMMAND-WRITTEN " not in capsys.readouterr().out
    assert all(record["status"] != "pending" for record in calendar_gate.list_drafts())


def test_calendar_owner_create_respects_governed_guard(owner_command, monkeypatch) -> None:
    # Given: this CLI copy is outside the governed mount.
    monkeypatch.setattr(calendar_governed, "refusal", lambda *_a, **_kw: "STALE-SKILL-COPY-BLOCK")
    monkeypatch.setattr(sys, "argv", owner_command)

    # When / Then: the command is refused before creating or executing any draft.
    with pytest.raises(SystemExit) as raised:
        calendar_cli.main()
    assert raised.value.code == 3
    assert calendar_gate.list_drafts() == []


def test_calendar_owner_create_requires_origin(owner_command, monkeypatch) -> None:
    # Given: the caller omitted the owner message coordinates.
    monkeypatch.setattr(sys, "argv", owner_command[:-4])

    # When / Then
    with pytest.raises(SystemExit) as raised:
        calendar_cli.main()
    assert raised.value.code == 2
