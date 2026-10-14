from __future__ import annotations

import dataclasses
import unicodedata
from datetime import date

import pytest

from automation.drive_taxonomy import (
    CATEGORIES,
    MAX_DEPTH,
    Category,
    Periodicity,
    TaxonomyError,
    artifact_name,
    bundle_name,
    category,
    ensure_depth,
    folder_parts,
    outputs_root,
    period_key,
)


@pytest.mark.parametrize(
    ("periodicity", "on", "expected"),
    [
        pytest.param("weekly", date(2026, 8, 23), "2026-W34", id="weekly-iso-week"),
        pytest.param("monthly", date(2026, 8, 23), "2026-08", id="monthly"),
        pytest.param("oneshot", date(2026, 8, 23), "2026-08-23", id="oneshot"),
        pytest.param("weekly", date(2021, 1, 1), "2020-W53", id="weekly-iso-year"),
        pytest.param("weekly", date(2026, 1, 5), "2026-W02", id="weekly-zero-padded"),
    ],
)
def test_period_key_matches_the_convention(
    periodicity: Periodicity,
    on: date,
    expected: str,
) -> None:
    # Given: a fixed date and one of the registered periodicities.
    # When: the period key is derived.
    # Then: it follows the ISO week / month / day convention.
    assert period_key(periodicity, on) == expected


def test_period_key_rejects_an_unknown_periodicity() -> None:
    # Given: a periodicity that no category declares.
    # When/Then: the taxonomy refuses to invent a key.
    with pytest.raises(TaxonomyError):
        period_key("daily", date(2026, 8, 23))  # pyright: ignore[reportArgumentType]


def test_registry_pins_folders_periodicity_and_flags() -> None:
    # Given: the single registry of output categories.
    # When/Then: every kind keeps its folder, periodicity and flags.
    assert CATEGORIES == {
        "report": Category(
            folder="주간동향",
            periodicity="weekly",
            always_bundle=True,
        ),
        "proposal": Category(folder="제안서", periodicity="oneshot"),
        "budget": Category(folder="예산", periodicity="monthly"),
        "meeting": Category(
            folder="회의록",
            periodicity="oneshot",
            skill_owned="skills/meeting/scripts/meeting_cli.py ingest --project <과제명>",
        ),
        "transcript": Category(
            folder="전사본",
            periodicity="oneshot",
            skill_owned="skills/speechtotext/scripts/speechtotext_cli.py",
        ),
        "audio": Category(
            folder="녹음원본",
            periodicity="oneshot",
            skill_owned="plaud_sync",
        ),
        "procurement": Category(folder="구매", periodicity="oneshot"),
        "doctype": Category(folder="문서", periodicity="oneshot"),
        "patent": Category(folder="특허", periodicity="oneshot", gate_only=True),
        "share": Category(
            folder="공유",
            periodicity="oneshot",
            skill_owned="skills/mail/scripts/triage_cli.py compose --drive-share <파일> --share-with <주소>",
        ),
    }


def test_category_entries_are_immutable() -> None:
    # Given: a registry entry shared by every caller.
    # When/Then: mutating it is impossible.
    with pytest.raises(dataclasses.FrozenInstanceError):
        CATEGORIES["report"].folder = "다른폴더"  # type: ignore[misc]


def test_folder_parts_uses_root_category_and_year() -> None:
    # Given: the default outputs root.
    # When: the folder parts for a weekly report are built.
    parts = folder_parts("report", 2026)

    # Then: they are root / category folder / year.
    assert parts == ("autophagy", "주간동향", "2026")


def test_folder_parts_honours_the_root_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an owner-provided root written with decomposed jamo.
    monkeypatch.setenv("DRIVE_OUTPUTS_ROOT", unicodedata.normalize("NFD", "산출물"))

    # When: folder parts are built.
    parts = folder_parts("doctype", 2026)

    # Then: the override wins and is normalized.
    assert parts == ("산출물", "문서", "2026")


def test_names_are_nfc_normalized() -> None:
    # Given: a title carrying decomposed jamo (macOS-style input).
    decomposed = unicodedata.normalize("NFD", "주간연구동향")
    assert decomposed != "주간연구동향"

    # When: artifact and bundle names are produced.
    artifact = artifact_name("2026-W34", decomposed, ".md")
    bundle = bundle_name("2026-W34", decomposed)

    # Then: both equal their composed form.
    assert artifact == "2026-W34_주간연구동향.md"
    assert bundle == "2026-W34_주간연구동향"
    assert artifact == unicodedata.normalize("NFC", artifact)
    assert bundle == unicodedata.normalize("NFC", bundle)


def test_depth_guard_accepts_a_bundle_internal_file() -> None:
    # Given: a file inside a weekly bundle (root counted as depth 1).
    parts = (
        *folder_parts("report", 2026),
        bundle_name("2026-W34", "주간연구동향"),
        artifact_name("2026-W34", "발표슬라이드", ".md"),
    )
    assert len(parts) == MAX_DEPTH

    # When/Then: depth 5 is the accepted ceiling.
    assert ensure_depth(parts) == parts


def test_depth_guard_rejects_one_level_below_a_bundle() -> None:
    # Given: an extra folder nested inside the bundle.
    parts = (
        *folder_parts("report", 2026),
        bundle_name("2026-W34", "주간연구동향"),
        "추가폴더",
        artifact_name("2026-W34", "발표슬라이드", ".md"),
    )

    # When/Then: depth 6 is refused.
    with pytest.raises(TaxonomyError):
        ensure_depth(parts)


def test_depth_guard_allows_shallow_placement() -> None:
    # Given: the budget ledger sheet parked directly under its category.
    parts = (outputs_root(), CATEGORIES["budget"].folder, "예산원장")

    # When/Then: only the upper bound is enforced.
    assert ensure_depth(parts) == parts


def test_depth_guard_normalizes_the_parts_it_returns() -> None:
    # Given: path parts with decomposed jamo.
    parts = (outputs_root(), unicodedata.normalize("NFD", "문서"), "2026")

    # When: the guard passes them through.
    checked = ensure_depth(parts)

    # Then: they come back composed.
    assert checked == (outputs_root(), "문서", "2026")


def test_gate_only_kind_is_refused_by_the_publish_path() -> None:
    # Given: patent is reserved for its dedicated export gate.
    assert CATEGORIES["patent"].gate_only is True

    # When/Then: the ordinary publish path refuses it.
    with pytest.raises(TaxonomyError) as excinfo:
        folder_parts("patent", 2026)
    assert "patent" in str(excinfo.value)


def test_gate_only_flag_is_reserved_for_patent() -> None:
    # Given: the registry.
    # When/Then: patent is the only gate-only kind, report the only bundled one.
    gate_only = tuple(kind for kind, item in CATEGORIES.items() if item.gate_only)
    bundled = tuple(kind for kind, item in CATEGORIES.items() if item.always_bundle)
    assert gate_only == ("patent",)
    assert bundled == ("report",)


def test_gate_only_kind_still_resolves_for_migration() -> None:
    # Given: the migrator needs the patent folder name without hardcoding it.
    # When/Then: the registry lookup keeps working for gate-only kinds.
    assert category("patent").folder == "특허"


@pytest.mark.parametrize(
    "kind",
    [
        pytest.param("wiki", id="never-registered"),
        pytest.param("Report", id="wrong-case"),
        pytest.param("", id="empty"),
        pytest.param("주간동향", id="folder-name-instead-of-kind"),
    ],
)
def test_unregistered_kind_raises(kind: str) -> None:
    # Given: a kind that is absent from the registry.
    # When/Then: both the lookup and the publish path fail closed.
    with pytest.raises(TaxonomyError):
        category(kind)
    with pytest.raises(TaxonomyError):
        folder_parts(kind, 2026)


def test_transcript_category_is_registered_for_audio_transcripts() -> None:
    from automation import drive_taxonomy

    selected = drive_taxonomy.category("transcript")
    assert selected.folder == "전사본"
    assert selected.periodicity == "oneshot"
    assert selected.gate_only is False


def test_audio_folder_parts_use_recording_source_folder() -> None:
    assert folder_parts("audio", 2026, project="lifelog") == (
        "autophagy", "녹음원본", "lifelog", "2026",
    )


def test_folder_parts_inserts_the_project_between_category_and_year() -> None:
    # Given: a category whose outputs are managed per research project.
    # When/Then: the project sits between the category and the year, and the file
    # that lands beneath it is exactly at the depth ceiling.
    parts = folder_parts("transcript", 2026, project="해양고신뢰성")
    assert parts == ("autophagy", "전사본", "해양고신뢰성", "2026")
    assert len(ensure_depth((*parts, artifact_name("2026-08-26", "킥오프", ".md")))) == MAX_DEPTH


def test_folder_parts_without_a_project_is_exactly_what_it_was() -> None:
    """Six other skills publish through this call and must not move."""
    assert folder_parts("transcript", 2026) == ("autophagy", "전사본", "2026")
    assert folder_parts("report", 2026) == ("autophagy", "주간동향", "2026")


def test_project_may_not_smuggle_a_path_separator() -> None:
    for bad in ("해양/고신뢰성", "해양\\고신뢰성", "   "):
        with pytest.raises(TaxonomyError):
            folder_parts("transcript", 2026, project=bad)


def test_a_bundle_inside_a_project_is_refused_by_the_depth_ceiling() -> None:
    """A project plus a bundle would put the file at depth 6 — fail closed, loudly."""
    parts = (
        *folder_parts("transcript", 2026, project="해양고신뢰성"),
        bundle_name("2026-08-26", "킥오프"),
        artifact_name("2026-08-26", "킥오프", ".md"),
    )
    with pytest.raises(TaxonomyError):
        ensure_depth(parts)


def test_project_parts_addresses_the_project_folder_itself() -> None:
    """The glossary lives in the project folder, so callers need to name it without
    inventing the path themselves — the registry keeps owning the category name."""
    from automation.drive_taxonomy import project_parts

    assert project_parts("transcript", "해양고신뢰성") == ("autophagy", "전사본", "해양고신뢰성")
    assert folder_parts("transcript", 2026, project="해양고신뢰성") == (
        *project_parts("transcript", "해양고신뢰성"), "2026",
    )
