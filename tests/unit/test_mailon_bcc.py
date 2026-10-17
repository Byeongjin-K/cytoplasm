"""Offline MailOn BCC recipient verification and DOM input contracts."""
from __future__ import annotations

import json
import subprocess
import sys
import types
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _install_third_party_stubs() -> None:
    for name in ("pyotp", "dotenv", "bs4"):
        if name in sys.modules:
            continue
        try:
            __import__(name)
        except ImportError:
            stub = types.ModuleType(name)
            stub.TOTP = stub.load_dotenv = stub.BeautifulSoup = object  # type: ignore[attr-defined]
            sys.modules[name] = stub


_install_third_party_stubs()
sys.path.insert(0, str(ROOT / "skills/mail/vendor"))

from mailon import main, send, send_trigger  # noqa: E402

TO = ("owner@example.test",)
BCC = ("one@example.test", "two@example.test")
BODY = "offline marker"


@dataclass(frozen=True)
class FormBrowser:
    form: dict

    def eval_js(self, script):
        return json.dumps(self.form)


def test_exact_bcc_multiset_is_accepted():
    # Given
    browser = FormBrowser({"to": TO[0], "bcc": ",".join(reversed(BCC)), "content": BODY})
    # When
    ok, dump = send_trigger.verify_compose_form(browser, TO, BODY, bcc=BCC)
    # Then
    assert ok
    assert all(address not in dump for address in BCC)


@pytest.mark.parametrize("actual", [
    BCC[0], ",".join((*BCC, "extra@example.test")), {"__truncated": 9000}, "",
])
def test_missing_extra_and_truncated_bcc_are_refused(actual):
    # Given
    browser = FormBrowser({"to": TO[0], "bcc": actual, "content": BODY})
    # When
    ok, dump = send_trigger.verify_compose_form(browser, TO, BODY, bcc=BCC)
    # Then
    assert not ok
    assert all(address not in dump for address in BCC)


def test_unexpected_bcc_is_refused_without_bcc_request():
    # Given
    browser = FormBrowser({"to": TO[0], "bcc": BCC[0], "content": BODY})
    # When
    ok, _ = send_trigger.verify_compose_form(browser, TO, BODY)
    # Then
    assert not ok


def test_tome_mode_cannot_hide_bcc():
    # Given
    browser = FormBrowser({"from": TO[0], "method": "tome", "bcc": BCC[0], "content": BODY})
    # When
    ok, _ = send_trigger.verify_compose_form(browser, TO, BODY, bcc=BCC)
    # Then
    assert not ok


def test_fill_script_writes_bcc_only_when_requested():
    # Given / When
    with_bcc = send._field_fill_script(TO[0], "", "seminar", bcc=",".join(BCC))
    without_bcc = send._field_fill_script(TO[0], "", "seminar")
    # Then
    assert send_trigger.BCC_SELECTOR in with_bcc
    assert send_trigger.BCC_SELECTOR not in without_bcc
    program = (
        "const fields={};global.document={querySelector:s=>fields[s]??=( {value:''})};"
        + with_bcc + ";process.stdout.write(JSON.stringify(fields));"
    )
    fields = json.loads(subprocess.run(
        ["node", "-e", program], capture_output=True, text=True, check=True, timeout=10,
    ).stdout)
    assert fields[send_trigger.BCC_SELECTOR]["value"] == ",".join(BCC)


def test_commit_tokenizes_every_bcc_address():
    # Given
    class Browser:
        def eval_js(self, script):
            program = """
const entered=[];class Input {
  set value(v){this.current=v;} get value(){return this.current;}
  focus(){} blur(){} dispatchEvent(e){if(e.type==='keydown')entered.push(this.value);}
}
global.HTMLInputElement=Input;global.HTMLTextAreaElement=Input;
global.Event=class {constructor(type){this.type=type;}};
global.KeyboardEvent=global.Event;
const fields={};global.document={
  querySelector:s=>fields[s]??=new Input(),querySelectorAll:()=>[]};
""" + script + ";process.stdout.write(JSON.stringify(entered));"
            self.entered = json.loads(subprocess.run(
                ["node", "-e", program], capture_output=True, text=True, check=True, timeout=10,
            ).stdout)
            return "fed"
    browser = Browser()
    # When
    send_trigger.commit_recipients(browser, TO, (), bcc=BCC)
    # Then
    assert browser.entered == [*TO, *BCC]


@pytest.mark.parametrize("length,expected", [(60, True), (240, False)])
def test_real_probe_accepts_sixty_bcc_and_refuses_truncation(length, expected):
    # Given
    recipients = tuple(f"recipient-{i:03d}-long-address@example.test" for i in range(length))
    class Browser:
        def eval_js(self, script):
            form = {"to": TO[0], "bcc": ",".join(recipients), "content": BODY}
            program = ("global.window={_compose:{getForm:()=>("
                       + json.dumps(form) + ")}};process.stdout.write(" + script + ");")
            return subprocess.run(
                ["node", "-e", program], capture_output=True, text=True, check=True, timeout=10,
            ).stdout
    # When
    ok, _ = send_trigger.verify_compose_form(Browser(), TO, BODY, bcc=recipients)
    # Then
    assert ok is expected


def test_vendor_parser_accepts_repeated_bcc():
    # Given / When
    args = main.build_parser().parse_args([
        "send", "--to", TO[0], "--bcc", BCC[0], "--bcc", BCC[1],
        "--subject", "seminar", "--body", BODY, "--dry-run",
    ])
    # Then
    assert args.bcc == list(BCC)
