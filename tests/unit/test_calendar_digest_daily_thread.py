"""Offline digest -> real calendar CLI/gate -> watcher scenario for t_bacebc3a."""
from __future__ import annotations

import contextlib
import importlib
import io
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError
from urllib.parse import unquote

import pytest

from tests.unit.test_calendar_single_live_request import OWNER, calendar_cli, calendar_confirm, calendar_gate, calendar_pending
from tests.unit.test_calendar_single_live_request import calendar_env as calendar_env  # shared isolated transport fixture

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills/mail/scripts"))
triage_digest = importlib.import_module("triage_digest")
triage_transport = importlib.import_module("triage_transport")
triage_core = importlib.import_module("triage_core")


class DigestClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 9, 18, tzinfo=UTC).astimezone(tz)


@pytest.fixture
def flow(calendar_env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    base, calls = calendar_env
    monkeypatch.setattr(triage_digest, "datetime", DigestClock)
    monkeypatch.setattr(calendar_cli, "datetime", DigestClock)
    monkeypatch.setattr(calendar_gate, "utc_now", lambda: "2026-09-18T00:00:00Z")
    from automation.interop import approval_lifecycle
    monkeypatch.setattr(approval_lifecycle, "_now", calendar_gate.utc_now)
    cards: dict[str, dict] = {}
    replies: list[dict] = []
    notices: list = []
    reactions: dict[str, str] = {}
    failures: list[str] = []
    events: list[dict] = []
    mails: list[dict] = []
    monkeypatch.setenv("TRIAGE_DB", str(tmp_path / "mail.db"))
    monkeypatch.setenv("CALENDAR_APPROVAL_LOG", str(tmp_path / "approvals.jsonl"))
    monkeypatch.setenv("TRIAGE_CALENDAR_CLI", str(ROOT / "skills/calendar/scripts/calendar_cli.py"))
    monkeypatch.setenv("CALENDAR_GWS_BIN", "fixture-gws")
    monkeypatch.setenv("CALENDAR_PEERS_FILE", str(tmp_path / "absent-peers.yaml"))
    monkeypatch.delenv("E2E_TEST_MODE", raising=False)

    def api(method, path, payload=None):
        parts = path.split("?")[0].strip("/").split("/")
        if method == "GET" and len(parts) == 3 and parts[-1] == "messages":
            return [{"id": key, **value} for key, value in cards.items() if key not in base.deleted]
        if method == "GET" and len(parts) > 5:
            emoji = unquote(parts[5])
            return [{"id": OWNER, "bot": False}] if reactions.get(parts[3]) == emoji else []
        if method == "POST" and payload and "message_reference" in payload:
            replies.append(payload)
            return {"id": f"result-{len(replies)}"}
        is_card = method == "POST" and payload and "sha256:" in payload.get("content", "")
        if is_card and failures and failures[0] == "before":
            failures.pop(0)
            raise URLError("offline post rejected")
        response = base(method, path, payload)
        if is_card:
            cards[response["id"]] = payload
            if failures and failures[0] == "after":
                failures.pop(0)
                raise URLError("offline response lost")
        if method == "PUT" and failures and failures[0] == "reaction":
            failures.pop(0)
            raise URLError("offline reaction failed")
        return response

    def run(argv, **kwargs):
        if argv[0] == "fixture-gws":
            if argv[3] == "list":  # same-event check before cards and inserts: an empty day
                return subprocess.CompletedProcess(argv, 0, json.dumps({"items": []}), "")
            if argv[3] == "insert":
                event = json.loads(argv[argv.index("--json") + 1])
                events.append({**event, "id": f"event-{len(events) + 1}"})
            return subprocess.CompletedProcess(argv, 0, json.dumps(events[-1]), "")
        assert Path(argv[1]).name == "calendar_cli.py", argv
        stdout, stderr = io.StringIO(), io.StringIO()
        with monkeypatch.context() as child, contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            child.setattr(sys, "argv", argv[1:])
            rc = calendar_cli.main()
        return subprocess.CompletedProcess(argv, rc, stdout.getvalue(), stderr.getvalue())

    monkeypatch.setattr(calendar_confirm, "_api", api)
    monkeypatch.setattr(triage_transport.subprocess, "run", run)
    monkeypatch.setattr(triage_digest.triage_gate, "db_path", lambda: tmp_path / "mail.db")
    monkeypatch.setattr(triage_transport, "_list_mails", lambda *_args: mails)
    monkeypatch.setattr(triage_transport, "_get_mail", lambda uid: next(row for row in mails if row["uid"] == uid))
    monkeypatch.setattr(triage_digest.triage_llm, "classify", lambda **kw: (
        triage_core.Classification("important", False, True, False, kw["body"], "fixture"), "fixture",
    ))
    monkeypatch.setattr(triage_digest.triage_llm, "summarize", lambda **_kw: "fixture summary")
    monkeypatch.setattr(triage_digest.triage_confirm, "dm_owner", lambda _body: None)
    from automation import owner_notice
    monkeypatch.setattr(owner_notice, "notify_owner", lambda content, **kw: notices.append(kw) or True)
    from tests.unit.test_calendar_approval_recovery_integration import watch
    return base, cards, replies, notices, reactions, failures, events, mails, watch


def digest(flow, texts: tuple[str, ...]) -> None:
    mails = flow[7]
    for text in texts:
        mails.append({"uid": f"mail-{len(mails)}", "subject": "fixture", "sender": "sender@example.invalid",
                      "body": text, "date": "2026-09-18T00:00:00Z"})
    assert triage_digest.run_digest(limit=20, sync=False, dry_run=False) == 0


def tick(flow) -> None:
    watch = flow[8]
    watch.run_once(
        store=calendar_pending.PendingConfirmStore(), owner_id=OWNER,
        discord=watch.DiscordApi(OWNER),
        commands=watch.CliCommands(ROOT / "skills/calendar/scripts/calendar_cli.py"),
        draft_sha256=watch._draft_sha256, now=datetime(2026, 9, 18, tzinfo=UTC),
    )


def pending():
    return calendar_pending.PendingConfirmStore().load()


TEXTS = ("2026-10-01 15:00 alpha meeting 1시간", "2026-10-01 15:00 beta meeting 1시간",
         "2026-10-01 17:00 gamma meeting 1시간")
CORRECTED = "2026-10-01 15:00 beta meeting 2시간"


def test_digest_posts_three_independent_cards_in_one_thread_when_three_mails_arrive(flow) -> None:
    # Given three schedule mails, including two different events at the same time.
    # When the real digest delegates through the calendar CLI.
    digest(flow, TEXTS)
    # Then approval publication, not just draft creation, is complete.
    base = flow[0]
    assert len(base.request_threads) == 1
    assert len(pending()) == 3
    assert len(set(base.post_channels)) == 1
    assert flow[6] == []


def test_digest_replaces_only_undecided_event_when_corrected_mail_arrives(flow) -> None:
    # Given three cards and the identity of the second card.
    digest(flow, TEXTS)
    assert len(pending()) == 3
    before = pending()[1]
    # When only the duration of event two changes.
    digest(flow, (CORRECTED,))
    # Then the lifecycle replaces that card in the existing daily thread.
    assert len(pending()) == 3
    assert before.dm_message_id in flow[0].deleted
    assert len(flow[0].request_threads) == 1
    updated = next(row for row in pending() if row.key == before.key)
    assert updated.sha256 != before.sha256
    assert updated.channel_id == before.channel_id


def test_digest_results_reply_to_each_card_when_owner_approves_and_cancels(flow) -> None:
    # Given independent owner decisions on card one and card three.
    digest(flow, TEXTS)
    assert len(pending()) == 3
    first, _, third = pending()
    flow[4].update({first.dm_message_id: "✅", third.dm_message_id: "⛔"})
    # When the real watcher consumes those decisions and runs the guarded CLI.
    tick(flow)
    # Then only one calendar write occurs and each result replies to its own card.
    assert len(flow[6]) == 1
    assert {row["message_reference"]["message_id"] for row in flow[2]} == {
        first.dm_message_id, third.dm_message_id,
    }
    assert len(pending()) == 1


@pytest.mark.parametrize("failure", ["before", "after", "reaction"])
def test_digest_retries_safely_next_tick_when_card_publication_fails(flow, failure: str) -> None:
    # Given a transport failure during event two's corrected-card publication.
    digest(flow, TEXTS)
    assert len(pending()) == 3
    flow[5].append(failure)
    digest(flow, (CORRECTED,))
    assert flow[3], "publication failure must notify the owner immediately"
    # When the next watcher tick runs without a three-minute orphan delay.
    tick(flow)
    # Then recovery adopts any already-posted message instead of duplicating it.
    assert len(pending()) == 3
    assert len(flow[1].keys() - flow[0].deleted) == 3
    assert len(flow[0].request_threads) == 1


def test_digest_posts_execution_failure_under_its_original_card(flow, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given an approved digest card whose calendar write fails.
    digest(flow, (TEXTS[0],))
    [entry] = pending()
    flow[4][entry.dm_message_id] = "✅"
    error = flow[8].ConfirmWatchError("fixture calendar write failed", fatal=True)
    monkeypatch.setattr(flow[8].CliCommands, "confirm", lambda *_args: (_ for _ in ()).throw(error))
    # When the watcher attempts that write.
    with pytest.raises(flow[8].ConfirmBatchError):
        tick(flow)
    # Then the failure is posted under the card, while the item remains retryable.
    assert flow[2][-1]["message_reference"]["message_id"] == entry.dm_message_id
    assert pending() == (entry,)


@pytest.mark.parametrize("reaction", ["✅", "⛔"])
def test_digest_never_reasks_when_item_was_decided(flow, reaction: str) -> None:
    # Given a consumed decision whose mail may later be sent again.
    digest(flow, (TEXTS[0],))
    assert len(pending()) == 1
    flow[4][pending()[0].dm_message_id] = reaction
    tick(flow)
    count = flow[0].posts
    # When the same event appears again in a later digest.
    digest(flow, (TEXTS[0],))
    # Then no second approval or calendar write is created.
    assert flow[0].posts == count
    assert pending() == ()
