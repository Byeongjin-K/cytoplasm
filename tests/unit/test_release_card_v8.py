"""Release card v8: the note's own summary line and only the switches this release changes."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from automation import release_notes
from automation.auto_write_policy import CARD_LINES, SURFACE_PREFIX
from automation.notice_bot import CARD_LINE, SURFACE
from automation.release_spec import ReleaseSpec, spec_from_record
from automation.release_spec_message import approval_surface_lines

KEPT = (("hermes-gateways:stopped", "d" * 64), (SURFACE, "e" * 64),
        *((SURFACE_PREFIX + kind, "f" * 64) for kind in CARD_LINES if kind != "yoodori-memory"))
NEW = (SURFACE_PREFIX + "yoodori-memory", "a" * 64)


def _spec(**fields) -> ReleaseSpec:
    rows = (*((f"home:package-{index:02}", "c" * 64) for index in range(30)), *KEPT, NEW)
    return ReleaseSpec("v2.4.0", "a" * 40, "b" * 32, rows,
                       "### runtime\n- first commit title\n- second commit title", **fields)


def test_summary_comes_from_the_note_and_only_new_switches_get_a_line() -> None:
    # Given: the previous release already carried every switch except one.
    spec = _spec(card_summary="유돌 기억 사본과 고장 알림", previous_surfaces=KEPT)
    # When
    card = spec.render()
    lines = card.splitlines()
    # Then: the note line leads, the new switch is shown, the kept ones collapse to one count.
    assert "유돌 기억 사본과 고장 알림" in lines[1]
    assert "first commit title" not in card
    assert CARD_LINES["yoodori-memory"] in card
    assert CARD_LINE not in card
    assert f"계속 켜져 있는 스위치 {len(KEPT)}개" in card
    assert len(lines) <= 12 and len(card) <= 1900
    assert spec.action_hash() == replace(spec, render_version=7, card_summary="", previous_surfaces=None).action_hash()


def test_a_switch_whose_value_changed_is_shown_again() -> None:
    # Given: the notice bot surface changed its digest since the previous release.
    previous = tuple(row if row[0] != SURFACE else (SURFACE, "0" * 64) for row in KEPT)
    # When / Then: its line comes back; a dropped changed switch would fail here.
    assert CARD_LINE in _spec(previous_surfaces=previous).render()


def test_unknown_previous_release_shows_every_switch_and_falls_back_to_a_commit_title() -> None:
    card = _spec().render()
    assert all(line in card for line in approval_surface_lines(_spec()))
    assert "first commit title" in card and "계속 켜져 있는" not in card


def test_record_replays_summary_and_previous_surfaces_byte_for_byte() -> None:
    from automation.interop.approval_surface import ApprovalBinding, ApprovalKind, ApprovalSurface, POLICY_VERSION

    spec = _spec(card_summary="요약", previous_surfaces=KEPT)
    binding = ApprovalBinding(ApprovalKind.RELEASE, ApprovalSurface.SKILL_APPROVALS, "999", POLICY_VERSION)
    record = spec.new_record("123", binding)
    replay = spec_from_record(record)
    assert replay.render() == spec.render() and replay.bound(spec.render(), record)


def test_plan_helpers_read_the_note_line_and_previous_surfaces(tmp_path: Path) -> None:
    (tmp_path / ("a" * 40 + ".md")).write_text("# 한 줄 요약\n본문", encoding="utf-8")
    release_notes.save_surfaces(tmp_path, "b" * 40, KEPT)
    assert release_notes.card_summary(tmp_path, "a" * 40) == "한 줄 요약"
    assert release_notes.card_summary(tmp_path, "c" * 40) == ""
    assert release_notes.previous_surfaces(tmp_path, "b" * 40) == [list(row) for row in KEPT]
    assert release_notes.previous_surfaces(tmp_path, "c" * 40) is None
    (tmp_path / ("d" * 40 + ".surfaces.json")).write_text(json.dumps({"bad": 1}), encoding="utf-8")
    assert release_notes.previous_surfaces(tmp_path, "d" * 40) is None
