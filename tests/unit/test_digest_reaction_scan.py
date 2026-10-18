"""P5: gateway-free bounded scan; real SQLite claims arbitrate overlapping ticks."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from urllib.error import HTTPError

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/mail/scripts"))
import digest_reaction_actions as actions  # noqa: E402
import digest_reaction_scan as scan  # noqa: E402
import mail_triage_watch as wrapper  # noqa: E402
import triage_cli  # noqa: E402
import triage_confirm  # noqa: E402
import triage_store  # noqa: E402
from test_mail_digest_reactions import _NOW, _discord, _seed, BOT, OWNER  # noqa: E402

from automation.cron_schedule import ScheduleError, on_calendar  # noqa: E402
from automation.cron_timer import Job, render  # noqa: E402
from automation.deploy_declarations import all_declarations  # noqa: E402


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "triage.db"
    monkeypatch.setenv("TRIAGE_DB", str(path))
    _seed(path)
    return path


def test_mode_calls_only_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: every full-tick effect would fail.
    calls = []
    monkeypatch.setattr(triage_cli.triage_mode, "effective_mode", lambda: "full-go")
    monkeypatch.setattr(scan, "run", lambda: calls.append("scan"))
    monkeypatch.setattr(triage_cli.triage_gate, "list_drafts", lambda: pytest.fail("draft polling"))
    monkeypatch.setattr(triage_cli, "cmd_watch", lambda _a: pytest.fail("full watch"))
    # When: the dedicated CLI handler executes.
    assert triage_cli.cmd_digest_reactions_only(argparse.Namespace()) == 0
    # Then: only the reaction scan ran.
    assert calls == ["scan"]


def test_wrapper_mode_selects_only_reaction_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a mounted CLI and intercepted subprocess.
    calls = []
    monkeypatch.setattr(wrapper, "CLI", Path(__file__))
    monkeypatch.setattr(wrapper, "_load_env_secrets", lambda: None)
    monkeypatch.setattr(wrapper, "_announce", lambda **_kw: pytest.fail("full-tick streak"))
    monkeypatch.setattr(sys, "argv", ["mail_triage_watch.py", "--digest-reactions-only"])
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(wrapper.subprocess, "run", run)
    # When: the public wrapper mode runs.
    assert wrapper.main() == 0
    # Then: credentials/runtime propagation uses the same wrapper, never watch.
    assert calls[0][0][-1] == "digest-reactions-only"
    assert "AUTOPHAGY_REPO_ROOT" in calls[0][1]["env"]


def test_full_tick_during_fast_action_cannot_execute_it_twice(db, monkeypatch) -> None:
    # Given: Discord has an owner body press; the real DB claim is not mocked.
    _discord(monkeypatch, {actions.BODY_EMOJI: [BOT, OWNER]})
    calls = []
    def run(action, card, attempt):
        calls.append(action)
        assert scan.run(now=_NOW) == 0  # own lock refuses reentry
        assert actions.run_tick(now=_NOW) == 0  # full tick sees the running claim
        return "done"
    monkeypatch.setattr(actions, "_run", run)
    # When: the 20-second path wins the claim.
    assert scan.run(now=_NOW) == 1
    # Then: one action and one terminal receipt, even after another full tick.
    assert calls == ["body"]
    assert actions.run_tick(now=_NOW) == 0
    assert triage_store.digest_action_status(db, "card-1", "body") == "done"


def test_cap_rotates_without_starving_cards(db, monkeypatch) -> None:
    # Given: more recent cards than the REST budget.
    cards = [{"message_id": str(n)} for n in range(scan.CARD_LIMIT + 3)]
    seen = []
    monkeypatch.setattr(actions, "_recent_cards", lambda _now: (db, cards))
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: "owner")
    monkeypatch.setattr(actions, "_act", lambda card, _owner, _db: seen.append(card["message_id"]) or 0)
    # When: two capped scans run.
    scan.run(now=_NOW)
    assert len(seen) == scan.CARD_LIMIT
    scan.run(now=_NOW + timedelta(seconds=20))
    # Then: every card is eventually visited, without exceeding the per-tick cap.
    assert len(seen) == scan.CARD_LIMIT * 2
    assert set(seen) == {str(n) for n in range(len(cards))}


def test_429_stops_reads_and_persists_retry_after_without_failing(db, monkeypatch) -> None:
    # Given: the shared REST client's bounded retries still end in 429.
    calls = []
    def limited(*_args):
        calls.append(1)
        raise HTTPError("https://discord.invalid", 429, "rate limited", {"Retry-After": "75"}, None)
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: "owner")
    monkeypatch.setattr(actions, "_owner_reactions", limited)
    # When: a scan is followed by ticks before and after Retry-After.
    assert scan.run(now=_NOW) == 0
    assert scan.run(now=_NOW + timedelta(seconds=20)) == 0
    assert len(calls) == 1
    assert scan.run(now=_NOW + timedelta(seconds=76)) == 0
    # Then: the timer stays successful, and no card was claimed.
    assert len(calls) == 2
    assert triage_store.digest_action_status(db, "card-1", "body") == ""
    assert json.loads(db.with_suffix(".reaction-scan.json").read_text())["after"] > _NOW.timestamp()


def test_twenty_second_declaration_renders_timer() -> None:
    # Given: the production manifest.
    root = Path(__file__).resolve().parents[2]
    row = next(row for row in all_declarations(root) if row.destination == "mail-digest-reactions")
    # When: the real timer renderer converts its declaration.
    service, timer = render(Job(row.destination, row.attr("schedule"), row.attr("script")), root="/runtime", timezone="")
    # Then: the runner uses its existing named-job lock and a 20-second calendar.
    assert row.account == "agent" and row.attr("runner") == "systemd"
    assert row.attr("schedule") == "every 20s"
    assert "OnCalendar=*-*-* *:*:00/20\n" in timer
    assert "--name mail-digest-reactions --script mail_digest_reactions_watch.py" in service
    assert on_calendar("every 60s") == "*-*-* *:*:00"
    with pytest.raises(ScheduleError):
        on_calendar("every 17s")
