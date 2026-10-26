"""Institutional-mail replies keep the original's formatting and read in Malgun Gothic.

Owner request 2026-10-19 (Q46·Q47): "기존 메일이 아래 달리는데 그 메일의 글씨체랑 띄어쓰기,
줄 간격 등이 좀 가시성이 안좋네. 원본 유지하면서 보낼 순 없나? 그리고 회신하는 글의 글씨체도
가시성 좋은것으로 선택하고 싶어." Until now the reply went out as plain text (the editor's
default Gulim 10pt / 150%) and the quote was the original's ``get_text`` dump, because the
vendored writer kept only ``body_text``.

The contract pinned here:
- every institutional draft also freezes ``--body-html``: the reply text, escaped, in
  ``맑은 고딕`` 10.5pt with line-height 160%, followed by the quoted original;
- the quote carries the original's stored HTML with scripts, event handlers and anything
  that loads on its own (remote images, CSS ``url()``, frames) removed, under a byte cap;
  without stored HTML, or over the cap, it falls back to the text quote with its line
  breaks and spacing kept;
- the approval card still shows only the reply text, plus one line about the quote;
- the frozen argv (and so the approval hash) carries the HTML, and a preflight rewrite of
  the reply text rebuilds it, while legacy drafts keep their hashes byte for byte.
"""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))
sys.path.insert(0, str(_REPO / "skills" / "mail" / "vendor"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import mail_preflight  # noqa: E402
import mail_quote  # noqa: E402
import triage_core  # noqa: E402
from test_mail_reply_quote import (  # noqa: E402
    ORIGINAL_BODY,
    REPLY_TEXT,
    _detail,
    _only_draft,
    _run_cli,
    _sent_body,
    _setup_env,
)

REPLY_DIV = (
    "<div style=\"font-family:'맑은 고딕','Malgun Gothic',sans-serif;"
    "font-size:10.5pt;line-height:160%\">"
)


@pytest.fixture(autouse=True)
def _released_html_reply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Existing HTML regressions exercise the explicitly released on mode."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    marker = Path.home() / ".hermes/mail-html-reply.mode"
    marker.parent.mkdir(parents=True)
    marker.write_bytes(b"on")


ORIGINAL_HTML = (
    '<table border="1"><tr><td style="color:#c00000;font-family:돋움"><b>견적 표</b></td>'
    "</tr></table><p>다음 주 회의 참석 가능 여부 회신 부탁드립니다.</p><p>감사합니다.</p>"
)


def _sent_html(record: dict) -> str:
    argv = list(record["argv"])
    return argv[argv.index("--body-html") + 1]


def _reply_draft(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **detail: object) -> dict:
    _setup_env(tmp_path, monkeypatch, _detail(tmp_path, **detail))
    assert _run_cli(monkeypatch, "draft", "--uid", "u-1", "--instruction", "참석한다고 답해줘", "--no-post") == 0
    return _only_draft()[1]


def test_reply_sends_the_reply_in_malgun_gothic_above_the_original_html(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a synced mail whose original HTML was stored; When: a reply draft is made
    record = _reply_draft(tmp_path, monkeypatch, body_html=ORIGINAL_HTML)
    html = _sent_html(record)
    # Then: the reply opens the HTML body in the owner's font, and the original's own
    # markup (table, colour, bold) follows it unchanged
    assert html.startswith(REPLY_DIV + REPLY_TEXT + "</div>")
    original_at = html.index('<td style="color:#c00000;font-family:돋움"><b>견적 표</b></td>')
    assert html.index(REPLY_TEXT) < html.index(mail_quote.SEPARATOR) < original_at
    assert record["quote_format"] == "original"
    # And: the plain body the send gate checks is the same text it always was
    assert _sent_body(record) == mail_quote.with_quote(REPLY_TEXT, record["quote"])


def test_original_without_stored_html_falls_back_to_the_text_quote_with_its_line_breaks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _reply_draft(tmp_path, monkeypatch)
    html = _sent_html(record)
    assert record["quote_format"] == "text"
    assert html.startswith(REPLY_DIV + REPLY_TEXT + "</div>")
    assert ORIGINAL_BODY.replace("\n", "<br>") in html


def test_original_html_over_the_byte_cap_falls_back_to_the_text_quote() -> None:
    huge = "<p>" + ("가" * (mail_quote.MAX_QUOTE_HTML_BYTES // 3 + 10)) + "</p>"
    original = mail_quote.Original(
        sender="s", to="", cc="", date="", subject="제목", body="글자 본문", body_html=huge,
    )
    quote = mail_quote.render_quote_html(original)
    assert quote.keeps_format is False
    assert "<p>" not in quote.html and "글자 본문" in quote.html
    assert len(quote.html.encode("utf-8")) < mail_quote.MAX_QUOTE_HTML_BYTES


@pytest.mark.parametrize(
    "dangerous",
    [
        "<script>alert(1)</script>",
        '<img src="http://tracker.invalid/p.gif">',
        '<img srcset="https://tracker.invalid/a.png 2x">',
        '<p onclick="steal()">x</p>',
        '<a href="javascript:steal()">x</a>',
        '<div style="background:url(https://tracker.invalid/b.png)">x</div>',
        '<table background="https://tracker.invalid/t.png"><tr><td>x</td></tr></table>',
        "<style>body{font-family:serif}</style>",
        '<iframe src="https://evil.invalid"></iframe>',
        '<link rel="stylesheet" href="https://evil.invalid/s.css">',
        '<form action="https://evil.invalid"><input name="q"></form>',
        '<object data="https://evil.invalid/x.swf"></object>',
        '<img src="cid:image001.png@01D">',
    ],
)
def test_sanitizer_drops_scripts_handlers_and_everything_that_loads_on_its_own(dangerous: str) -> None:
    cleaned = mail_quote.sanitize_html("<p>앞</p>" + dangerous + "<p>뒤</p>").lower()
    for marker in ("<script", "alert(", "tracker.invalid", "onclick", "javascript:", "url(",
                   "<style", "<iframe", "evil.invalid", "<form", "<input", "<object", "cid:"):
        assert marker not in cleaned, (marker, cleaned)
    assert "<p>앞</p>" in cleaned and "<p>뒤</p>" in cleaned


def test_sanitizer_keeps_formatting_links_and_inline_images() -> None:
    inline = "data:image/png;base64,iVBORw0KGgo="
    source = (
        '<p style="margin:0;color:#333">본문 &amp; 표</p>'
        f'<a href="https://example.invalid/doc">문서</a><img alt="로고" src="{inline}">'
        '<font face="굴림" size="2">작게</font><br>'
    )
    cleaned = mail_quote.sanitize_html(source)
    assert '<p style="margin:0;color:#333">본문 &amp; 표</p>' in cleaned
    assert '<a href="https://example.invalid/doc">문서</a>' in cleaned
    assert f'<img alt="로고" src="{inline}">' in cleaned
    assert '<font face="굴림" size="2">작게</font><br>' in cleaned


def _seen_lines(fragment: str) -> list[str]:
    """The lines a recipient sees for one ``<div>`` of text the editor received.

    The webmail stores and delivers the HTML re-indented: a newline and spaces around
    every ``<br>`` (the owner's copy of the 2026-10-26 13:45 KST mail). Under
    ``white-space:pre-wrap`` those characters are shown as more line breaks and indents;
    under the normal rule they collapse and only ``<br>`` breaks a line.
    """
    match = re.fullmatch(r'<div(?: style="([^"]*)")?>(.*)</div>', fragment, re.S)
    assert match, fragment
    delivered = match.group(2).replace("<br>", "\n    <br/>\n")
    if "pre-wrap" in (match.group(1) or ""):
        lines = delivered.replace("<br/>", "\n").split("\n")
    else:
        lines = [line.strip(" ") for line in re.sub(r"[ \t\n]+", " ", delivered).split("<br/>")]
    return [html.unescape(line).replace("\xa0", " ") for line in lines]


def test_one_blank_line_between_paragraphs_reaches_the_recipient_as_one_blank_line() -> None:
    # Given: plain text with one blank line between paragraphs (the 13:45 mail's shape)
    body = "안녕하세요.\n\n둘째 문단입니다.\n이어지는 줄\n\n감사합니다."
    # When: it becomes the editor HTML and the webmail re-indents it on delivery
    seen = _seen_lines(mail_quote.html_body(body, ""))
    # Then: the recipient sees exactly the lines that were written, no extra blank lines
    assert seen == ["안녕하세요.", "", "둘째 문단입니다.", "이어지는 줄", "", "감사합니다."]


def test_spaces_the_writer_typed_survive_the_re_indent() -> None:
    seen = _seen_lines(mail_quote.html_body("가  나\n   들여쓴 줄\n\t탭", ""))
    assert seen == ["가  나", "   들여쓴 줄", "    탭"]


def test_text_quote_keeps_its_lines_without_doubling_them() -> None:
    seen = _seen_lines(mail_quote.text_quote_html("-----원본 메시지-----\n제목: 안내\n\n본문 첫 줄"))
    assert seen == ["-----원본 메시지-----", "제목: 안내", "", "본문 첫 줄"]


def test_reply_text_is_escaped_and_keeps_its_line_breaks() -> None:
    html = mail_quote.html_body("A & B <확인>\n둘째 줄", "")
    assert html == REPLY_DIV + "A &amp; B &lt;확인&gt;<br>둘째 줄</div>"


def test_compose_without_a_quote_still_sends_the_reply_font(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_env(tmp_path, monkeypatch, _detail(tmp_path))
    assert _run_cli(
        monkeypatch, "compose", "--to", "peer@example.invalid", "--subject", "안내",
        "--body", "새 메일 본문", "--no-post",
    ) == 0
    _path, record = _only_draft()
    assert _sent_html(record) == REPLY_DIV + "새 메일 본문</div>"
    assert "quote_html" not in record and "quote_format" not in record


def test_card_shows_the_reply_only_and_one_line_about_the_original_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _reply_draft(tmp_path, monkeypatch, body_html=ORIGINAL_HTML)
    card = triage_core.render_approvals_message(record)
    assert "- 원문 인용: 원본 서식 유지" in card
    assert REPLY_TEXT in card
    assert "견적 표" not in card and "<div" not in card and "<td" not in card


def test_card_says_when_the_quote_could_not_keep_the_original_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _reply_draft(tmp_path, monkeypatch)
    card = triage_core.render_approvals_message(record)
    assert "- 원문 인용: 포함 (원본 서식 없이 글자만)" in card
    assert "원본 서식 유지" not in card


def test_approval_hash_binds_the_html_quote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _reply_draft(tmp_path, monkeypatch, body_html=ORIGINAL_HTML)
    assert record["sha256"] == triage_core.draft_sha256(record)
    tampered = dict(record, quote_html=record["quote_html"].replace("견적 표", "다른 표"))
    assert triage_core.draft_sha256(tampered) != record["sha256"]
    downgraded = dict(record, quote_format="text")
    assert triage_core.draft_sha256(downgraded) != record["sha256"]


def test_legacy_draft_hash_is_byte_identical() -> None:
    legacy = {
        "argv": ["python3", "-m", "mailon.main", "send", "--to", "peer@example.invalid",
                 "--subject", "Re: 안내", "--body", "회신\n\n-----원본 메시지-----\n제목: 안내",
                 "--confirm-send", "--json"],
        "body": "회신", "subject": "Re: 안내", "to": "peer@example.invalid", "uid": "u-1",
        "cc": "", "quote": "-----원본 메시지-----\n제목: 안내",
    }
    # Computed with the pre-change triage_core.draft_sha256 (upstream 4675feb).
    assert triage_core.draft_sha256(legacy) == LEGACY_SHA256


def test_preflight_rewrite_rebuilds_the_html_body_around_the_new_reply_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _reply_draft(tmp_path, monkeypatch, body_html=ORIGINAL_HTML)
    payload = {"to": record["to"], "cc": record["cc"], "subject": record["subject"], "body": "고친 회신"}
    updated = mail_preflight._draft_with_payload(record, payload)
    assert _sent_html(updated) == mail_quote.html_body("고친 회신", record["quote_html"])
    assert _sent_body(updated) == mail_quote.with_quote("고친 회신", record["quote"])
    assert updated["sha256"] == triage_core.draft_sha256(updated) != record["sha256"]


def test_legacy_draft_without_html_keeps_its_argv_through_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _reply_draft(tmp_path, monkeypatch)
    legacy = dict(record)
    argv = list(legacy["argv"])
    index = argv.index("--body-html")
    del argv[index:index + 2]
    legacy["argv"] = argv
    for key in ("quote_html", "quote_format"):
        legacy.pop(key)
    payload = {"to": legacy["to"], "cc": legacy["cc"], "subject": legacy["subject"], "body": legacy["body"]}
    updated = mail_preflight._draft_with_payload(legacy, payload)
    assert updated["argv"] == argv
    assert "--body-html" not in updated["argv"]


def test_drive_link_fills_the_placeholder_in_both_bodies() -> None:
    import mail_drive_share

    body = f"자료는 {mail_drive_share.PLACEHOLDER} 에 있습니다."
    draft = {"argv": ["python3", "-m", "mailon.main", "send", "--to", "peer@example.invalid",
                      "--subject", "s", "--body", body,
                      "--body-html", mail_quote.html_body(body, ""), "--confirm-send", "--json"]}
    sent = mail_drive_share.with_link(draft, "https://drive.invalid/f?id=1&usp=x")["send_argv"]
    assert sent[sent.index("--body") + 1] == "자료는 https://drive.invalid/f?id=1&usp=x 에 있습니다."
    assert sent[sent.index("--body-html") + 1] == (
        REPLY_DIV + '자료는 <a href="https://drive.invalid/f?id=1&amp;usp=x">'
        "https://drive.invalid/f?id=1&amp;usp=x</a> 에 있습니다.</div>"
    )


LEGACY_SHA256 = "a2ef0d926584d829fcbfea7ee8384403b120c6639ce5826282c439a47660efdd"
