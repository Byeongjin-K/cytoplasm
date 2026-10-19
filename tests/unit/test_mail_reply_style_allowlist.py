"""The quoted original keeps its look but cannot hide or move text.

Owner Q275 (2026-10-19, accepting PR #119): "받기 — 다음 릴리스에서, 숨김·위치 같은 서식은 빼고".
The original mail's HTML is written by whoever sent it, and the approval card shows only one line
about the quote, so a style that hides or overlays text in the quote must not reach the outgoing
mail. New file so PR #119's own tests keep their shape.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/mail/scripts"))

import mail_quote  # noqa: E402


@pytest.mark.parametrize("style", [
    "display:none", "visibility:hidden", "position:absolute;top:0;left:0", "z-index:9",
    "opacity:0", "overflow:hidden", "transform:scale(0)", "font-size:0", "font-size:1px",
    "line-height:0", "color:transparent", "clip-path:inset(50%)",
])
def test_a_hiding_or_positioning_style_is_dropped(style: str) -> None:
    cleaned = mail_quote.sanitize_html(f'<p style="{style}">숨긴 글</p>')
    assert "숨긴 글" in cleaned and "style=" not in cleaned


def test_display_styles_are_kept() -> None:
    cleaned = mail_quote.sanitize_html(
        '<td style="color:#c00000;font-family:돋움;font-size:10pt;border:1px solid #000;'
        'text-align:center;position:absolute">표</td>')
    assert 'style="color:#c00000;font-family:돋움;font-size:10pt;border:1px solid #000;text-align:center"' in cleaned
