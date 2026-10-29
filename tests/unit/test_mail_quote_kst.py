"""The quote header shows the original's date in Korea time.

2026-10-19: a mail received 10/18 22:12 KST was quoted as "2026-10-18 13:12" because MailOn stores
the received time naive. New file so the recorded quote cases keep their shape.

2026-10-29: the naive value is the mail host's local time (mailon ``parse_millis`` uses
``datetime.fromtimestamp``), not always UTC: a host in Asia/Seoul stores KST, and reading it as UTC
quoted a 11:12 mail as 20:12. A naive date is read in the host's zone and converted once.
"""
from __future__ import annotations

import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/mail/scripts"))

import mail_quote  # noqa: E402


@pytest.fixture
def host_zone(monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    def use(zone: str) -> None:
        monkeypatch.setenv("TZ", zone)
        time.tzset()

    yield use
    monkeypatch.undo()
    time.tzset()


@pytest.mark.parametrize(("zone", "stored", "shown"), [
    ("UTC", "2026-10-18T13:12:00", "2026-10-18 22:12"),
    ("UTC", "2026-10-18T20:30:00", "2026-10-19 05:30"),
    ("Asia/Seoul", "2026-10-13T11:12:23", "2026-10-13 11:12"),
    ("Asia/Seoul", "2026-10-18T23:30:00", "2026-10-18 23:30"),
    ("Asia/Seoul", "2026-10-18T13:12:00Z", "2026-10-18 22:12"),
    ("UTC", "2026-10-18T22:12:00+09:00", "2026-10-18 22:12"),
    ("Asia/Seoul", "어제 오후", "어제 오후"),
])
def test_the_quote_header_date_is_korea_time(host_zone, zone: str, stored: str, shown: str) -> None:
    host_zone(zone)
    original = mail_quote.parse_original({"sender": "a <a@example.invalid>", "subject": "s", "date": stored, "body": None})
    assert f"보낸 날짜: {shown}\n" in mail_quote.render_quote(original)
