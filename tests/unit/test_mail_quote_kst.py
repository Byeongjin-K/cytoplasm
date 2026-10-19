"""The quote header shows the original's date in Korea time.

2026-10-19: a mail received 10/18 22:12 KST was quoted as "2026-10-18 13:12" because MailOn stores
the received time as naive UTC. New file so the recorded quote cases keep their shape.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/mail/scripts"))

import mail_quote  # noqa: E402


@pytest.mark.parametrize(("stored", "shown"), [
    ("2026-10-18T13:12:00", "2026-10-18 22:12"),
    ("2026-10-18T20:30:00", "2026-10-19 05:30"),
    ("2026-10-18T13:12:00Z", "2026-10-18 22:12"),
    ("2026-10-18T22:12:00+09:00", "2026-10-18 22:12"),
    ("어제 오후", "어제 오후"),
])
def test_the_quote_header_date_is_korea_time(stored: str, shown: str) -> None:
    original = mail_quote.parse_original({"sender": "a <a@example.invalid>", "subject": "s", "date": stored, "body": None})
    assert f"보낸 날짜: {shown}\n" in mail_quote.render_quote(original)
