"""t_0c5b0302 — the digest's calendar delegation must name its failure cause.

The digest hands a schedule line to the calendar CLI and renders the returned
string on the owner's card. Before this regression bank existed, every non-zero
child exit code except 5 collapsed into one opaque ``calendar-failed`` string,
the child's stderr was thrown away, and a slow child raised ``TimeoutExpired``
straight through ``build_item`` — one hung calendar CLI took the whole digest
down.

Mail content is sensitive, so everything here is synthetic: the schedule text is
a marked placeholder, the child's stderr is a canary token, and the uid is
already masked exactly as ``triage_digest`` masks it before delegating.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))

import triage_core  # noqa: E402
import triage_transport  # noqa: E402

SCHEDULE_TEXT = "SYNTHETIC-SCHEDULE 2026-09-03 14:00"  # synthetic — never real mail text
UID_OPAQUE = triage_core.mask_value("uid-synthetic-0c5b0302")
STDERR_CANARY = "SYNTHETIC-CHILD-STDERR-cafe0123"  # must never reach the digest card

# The calendar CLI's own exit-code contract (skills/calendar/scripts/calendar_cli.py
# main(): AmbiguousTime=5, ParseRejected=2, GateError.exit_code, FileNotFoundError=3;
# calendar_gate.GateError documents 1=unconfirmed, 3=config, 6=exec; calendar_cli
# ROUTING_REJECT_EXIT_CODE=4). Anything outside that contract is a crash.
CAUSE_BY_RC = (
    (1, "calendar-refused"),        # gate refused — draft not pending / hash mismatch
    (2, "calendar-unparsed"),       # INPUT-REJECTED — the schedule line is unusable
    (3, "calendar-misconfigured"),  # GATE-REFUSED — peers registry / gws bin / missing file
    (4, "calendar-routing"),        # ROUTING-REJECT / ROUTING-CLARIFY — not a solo event
    (5, "calendar-ambiguous"),      # AMBIGUOUS-TIME — owner must be re-asked
    (6, "calendar-exec-failed"),    # gws execution failed
    (9, "calendar-failed-rc9"),     # outside the contract — a crash, and it says so
)


def _fake_cli(tmp_path: Path, *, rc: int, stdout: str = "", stderr: str = "") -> Path:
    """A stand-in calendar CLI that reproduces one exit code/stream combination."""
    script = tmp_path / "calendar_cli.py"
    script.write_text(
        "import sys\n"
        f"sys.stdout.write({stdout!r})\n"
        f"sys.stderr.write({stderr!r})\n"
        f"raise SystemExit({rc})\n",
        encoding="utf-8",
    )
    return script


def _delegate(monkeypatch: pytest.MonkeyPatch, cli: Path) -> str:
    monkeypatch.setenv("TRIAGE_CALENDAR_CLI", str(cli))
    return triage_transport._delegate_schedule(SCHEDULE_TEXT, UID_OPAQUE).note


def _cal_lines(stderr: str) -> list[str]:
    return [line for line in stderr.splitlines() if line.startswith("CAL-")]


# --- ① the ticket's core demand: 실패 원인을 구분 --------------------------------

@pytest.mark.parametrize(("rc", "expected"), CAUSE_BY_RC)
def test_delegation_when_child_exits_nonzero_then_note_names_that_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rc: int, expected: str,
) -> None:
    note = _delegate(monkeypatch, _fake_cli(tmp_path, rc=rc, stderr=STDERR_CANARY))

    assert note == expected


def test_delegation_when_causes_differ_then_notes_differ(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An approval refusal and a crash must not read the same on the owner's card."""
    notes = [_delegate(monkeypatch, _fake_cli(tmp_path, rc=rc)) for rc, _ in CAUSE_BY_RC]

    assert len(set(notes)) == len(CAUSE_BY_RC), notes


# --- ② stderr propagation (truncated, off the card) -----------------------------

def test_delegation_when_child_writes_stderr_then_the_operator_line_carries_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    note = _delegate(monkeypatch, _fake_cli(tmp_path, rc=3, stderr=f"GATE-REFUSED {STDERR_CANARY}"))
    captured = capsys.readouterr()

    assert STDERR_CANARY in captured.err  # the child's own reason reaches the log
    assert "rc=3" in captured.err
    assert UID_OPAQUE in captured.err
    assert STDERR_CANARY not in note  # ...but never onto the digest card


def test_delegation_when_child_stderr_is_huge_then_it_is_clipped_to_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    flood = f"{STDERR_CANARY}\n" + "\n".join(f"traceback frame {n} " * 8 for n in range(200))
    _delegate(monkeypatch, _fake_cli(tmp_path, rc=6, stderr=flood))
    captured = capsys.readouterr()

    lines = _cal_lines(captured.err)
    assert len(lines) == 1, captured.err  # a mail digest log is not a dumping ground
    assert STDERR_CANARY in lines[0]
    assert len(lines[0]) <= 400


# --- ③ a slow or unlaunchable child must not take the digest down ---------------

def test_delegation_when_child_times_out_then_the_digest_survives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    def timeout(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=["calendar_cli.py"], timeout=120)

    monkeypatch.setattr(triage_transport.subprocess, "run", timeout)

    note = _delegate(monkeypatch, _fake_cli(tmp_path, rc=0))
    captured = capsys.readouterr()

    assert note == "calendar-timeout"
    assert _cal_lines(captured.err), captured.err


def test_delegation_when_the_child_cannot_be_spawned_then_the_digest_survives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    def refuse(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(triage_transport.subprocess, "run", refuse)

    note = _delegate(monkeypatch, _fake_cli(tmp_path, rc=0))
    captured = capsys.readouterr()

    assert note == "calendar-spawn-failed"
    assert _cal_lines(captured.err), captured.err


# --- ④ whatever we return still has to be safe to render ------------------------

def test_delegation_notes_stay_short_inert_and_free_of_mail_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """triage_digest renders the note inside backticks; triage_pipeline joins on ','."""
    notes = [
        _delegate(monkeypatch, _fake_cli(tmp_path, rc=rc, stderr=f"{STDERR_CANARY} {SCHEDULE_TEXT}"))
        for rc, _ in CAUSE_BY_RC
    ]

    for note in notes:
        assert note.startswith("calendar")
        assert len(note) <= 32
        assert not set(note) & set(" \t\n`,")
        assert SCHEDULE_TEXT not in note
        assert STDERR_CANARY not in note
        assert UID_OPAQUE not in note


# --- ⑤ the paths that already worked must keep working --------------------------

def test_delegation_when_the_draft_is_created_then_its_id_is_returned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = _fake_cli(tmp_path, rc=0, stdout="CHANGE summary\nDRAFT-CREATED id=cal7f3a action=create\n")

    assert _delegate(monkeypatch, cli) == "calendar:cal7f3a"


def test_delegation_when_the_card_is_posted_then_its_link_and_event_come_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a calendar CLI that posted the digest card and printed its DIGEST-CARD line.
    cli = _fake_cli(tmp_path, rc=0, stdout=(
        "DRAFT-CREATED id=cal7f3a action=create\n"
        "DIGEST-CARD draft=cal7f3a url=https://discord.com/channels/11/22/33 "
        "start=2026-10-19T13:30:00+09:00 end=2026-10-19T14:30:00+09:00 summary=Synthetic review meeting\n"
    ))
    monkeypatch.setenv("TRIAGE_CALENDAR_CLI", str(cli))

    note = triage_transport._delegate_schedule(SCHEDULE_TEXT, UID_OPAQUE)

    # Then the note keeps its id and the card link and event travel beside it.
    assert note == triage_transport.ScheduleNote(
        "calendar:cal7f3a", "https://discord.com/channels/11/22/33",
        "2026-10-19T13:30:00+09:00", "2026-10-19T14:30:00+09:00", "Synthetic review meeting",
    )


def test_delegation_when_the_card_link_is_unavailable_then_no_link_is_invented(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli = _fake_cli(tmp_path, rc=0, stdout=(
        "DRAFT-CREATED id=cal7f3a action=create\n"
        "DIGEST-CARD draft=cal7f3a url=unavailable start=- end=- summary=\n"
    ))
    monkeypatch.setenv("TRIAGE_CALENDAR_CLI", str(cli))

    assert triage_transport._delegate_schedule(SCHEDULE_TEXT, UID_OPAQUE) == triage_transport.ScheduleNote(
        "calendar:cal7f3a"
    )


def test_delegation_when_the_cli_is_absent_then_it_reports_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _delegate(monkeypatch, tmp_path / "not-deployed" / "calendar_cli.py") == "calendar-unavailable"
