"""Attachment names with a human size tail ("(1.37 MB)") split like the byte form.

A new file because the existing scraper tests pin their cases.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

for _name in ("pyotp", "dotenv", "bs4"):
    try:
        __import__(_name)
    except ImportError:
        _stub = types.ModuleType(_name)
        _stub.TOTP = _stub.load_dotenv = _stub.BeautifulSoup = object  # type: ignore[attr-defined]
        sys.modules[_name] = _stub
_VENDOR = str(Path(__file__).resolve().parents[2] / "skills/mail/vendor")
if _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)
from mailon.scraper import split_filename_and_size  # noqa: E402


@pytest.mark.parametrize(("raw", "expected"), [
    ("발표자료.pptx (1.37 MB)", ("발표자료.pptx", int(1.37 * 1024 ** 2))),
    ("영상.mp4 12.9 MB", ("영상.mp4", int(12.9 * 1024 ** 2))),
    ("표.xlsx (500 KB)", ("표.xlsx", 500 * 1024)),
    ("파일.mp4 547980148", ("파일.mp4", 547980148)),
    ("보고서 2 MB.hwp", ("보고서 2 MB.hwp", None)),
    ("12 MB", ("12 MB", None)),
])
def test_split_handles_byte_and_human_size_tails(raw: str, expected: tuple[str, int | None]) -> None:
    assert split_filename_and_size(raw) == expected
