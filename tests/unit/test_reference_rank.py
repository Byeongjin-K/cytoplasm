"""참고자료 후보 줄 세우기 — 읽을 수 있는 것이 먼저, 질의를 넓게 맞추는 것이 먼저."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from automation import reference_rank
from automation.document_text import HWP_REASON, SUPPORTED_REASON

MAX_BYTES = 64 * 1024 * 1024
_FORM = "application/vnd.google-apps.form"
_DOC = "application/vnd.google-apps.document"


@dataclass(frozen=True, slots=True)
class _Candidate:
    name: str
    path: str
    mime_type: str = "application/octet-stream"
    modified: str = "2026-08-01T09:00:00.000Z"
    size: int = 0


def _md(
    name: str = "기준.md",
    *,
    mime_type: str = "application/octet-stream",
    size: int = 0,
) -> _Candidate:
    return _Candidate(name=name, path=f"KIMM/{name}", mime_type=mime_type, size=size)


def test_supported_suffix_has_no_refusal() -> None:
    assert reference_rank.refusal(_md(), MAX_BYTES) == ""


def test_google_document_is_readable_through_export() -> None:
    assert reference_rank.refusal(_md("설계 기준", mime_type=_DOC), MAX_BYTES) == ""


def test_google_form_is_refused_without_touching_it() -> None:
    refusal = reference_rank.refusal(_md("굴착 설문", mime_type=_FORM), MAX_BYTES)
    assert refusal == "내보낼 수 없는 Google 형식입니다"


def test_old_hwp_carries_the_actionable_reason() -> None:
    assert reference_rank.refusal(_md("계획서.hwp"), MAX_BYTES) == HWP_REASON


def test_unknown_suffix_names_what_is_supported() -> None:
    assert reference_rank.refusal(_md("설치본.exe"), MAX_BYTES) == SUPPORTED_REASON


def test_oversized_file_is_refused_from_its_metadata_alone() -> None:
    huge = _md("대용량.pdf", size=MAX_BYTES + 1)
    assert reference_rank.refusal(huge, MAX_BYTES) == "64MiB 를 넘습니다"


def test_unknown_size_is_not_treated_as_oversized() -> None:
    assert reference_rank.refusal(_md("크기미상.pdf", size=0), MAX_BYTES) == ""


def test_refusable_candidates_sort_behind_readable_ones() -> None:
    form = _md("굴착 오차 기준 설문", mime_type=_FORM)
    note = _md("메모.md")
    ordered = sorted((form, note), key=lambda item: reference_rank.fetch_key(item, ("굴착", "오차"), MAX_BYTES))

    assert [item.name for item in ordered] == ["메모.md", "굴착 오차 기준 설문"]


def test_name_matches_still_lead_among_readable_candidates() -> None:
    wanted = ("굴착", "오차")
    ordered = sorted(
        (_md("메모.md"), _md("굴착 오차 관리.md")),
        key=lambda item: reference_rank.fetch_key(item, wanted, MAX_BYTES),
    )

    assert [item.name for item in ordered] == ["굴착 오차 관리.md", "메모.md"]


def test_coverage_counts_distinct_terms_not_repeats() -> None:
    wanted = ("굴착", "오차", "기준")
    assert reference_rank.coverage("굴착 굴착 굴착 굴착 굴착", wanted) == 1
    assert reference_rank.coverage("굴착 오차 기준", wanted) == 3


def test_coverage_sees_the_path_as_well_as_the_body() -> None:
    assert reference_rank.coverage("KIMM/굴착 기준.md\n오차를 다룬다", ("굴착", "오차")) == 2


def test_text_score_still_counts_every_occurrence() -> None:
    assert reference_rank.text_score("굴착 굴착 오차", ("굴착", "오차")) == 3


def test_terms_drops_stopwords_and_single_characters() -> None:
    found = reference_rank.terms("회의 자료 굴착 오차 a")

    assert found == ("굴착", "오차")


def test_snippet_centres_on_the_first_match() -> None:
    body = "머리말 " * 60 + "굴착 오차는 10 mm 이하로 관리한다." + " 꼬리말" * 60
    quoted = reference_rank.snippet(body, ("굴착",))

    assert "굴착 오차는 10 mm 이하로 관리한다." in quoted
    assert quoted.startswith("…")


def test_link_for_builds_the_drive_view_url() -> None:
    assert reference_rank.link_for("file1") == "https://drive.google.com/file/d/file1/view"


def test_old_hwp_is_readable_when_ocr_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCUMENT_OCR_CMD", "dori-ocr")
    assert reference_rank.refusal(_md("계획서.hwp"), MAX_BYTES) == ""
    assert reference_rank.refusal(_md("현장사진.png"), MAX_BYTES) == ""
    assert reference_rank.refusal(_md("설치본.exe"), MAX_BYTES) == SUPPORTED_REASON
