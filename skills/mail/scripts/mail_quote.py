"""Quote the answered mail below a reply, the way a mail client's reply does.

The vendored mailon runtime has no reply command — every outbound mail is a
fresh compose (``mailon.main send --body``) — so a reply used to carry ONLY the
newly written text (owner request 2026-09-01). This module renders the original
(Outlook-style Korean header block + body) from what ``mail_wrapper get --body``
returns, and computes the reply-all Cc set. Pure: no I/O, stdlib only.

The quote goes into the SENT body (the frozen argv) only. A draft's ``body``
stays the owner-reviewed reply text and the approval message notes the quote in
one line — dumping it would blow the Discord 2,000-char post limit.

HTML body (owner request 2026-10-19): the same argv also carries ``--body-html`` —
the reply text in 맑은 고딕 10.5pt / line-height 160%, then the quote. The quote
keeps the original's stored HTML after ``sanitize_html`` drops scripts, event
handlers and everything that would load by itself; without stored HTML, or past
``MAX_QUOTE_HTML_BYTES``, it is the text quote with its line breaks kept.
"""
from __future__ import annotations

import html
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from zoneinfo import ZoneInfo
from typing import Final

SEPARATOR: Final = "-----원본 메시지-----"
_SEOUL: Final = ZoneInfo("Asia/Seoul")
MAX_QUOTE_CHARS: Final = 20_000
_BODY_HEADING: Final = "## Body"
_EMPTY_BODY_PLACEHOLDER: Final = "(빈 본문)"  # what mailon.writer stores for an empty body
_FRONTMATTER_KEYS: Final = frozenset({"from", "to", "cc", "date", "subject"})
_ADDR: Final = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_YAML_ESCAPE: Final = re.compile(r"\\(.)")

REPLY_STYLE: Final = (
    "font-family:'맑은 고딕','Malgun Gothic',sans-serif;"
    "font-size:10.5pt;line-height:160%"
)
# One argv string may not exceed 128 KiB on Linux (MAX_ARG_STRLEN); the reply and
# the header block share the --body-html argument with the quoted original.
MAX_QUOTE_HTML_BYTES: Final = 100_000


@dataclass(frozen=True, slots=True)
class Original:
    """Headers and text of the mail being answered."""

    sender: str
    to: str
    cc: str
    date: str
    subject: str
    body: str
    body_html: str = ""


@dataclass(frozen=True, slots=True)
class QuoteHtml:
    """The HTML quote and whether it carries the original's own formatting."""

    html: str
    keeps_format: bool


def parse_original(detail: Mapping[str, object]) -> Original:
    """Read the answered mail from a wrapper ``get --body`` payload.

    ``body`` is the mailon markdown document (YAML front matter + ``## Body``).
    Front-matter values win over the row metadata; a missing document, a plain
    text body, or an unparsable front matter degrade to the row's sender /
    subject / date and the raw text — never an exception.
    """
    raw = detail.get("body")
    text = raw if isinstance(raw, str) else ""
    fields, remainder = _split_frontmatter(text)
    return Original(
        sender=fields.get("from") or _text(detail.get("sender")),
        to=fields.get("to", ""),
        cc=fields.get("cc", ""),
        date=fields.get("date") or _text(detail.get("date")),
        subject=fields.get("subject") or _text(detail.get("subject")),
        body=_body_text(remainder),
        body_html=_text(detail.get("body_html")),
    )


def render_quote(original: Original) -> str:
    """The header block plus the (capped) original body, ready to append."""
    lines = [SEPARATOR, f"보낸 사람: {original.sender}"]
    if original.date:
        lines.append(f"보낸 날짜: {_display_date(original.date)}")
    if original.to:
        lines.append(f"받는 사람: {original.to}")
    if original.cc:
        lines.append(f"참조: {original.cc}")
    lines.append(f"제목: {original.subject}")
    header = "\n".join(lines) + "\n"
    body = _capped(original.body)
    return header if not body else f"{header}\n{body}"


def with_quote(body: str, quote: str) -> str:
    """The text that is actually sent: the reply text, a blank line, the quote."""
    return body if not quote else f"{body}\n\n{quote}"


def render_quote_html(original: Original) -> QuoteHtml:
    """The header block plus the original's sanitized HTML, or the text quote."""
    cleaned = sanitize_html(original.body_html) if original.body_html.strip() else ""
    if cleaned.strip() and len(cleaned.encode("utf-8")) <= MAX_QUOTE_HTML_BYTES:
        header = _text_html(render_quote(Original(
            sender=original.sender, to=original.to, cc=original.cc,
            date=original.date, subject=original.subject, body="",
        )).rstrip("\n"))
        return QuoteHtml(f'<div style="{REPLY_STYLE}">{header}</div><br><div>{cleaned}</div>', True)
    return QuoteHtml(text_quote_html(render_quote(original)), False)


def text_quote_html(quote: str) -> str:
    """A text quote as HTML that keeps its line breaks and spacing."""
    return f'<div>{_text_html(quote)}</div>'


def html_body(body: str, quote_html: str) -> str:
    """The HTML that is actually sent: the reply in the reply font, then the quote."""
    reply = f'<div style="{REPLY_STYLE}">{_text_html(body)}</div>'
    return reply if not quote_html else f"{reply}<br>{quote_html}"


def sanitize_html(fragment: str) -> str:
    """Keep the original's formatting; drop scripts, handlers and self-loading content.

    Allow-list: unknown elements are unwrapped (their text kept), dropped elements
    lose their whole content, attributes outside the list and every ``on*`` handler
    go, links keep only http(s)/mailto, images keep only inline ``data:image/``
    sources (a remote or ``cid:`` image becomes its alt text), and a style
    declaration that could fetch anything (``url(``, ``@import``, ``expression``)
    is removed.
    """
    parser = _Sanitizer()
    parser.feed(fragment)
    parser.close()
    return parser.result()


def _text_html(text: str) -> str:
    # No white-space:pre-wrap: the webmail stores and delivers the HTML re-indented, a
    # newline and spaces around every <br>, and pre-wrap showed them as extra blank lines
    # and indents (2026-10-26, a one-blank-line mail arrived with each break doubled).
    # <br> breaks the line; a run of spaces or a line-leading space keeps its width as
    # &nbsp;, which a re-indent cannot change.
    escaped = html.escape(text.expandtabs(4), quote=False)
    return re.sub(r"(?m)^ | (?= )", "&nbsp;", escaped).replace("\n", "<br>")


_DROP_WITH_CONTENT: Final = frozenset({
    "script", "style", "iframe", "frame", "frameset", "object", "embed", "applet",
    "form", "noscript", "template", "svg", "math", "audio", "video", "head", "title",
    "textarea", "select", "button",
})
_VOID: Final = frozenset({"br", "hr", "img", "col", "wbr"})
_ALLOWED: Final = frozenset({
    "a", "abbr", "b", "bdo", "big", "blockquote", "br", "caption", "center", "cite",
    "code", "col", "colgroup", "dd", "del", "dfn", "div", "dl", "dt", "em", "font",
    "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "ins", "kbd", "li", "mark",
    "ol", "p", "pre", "q", "s", "samp", "small", "span", "strike", "strong", "sub",
    "sup", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "tt", "u", "ul", "wbr",
})
_ATTRIBUTES: Final = frozenset({
    "align", "alt", "bgcolor", "border", "cellpadding", "cellspacing", "color", "cols",
    "colspan", "dir", "face", "height", "href", "lang", "rowspan", "size", "span",
    "src", "start", "style", "title", "type", "valign", "width",
})
_UNSAFE_STYLE: Final = re.compile(r"url\s*\(|@import|expression\s*\(|behavior\s*:|-moz-binding|javascript:", re.I)
_SAFE_HREF: Final = re.compile(r"(?:https?:|mailto:)", re.I)
_INLINE_IMAGE: Final = re.compile(r"data:image/(?:png|gif|jpe?g|webp|bmp);base64,[A-Za-z0-9+/=\s]+\Z", re.I)


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._open: list[str] = []
        self._dropping: list[str] = []

    def result(self) -> str:
        return "".join(self._out) + "".join(f"</{tag}>" for tag in reversed(self._open))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._dropping:
            if tag in _DROP_WITH_CONTENT and tag not in _VOID:
                self._dropping.append(tag)
            return
        if tag in _DROP_WITH_CONTENT:
            self._dropping.append(tag)
            return
        if tag not in _ALLOWED:
            return
        kept = _safe_attributes(tag, attrs)
        if kept is None:  # an image that would load from elsewhere
            alt = dict(attrs).get("alt") or ""
            if alt.strip():
                self._out.append(html.escape(f"[{alt.strip()}]", quote=False))
            return
        rendered = "".join(f' {name}="{html.escape(value, quote=True)}"' for name, value in kept)
        self._out.append(f"<{tag}{rendered}>")
        if tag not in _VOID:
            self._open.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID and self._open and self._open[-1] == tag and not self._dropping:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if self._dropping:
            if tag == self._dropping[-1]:
                self._dropping.pop()
            return
        if tag in _VOID or tag not in self._open:
            return
        while self._open:
            current = self._open.pop()
            self._out.append(f"</{current}>")
            if current == tag:
                return

    def handle_data(self, data: str) -> None:
        if not self._dropping:
            self._out.append(html.escape(data, quote=False))


# Owner Q275 (2026-10-19): keep the original's look, but nothing that hides or moves text. Only
# these display properties survive; display, position, visibility, overflow, z-index, opacity,
# offsets, transforms and everything else are dropped.
_STYLE_ALLOWED: Final = frozenset({
    "font", "font-family", "font-size", "font-weight", "font-style", "font-variant", "color",
    "background-color", "text-align", "text-decoration", "text-indent", "line-height",
    "letter-spacing", "word-spacing", "white-space", "vertical-align", "width", "height",
    "margin", "margin-top", "margin-right", "margin-bottom", "margin-left",
    "padding", "padding-top", "padding-right", "padding-bottom", "padding-left",
    "border", "border-top", "border-right", "border-bottom", "border-left", "border-color",
    "border-style", "border-width", "border-collapse", "border-spacing",
})


def _safe_style(value: str) -> str:
    kept: list[str] = []
    for part in value.split(";"):
        name, separator, raw = part.partition(":")
        prop, val = name.strip().lower(), raw.strip()
        if not separator or prop not in _STYLE_ALLOWED or not val or _UNSAFE_STYLE.search(part):
            continue
        if "transparent" in val.lower():
            continue
        if prop in ("font-size", "line-height", "font") and _too_small(val, prop):
            continue
        kept.append(f"{prop}:{val}")
    return ";".join(kept)


def _too_small(value: str, prop: str = "") -> bool:
    """A size that makes text unreadable (0, or under 6px/5pt/0.4em) hides it.

    A unitless line-height is a multiple of the font size (`1.5`), not pixels; reading it as px
    dropped every normal line spacing from the quote (2026-10-19 test reply).
    """
    match = re.match(r"^\s*(\d*\.?\d+)\s*(px|pt|em|rem|%)?", value, re.I)
    if match is None:
        return False
    number = float(match.group(1))
    if match.group(2) is None and prop == "line-height":
        return number < 0.5
    unit = (match.group(2) or "px").lower()
    floor = {"px": 6.0, "pt": 5.0, "em": 0.4, "rem": 0.4, "%": 40.0}[unit]
    return number < floor


def _safe_attributes(tag: str, attrs: list[tuple[str, str | None]]) -> list[tuple[str, str]] | None:
    """The attributes to keep, or ``None`` for an image without an inline source."""
    kept: list[tuple[str, str]] = []
    has_inline_image = False
    for raw_name, raw_value in attrs:
        name = raw_name.lower()
        value = (raw_value or "").strip()
        if name not in _ATTRIBUTES:
            continue
        if name == "href":
            if tag != "a" or not _SAFE_HREF.match(value):
                continue
        elif name == "src":
            if tag != "img" or not _INLINE_IMAGE.match(value):
                continue
            has_inline_image = True
        elif name == "style":
            value = _safe_style(value)
            if not value:
                continue
        kept.append((name, value))
    if tag == "img" and not has_inline_image:
        return None
    return kept


def reply_all_cc(original: Original, *, to: str, owner: str) -> str:
    """Original To ∪ Cc as a comma-joined Cc, minus the reply target and the owner.

    Addresses are lowercased and de-duplicated in first-seen order; display
    names are dropped because the send argv carries bare addresses.
    """
    excluded = {to.strip().lower(), owner.strip().lower()}
    kept: list[str] = []
    addresses: list[str] = _ADDR.findall(f"{original.to}, {original.cc}")
    for address in addresses:
        lowered = address.lower()
        if lowered not in excluded and lowered not in kept:
            kept.append(lowered)
    return ", ".join(kept)


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    closing = next((index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
    if closing is None:
        return {}, text
    fields: dict[str, str] = {}
    for line in lines[1:closing]:
        key, _separator, value = line.partition(":")
        name = key.strip().lower()
        if name in _FRONTMATTER_KEYS and name not in fields:
            fields[name] = _unquote(value.strip())
    return fields, "\n".join(lines[closing + 1 :])


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return _YAML_ESCAPE.sub(r"\1", value[1:-1])
    return value


def _body_text(remainder: str) -> str:
    lines = remainder.splitlines()
    heading = next((index for index, line in enumerate(lines) if line.strip() == _BODY_HEADING), None)
    body = remainder.strip() if heading is None else "\n".join(lines[heading + 1 :]).strip()
    return "" if body == _EMPTY_BODY_PLACEHOLDER else body


def _display_date(value: str) -> str:
    """Show the original's date in Korea time; MailOn stores it as naive UTC (2026-10-19)."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(_SEOUL).strftime("%Y-%m-%d %H:%M")


def _capped(body: str) -> str:
    if len(body) <= MAX_QUOTE_CHARS:
        return body
    return (
        body[:MAX_QUOTE_CHARS].rstrip()
        + f"\n…(원문 {len(body)}자 중 {MAX_QUOTE_CHARS}자까지 인용)"
    )
