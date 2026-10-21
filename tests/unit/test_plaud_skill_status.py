"""``skills/plaud`` — read-only status of the plaud lifelog sync watcher.

The owner asked for a skill so "read ~/.hermes/plaud-sync/state.json and tell me the
per-status counts and last_poll_at" does not have to be remembered as a path. The CLI
is stdlib-only so the sandbox (disposable HOME, no repo on sys.path) can run it, and it
never writes anywhere.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Final

import pytest

from automation.interop.owner_message import Space, discord_link

_REPO: Final = Path(__file__).resolve().parents[2]
_CLI: Final = _REPO / "skills" / "plaud" / "scripts" / "plaud_cli.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("plaud_cli_under_test", _CLI)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _record(status: str, **extra: object) -> dict[str, object]:
    base: dict[str, object] = {
        "recording_id": "rec-x",
        "recorded_at": "2026-09-02T04:00:00Z",
        "note_relpath": "000_PARA/Area/Lifelog/2026/2026-09-02-x--abcdef123456.md",
        "status": status,
        "channel_id": "111",
        "message_id": "m-1" if status == "posted" else None,
        "approval_thread_id": "111" if status == "posted" else None,
    }
    base.update(extra)
    return base


def _state(**records: dict[str, object]) -> dict[str, object]:
    return {"version": 1, "last_poll_at": "2026-09-02T08:38:52Z", "records": records}


def test_summarize_counts_every_status_and_lists_cards_waiting_for_the_owner() -> None:
    cli = _load()
    summary = cli.summarize(
        _state(
            a=_record("posted", recording_id="rec-a"),
            b=_record("planned", recording_id="rec-b"),
            c=_record("written", recording_id="rec-c"),
            d=_record("posted", recording_id="rec-d", approval_thread_id="222"),
        )
    )
    assert summary.total == 4
    assert dict(summary.counts) == {
        "transcribing": 0, "planned": 1, "posted": 2, "approved": 0, "written": 1,
        "abandoned": 0,
    }
    assert summary.last_poll_at == "2026-09-02T08:38:52Z"
    assert [(p.recording_id, p.thread_id) for p in summary.pending] == [
        ("rec-a", "111"), ("rec-d", "222"),
    ]
    assert summary.pending[0].note_name == "2026-09-02-x--abcdef123456.md"


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"version": 1, "records": []},
        _state(a=_record("teleported")),
        {"version": 1, "last_poll_at": 5, "records": {}},
    ],
)
def test_summarize_refuses_a_state_it_does_not_understand(payload: object) -> None:
    cli = _load()
    with pytest.raises(cli.StatusError):
        _ = cli.summarize(payload)


def test_status_reports_absent_state_without_failing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = _load()
    assert cli.main(["status", "--state", str(tmp_path / "missing.json")]) == 0
    assert capsys.readouterr().out.strip() == "PLAUD-STATUS state=absent"


def test_status_renders_counts_poll_time_in_kst_and_pending_cards(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = _load()
    path = tmp_path / "state.json"
    _ = path.write_text(
        json.dumps(_state(a=_record("posted", recording_id="rec-a"), b=_record("planned"))),
        encoding="utf-8",
    )
    assert cli.main(["status", "--state", str(path)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("PLAUD-STATUS state=present")
    assert "2026-09-02T08:38:52Z" in out and "2026-09-02 17:38 KST" in out
    assert "레코드 2건" in out and "posted 1" in out and "planned 1" in out
    assert "rec-a · 스레드 111 · 2026-09-02-x--abcdef123456.md" in out


def test_status_json_is_machine_readable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = _load()
    path = tmp_path / "state.json"
    _ = path.write_text(json.dumps(_state(a=_record("posted"))), encoding="utf-8")
    assert cli.main(["status", "--state", str(path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "present"
    assert payload["counts"]["posted"] == 1
    assert payload["pending"][0]["recording_id"] == "rec-x"


def test_status_exits_two_on_an_unreadable_state(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = _load()
    path = tmp_path / "state.json"
    _ = path.write_text("{", encoding="utf-8")
    assert cli.main(["status", "--state", str(path)]) == 2
    assert "PLAUD-STATUS state=unreadable" in capsys.readouterr().err


def test_status_lists_approved_records_with_their_write_failure_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = _load()
    path = tmp_path / "state.json"
    _ = path.write_text(
        json.dumps(
            _state(
                a=_record(
                    "approved",
                    recording_id="rec-a",
                    last_block_reason="write: ObsidianWriteError: fetch before upsert failed",
                ),
            )
        ),
        encoding="utf-8",
    )
    assert cli.main(["status", "--state", str(path)]) == 0
    out = capsys.readouterr().out
    assert "저장 대기(approved) 1건" in out
    assert "rec-a · 사유 write: ObsidianWriteError: fetch before upsert failed" in out


_LEGACY_HUMAN: Final = """PLAUD-STATUS state=present
- 마지막 Plaud 폴: 2026-09-02T08:38:52Z (2026-09-02 17:38 KST)
- 레코드 1건: transcribing 0 · planned 0 · posted 1 · approved 0 · written 0 · abandoned 0
- 승인 대기(posted) 1건:
  - rec-x · 스레드 111 · 2026-09-02-x--abcdef123456.md"""
_LEGACY_JSON: Final = """{
  "state": "present", "last_poll_at": "2026-09-02T08:38:52Z", "total": 1,
  "counts": {"transcribing": 0, "planned": 0, "posted": 1, "approved": 0,
             "written": 0, "abandoned": 0},
  "approved": [], "transcribing": [], "transcripts_dir": null, "transcripts": [],
  "pending": [{"recording_id": "rec-x", "thread_id": "111",
               "note_name": "2026-09-02-x--abcdef123456.md",
               "recorded_at": "2026-09-02T04:00:00Z"}]
}"""


def test_legacy_status_bytes_and_json_keys_are_preserved() -> None:
    cli = _load()
    summary = cli.summarize(_state(a=_record("posted")))
    assert cli.render(summary) == _LEGACY_HUMAN
    payload = cli._payload(summary)
    for pending in payload["pending"]:
        pending.pop("thread_url", None)
    assert payload == json.loads(_LEGACY_JSON)


def test_legacy_thread_id_falls_back_to_the_stored_channel() -> None:
    cli = _load()
    summary = cli.summarize(_state(a=_record("posted", approval_thread_id=None)))
    assert summary.pending[0].thread_id == "111"
    assert cli.render(summary) == _LEGACY_HUMAN


def test_status_cli_reports_clickable_thread_without_changing_existing_json_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    cli = _load()
    path = Path("state.json")
    state = json.dumps(_state(a=_record(
        "posted", approval_guild_id="111", approval_thread_id="222",
    )))
    _ = path.write_text(state, encoding="utf-8")
    assert cli.main(["status", "--state", "state.json", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "state": "present", "last_poll_at": "2026-09-02T08:38:52Z", "total": 1,
        "counts": {"transcribing": 0, "planned": 0, "posted": 1, "approved": 0,
                   "written": 0, "abandoned": 0},
        "approved": [], "transcribing": [], "transcripts_dir": "transcripts",
        "transcripts": [],
        "pending": [{"recording_id": "rec-x", "thread_id": "222",
                     "note_name": "2026-09-02-x--abcdef123456.md",
                     "recorded_at": "2026-09-02T04:00:00Z",
                     "thread_url": "https://discord.com/channels/111/222"}],
    }
    assert cli.main(["status", "--state", "state.json"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "PLAUD-STATUS state=present"
    assert out.splitlines()[-1] == (
        "  - rec-x · 스레드 [승인 스레드](<https://discord.com/channels/111/222>) · 2026-09-02-x--abcdef123456.md"
    )
    assert path.read_text(encoding="utf-8") == state
    assert sorted(p.name for p in tmp_path.iterdir()) == ["state.json"]


@pytest.mark.parametrize(("guild_id", "thread_id", "expected"), [
    ("111", "222", "https://discord.com/channels/111/222"),
    ("333", "444", "https://discord.com/channels/333/444"),
    ("00111", "00222", "https://discord.com/channels/00111/00222"),
    ("1", "1", "https://discord.com/channels/1/1"),
])
def test_thread_url_matches_the_shared_definition(
    guild_id: str, thread_id: str, expected: str,
) -> None:
    cli = _load()
    summary = cli.summarize(_state(a=_record(
        "posted", approval_guild_id=guild_id, approval_thread_id=thread_id, message_id=None,
    )))
    pending = summary.pending[0]
    assert getattr(pending, "guild_id", None) == guild_id
    assert getattr(pending, "thread_url", None) == expected
    assert pending.thread_url == discord_link(
        space="guild", guild_id=guild_id, channel_id=thread_id, message_id=None,
    ).url


@pytest.mark.parametrize("key", ["approval_guild_id", "approval_thread_id"])
@pytest.mark.parametrize(("invalid", "coordinate"), [
    (None, ""), ("", ""), ("0", "0"), ("000", "000"), ("bad", "bad"),
    ("1/2", "1/2"), ("-1", "-1"), ("+1", "+1"), ("1.0", "1.0"),
    (" 111", " 111"), ("111 ", "111 "), ("١١١", "١١١"), ("１１１", "１１１"),
    (111, ""), (True, ""),
])
def test_missing_or_malformed_coordinates_never_guess_a_link(
    key: str, invalid: str | int | None, coordinate: str,
) -> None:
    cli = _load()
    record = _record("posted", approval_guild_id="111", approval_thread_id="222")
    record[key] = invalid
    summary = cli.summarize(_state(a=record))
    payload = cli._payload(summary)
    assert "thread_url" in payload["pending"][0]
    assert payload["pending"][0]["thread_url"] is None
    # Explicit fixture coordinates pin the boundary conversion, not a production helper.
    assert payload["pending"][0]["thread_url"] == discord_link(
        space="guild", guild_id=coordinate if key == "approval_guild_id" else "111",
        channel_id=coordinate if key == "approval_thread_id" else "222",
    ).url
    out = cli.render(summary)
    assert "discord.com/channels" not in out
    assert "@me" not in out + json.dumps(payload)
    assert "rec-x" in out and "2026-09-02-x--abcdef123456.md" in out


@pytest.mark.parametrize(("space", "guild_id", "thread_id"), [
    ("unknown", None, "222"), ("guild", None, "222"),
    ("guild", "111", None), ("guild", None, None),
])
def test_missing_coordinates_match_shared_unavailable_branches(
    space: Space, guild_id: str | None, thread_id: str | None,
) -> None:
    # Plaud stores guild/thread coordinates, not a space discriminator or message target.
    cli = _load()
    summary = cli.summarize(_state(a=_record(
        "posted", approval_guild_id=guild_id, approval_thread_id=thread_id, message_id=None,
    )))
    assert summary.pending[0].thread_url is None
    assert summary.pending[0].thread_url == discord_link(
        space=space, guild_id=guild_id, channel_id=thread_id, message_id=None,
    ).url


def test_legacy_json_explicitly_reports_no_thread_url(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    cli = _load()
    path = tmp_path / "state.json"
    _ = path.write_text(json.dumps(_state(a=_record("posted"))), encoding="utf-8")
    assert cli.main(["status", "--state", str(path), "--json"]) == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["pending"] == [{
        "recording_id": "rec-x", "thread_id": "111",
        "note_name": "2026-09-02-x--abcdef123456.md",
        "recorded_at": "2026-09-02T04:00:00Z", "thread_url": None,
    }]
    assert "@me" not in out
