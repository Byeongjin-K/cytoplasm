"""A mail saved without its HTML is quoted in readable lines, not one word per line.

2026-10-29: mails collected before 2026-10-19 keep no ``.html`` copy, and MailOn stored their
text with ``get_text("\\n", strip=True)`` - a line break between every inline run of the HTML,
so an Outlook mail arrives as '안녕하세요\\n,\\n박사님\\n!\\n…'. The text quote of such a mail now
joins the runs back; a mail with stored HTML, and any text without that shape, keep their bytes.
The fixture below is invented; only its shape follows a stored mail.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/mail/scripts"))

import mail_quote  # noqa: E402

SPLIT_BODY = "\n".join([
    "안녕하세요", ",", "홍길동 박사님", "!",
    "가상상사 이몽룡 입니다", ".",
    "지난번 말씀하신", "3", "건의", "회의 자료 정리해 보냅니다", ".",
    "검토는", "2", "일 정도 걸립니다", ".",
    "From:", "owner@example.invalid",
    "Subject:", "자료 문의",
    "- 참석: 5명",
])
JOINED_BODY = "\n".join([
    "안녕하세요, 홍길동 박사님!",
    "가상상사 이몽룡 입니다.",
    "지난번 말씀하신 3 건의",
    "회의 자료 정리해 보냅니다.",
    "검토는 2 일 정도 걸립니다.",
    "From: owner@example.invalid",
    "Subject: 자료 문의",
    "- 참석: 5명",
])


def _quote(body: str, body_html: str = "") -> str:
    original = mail_quote.Original(
        sender="peer@example.invalid", to="", cc="", date="", subject="s", body=body, body_html=body_html,
    )
    return mail_quote.render_quote(original)


def test_a_mail_saved_without_html_is_quoted_in_joined_lines() -> None:
    assert _quote(SPLIT_BODY).endswith(f"\n\n{JOINED_BODY}")


def test_the_html_fallback_quote_shows_the_joined_lines() -> None:
    original = mail_quote.Original(
        sender="peer@example.invalid", to="", cc="", date="", subject="s", body=SPLIT_BODY,
    )
    quote = mail_quote.render_quote_html(original)
    assert not quote.keeps_format
    assert "안녕하세요, 홍길동 박사님!<br>" in quote.html


def test_a_mail_with_stored_html_keeps_its_text_quote_bytes() -> None:
    assert _quote(SPLIT_BODY, body_html="<p>원문</p>").endswith(f"\n\n{SPLIT_BODY}")


def test_text_with_a_blank_line_is_not_a_split_mail() -> None:
    body = SPLIT_BODY.replace("\n.\n지난번", "\n.\n\n지난번")
    assert "\n\n" in body
    assert _quote(body).endswith(f"\n\n{body}")


def test_text_with_at_most_one_punctuation_line_is_not_a_split_mail() -> None:
    body = "회의는 3시\n.\n장소는\n2층"
    assert _quote(body).endswith(f"\n\n{body}")
