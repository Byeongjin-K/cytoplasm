"""Recovery never transfers an old owner's decision to a new commit."""
from __future__ import annotations

import json
from http.client import IncompleteRead
from pathlib import Path
from typing import TypedDict

import pytest

from automation import owner_notice, release_abandon, release_approval, skill_gate
from automation.interop.approval_types import Probe
from tests.unit.test_release_approval import _pending, _StubGate


class Reply(TypedDict):
    content: str
    message_reference: dict[str, str | bool]
    allowed_mentions: dict[str, list[str] | bool]


@pytest.fixture
def gate_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(skill_gate, "GATE_DIR", tmp_path)
    monkeypatch.setattr(skill_gate, "APPROVAL_LOG", tmp_path / "approvals.jsonl")
    monkeypatch.setattr(release_approval, "_gate", lambda spec: _StubGate(Probe.APPROVED))
    monkeypatch.setattr(skill_gate, "_api", lambda method, path, payload: {"id": "reply"})
    return tmp_path


def test_retire_abandons_when_approved_head_is_neither_tip_nor_released_base(
    gate_dir: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: an approved, untagged record left behind by origin/main.
    record = _pending(gate_dir)
    before = (gate_dir / "pending/release.json").read_bytes()
    # When: release.sh starts retirement with its fetched tip.
    rc = release_approval.main(["retire", "--head", "c" * 40, "--tip", "e" * 40])
    # Then: audited abandonment, never an executed-release receipt.
    assert rc == 0
    assert "RELEASE-ABANDONED" in capsys.readouterr().out
    archived = list((gate_dir / "release-abandoned").glob("*.json"))
    assert [p.read_bytes() for p in archived] == [before]
    assert not (gate_dir / "release-history").exists()
    assert not (gate_dir / "pending/release.json").exists()
    assert json.loads(archived[0].read_text())["head_sha"] == record["head_sha"]


def test_decision_notifies_once_when_same_approved_request_stays_stale(
    gate_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the same approved request across several changing tips.
    _pending(gate_dir)
    notices: list[str] = []
    monkeypatch.setattr(owner_notice, "notify_owner", lambda body, *, message=None, fault=False: notices.append(body) or True)
    # When: independent completer decision ticks inspect the episode.
    results = [release_approval.main(["decision", "--head", tip * 40, "--notify-stale"])
               for tip in ("c", "d", "e")]
    # Then: one notice and no authorization for any new tip.
    assert results == [2, 2, 2]
    assert len(notices) == 1


@pytest.mark.parametrize("probe", [Probe.BOUND_PENDING, Probe.CANCELLED, Probe.UNVERIFIABLE])
def test_decision_sends_nothing_when_stale_request_is_not_approved(
    gate_dir: Path, monkeypatch: pytest.MonkeyPatch, probe: Probe,
) -> None:
    # Given: a stale request without a definite approval.
    _pending(gate_dir)
    monkeypatch.setattr(release_approval, "_gate", lambda spec: _StubGate(probe))
    notices: list[str] = []
    monkeypatch.setattr(owner_notice, "notify_owner", lambda body, *, message=None, fault=False: notices.append(body) or True)
    # When: a completer tick inspects it.
    rc = release_approval.main(["decision", "--head", "e" * 40, "--notify-stale"])
    # Then: fail closed without inventing an approved-stale incident.
    assert rc == 2
    assert notices == []


def test_decision_retries_notice_when_delivery_failed(
    gate_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one transport failure followed by a functioning transport.
    _pending(gate_dir)
    attempts: list[str] = []
    monkeypatch.setattr(owner_notice, "notify_owner", lambda body, *, message=None, fault=False: attempts.append(body) or len(attempts) > 1)
    # When: ticks repeat the same episode.
    results = [release_approval.main(["decision", "--head", "e" * 40, "--notify-stale"])
               for _ in range(3)]
    # Then: the failed send did not consume the notification.
    assert results == [2, 2, 2]
    assert len(attempts) == 2


def test_new_request_gets_its_own_notice_when_version_and_head_are_reused(
    gate_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a previously notified request and its replacement with a new message id.
    record = _pending(gate_dir)
    notices: list[str] = []
    monkeypatch.setattr(owner_notice, "notify_owner", lambda body, *, message=None, fault=False: notices.append(body) or True)
    assert release_approval.main(["decision", "--head", "e" * 40, "--notify-stale"]) == 2
    path = gate_dir / "pending/release.json"
    path.write_text(json.dumps({**record, "message_id": "replacement"}), encoding="utf-8")
    # When: the replacement is also approved and stale.
    rc = release_approval.main(["decision", "--head", "f" * 40, "--notify-stale"])
    # Then: the old episode's marker does not hide it.
    assert rc == 2
    assert len(notices) == 2


@pytest.mark.parametrize("failure", [None, OSError("offline"), IncompleteRead(b"partial"), SystemExit(2)])
def test_abandon_preserves_decision_when_status_reply_succeeds_or_fails(
    gate_dir: Path, monkeypatch: pytest.MonkeyPatch, failure: OSError | IncompleteRead | SystemExit | None,
) -> None:
    # Given: an approval card and an optional Discord outage.
    record = _pending(gate_dir)
    before = (gate_dir / "pending/release.json").read_bytes()
    posts: list[tuple[str, str, Reply]] = []

    def api(method: str, path: str, payload: Reply) -> None:
        posts.append((method, path, payload))
        if failure is not None:
            raise failure

    monkeypatch.setattr(skill_gate, "_api", api)
    args = ["--version", record["version"], "--head", record["head_sha"],
            "--message-id", record["message_id"], "--reason", "superseded"]
    # When: the audited command abandons the exact request.
    rc = release_abandon.main(args)
    # Then: only an additive reply; bytes and successful abandonment are independent of delivery.
    assert rc == 0
    assert len(posts) == 1
    method, path, payload = posts[0]
    assert (method, path) == ("POST", f"/channels/{record['channel_id']}/messages")
    assert payload["message_reference"]["message_id"] == record["message_id"]
    assert payload["allowed_mentions"] == {"parse": [], "replied_user": False}
    assert payload["content"]
    assert [p.read_bytes() for p in (gate_dir / "release-abandoned").glob("*.json")] == [before]
    audit = [json.loads(line) for line in (gate_dir / "approval-abandons.jsonl").read_text().splitlines()]
    assert [(row["event"], row["reason"]) for row in audit] == [("release-abandon", "superseded")]


@pytest.mark.parametrize("probe", [Probe.CANCELLED, Probe.BOUND_PENDING, Probe.UNVERIFIABLE])
def test_retire_keeps_record_when_stale_decision_is_not_approved(
    gate_dir: Path, monkeypatch: pytest.MonkeyPatch, probe: Probe,
) -> None:
    # Given: a non-approved old request.
    _pending(gate_dir)
    before = (gate_dir / "pending/release.json").read_bytes()
    monkeypatch.setattr(release_approval, "_gate", lambda spec: _StubGate(probe))
    # When: release.sh checks it for retirement.
    rc = release_approval.main(["retire", "--head", "c" * 40, "--tip", "e" * 40])
    # Then: the lifecycle still owns it; no automatic decided-record override.
    assert rc == 0
    assert (gate_dir / "pending/release.json").read_bytes() == before


def test_retire_uses_executed_history_when_approved_head_matches_signed_base(gate_dir: Path) -> None:
    # Given: an approved release which was actually tagged before main advanced.
    record = _pending(gate_dir)
    before = (gate_dir / "pending/release.json").read_bytes()
    # When: the next release starts with that signed base.
    rc = release_approval.main(["retire", "--head", record["head_sha"], "--tip", "e" * 40])
    # Then: existing executed-history semantics survive, rather than a false abandonment.
    assert rc == 0
    assert (gate_dir / "release-history" / f"{record['head_sha']}.json").read_bytes() == before
    assert not (gate_dir / "release-abandoned").exists()


def test_retire_refuses_when_approved_request_still_matches_tip(gate_dir: Path) -> None:
    # Given: a live approval for the current tip.
    record = _pending(gate_dir)
    before = (gate_dir / "pending/release.json").read_bytes()
    # When: retirement is attempted before its tag exists.
    rc = release_approval.main(["retire", "--head", "c" * 40, "--tip", record["head_sha"]])
    # Then: retirement cannot destroy an executable approval.
    assert rc == 4
    assert (gate_dir / "pending/release.json").read_bytes() == before


def test_repeat_abandon_posts_nothing_when_record_already_archived(
    gate_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a completed abandon, with its original card still present.
    record = _pending(gate_dir)
    args = ["abandon", "--version", record["version"], "--head", record["head_sha"],
            "--message-id", record["message_id"], "--reason", "superseded"]
    assert release_approval.main(args) == 0
    calls: list[str] = []
    monkeypatch.setattr(skill_gate, "_api", lambda method, path, payload: calls.append(method))
    # When: the same abandoned identity is submitted again.
    rc = release_approval.main(args)
    # Then: neither another audit action nor another Discord reply.
    assert rc == 1
    assert calls == []
    assert len((gate_dir / "approval-abandons.jsonl").read_text().splitlines()) == 1
