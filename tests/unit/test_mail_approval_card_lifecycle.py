"""Real lifecycle/store/probe paths with the existing offline Discord wire fakes."""
from __future__ import annotations

import builtins

import pytest

from automation.interop import owner_message
from automation.interop.approval_lifecycle import Probe
from tests.unit import test_budget_single_live_request as budget
from tests.unit import test_calendar_single_live_request as calendar
from tests.unit import test_coordination_single_live_request as coord
from tests.unit import test_mail_single_live_request as mail
from tests.unit.test_budget_single_live_request import budget_env as budget_env
from tests.unit.test_calendar_single_live_request import calendar_env as calendar_env
from tests.unit.test_coordination_single_live_request import coordination_env as coordination_env
from tests.unit.test_mail_single_live_request import mail_env as mail_env


@pytest.mark.parametrize("producer", ["mail", "budget", "calendar", "coordination"])
@pytest.mark.parametrize("available", [True, False])
@pytest.mark.parametrize("binding_intact", [True, False])
def test_card_version_and_probe_when_request_posts(producer: str, available: bool, binding_intact: bool,
                                                  request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given isolated real stores and a wire fake; an old optional envelope runtime can be absent.
    fake = request.getfixturevalue(producer + "_env")[0]
    if not available:
        original = builtins.__import__

        def importing(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "automation.interop" and "owner_message" in fromlist:
                raise ImportError("synthetic optional-runtime absence")
            return original(name, globals, locals, fromlist, level)

        monkeypatch.setattr(builtins, "__import__", importing)
    # When the public producer posts, its real consumer probes that exact content.
    match producer:
        case "mail":
            draft = mail._draft()
            mail.triage_approval.post_for_approval(draft)
            stored = mail.triage_gate.load_draft(draft["id"])
            gate = mail.triage_approval.MailApprovalGate(stored)
            bound = mail.triage_approval.request_of(stored)
            version = stored["render_version"]
        case "budget":
            draft = budget._draft()
            budget.budget_approval.post_for_approval(draft)
            stored = budget.budget_gate.load_draft(draft["id"])
            gate = budget.budget_approval.BudgetApprovalGate(stored, budget.budget_approval.budget_binding.stored_binding(stored))
            bound = gate.outstanding(budget.budget_approval.approval_key(stored))[0]
            version = stored["render_version"]
        case "calendar":
            draft = calendar._draft()
            if not available:
                # New detailed cards must not degrade into blind approval requests.
                root = request.getfixturevalue("tmp_path")
                before = {
                    path: path.read_bytes() for path in root.rglob("*")
                    if path.suffix in {".json", ".jsonl"}
                }
                with pytest.raises(calendar.calendar_gate.GateError):
                    calendar._approval().request_confirmation(draft)
                assert fake.posts == 0
                assert not fake.request_threads
                assert not fake.notice_ids
                assert not calendar._store().load()
                assert {
                    path: path.read_bytes() for path in root.rglob("*")
                    if path.suffix in {".json", ".jsonl"}
                } == before
                assert not list(root.rglob("posting-journal/*.json"))
                return
            entry = calendar._approval().request_confirmation(draft)
            gate = calendar._approval().CalendarApprovalGate(None, calendar._store(), calendar.OWNER)
            bound = calendar._approval().request_of(entry)
            version = entry.render_version
        case "coordination":
            coord._request()
            entry = coord._store().load()[0]
            gate = coord._approval().CoordinationApprovalGate(None, coord._store(), coord.OWNER)
            bound = coord._approval().request_of(entry)
            version = entry.render_version
        case _:
            raise AssertionError(producer)
    if not binding_intact:
        if producer == "mail":  # v4 mail cards bind by the recorded digest of the posted text
            fake.contents[bound.message_id] += " (변조)"
        else:
            fake.contents[bound.message_id] = fake.contents[bound.message_id].replace(bound.action_hash, "wrong-binding")
    probe = gate.probe(bound)
    # Then the actual consumer accepts only intact wire, including legacy fallback cards.
    expected_version = "4" if producer in ("calendar", "mail") else ("3" if available else "1")
    assert version == expected_version
    assert probe is (Probe.BOUND_PENDING if binding_intact else Probe.BINDING_MISMATCH)
    assert fake.posts == 1
    content = next(iter(fake.contents.values()))
    if producer == "mail":
        assert content.startswith("📨 **")
        assert str(stored["sha256"]) not in content and str(stored["id"]) not in content
    else:
        assert content.startswith("**🔔 ") is available


@pytest.mark.parametrize("producer", ["mail", "budget", "calendar", "coordination"])
def test_zero_external_effects_when_both_card_versions_are_unpostable(producer: str, request: pytest.FixtureRequest,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    # Given an unpostable card; lower the real length boundary, not the lifecycle or transport.
    from automation.interop import approval_card
    fake = request.getfixturevalue(producer + "_env")[0]
    monkeypatch.setattr(approval_card, "MAX_CONTENT", 1)
    drafts = {"mail": mail._draft, "budget": budget._draft, "calendar": calendar._draft}
    draft = drafts[producer]() if producer in drafts else None
    root = request.getfixturevalue("tmp_path")
    before = {path: path.read_bytes() for path in root.rglob("*") if path.suffix in {".json", ".jsonl"}}
    # When the public producer refuses; then no record, journal, post or thread is changed.
    with pytest.raises((mail.triage_gate.GateError, budget.budget_gate.GateError,
                        calendar.calendar_gate.GateError, coord.io.CoordinationError)):
        match producer:
            case "mail":
                mail.triage_approval.post_for_approval(draft)
            case "budget":
                budget.budget_approval.post_for_approval(draft)
            case "calendar":
                calendar._approval().request_confirmation(draft)
            case "coordination":
                coord._request()
            case _:
                raise AssertionError(producer)
    assert fake.posts == 0
    threads = {"mail": "threads", "budget": "thread_names", "calendar": "request_threads", "coordination": "request_threads"}
    assert not getattr(fake, threads[producer])
    # Lease locks are synchronization, not pending records or posting receipts.
    assert {path: path.read_bytes() for path in root.rglob("*") if path.suffix in {".json", ".jsonl"}} == before
    assert not list(root.rglob("posting-journal/*.json"))
    if producer == "coordination":
        assert not list(root.glob("calendar-gate/drafts/*.json"))
        assert not (root / "pending.jsonl").exists()


@pytest.mark.parametrize("producer", ["mail", "budget", "calendar", "coordination"])
def test_existing_card_is_preserved_when_envelope_runtime_changes(producer: str, request: pytest.FixtureRequest,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    # Given a v1 card already carrying its owner's approval, and an upgraded renderer.
    fake = request.getfixturevalue(producer + "_env")[0]
    match producer:
        case "mail":
            draft = mail._draft()
            def post():
                return mail.triage_approval.post_for_approval(mail.triage_gate.load_draft(draft["id"]))
        case "budget":
            draft = budget._draft()
            def post():
                return budget.budget_approval.post_for_approval(budget.budget_gate.load_draft(draft["id"]))
        case "calendar":
            draft = calendar._draft()
            # The old stored version is explicit; new requests now require v3.
            draft["render_version"] = "1"
            calendar.calendar_gate.persist_draft(draft)
            def post():
                return calendar._approval().request_confirmation(calendar.calendar_gate.load_draft(draft["id"]))
        case "coordination":
            post = coord._request
        case _:
            raise AssertionError(producer)
    def unavailable(*args, **kwargs):
        raise owner_message.OwnerMessageError()

    with monkeypatch.context() as runtime:
        runtime.setattr(owner_message, "render", unavailable)
        post()
    message_id = next(iter(fake.contents))
    before = dict(fake.contents)
    fake.approved.add(message_id)
    # When the producer retries after upgrade; then the approved card is never replaced.
    with pytest.raises((mail.triage_gate.GateError, budget.budget_gate.GateError,
                        calendar.calendar_gate.GateError, coord.io.CoordinationError)):
        post()
    assert fake.contents == before
    assert fake.posts == 1
    assert not any(call.startswith("DELETE:") for call in fake.calls)


@pytest.mark.parametrize("producer", ["mail", "budget", "calendar", "coordination"])
@pytest.mark.parametrize("decided", [False, True])
def test_bound_reuse_never_calls_renderer(producer: str, request: pytest.FixtureRequest,
                                                                monkeypatch: pytest.MonkeyPatch, decided: bool) -> None:
    # Given a genuinely bound v2 request, pending or already owner-approved.
    fake = request.getfixturevalue(producer + "_env")[0]
    match producer:
        case "mail":
            draft = mail._draft()
            def post():
                return mail.triage_approval.post_for_approval(mail.triage_gate.load_draft(draft["id"]))
        case "budget":
            draft = budget._draft()
            def post():
                return budget.budget_approval.post_for_approval(budget.budget_gate.load_draft(draft["id"]))
        case "calendar":
            draft = calendar._draft()
            def post():
                return calendar._approval().request_confirmation(calendar.calendar_gate.load_draft(draft["id"]))
        case "coordination":
            post = coord._request
        case _:
            raise AssertionError(producer)
    post()
    before = dict(fake.contents)
    message_id = next(iter(before))
    if decided:
        fake.approved.add(message_id)
    calls = []

    def broken(*args, **kwargs):
        calls.append("render")
        raise owner_message.OwnerMessageError()

    monkeypatch.setattr(owner_message, "render", broken)
    error = None
    result = None
    try:
        result = post()
    except (mail.triage_gate.GateError, budget.budget_gate.GateError,
            calendar.calendar_gate.GateError, coord.io.CoordinationError) as caught:
        error = caught
    assert calls == [], "an intact binding must not invoke even a failing renderer"
    if decided:
        assert error is not None and "owner-decided" in str(error)
    else:
        assert error is None
        assert result == (7 if producer == "coordination" else
                          calendar._store().load()[0] if producer == "calendar" else message_id)
    assert fake.contents == before
    assert fake.posts == 1
    assert not any(call.startswith("DELETE:") for call in fake.calls)


def test_coordination_reuse_reports_the_persisted_draft_id(coordination_env, monkeypatch, capsys) -> None:
    ids = iter(("abc123", "def456"))
    monkeypatch.setattr(calendar.calendar_gate.secrets, "token_hex", lambda size: next(ids))
    coord._request()
    capsys.readouterr()
    coord._request()
    tokens = dict(token.split("=", 1) for token in capsys.readouterr().out.split() if "=" in token)
    assert tokens["draft"] == "abc123"
    assert [draft["id"] for draft in calendar.calendar_gate.list_drafts()] == ["abc123"]
