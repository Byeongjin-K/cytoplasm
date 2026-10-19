"""Owner-command regressions live separately from the recorded approval tests."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from types import ModuleType

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "todo" / "scripts"))
_ARGV = [
    "write-now", "--title", "합성 할 일", "--notes", "합성 메모",
    "--due", "2026-10-20T00:00:00Z", "--tasklist", "list-fixture",
    "--origin-channel-id", "123456789012345678", "--origin-message-id", "123456789012345679",
]


@pytest.fixture
def todo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    module = import_module("todo_cli")
    monkeypatch.setenv("AUTOPHAGY_RUNTIME_ROOT", str(_REPO))
    monkeypatch.setenv("TODO_APPROVAL_ROOT", str(tmp_path / "state"))
    monkeypatch.setenv("TODO_OWNER_ID", "owner-fixture")
    monkeypatch.setenv("AUTOPHAGY_SKILL_LIVE_ROOT", str(tmp_path / "absent-live"))

    def forbid(*_args, **_kwargs):
        pytest.fail("owner-command unit tests must never call real gws or post a card")

    monkeypatch.setattr(module, "run_gws", forbid)
    monkeypatch.setattr(module, "gate_module", forbid)
    monkeypatch.setattr(import_module("todo_approval"), "request_cli_approval", forbid)
    monkeypatch.setattr(module, "_notify_created", forbid)
    return module


@dataclass(slots=True)
class FakeGws:
    """Keep the inserted body so readback can prove or contradict the actual write."""

    mismatch: str = ""
    fail_insert: bool = False
    calls: list[list[str]] = field(default_factory=list)
    body: dict[str, str] = field(default_factory=dict)

    def __call__(self, argv: list[str]) -> dict[str, str]:
        self.calls.append(argv)
        if argv[3] == "insert":
            if self.fail_insert:
                raise OSError("synthetic connection lost")
            self.body.update(json.loads(argv[argv.index("--json") + 1]))
            return {"id": "task-fixture", **self.body}
        return {"id": "wrong-id" if self.mismatch == "id" else "task-fixture",
                **self.body, "title": "other title" if self.mismatch == "title" else self.body["title"]}


def test_write_now_verifies_and_receipts_owner_command(
    todo: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a fake connector and no card approval.
    gws = FakeGws()
    monkeypatch.setattr(todo, "run_gws", gws)
    # When: the CLI receives the gate's owner-command argv.
    rc = todo.main(_ARGV)
    # Then: the marker follows a verified insert/get and a private origin receipt.
    assert rc == 0
    assert capsys.readouterr().out == "OWNER-COMMAND-WRITTEN todo task-fixture\n"
    assert [call[3] for call in gws.calls] == ["insert", "get"]
    assert gws.body == {"title": "합성 할 일", "notes": "합성 메모", "due": "2026-10-20T00:00:00Z"}
    assert json.loads(gws.calls[1][5]) == {"task": "task-fixture", "tasklist": "list-fixture"}
    path, = (tmp_path / "state" / "owner-command").glob("*.json")
    receipt = json.loads(path.read_text())
    assert receipt["method"] == "owner_command" and receipt["state"] == "verified"
    assert receipt["origin_channel_id"] == _ARGV[-3]
    assert receipt["origin_message_id"] == _ARGV[-1]
    assert receipt["task_id"] == "task-fixture"
    assert receipt["verification"] == "tasks.tasks.get"
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("mismatch", ["title", "id"])
def test_write_now_mismatched_readback_has_no_success_marker(
    todo: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], mismatch: str,
) -> None:
    # Given: Tasks reads back a different title or id.
    gws = FakeGws(mismatch=mismatch)
    monkeypatch.setattr(todo, "run_gws", gws)
    # When: the owner command runs.
    rc = todo.main(_ARGV)
    # Then: the unverified write is retained for reconciliation, never reported done.
    assert rc == 6
    assert "OWNER-COMMAND-WRITTEN " not in capsys.readouterr().out
    path, = (tmp_path / "state" / "owner-command").glob("*.json")
    assert json.loads(path.read_text())["state"] == "write_started"


@pytest.mark.parametrize("fail_insert", [False, True])
def test_write_now_repeat_never_inserts_after_success_or_uncertain_failure(
    todo: ModuleType, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], fail_insert: bool,
) -> None:
    # Given: a prior attempt either verified or lost its response after starting.
    gws = FakeGws(fail_insert=fail_insert)
    monkeypatch.setattr(todo, "run_gws", gws)
    assert todo.main(_ARGV) == (5 if fail_insert else 0)
    capsys.readouterr()
    # When: that same origin and command are retried.
    rc = todo.main(_ARGV)
    # Then: O_EXCL refuses another insert and another success marker.
    assert rc == 7
    assert [call[3] for call in gws.calls].count("insert") == 1
    assert "OWNER-COMMAND-WRITTEN " not in capsys.readouterr().out


@pytest.mark.parametrize("command", ["create", "write-now"])
def test_write_requires_both_origin_coordinates(todo: ModuleType, command: str) -> None:
    # Given: a command without the origin message.
    # When / Then: argument parsing refuses it before any connector call.
    with pytest.raises(SystemExit) as error:
        todo.main([command, *_ARGV[1:-2]])
    assert error.value.code == 2


def test_create_defaults_to_verified_owner_command(
    todo: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: the normal create command has an owner origin and no approval record.
    gws = FakeGws()
    monkeypatch.setattr(todo, "run_gws", gws)
    # When: the gate calls create with the frozen owner request.
    rc = todo.main(["create", *_ARGV[1:]])
    # Then: it writes and verifies without invoking the card producer.
    assert rc == 0
    assert [call[3] for call in gws.calls] == ["insert", "get"]
    assert capsys.readouterr().out == "OWNER-COMMAND-WRITTEN todo task-fixture\n"


def test_request_card_command_is_not_available(todo: ModuleType) -> None:
    # Given: a former request-card command, even with both origin coordinates.
    # When / Then: parsing rejects it before any producer or connector is reached.
    with pytest.raises(SystemExit) as error:
        todo.build_parser().parse_args(["request", *_ARGV[1:]])
    assert error.value.code == 2


def test_write_now_stale_copy_is_refused(
    todo: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: the governed-copy check rejects this CLI path.
    monkeypatch.setattr(import_module("todo_governed"), "refusal", lambda _path: "STALE-SKILL-COPY-BLOCK")
    # When: a cardless write is requested.
    rc = todo.main(_ARGV)
    # Then: it is blocked before any connector call.
    assert rc == 3
    assert "OWNER-COMMAND-WRITTEN " not in capsys.readouterr().out
