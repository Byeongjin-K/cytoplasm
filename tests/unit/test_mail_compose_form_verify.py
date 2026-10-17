"""Fail-closed exact-set recipient verification for the MailOn send gate.

`send_trigger.verify_compose_form` is the LAST gate before a browser-automated
`_compose.send()` fires.  A subset/substring check (2026-07-29 incident RCA on
ticket t_80732add) let extra recipients through and matched addresses inside
other addresses.  These tests pin the required security property: the live
compose form's To/Cc must be EXACTLY the intended set (order-insensitive
multiset), Bcc must be empty, and any missing/extra/mutated/hidden recipient
fails closed BEFORE send.

The browser doubles here expose only `_compose.getForm()` through the real
production `_FORM_PROBE_JS` (run with node).  Any attempt to reach a send
trigger raises, so these tests cannot send mail or touch a network transport.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "mail" / "vendor"))
pytest.importorskip("mailon")

send_trigger = pytest.importorskip("mailon.send_trigger")


BODY = "offline compose probe marker line"
_NODE = shutil.which("node")


LONG_RECIPIENTS = tuple(
    f"synthetic-recipient-{index:02d}-long-address@example.test" for index in range(11)
)


@dataclass
class NodeFormProbeBrowser:
    """Run the production probe JS against a synthetic in-memory form only."""

    form: dict[str, str]
    probe_calls: int = 0

    def eval_js(self, script: str) -> str:
        if "compose form probe" not in script:
            raise AssertionError("send transport is stubbed; only the form probe is allowed")
        self.probe_calls += 1
        program = (
            "const form = JSON.parse(process.argv[1]);"
            "global.window = {_compose: {getForm: () => form}};"
            f"const result = ({script});"
            "process.stdout.write(result);"
        )
        completed = subprocess.run(
            ["node", "-e", program, json.dumps(self.form)],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return completed.stdout


@dataclass
class DictProbeBrowser:
    """Return a form dict directly, bypassing node (probe JS not exercised)."""

    form: dict[str, str]
    probe_calls: int = 0

    def eval_js(self, script: str) -> str:
        if "compose form probe" not in script:
            raise AssertionError("send transport is stubbed; only the form probe is allowed")
        self.probe_calls += 1
        return json.dumps(json.dumps(self.form))


def _form(
    to_value: str,
    *,
    cc_value: str = "",
    from_value: str = "synthetic-sender@example.test",
    # The REAL value this webmail reports for a compose. The hidden `method`
    # input is empty on a fresh compose and stays empty (measured 2026-08-18:
    # 12s poll; production logs show "" on 08-12/13/14 while sends succeeded).
    # This fixture used to default to "send" — a value the site never produces —
    # so the whole suite passed green while every real send failed closed.
    method: str = "",
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    form: dict[str, str] = {
        "method": method,
        "to": to_value,
        "cc": cc_value,
        "from": from_value,
        "content": BODY,
    }
    if extra:
        form.update(extra)
    return form


def _verify(
    browser, recipients: tuple[str, ...], cc: tuple[str, ...] = ()
) -> tuple[bool, dict[str, object]]:
    ok, dump = send_trigger.verify_compose_form(browser, recipients, BODY, cc=cc)
    return ok, json.loads(dump)


requires_node = pytest.mark.skipif(_NODE is None, reason="node runtime not available")


# --------------------------------------------------------------------------- #
# Happy path: exact set, order-insensitive.
# --------------------------------------------------------------------------- #


def test_exact_to_set_passes() -> None:
    to_value = ", ".join(LONG_RECIPIENTS)
    ok, dump = _verify(DictProbeBrowser(_form(to_value)), LONG_RECIPIENTS)
    assert ok is True
    assert dump["to"] == to_value


def test_reordered_recipients_still_pass() -> None:
    """MailOn may reorder chips; a benign reorder must NOT downgrade to no-go."""
    reordered = ", ".join(reversed(LONG_RECIPIENTS))
    ok, _ = _verify(DictProbeBrowser(_form(reordered)), LONG_RECIPIENTS)
    assert ok is True


def test_case_and_whitespace_insensitive() -> None:
    messy = "  " + " , ".join(r.upper() for r in LONG_RECIPIENTS) + "  "
    ok, _ = _verify(DictProbeBrowser(_form(messy)), LONG_RECIPIENTS)
    assert ok is True


def test_display_name_forms_pass() -> None:
    named = ", ".join(f"User {i} <{r}>" for i, r in enumerate(LONG_RECIPIENTS))
    ok, _ = _verify(DictProbeBrowser(_form(named)), LONG_RECIPIENTS)
    assert ok is True


def test_quoted_display_name_with_comma_passes() -> None:
    """A comma inside a quoted display name must not split into two mailboxes."""
    recipient = "jane@example.test"
    named = f'"Doe, Jane" <{recipient}>'
    ok, _ = _verify(DictProbeBrowser(_form(named)), (recipient,))
    assert ok is True


# --------------------------------------------------------------------------- #
# Fail-closed: the security property.
# --------------------------------------------------------------------------- #


def test_extra_unexpected_recipient_fails() -> None:
    """An autocomplete-injected extra address must block the send."""
    with_extra = ", ".join((*LONG_RECIPIENTS, "attacker@evil.test"))
    ok, _ = _verify(DictProbeBrowser(_form(with_extra)), LONG_RECIPIENTS)
    assert ok is False


def test_missing_recipient_fails() -> None:
    dropped = ", ".join(LONG_RECIPIENTS[:-1])
    ok, _ = _verify(DictProbeBrowser(_form(dropped)), LONG_RECIPIENTS)
    assert ok is False


def test_substring_collision_fails() -> None:
    """`a@x.test` must NOT match inside `xa@x.test` (no token boundary before)."""
    intended = ("a@example.test",)
    actual = "xa@example.test"
    ok, _ = _verify(DictProbeBrowser(_form(actual)), intended)
    assert ok is False


def test_duplicate_count_change_fails() -> None:
    intended = ("dup@example.test", "dup@example.test")
    actual = "dup@example.test"
    ok, _ = _verify(DictProbeBrowser(_form(actual)), intended)
    assert ok is False


def test_intended_element_with_two_mailboxes_fails() -> None:
    """One intended list element carrying two addresses is a recipient injection."""
    intended = ("a@example.test,b@example.test",)
    actual = "a@example.test, b@example.test"
    ok, _ = _verify(DictProbeBrowser(_form(actual)), intended)
    assert ok is False


# --------------------------------------------------------------------------- #
# Cc / Bcc enforcement (the caller passes cc; it was previously unchecked).
# --------------------------------------------------------------------------- #


def test_exact_cc_set_passes() -> None:
    to = ("to@example.test",)
    cc = ("cc1@example.test", "cc2@example.test")
    form = _form(",".join(to), cc_value=", ".join(cc))
    ok, _ = _verify(DictProbeBrowser(form), to, cc=cc)
    assert ok is True


def test_missing_cc_fails() -> None:
    to = ("to@example.test",)
    cc = ("cc1@example.test", "cc2@example.test")
    form = _form(",".join(to), cc_value="cc1@example.test")
    ok, _ = _verify(DictProbeBrowser(form), to, cc=cc)
    assert ok is False


def test_extra_cc_fails() -> None:
    to = ("to@example.test",)
    form = _form(",".join(to), cc_value="unexpected-cc@example.test")
    ok, _ = _verify(DictProbeBrowser(form), to, cc=())
    assert ok is False


def test_recipient_moved_to_cc_fails() -> None:
    """Moving an intended To recipient into Cc changes routing and must fail."""
    intended = ("a@example.test", "b@example.test")
    form = _form("a@example.test", cc_value="b@example.test")
    ok, _ = _verify(DictProbeBrowser(form), intended, cc=())
    assert ok is False


def test_nonempty_bcc_fails() -> None:
    to = ("to@example.test",)
    form = _form(",".join(to), extra={"bcc": "silent@evil.test"})
    ok, _ = _verify(DictProbeBrowser(form), to, cc=())
    assert ok is False


def test_recipient_only_in_from_or_body_fails() -> None:
    """An intended To recipient absent from the To field must fail even if it
    appears elsewhere (from/body) in the dumped form."""
    intended = ("routed@example.test",)
    form = _form("someone-else@example.test", from_value="routed@example.test")
    ok, _ = _verify(DictProbeBrowser(form), intended, cc=())
    assert ok is False


# --------------------------------------------------------------------------- #
# tome (self-send) branch: exact single-address, no cc/bcc.
# --------------------------------------------------------------------------- #


def test_tome_true_self_send_passes() -> None:
    me = "me@example.test"
    form = _form("", from_value=me, method="tome")
    ok, _ = _verify(DictProbeBrowser(form), (me,), cc=())
    assert ok is True


def test_tome_from_mismatch_fails() -> None:
    form = _form("", from_value="someone@example.test", method="tome")
    ok, _ = _verify(DictProbeBrowser(form), ("me@example.test",), cc=())
    assert ok is False


def test_tome_multiple_intended_fails() -> None:
    me = "me@example.test"
    form = _form("", from_value=me, method="tome")
    ok, _ = _verify(DictProbeBrowser(form), (me, "other@example.test"), cc=())
    assert ok is False


def test_tome_with_cc_fails() -> None:
    me = "me@example.test"
    form = _form("", from_value=me, method="tome")
    ok, _ = _verify(DictProbeBrowser(form), (me,), cc=("cc@example.test",))
    assert ok is False


def test_unknown_method_fails() -> None:
    to = ("to@example.test",)
    form = _form(",".join(to), method="mystery")
    ok, _ = _verify(DictProbeBrowser(form), to, cc=())
    assert ok is False

def test_empty_method_is_a_normal_compose_and_passes() -> None:
    """The value this webmail actually reports — fail-closing on it blocks all mail.

    Regression for 2026-08-18: `method` was required to be literally "send", but a
    fresh compose leaves the hidden input EMPTY and never fills it, and the site's
    own send() only special-cases 'tome'. The check landed 2026-07-29 yet the mailon
    runtime stayed on the 07-30 release for 19 days, so it never ran in production;
    deploying the vendor tree on 08-18 shipped it and every send started failing,
    which tripped the two-strike rule into mail-mode no-go.
    """
    to = ("to@example.test",)
    ok, dump = _verify(DictProbeBrowser(_form(",".join(to), method="")), to)
    assert ok is True
    assert dump["method"] == ""


def test_literal_send_method_still_passes() -> None:
    """The other accepted value keeps working — widening must not swap one for another."""
    to = ("to@example.test",)
    ok, _ = _verify(DictProbeBrowser(_form(",".join(to), method="send")), to)
    assert ok is True


def test_empty_method_still_enforces_the_exact_recipient_set() -> None:
    """Accepting "" must not weaken the 2026-07-29 exact-set guarantee."""
    to = ("to@example.test",)
    form = _form("to@example.test, sneaky@example.test", method="")
    ok, _ = _verify(DictProbeBrowser(form), to)
    assert ok is False

# --------------------------------------------------------------------------- #
# body marker must still be present (existing 2026-07-19 guarantee).
# --------------------------------------------------------------------------- #


def test_missing_body_marker_fails() -> None:
    to = ("to@example.test",)
    form = _form(",".join(to), extra={"content": "unrelated content"})
    ok, _ = _verify(DictProbeBrowser(form), to, cc=())
    assert ok is False


# --------------------------------------------------------------------------- #
# Real production probe JS (node): explicit keys + truncation sentinel.
# --------------------------------------------------------------------------- #


@requires_node
def test_real_probe_preserves_all_11_long_recipients() -> None:
    to_value = ", ".join(LONG_RECIPIENTS)
    browser = NodeFormProbeBrowser(_form(to_value))
    ok, dump = _verify(browser, LONG_RECIPIENTS)
    assert ok is True
    assert dump["to"] == to_value
    assert LONG_RECIPIENTS[-1] in str(dump["to"])
    assert browser.probe_calls == 1


@requires_node
def test_real_probe_reads_explicit_bcc_key() -> None:
    """A Bcc past a huge pile of decoy keys must NOT be hidden by a key cap."""
    decoys = {f"decoy_{i}": "x" for i in range(80)}
    form = _form("to@example.test", extra={**decoys, "bcc": "silent@evil.test"})
    browser = NodeFormProbeBrowser(form)
    ok, dump = _verify(browser, ("to@example.test",), cc=())
    assert dump.get("bcc") == "[hidden]"
    assert ok is False


@requires_node
def test_real_probe_truncation_sentinel_on_overlong_field() -> None:
    """A To field longer than the probe bound must fail closed, not silently
    drop the tail (that silent drop was the original 2026-07-29 incident)."""
    huge = ",".join(f"filler-{i:04d}@example.test" for i in range(300))
    assert len(huge) > 4000
    browser = NodeFormProbeBrowser(_form(huge))
    ok, _ = _verify(browser, ("filler-0000@example.test",))
    assert ok is False


# --------------------------------------------------------------------------- #
# The probe itself: a long BODY must stay verifiable, a long ADDRESS must not.
# --------------------------------------------------------------------------- #
@requires_node
def test_real_probe_keeps_a_long_body_verifiable() -> None:
    """A body past the probe bound must stay a STRING that still carries the marker.

    Regression for 2026-08-18: content was given the address fields' truncation
    sentinel, an OBJECT, so `marker in str(value)` could never match and every
    body past the bound became permanently unsendable. The editor refill could
    not help — the body was never the problem — and send.py raised
    "compose form missing body/recipients". Latent 19 days: the 07-30 release
    carries neither this nor the method guard, so sends kept working until the
    vendor tree was deployed on 08-18.
    """
    to = ("to@example.test",)
    long_body = BODY + "\n" + ("가" * 2000)
    wrapped = (
        '<div style="font-family:굴림; font-size:10pt; line-height:150%">'
        + long_body.replace("\n", "<br>")
        + "</div>"
    )
    form = {
        "method": "", "to": to[0], "cc": "", "bcc": "",
        "from": "synthetic-sender@example.test", "content": wrapped,
    }
    ok, dump = send_trigger.verify_compose_form(
        NodeFormProbeBrowser(form), to, long_body, cc=()
    )
    payload = json.loads(dump)
    assert isinstance(payload["content"], str), (
        "content must be head-clipped, never replaced by a truncation sentinel: "
        f"got {payload['content']!r}"
    )
    assert ok is True


@requires_node
def test_real_probe_still_sentinels_an_overlong_recipient_field() -> None:
    """The 2026-07-29 guarantee stays: an address past its bound fails closed."""
    to = ("to@example.test",)
    form = {
        "method": "", "to": "padding-address@example.test, " * 200, "cc": "",
        "bcc": "", "from": "synthetic-sender@example.test", "content": BODY,
    }
    ok, dump = send_trigger.verify_compose_form(
        NodeFormProbeBrowser(form), to, BODY, cc=()
    )
    payload = json.loads(dump)
    assert isinstance(payload["to"], dict) and "__truncated" in payload["to"], (
        "an overlong recipient field must still emit the truncation sentinel"
    )
    assert ok is False
