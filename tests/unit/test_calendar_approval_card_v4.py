"""Calendar v4 card tests; older shared card captures and FS3 tests stay untouched."""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "skills" / "calendar" / "scripts"))
sys.path.insert(0, str(_ROOT / "tests" / "unit"))

calendar_card = import_module("calendar_card")
calendar_confirm = import_module("calendar_confirm")
calendar_core = import_module("calendar_core")
calendar_pending = import_module("calendar_pending")
CALENDAR_V3: str = import_module("mail_approval_card_golden").CALENDAR_V3


def _create_draft(
    *,
    version: str = "4",
    summary: str = "Synthetic appointment A",
    start: str = "2031-10-01T09:00:00+09:00",
    end: str = "2031-10-01T10:00:00+09:00",
) -> dict[str, str | list[str]]:
    if "T" not in start:
        body = {"summary": summary, "start": {"date": start}, "end": {"date": end}}
    else:
        body = {
            "summary": summary,
            "start": {"dateTime": start, "timeZone": "Asia/Seoul"},
            "end": {"dateTime": end, "timeZone": "Asia/Seoul"},
        }
    argv = [
        "gws", "calendar", "events", "insert",
        "--params", json.dumps({"calendarId": "primary"}, separators=(",", ":"), sort_keys=True),
        "--json", json.dumps(body, separators=(",", ":"), sort_keys=True),
    ]
    draft: dict[str, str | list[str]] = {
        "id": "abc123",
        "created": "2030-01-01T00:00:00Z",
        "action": "create",
        "argv": argv,
        "calendar_id": "primary",
        "event_id": "",
        "summary": summary,
        "start": start,
        "end": end,
        "channel_id": "dm",
        "status": "pending",
        "render_version": version,
    }
    draft["sha256"] = calendar_core.draft_sha256(draft)
    return draft


def _update_draft() -> dict[str, str | list[str]]:
    draft = _create_draft(summary="Changed title")
    draft.update(
        action="update",
        event_id="event-1",
        argv=list(calendar_core.build_patch_argv(
            "primary", "event-1", {"summary": "Changed title"},
        )),
    )
    draft["sha256"] = calendar_core.draft_sha256(draft)
    return draft


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (
            "2031-10-01T00:00:00Z",
            "2031-10-01T01:00:00Z",
            ("2031-10-01 09:00 (+09:00)", "2031-10-01 10:00 (+09:00)"),
        ),
        ("2031-10-01", "2031-10-02", ("2031-10-01", "2031-10-02")),
    ],
)
def test_v4_fact_when_time_kind_changes_keeps_four_structured_lines(
    start: str,
    end: str,
    expected: tuple[str, str],
) -> None:
    # Given
    draft = _create_draft(start=start, end=end)
    # When
    lines = calendar_card.change_details(draft).splitlines()
    # Then
    assert [line.partition(":")[0] for line in lines] == ["작업", "제목", "시작", "종료"]
    assert lines[2].endswith(expected[0])
    assert lines[3].endswith(expected[1])


def test_v4_update_fact_when_only_title_changes_keeps_v3_markers() -> None:
    # Given
    draft = _update_draft()
    # When
    lines = calendar_card.change_details(draft).splitlines()
    # Then
    assert lines[1].endswith("(변경)")
    assert lines[2].endswith("(유지·초안 조회값)")
    assert lines[3].endswith("(유지·초안 조회값)")


def test_v4_confirmation_when_rendered_uses_owner_v2_and_full_hash_footer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    from automation.interop import owner_message

    draft = _create_draft()
    captured: list[owner_message.OwnerMessage] = []

    def render(message: owner_message.OwnerMessage, *, destination: owner_message.Ref) -> str:
        captured.append(message)
        assert destination == owner_message.Ref(scope="self")
        return "CARD"

    monkeypatch.setattr(owner_message, "render", render)
    # When
    content = calendar_confirm.render_confirmation(draft)
    # Then
    assert len(captured) == 1
    assert captured[0].render_version == "owner-ko-v2"
    assert content == f"CARD\n해시: `sha256:{draft['sha256']}`"


def test_new_calendar_card_when_prepared_selects_v4_without_changing_hash() -> None:
    # Given
    draft = _create_draft()
    draft.pop("render_version")
    digest = draft["sha256"]
    # When
    prepared = calendar_card.prepare_draft(draft)
    # Then
    assert prepared["render_version"] == "4"
    assert prepared["sha256"] == digest
    assert prepared["approval_content"].splitlines()[-1] == f"해시: `sha256:{digest}`"


def test_pending_calendar_card_when_v4_is_stored_round_trips_strictly(tmp_path: Path) -> None:
    # Given
    store = calendar_pending.PendingConfirmStore(tmp_path / "pending.jsonl")
    entry = calendar_pending.PendingConfirm(
        draft_id="abc123",
        sha256="digest",
        dm_channel_id="222",
        dm_message_id="333",
        created=datetime(2030, 1, 1, tzinfo=UTC),
        render_version="4",
    )
    # When
    store.append(entry)
    # Then
    assert store.load() == (entry,)


@pytest.mark.parametrize("version", ["1", "2", "3", "4"])
def test_frozen_calendar_card_when_stored_version_replays_posted_copy(
    version: str,
) -> None:
    # Given
    from tests.unit.mail_approval_card_golden import BYTES, CALENDAR_V4
    from tests.unit.test_mail_approval_cards import LEGACY, record

    if version in {"3", "4"}:
        draft = _create_draft(version=version)
        expected = CALENDAR_V3 if version == "3" else CALENDAR_V4
    else:
        draft = {**record(), "render_version": version}
        expected = LEGACY["calendar"] if version == "1" else BYTES["calendar"]
    # When
    content = calendar_confirm.render_confirmation(draft)
    # Then
    assert content == expected
