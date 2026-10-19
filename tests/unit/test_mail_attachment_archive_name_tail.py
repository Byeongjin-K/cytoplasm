"""Drive names for attachments whose MailOn row kept the page's size tail (2026-10-19).

Three large attachments recorded before MailOn's scraper split sizes kept names such as
"bg&str.wrl 37.86 MB", and so did their saved copies. The archive named the Drive files
"9785553__bg&str.wrl 37.86 MB", which no longer end in their file type. New file so the
recorded drive-sync cases keep their shape.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))

import mail_attachment_archive as archive  # noqa: E402


def _item(tmp_path: Path, filename: str) -> archive.Attachment:
    local = tmp_path / "9785553" / filename
    return archive.Attachment(
        key="b" * 64, uid="9785553", filename=filename, local=local,
        size=1, mtime_ns=1, year="2026", month="10", mailbox="inbox",
    )


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("bg&str.wrl 37.86 MB", "9785553__bg&str.wrl"),
        ("프로그램검토1018.pptx 254.34 KB", "9785553__프로그램검토1018.pptx"),
        ("Pretendard-1.3.9 (1) (2).zip (44.88 MB)", "9785553__Pretendard-1.3.9 (1) (2).zip"),
        ("KIMM_AIDX_v03.mp4 547980148", "9785553__KIMM_AIDX_v03.mp4"),
    ],
)
def test_a_size_tail_is_dropped_and_the_file_type_ends_the_name(
    tmp_path: Path, filename: str, expected: str,
) -> None:
    # Given: a row and saved copy that both still carry the page's size tail.
    # When / Then: the Drive name ends in the real file type.
    assert archive.remote_name(_item(tmp_path, filename)) == expected


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("회의록 260330.hwp", "9785553__회의록 260330.hwp"),
        ("v123.pdf", "9785553__v123.pdf"),
        ("12.5 MB", "9785553__12.5 MB"),
        ("보고서 2026.zip", "9785553__보고서 2026.zip"),
    ],
)
def test_names_without_a_size_tail_are_unchanged(tmp_path: Path, filename: str, expected: str) -> None:
    # Given: names whose digits are part of the name; When / Then: nothing is cut.
    assert archive.remote_name(_item(tmp_path, filename)) == expected


def test_the_attachment_key_still_uses_the_recorded_name() -> None:
    # Given / When: the identity of an already archived row; Then: unchanged by the display fix.
    assert archive.attachment_key("9785553", "bg&str.wrl 37.86 MB") != archive.attachment_key(
        "9785553", "bg&str.wrl")
