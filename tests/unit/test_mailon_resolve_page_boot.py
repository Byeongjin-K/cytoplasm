"""Recipient lookup finishes a webmail page whose 'load' event never fires.

2026-10-21 live read on the node: `mail_wrapper.py resolve --name` failed every time
with exit 2 (`auth_or_browser_error`) although login succeeded and the 10-minute sync
on the same account worked. The page had the 2026-10-13 slow-window state (login page
open timed out, readyState stuck at 'interactive'), so `_tbar.compose()` kept throwing
a TypeError. The 2026-10-13 fix (`folders.finish_page_boot`) was wired into sync and
send only; resolve opened compose straight after login. Running the page's own load
handler first made the same lookup return the owner's own entry.

Kept apart from `test_mailon_compose_readiness.py`, whose output is pinned by the FS3
public baseline (tests/AGENTS.md: no new cases in pinned files).
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

from mailon import folders, resolve as resolve_mod  # noqa: E402
from mailon.browser import BrowserError  # noqa: E402

_CLOCK = {"now": 0.0}


class _StuckLoadPage:
    def __init__(self) -> None:
        self.booted = False
        self.calls: list[str] = []

    def eval_js(self, script: str) -> str:
        if script == folders._BOOT_STATE_JS:
            self.calls.append("boot_state")
            return json.dumps({"ready": "interactive", "init": self.booted, "runner": True})
        if script == folders._RUN_ONLOAD_JS:
            self.calls.append("run_onload")
            self.booted = True
            return '"onload-ran"'
        if "_tbar.compose" in script:
            self.calls.append("compose")
            if not self.booted:
                raise BrowserError("TypeError: tabPanel._getMenuById is not a function")
            return '"compose-opened"'
        self.calls.append("eval_js")
        return '""'

    def eval_json(self, _script: str):
        return ['Organization\n"Sample" <sample@example.invalid>\n:: Sample Team']

    def focus(self, _ref: str) -> None:
        self.calls.append("focus")

    def type_text(self, _ref: str, _value: str) -> None:
        self.calls.append("type_text")

    def wait_ms(self, milliseconds: int) -> None:
        _CLOCK["now"] += milliseconds / 1000.0

    def clear_network_requests(self) -> None:
        return None

    def network_post_count(self) -> int:
        return 0


@pytest.fixture(autouse=True)
def _fake_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    _CLOCK["now"] = 0.0
    monkeypatch.setattr(time, "monotonic", lambda: _CLOCK["now"])


def test_resolve_runs_the_page_load_handler_before_compose() -> None:
    # Given: a page stuck at readyState 'interactive' whose load handler never ran.
    page = _StuckLoadPage()

    # When: a recipient name is looked up.
    cells, _posts = resolve_mod.resolve_name(page, "Sample")

    # Then: the handler ran before compose, compose opened first try, and the grid was read.
    assert page.calls.index("run_onload") < page.calls.index("compose")
    assert page.calls.count("compose") == 1
    assert resolve_mod.parse_candidates(cells, "Sample")[0].group == "organization"
