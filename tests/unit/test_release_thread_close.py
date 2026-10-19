"""Release completion closes its approval thread only after the final notice."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from automation import owner_notice, release_abandon, release_applied_notice, release_approval, skill_gate
from automation.interop.approval_types import Probe
from automation.skill_gate_surface import JsonValue
from tests.unit.test_release_approval import _StubGate, _pending

PARENT = "222"
THREAD = "333"
NAME = "🏷️ 릴리스 v1.2.3"
HEAD = "a" * 40


class Wire:
    """Offline Discord with active/archived lists and ordered effects."""

    def __init__(self) -> None:
        self.effects: list[tuple[str, str, dict[str, JsonValue]]] = []
        self.found = True
        self.archived = False
        self.fail_patch = False
        self.others: list[dict[str, JsonValue]] = []

    def api(self, method: str, path: str, payload: dict[str, JsonValue] | None = None) -> JsonValue:
        if method == "GET":
            if "/threads/" in path:
                archived = "/archived/" in path
                own: list[dict[str, JsonValue]] = [{"id": THREAD, "parent_id": PARENT, "name": NAME}]
                return {"threads": (own if self.found and self.archived == archived else [])
                        + ([] if archived else self.others)}
            return {"id": path.rsplit("/", 1)[-1], "name": NAME, "guild_id": "111",
                    "type": 11 if path == f"/channels/{THREAD}" else 0, "parent_id": PARENT}
        self.effects.append((method, path, payload or {}))
        if method == "PATCH" and self.fail_patch:
            raise OSError("offline")
        return {"id": "444"}


@pytest.fixture
def wire(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Wire:
    # Given: an isolated configured approval channel and matching active release pointer.
    config = tmp_path / "interop.json"
    config.write_text(json.dumps({"owner_id": "555", "deploy_approvals_channel_id": PARENT}))
    monkeypatch.setattr(skill_gate, "INTEROP_CONFIG", config)
    monkeypatch.setattr(skill_gate, "GATE_DIR", tmp_path)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fixture")
    pointer = tmp_path / "current"
    pointer.symlink_to(tmp_path / HEAD)
    monkeypatch.setenv("NODE_RELEASE_CURRENT", str(pointer))
    result = Wire()
    monkeypatch.setattr(skill_gate, "_api", result.api)

    def deliver(*_args: str, **_kwargs: JsonValue) -> bool:
        result.effects.append(("NOTICE", "", {}))
        return True

    monkeypatch.setattr(owner_notice, "notify_owner", deliver)
    return result


@pytest.mark.parametrize("archived", [False, True])
def test_applied_notice_closes_thread_when_found(wire: Wire, archived: bool) -> None:
    # Given: the same version can be active or already auto-archived.
    wire.archived = archived
    # When: the applied notice succeeds.
    rc = release_applied_notice.send("v1.2.3", HEAD)
    # Then: the notice precedes exactly one completed-name/archive PATCH.
    assert rc == 0
    assert wire.effects == [
        ("NOTICE", "", {}),
        ("PATCH", f"/channels/{THREAD}", {"name": f"✅ 완료 · {NAME}", "archived": True}),
    ]


def test_cancel_reply_precedes_archive_when_owner_denies(
    wire: Wire, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a live request in the release thread with an owner cancellation.
    record = _pending(tmp_path)
    record["channel_id"] = THREAD
    (tmp_path / "pending/release.json").write_text(json.dumps(record))
    monkeypatch.setattr(release_approval, "_gate", lambda _spec: _StubGate(Probe.CANCELLED))
    # When: the normal CLI decision path reads the reaction.
    rc = release_approval.main(["decision", "--head", HEAD])
    # Then: the existing denial exit survives; the reply is the last POST before close.
    assert rc == 9
    assert [(method, path) for method, path, _ in wire.effects] == [
        ("POST", f"/channels/{THREAD}/messages"), ("PATCH", f"/channels/{THREAD}"),
    ]
    assert wire.effects[-1][2] == {"name": f"⛔ 취소 · {NAME}", "archived": True}


def test_abandon_reply_precedes_archive_when_superseded(wire: Wire, tmp_path: Path) -> None:
    # Given: the exact version/HEAD/message being retired, with its stored thread.
    record = _pending(tmp_path)
    record["channel_id"] = THREAD
    (tmp_path / "pending/release.json").write_text(json.dumps(record))
    order = release_abandon.ReleaseAbandonOrder("v1.2.3", HEAD, record["message_id"], "superseded", "operator")
    # When: the audited abandon retires it.
    result = release_abandon.abandon(tmp_path, order, tmp_path / "audit.jsonl")
    # Then: expiry follows, rather than precedes, its status reply.
    assert result.exit_code == 0
    assert [(method, path) for method, path, _ in wire.effects] == [
        ("POST", f"/channels/{THREAD}/messages"), ("PATCH", f"/channels/{THREAD}"),
    ]
    assert wire.effects[-1][2] == {"name": f"⌛ 만료 · {NAME}", "archived": True}


def test_applied_notice_closes_older_open_release_threads(wire: Wire) -> None:
    # Given: older versions whose own deploy never sent an applied notice (blocked, rc=10),
    # next to a decided older thread, a newer card, and another channel's thread.
    wire.others = [
        {"id": "601", "parent_id": PARENT, "name": "🏷️ 릴리스 v1.2.2"},
        {"id": "602", "parent_id": PARENT, "name": "🏷️ 릴리스 v1.1.10"},
        {"id": "603", "parent_id": PARENT, "name": "⛔ 취소 · 🏷️ 릴리스 v1.2.1"},
        {"id": "604", "parent_id": PARENT, "name": "🏷️ 릴리스 v1.2.10"},
        {"id": "605", "parent_id": "999", "name": "🏷️ 릴리스 v1.2.0"},
    ]
    # When: v1.2.3, whose tree contains them, is applied.
    rc = release_applied_notice.send("v1.2.3", HEAD)
    # Then: only the older undecided threads in this channel close as done, after its own thread.
    assert rc == 0
    assert wire.effects == [
        ("NOTICE", "", {}),
        ("PATCH", f"/channels/{THREAD}", {"name": f"✅ 완료 · {NAME}", "archived": True}),
        ("PATCH", "/channels/601", {"name": "✅ 완료 · 🏷️ 릴리스 v1.2.2", "archived": True}),
        ("PATCH", "/channels/602", {"name": "✅ 완료 · 🏷️ 릴리스 v1.1.10", "archived": True}),
    ]


def test_applied_notice_succeeds_when_thread_is_absent(wire: Wire) -> None:
    # Given: no thread exists under that approval channel.
    wire.found = False
    # When: the release is applied.
    rc = release_applied_notice.send("v1.2.3", HEAD)
    # Then: no PATCH and no change to the successful notice result.
    assert rc == 0
    assert wire.effects == [("NOTICE", "", {})]


def test_applied_notice_succeeds_when_close_fails(wire: Wire, capsys: pytest.CaptureFixture[str]) -> None:
    # Given: Discord rejects the archive after accepting the notice.
    wire.fail_patch = True
    # When: the release is applied.
    rc = release_applied_notice.send("v1.2.3", HEAD)
    # Then: one close diagnostic, without turning a sent notice into a retry.
    assert rc == 0
    assert len([line for line in capsys.readouterr().err.splitlines()
                if line.startswith("THREAD-CLOSE-FAIL")]) == 1
