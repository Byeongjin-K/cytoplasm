"""An approved send survives a mail page whose window 'load' never fires.

2026-10-13: an owner-approved forward failed every watch tick for over an hour with
``compose did not become callable within 30s: TypeError: tabPanel._getMenuById is
not a function``. Live read-only probes the same day found the cause: the page builds
itself from callbacks queued with ``cr.onLoad``, which its own window 'load' handler
(``cr.ev.processOnLoad``) drains, and in slow windows 'load' never fires (readyState
stays 'interactive', also after a reload). Running that handler built the page
within 3 s. These tests fake that page; the failure record must also name the step,
the agent-browser command and the error kind.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

_VENDOR = Path(__file__).resolve().parents[2] / "skills" / "mail" / "vendor"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

from mailon.browser import BrowserError  # noqa: E402
from mailon.send import ComposeSender, SendRequest  # noqa: E402

_CLOCK = {"now": 0.0}
_NOT_BUILT = (
    "agent-browser failed (1): cmd=eval --stdin stderr=Evaluation error: "
    "TypeError: tabPanel._getMenuById is not a function"
)


def _eval_error(message: str) -> BrowserError:
    error = BrowserError(message)
    error.command = "eval"
    return error


class _StuckPage:
    """A mail page whose 'load' never fires until the page itself is helped.

    ``runner`` says whether the page exposes its load handler; ``built_by_reload``
    makes a reload alone enough (the healthy-reload case of 2026-10-12)."""

    def __init__(self, *, runner: bool = True, built_by_reload: bool = False) -> None:
        self.runner = runner
        self.built_by_reload = built_by_reload
        self.built = False
        self.calls: list[str] = []

    def eval_js(self, script: str) -> str:
        if "processOnLoad()" in script:
            self.calls.append("onload")
            if not self.runner:
                raise _eval_error("Evaluation error: cr is not defined")
            self.built = True
            return '"onload-ran"'
        if "_page.config.init" in script:
            return json.dumps(json.dumps(
                {"ready": "interactive", "init": self.built, "runner": self.runner}))
        if "location.reload" in script:
            self.calls.append("reload")
            self.built = self.built_by_reload
            return '"reload-scheduled"'
        if "_tbar.compose" in script:
            self.calls.append("compose")
            if not self.built:
                raise _eval_error(_NOT_BUILT)
            return '"compose-opened"'
        return '""'

    def eval_json(self, _script: str):
        return {"csrf": {"name": "t", "value": "v"}, "file_input": None}

    def fill(self, _selector: str, _value: str) -> None:
        return None

    def upload(self, _selector: str, _paths: tuple[Path, ...]) -> None:
        return None

    def wait_ms(self, milliseconds: int) -> None:
        _CLOCK["now"] += milliseconds / 1000.0

    def clear_network_requests(self) -> None:
        return None

    def network_post_count(self) -> int:
        return 0

    def network_requests(self) -> str:
        return "[]"


@pytest.fixture(autouse=True)
def _fake_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    _CLOCK["now"] = 0.0
    monkeypatch.setattr(time, "monotonic", lambda: _CLOCK["now"])


def _request() -> SendRequest:
    return SendRequest(recipients=("owner@example.invalid",), cc=(), subject="FW: s",
                       body="b", attachments=())


def test_send_runs_the_page_load_handler_when_load_never_fired() -> None:
    # Given: the page measured on 2026-10-13 — built only once its load handler runs
    page = _StuckPage()

    # When: an approved mail is composed
    result = ComposeSender(page, clock=lambda: _CLOCK["now"]).send(_request(), dry_run=True)

    # Then: the handler ran once, compose opened, and nothing needed a reload
    assert result.status == "dry_run"
    assert getattr(result, "page_boot", None) == "onload-run"
    assert page.calls.count("onload") == 1
    assert "reload" not in page.calls


def test_compose_reloads_when_the_page_cannot_be_helped_in_place() -> None:
    # Given: a page without the load handler that a reload does build
    page = _StuckPage(runner=False, built_by_reload=True)

    # When: an approved mail is composed
    result = ComposeSender(page, clock=lambda: _CLOCK["now"]).send(_request(), dry_run=True)

    # Then: compose failed first, the page was reloaded, then compose opened
    assert result.status == "dry_run"
    assert page.calls.index("compose") < page.calls.index("reload")
    assert page.calls[-1] == "compose"


def test_a_compose_that_never_opens_names_its_step_command_and_kind() -> None:
    from mailon import folders
    from mailon.send import SendStepError, failure_details

    # Given: a page that never builds, not even after a reload
    page = _StuckPage(runner=False)

    # When: an approved mail is composed
    with pytest.raises(SendStepError) as caught:
        ComposeSender(page, clock=lambda: _CLOCK["now"]).send(_request(), dry_run=True)

    # Then: the failure record says which command of which step failed and how
    error = caught.value
    assert page.calls.count("reload") == folders.RELOAD_ATTEMPTS
    assert (error.error_code, error.cause) == ("external_service_error", "compose_not_ready")
    assert failure_details(error) == {
        "step": "compose_open", "browser_command": "eval", "error_kind": "js_type_error"}


def test_a_failed_send_leaves_a_line_in_the_attempt_log(tmp_path: Path) -> None:
    from mailon.send import SendStepError, failure_details, record_send_failure

    # Given: the safe error payload of a send that stopped at compose
    payload = {"status": "error", "error_code": "external_service_error",
               "cause": "compose_not_ready", "message": "safe text",
               **failure_details(SendStepError("compose_open", _eval_error(_NOT_BUILT)))}

    # When: it is recorded
    record_send_failure(tmp_path, payload)

    # Then: the attempt log names the step and command, without the free text
    line = json.loads((tmp_path / "send-attempts.jsonl").read_text().splitlines()[-1])
    assert (line["status"], line["step"], line["cause"], line["browser_command"]) == (
        "error", "compose_open", "compose_not_ready", "eval")
    assert "message" not in line and line["at"].endswith("Z")
