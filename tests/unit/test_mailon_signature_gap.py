"""The webmail signature sits one body line under the mail, not six.

Owner 2026-10-26 ("한6줄은되보여", "큰 글씨 폰트로 3줄들어가있어"): the institutional
webmail posts the signature as its own form field (signUid + signContent, signature
placed outside the editor) and its server joins body + three default-size empty
paragraphs + signature. The compose script below is the webmail's own logic as
captured from the live compose page 2026-10-26 (getForm reads every named field
under #mcp_wrap; signSelected(-1) on an outside-editor signature clears it). The
server join is modelled as observed in delivered copies.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_VENDOR = _HERE.parents[1] / "skills" / "mail" / "vendor"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

from mailon import send as send_module  # noqa: E402
from mailon import send_trigger  # noqa: E402
from mailon.send import ComposeSender, SendRequest  # noqa: E402

BODY = '<div style="font-family:굴림; font-size:10pt; line-height:150%">안녕하세요.<br>홍길동 올림</div>'
SIGNATURE = ' \n \n  <div><p style="margin: 0cm 0cm 0pt;">=========</p><p>홍길동 / 예시기관</p></div>'
EMPTY_PARAGRAPH = "<p><br/></p>"

_PAGE = r"""
const state = __STATE__;
class Field { constructor(name, value) { this.name = name; this.value = value; } getAttribute(k) { return k === 'name' ? this.name : null; } }
const place = new Field('signPlaceType', state.place);
const sel = new Field('signUid', state.uid);
const ipt = new Field('signContent', state.sign);
const named = [sel, ipt, place];
global.document = { querySelector: (s) => {
  if (s === '#signPlaceType') return place;
  if (s === '#mcp_wrap select[name="signUid"]') return sel;
  if (s === '#mcp_wrap [name="signContent"]') return ipt;
  return null; } };
let content = state.content;
global.window = {
  mail_editor: { getContent: () => content, getMode: () => state.mode },
  _compose: {
    signSelected: (t) => { if (place.value === '1' && t.value === '-1') { ipt.value = ''; } },
    getForm: () => { const p = {}; for (const f of named) p[f.name] = f.value;
      p.content = state.staleForm ? state.content : window.mail_editor.getContent(); return p; },
  },
};
const result = JSON.parse(__SCRIPT__);
const p = window._compose.getForm();
process.stdout.write(JSON.stringify({result, form: p}));
"""


class _NodePage:
    def __init__(self, **state: object) -> None:
        self.state = {"place": "1", "uid": "546", "sign": SIGNATURE, "content": BODY,
                      "mode": "html", "staleForm": False, **state}
        self.form: dict = {}

    def eval_js(self, script: str) -> str:
        program = (_PAGE.replace("__STATE__", json.dumps(self.state))
                   .replace("__SCRIPT__", script.strip()))
        out = json.loads(subprocess.run(
            ["node", "-e", program], capture_output=True, text=True, check=True, timeout=10,
        ).stdout)
        self.form = out["form"]
        return json.dumps(out["result"])


def _server_join(form: dict) -> str:
    """send_async as seen in delivered copies: a posted signature comes after three empty paragraphs."""
    sign = form.get("signContent", "")
    return form["content"] + (EMPTY_PARAGRAPH * 3 + sign if form.get("signUid") != "-1" and sign else "")


def test_signature_lands_one_body_line_under_the_mail() -> None:
    page = _NodePage()
    result = send_trigger.move_signature_into_body(page)
    delivered = _server_join(page.form)
    assert result["moved"] is True
    assert delivered == BODY + send_trigger.SIGNATURE_SPACER + SIGNATURE
    assert delivered.count("=========") == 1
    assert EMPTY_PARAGRAPH not in delivered
    assert page.form["signUid"] == "-1" and page.form["signContent"] == ""


@pytest.mark.parametrize("state", [
    {"uid": "-1", "sign": ""},          # owner picked 사용안함
    {"sign": "   "},                    # selected signature is empty
    {"mode": "text"},                   # plain-text compose: never paste HTML into it
    {"place": "0"},                     # signature placed inside the editor
])
def test_compose_without_an_outside_signature_is_left_alone(state: dict) -> None:
    page = _NodePage(**state)
    result = send_trigger.move_signature_into_body(page)
    assert result["moved"] is False
    assert page.form["content"] == BODY
    assert page.form["signUid"] == page.state["uid"]
    assert page.form["signContent"] == page.state["sign"]


def test_a_form_that_does_not_read_back_the_new_body_is_put_back() -> None:
    page = _NodePage(staleForm=True)
    result = send_trigger.move_signature_into_body(page)
    assert result == {"moved": False, "reason": "content-not-posted"}
    assert page.form["signUid"] == "546" and page.form["signContent"] == SIGNATURE
    assert _server_join(page.form).count("=========") == 1


def test_a_failed_eval_reports_and_does_not_raise() -> None:
    class _Broken:
        def eval_js(self, _script: str) -> str:
            raise RuntimeError("browser gone")
    assert send_trigger.move_signature_into_body(_Broken()) == {
        "moved": False, "reason": "eval-failed:RuntimeError"}


def test_send_moves_the_signature_after_the_form_check_and_before_the_trigger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []

    class _Browser:
        def eval_json(self, _script: str):
            return {"csrf": {"name": "t", "value": "v"}, "file_input": None}

        def eval_js(self, _script: str) -> str:
            return '""'

        def wait_ms(self, _ms: int) -> None:
            return None

        def clear_network_requests(self) -> None:
            return None

        def network_post_count(self) -> int:
            return 1

        def network_requests(self) -> str:
            return "[]"

    def _verify(*_args, **_kwargs):
        order.append("verify")
        return True, "{}"

    monkeypatch.setattr(send_module.folders, "finish_page_boot", lambda _b: "load")
    monkeypatch.setattr(send_module.send_verify, "resolve_current_folder_uid",
                        lambda *_a, **_k: ("f", "/mail"))
    monkeypatch.setattr(send_module.send_verify, "find_mail_match",
                        lambda *_a, **_k: order.count("trigger") > 0)
    monkeypatch.setattr(send_module.send_verify, "check_network_fast_fail", lambda *_a, **_k: None)
    monkeypatch.setattr(send_module.send_verify, "log_network_forensics", lambda *_a, **_k: None)
    monkeypatch.setattr(send_trigger, "open_compose_when_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(send_trigger, "commit_recipients", lambda *_a, **_k: None)
    monkeypatch.setattr(send_trigger, "sync_field_events", lambda *_a, **_k: None)
    monkeypatch.setattr(send_trigger, "discover_compose", lambda *_a, **_k: "")
    monkeypatch.setattr(send_trigger, "verify_compose_form", _verify)
    monkeypatch.setattr(send_trigger, "move_signature_into_body",
                        lambda _b: order.append("signature") or {"moved": True})
    monkeypatch.setattr(send_trigger, "call_compose_send",
                        lambda _b: order.append("trigger") or "_compose.send()")
    monkeypatch.setattr(send_module, "_fill_editor_when_ready", lambda *_a, **_k: None)
    request = SendRequest(recipients=("owner@example.invalid",), cc=(), subject="s",
                          body="b", attachments=())
    result = ComposeSender(_Browser(), clock=lambda: 0.0).send(request, dry_run=False)
    assert result.status == "submitted"
    assert order == ["verify", "signature", "verify", "trigger"]
