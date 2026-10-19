"""The vendored mailon keeps the original mail's HTML and can send an HTML body.

Owner request 2026-10-19 (Q46·Q47, see ``test_mail_reply_html_format.py`` for the draft
side). Two vendor facts made every reply plain: ``writer.build_markdown`` stored only
``body_text`` although the scraper had ``body_html``, and ``send`` filled the Namo editor
with ``innerText``. Pinned here:
- ``write_mail`` keeps ``body_html`` in a sibling ``.html`` file, and the read wrapper
  hands it out only on ``get --body --with-html``;
- ``send --body-html`` fills the editor with that HTML (the plain ``--body`` stays the
  text the send gate checks); without it the fill is the old ``innerText`` path;
- the send gate finds the reply's marker in the HTML form content even when the editor
  escaped ``&``/``<``/``>``, and still refuses a missing marker or a wrong recipient.

New file rather than more cases in the editor/compose files, which pin other incidents.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_VENDOR = _HERE.parents[1] / "skills" / "mail" / "vendor"
for path in (_VENDOR, _HERE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from mailon import main as mailon_main  # noqa: E402
from mailon import send_trigger  # noqa: E402
from mailon.send import ComposeSender, SendRequest  # noqa: E402
from mailon.writer import Mail, write_mail  # noqa: E402
from test_mail_wrapper import run_cli, stub_repo  # noqa: E402,F401

HTML = '<div style="font-family:\'맑은 고딕\'">회신 &amp; 확인</div><table><tr><td>원문</td></tr></table>'


class _RecordingBrowser:
    def __init__(self) -> None:
        self.scripts: list[str] = []

    def eval_js(self, script: str) -> str:
        self.scripts.append(script)
        return '""'

    def eval_json(self, _script: str):
        return {"csrf": {"name": "t", "value": "v"}, "file_input": None}

    def wait_ms(self, _milliseconds: int) -> None:
        return None

    def clear_network_requests(self) -> None:
        return None

    def network_post_count(self) -> int:
        return 0

    def network_requests(self) -> str:
        return "[]"

    def fill(self, _selector: str, _value: str) -> None:
        return None

    def upload(self, _selector: str, _paths: tuple[Path, ...]) -> None:
        return None

    def editor_fills(self) -> list[str]:
        return [script for script in self.scripts if "NamoSE_Ifr__mail_editor_nm" in script]


class _FormBrowser:
    def __init__(self, form: dict) -> None:
        self._form = form

    def eval_js(self, _script: str) -> str:
        return json.dumps(self._form)


def _request(**overrides: object) -> SendRequest:
    values: dict = {
        "recipients": ("owner@example.invalid",), "cc": (), "subject": "s",
        "body": "회신 & 확인", "attachments": (),
    }
    values.update(overrides)
    return SendRequest(**values)


def test_send_fills_the_editor_with_the_html_body() -> None:
    browser = _RecordingBrowser()
    result = ComposeSender(browser).send(_request(body_html=HTML), dry_run=True)
    assert result.status == "dry_run"
    fills = browser.editor_fills()
    assert fills and all("innerHTML = value" in script for script in fills)
    assert json.dumps(HTML) in fills[-1]
    assert not any("innerText" in script for script in fills)


def test_send_without_html_keeps_the_plain_text_fill() -> None:
    browser = _RecordingBrowser()
    ComposeSender(browser).send(_request(), dry_run=True)
    fills = browser.editor_fills()
    assert fills and all("innerText = value" in script for script in fills)


def test_gate_finds_the_reply_marker_in_escaped_html_content() -> None:
    form = {"method": "", "to": "owner@example.invalid", "cc": "", "bcc": "",
            "from": "me@example.invalid",
            "content": '<div style="font-family:굴림">' + HTML + "</div>"}
    ok, _dump = send_trigger.verify_compose_form(
        _FormBrowser(form), ("owner@example.invalid",), "회신 & 확인")
    assert ok


@pytest.mark.parametrize(
    ("content", "to"),
    [
        ("<div>다른 글</div>", "owner@example.invalid"),
        (HTML, "owner@example.invalid, extra@example.invalid"),
    ],
)
def test_gate_still_refuses_a_missing_marker_or_an_extra_recipient(content: str, to: str) -> None:
    form = {"method": "", "to": to, "cc": "", "bcc": "", "from": "me@example.invalid",
            "content": content}
    ok, _dump = send_trigger.verify_compose_form(
        _FormBrowser(form), ("owner@example.invalid",), "회신 & 확인")
    assert not ok


def test_editor_api_refill_uses_the_html_body_when_given() -> None:
    browser = _RecordingBrowser()
    send_trigger.fill_body_via_editor_api(browser, "회신 & 확인", html=HTML)
    assert json.dumps(HTML) in browser.scripts[-1]


def test_cli_passes_body_html_into_the_send_request(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[SendRequest] = []

    class _Result:
        status = "dry_run"
        network_post_count = 0

        def to_json(self) -> str:
            return "{}"

    class _Sender:
        def __init__(self, _browser: object) -> None:
            return None

        def send(self, request: SendRequest, *, dry_run: bool):
            captured.append(request)
            return _Result()

    class _Browser:
        def clear_network_requests(self) -> None:
            return None

        def close(self) -> None:
            return None

    monkeypatch.setattr(mailon_main, "_make_send_browser", lambda _cfg: _Browser())
    monkeypatch.setattr(mailon_main, "login", lambda _browser, _cfg: None)
    monkeypatch.setattr(mailon_main, "ComposeSender", _Sender)
    monkeypatch.setattr(mailon_main, "record_send_result", lambda _logs, _result: None)
    args = mailon_main.build_parser().parse_args([
        "send", "--to", "owner@example.invalid", "--subject", "s", "--body", "b",
        "--body-html", HTML, "--dry-run",
    ])
    cfg = type("C", (), {"logs_dir": Path("/nonexistent")})()
    assert mailon_main._perform_send(args, cfg) == 0
    assert captured[0].body_html == HTML
    plain = mailon_main.build_parser().parse_args([
        "send", "--to", "owner@example.invalid", "--subject", "s", "--body", "b"])
    assert plain.body_html == ""


def test_writer_keeps_the_original_html_beside_the_markdown(tmp_path: Path) -> None:
    mails = tmp_path / "data" / "mails"
    with_html = Mail(uid="u-1", folder="inbox", subject="제목", sender="s",
                     date=datetime(2026, 10, 19, 9, 0), body_text="본문", body_html="<p>본문</p>")
    md_path = write_mail(with_html, mails, tmp_path)
    assert md_path.with_suffix(".html").read_text(encoding="utf-8") == "<p>본문</p>"
    plain = Mail(uid="u-2", folder="inbox", subject="제목2", sender="s",
                 date=datetime(2026, 10, 19, 9, 0), body_text="본문")
    assert not write_mail(plain, mails, tmp_path).with_suffix(".html").exists()


def test_wrapper_hands_out_the_stored_html_only_when_asked(stub_repo: Path) -> None:  # noqa: F811
    md = stub_repo / "data" / "mails" / "2026" / "07" / "f1.md"
    md.parent.mkdir(parents=True)
    md.write_text("본문", encoding="utf-8")
    md.with_suffix(".html").write_text("<p>본문</p>", encoding="utf-8")
    rc, out = run_cli("get", "u-101", "--body", "--with-html")
    assert rc == 0 and out["mail"]["body_html"] == "<p>본문</p>"
    rc, out = run_cli("get", "u-101", "--body")
    assert rc == 0 and "body_html" not in out["mail"]
    rc, out = run_cli("get", "u-101", "--body", "--with-html", "--masked")
    assert rc == 0 and "body_html" not in out["mail"]
