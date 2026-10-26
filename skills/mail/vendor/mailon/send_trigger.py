"""Actual send trigger for the MailOn compose layer.

Root cause locked 2026-07-19: `window._compose.beforeSend()` is a PRE-send
hook — calling it alone has NEVER delivered a mail (the W0-7c self-test of
2026-07-15 never arrived; 0 hits in 757 synced mails). The faithful trigger
is what a human does: click the compose form's 보내기 button, which runs the
full handler chain (validation → beforeSend → actual submit).

This module also dispatches native input/change/blur events on the address
and subject fields first (jQuery-era UIs keep internal models that raw
`.value` writes do not update), and dumps a discovery snapshot of
`window._compose` so every attempt — pass or fail — documents ground truth.
"""
from __future__ import annotations

import json
import logging
import time
from collections import Counter
from email.headerregistry import AddressHeader
from email.errors import HeaderParseError, MessageError
from typing import Callable, Protocol

from .browser import BrowserError

log = logging.getLogger(__name__)


class TriggerBrowser(Protocol):
    def eval_js(self, script: str) -> str: ...


class ComposeOpenBrowser(Protocol):
    def eval_js(self, script: str) -> str: ...
    def wait_ms(self, milliseconds: int) -> None: ...


# The mailbox is a SPA: reaching /mail does not mean it can service a compose
# call yet, and it passes through several *distinct* broken states on the way.
# Measured 2026-08-18 by retrying compose right after login:
#     t+0.1s  TypeError: Cannot read properties of undefined (reading 'compose')
#     t+0.7s  TypeError: tabPanel._getMenuById is not a function
#     t+1.3s  TypeError: tabPanel._getMenuById is not a function
#     t+1.9s  compose OK  (to-field present)
# A readiness *predicate* cannot cover that. An earlier guard checked whether
# `tabPanel` was defined; it passed while the object still could not serve
# `_getMenuById`, so compose was let through and threw anyway. Drive the real
# call instead and let its own success be the readiness signal. Opening compose
# is read-only — nothing is submitted — so retrying it is safe.
#
# Without this the throw becomes a BrowserError, which mailon folds into exit 2
# (auth_or_browser_error): a startup race reported as an auth failure.
_COMPOSE_JS = "window._tbar.compose(); 'compose-opened';"


def open_compose_when_ready(
    browser: ComposeOpenBrowser,
    timeout_s: float = 30.0,
    clock: Callable[[], float] | None = None,
) -> None:
    """Open compose, retrying while the mailbox SPA is still building."""
    active_clock = clock or time.monotonic
    deadline = active_clock() + timeout_s
    last = ""
    command = ""
    while True:
        try:
            browser.eval_js(_COMPOSE_JS)
            return
        except BrowserError as error:
            last = str(error)
            command = getattr(error, "command", "")
        if active_clock() >= deadline:
            raise BrowserError(
                f"compose did not become callable within {timeout_s:.0f}s: {last}",
                command=command,
            )
        browser.wait_ms(500)


def _unwrap(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith('"') and raw.endswith('"'):
        raw = json.loads(raw)
    return raw


_FIELD_EVENTS_JS = r"""
(() => {
  const fire = (el) => ['input','change','blur'].forEach(
    t => el.dispatchEvent(new Event(t, {bubbles: true})));
  const done = [];
  for (const sel of ['#adr-to-ipt_ta', '#adr-cc-ipt_ta', '#compose_subject']) {
    const el = document.querySelector(sel);
    if (!el) continue;
    const proto = el.tagName === 'TEXTAREA'
      ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const desc = Object.getOwnPropertyDescriptor(proto, 'value');
    if (desc && desc.set) desc.set.call(el, el.value);
    fire(el);
    done.push(sel);
  }
  return 'events:' + done.length;
})()
"""

_DISCOVERY_JS = r"""
JSON.stringify((() => {
  const c = window._compose || {};
  const compose_keys = Object.keys(c).slice(0, 60);
  const PRIORITY = ['getForm', 'send', 'prepareSend', 'beforeSend', 'beforeReview',
                    'command', 'validateForm', 'getSendResult'];
  const interesting = {};
  for (const k of PRIORITY) {
    if (!(k in c)) continue;
    try { interesting[k] = String(c[k]).slice(0, k === 'getForm' ? 3000 : 300); } catch (e) { interesting[k] = 'err'; }
  }
  return {compose_keys: compose_keys, interesting: interesting,
          tbar_keys: Object.keys(window._tbar || {}).slice(0, 30)};
})())
"""

# The real send entry point (discovered 2026-07-19 22:21 KST from the live
# compose object): _compose exposes the full chain validateForm ->
# prepareSend -> send. send() is what the UI button ultimately invokes.
_SEND_CALL_JS = r"""
JSON.stringify((() => {
  const c = window._compose || {};
  if (typeof c.send !== 'function') return {called: null, reason: 'no _compose.send'};
  let src = '';
  try { src = String(c.send).slice(0, 400); } catch (e) {}
  try {
    const r = c.send();
    return {called: '_compose.send()', result: String(r).slice(0, 120), src: src};
  } catch (e) {
    return {called: null, reason: 'send() threw: ' + String(e).slice(0, 200), src: src};
  }
})())
"""

# Click the compose form's real send button. Scope search to the form owning
# the send CSRF token first; document-wide fallback only accepts a UNIQUE
# candidate. 전달(forward)/예약/저장/취소 etc. are excluded by design review.
_CLICK_SEND_JS = r"""
JSON.stringify((() => {
  const RX = /(보내기|발송|전송|^send$)/i;
  const BAD = /(예약|임시저장|저장|취소|닫기|삭제|수신확인|전달|미리보기)/;
  const collect = (scope) => {
    const els = Array.from(scope.querySelectorAll(
      'button, input[type=submit], input[type=button], a, [role=button]'));
    const out = [];
    for (const el of els) {
      const label = ((el.textContent || '') + ' ' + (el.value || '') + ' '
                     + (el.id || '') + ' ' + (el.className || '')).trim();
      if (!RX.test(label) || BAD.test(label)) continue;
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      out.push({el: el, area: r.width * r.height, label: label.slice(0, 80)});
    }
    out.sort((a, b) => a.area - b.area);
    return out;
  };
  const csrf = document.querySelector('#sendCSRFToken');
  const form = csrf ? csrf.closest('form') : null;
  let hit = null, scope_used = '';
  if (form) {
    const cands = collect(form);
    if (cands.length) { hit = cands[0]; scope_used = 'form'; }
  }
  if (!hit) {
    const cands = collect(document);
    if (cands.length === 1) { hit = cands[0]; scope_used = 'document'; }
    else if (cands.length > 1) {
      return {clicked: null, ambiguous: cands.map(c => c.label).slice(0, 5)};
    }
  }
  if (!hit) return {clicked: null};
  const info = {clicked: hit.label, scope: scope_used,
                tag: hit.el.tagName, id: hit.el.id || '',
                cls: String(hit.el.className).slice(0, 80),
                onclick: String(hit.el.getAttribute('onclick') || '').slice(0, 200)};
  try { hit.el.focus(); } catch (e) {}
  try {
    hit.el.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));
    hit.el.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
  } catch (e) {}
  hit.el.click();
  return info;
})())
"""


def sync_field_events(browser: TriggerBrowser) -> None:
    """Re-commit field values through native setters + input/change/blur."""
    try:
        result = _unwrap(browser.eval_js(_FIELD_EVENTS_JS))
        log.info("compose field events dispatched: %s", result)
    except Exception as error:  # best-effort: the click chain may still work
        log.warning("field event sync failed: %s", str(error)[:150])


def discover_compose(browser: TriggerBrowser) -> str:
    """Ground-truth snapshot of the compose JS surface (logged every attempt)."""
    try:
        return _unwrap(browser.eval_js(_DISCOVERY_JS))[:6000]
    except Exception as error:
        return f"discovery-failed: {str(error)[:120]}"


def click_send_button(browser: TriggerBrowser) -> str | None:
    """Click the real 보내기 button. Returns a description of what was
    clicked, or None (button missing/ambiguous — caller must fail closed)."""
    raw = _unwrap(browser.eval_js(_CLICK_SEND_JS))
    try:
        info = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        log.warning("send-button click returned unparseable: %s", str(raw)[:200])
        return None
    if not isinstance(info, dict) or not info.get("clicked"):
        log.warning("send button not found/ambiguous: %s", json.dumps(
            info, ensure_ascii=False)[:400] if isinstance(info, dict) else raw[:200])
        return None
    description = json.dumps(info, ensure_ascii=False)[:400]
    log.info("send button clicked: %s", description)
    return description


def call_compose_send(browser: TriggerBrowser) -> str | None:
    """Invoke `window._compose.send()` — the real send entry point. Returns
    a description on success, None when unavailable/threw (caller falls back
    to the button click, then fails closed)."""
    raw = _unwrap(browser.eval_js(_SEND_CALL_JS))
    try:
        info = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        log.warning("_compose.send() call returned unparseable: %s", str(raw)[:200])
        return None
    if not isinstance(info, dict) or not info.get("called"):
        log.warning("_compose.send() unavailable: %s",
                    json.dumps(info, ensure_ascii=False)[:500]
                    if isinstance(info, dict) else str(raw)[:200])
        return None
    description = json.dumps(info, ensure_ascii=False)[:500]
    log.info("send triggered via _compose.send(): %s", description)
    return description


# The probe reads the exact keys the send gate compares, each with its own
# bound.  An ADDRESS field longer than its bound emits a truncation sentinel
# instead of a silently clipped value, so a recipient hidden past the bound
# fails closed rather than escaping the exact-set check (2026-07-29 incident:
# a 200-char clip dropped the tail recipient and the gate still passed).
#
# `content` must NOT get that treatment.  It is never compared as a set — the
# gate only asks whether our body marker (the first 30 chars of the first body
# line) appears in the form, and the sentinel is an OBJECT, so
# `marker in str(value)` can never match it.  Applying the address rule to
# content therefore made every body past the bound permanently unsendable: the
# editor refill could not help because the body was never the problem, and
# send.py raised "compose form missing body/recipients".
#
# This shipped together with the method guard and stayed latent the same 19
# days — the 07-30 release carries neither, so the sends that succeeded up to
# 08-14 prove nothing about it.  Measured 2026-08-18: a 236-char body was
# refused on every attempt.  Clip the head instead; the marker sits right after
# the editor's ~62-char style wrapper, so a 600-char head always carries it.
BCC_SELECTOR = "#adr-bcc-ipt_ta"

_FORM_PROBE_JS = r"""
JSON.stringify((() => { /* compose form probe */
  const c = window._compose || {};
  if (typeof c.getForm !== 'function') return {__probe_error: 'getForm unavailable'};
  let p = null;
  try { p = c.getForm(); } catch (e) { return {__probe_error: String(e).slice(0, 150)}; }
  if (!p || typeof p !== 'object') return {__probe_error: 'getForm() returned ' + String(p)};
  const out = {};
  const bounds = {to: 4000, cc: 4000, bcc: 8000, from: 400, method: 40, content: 600};
  const isAddress = {to: true, cc: true, bcc: true, from: true, method: true};
  for (const k of Object.keys(bounds)) {
    try {
      const raw = p[k] === undefined || p[k] === null ? '' : String(p[k]);
      out[k] = raw.length > bounds[k]
        ? (isAddress[k] ? {__truncated: raw.length} : raw.slice(0, bounds[k]))
        : raw;
    } catch (e) { out[k] = 'err'; }
  }
  return out;
})())
"""

_EDITOR_API_FILL_TEMPLATE = r"""
(() => {
  const html = __HTML__;
  const wrapped = '<div style="font-family:\uad74\ub9bc; font-size:10pt; line-height:150%">'
                  + html + '</div>';
  const done = [];
  const report = [];
  const ed = window.mail_editor;  // getForm(): param.content = mail_editor.getContent()
  if (!ed) return 'refill:[] | no window.mail_editor';
  if (typeof ed.setContent === 'function') {
    try { ed.setContent(html); done.push('setContent'); }
    catch (e) { report.push('setContent:' + String(e).slice(0, 60)); }
  }
  let after = '';
  try { after = String(ed.getContent()); } catch (e) { after = 'err:' + String(e).slice(0, 60); }
  if (after.indexOf('undefined') !== -1 || !after) {
    // editor engine dead in automation: override the exact accessor getForm uses
    ed.getContent = function() { return wrapped; };
    if (typeof ed.getMode === 'function') {
      const origMode = (() => { try { return ed.getMode(); } catch (e) { return 'html'; } })();
      ed.getMode = function() { return origMode === 'text' ? 'text' : 'html'; };
    }
    done.push('getContent-override');
    try { after = String(ed.getContent()); } catch (e) {}
  }
  return 'refill:[' + done.join(',') + '] | getContent-after: ' + after.slice(0, 150)
         + ' | ' + report.join(' / ').slice(0, 200);
})()
"""


def _body_marker(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:30]
    return body.strip()[:30]


class _RecipientParseError(ValueError):
    """An address field could not be reduced to a safe canonical set."""


def _canonical_addresses(value: str) -> list[str]:
    """Parse an address field into lower-cased addr-specs, order preserved.

    Uses the email header-registry parser so ``Name <addr>`` and quoted
    display names with commas are handled, while defects (CR/LF injection,
    malformed groups, empty or domain-less addresses) fail closed.  No
    normalization beyond case-fold + surrounding whitespace is applied:
    plus-tag stripping, dot folding, IDNA or Unicode folding could merge two
    distinct addresses and are deliberately avoided."""
    text = value.strip()
    if not text:
        return []
    if "\r" in text or "\n" in text:
        raise _RecipientParseError("address field contains a line break")
    try:
        parsed = AddressHeader.value_parser(text)
    except (HeaderParseError, MessageError, ValueError, IndexError) as error:
        raise _RecipientParseError(f"unparseable address field: {error}") from error
    if parsed.all_defects:
        raise _RecipientParseError(f"address field has defects: {parsed.all_defects!r}")
    addresses: list[str] = []
    for mailbox in parsed.all_mailboxes:
        local = mailbox.local_part
        domain = mailbox.domain
        if not local or not domain:
            raise _RecipientParseError(f"address is missing a local or domain part: {mailbox!r}")
        addresses.append(f"{local}@{domain}".strip().lower())
    return addresses


def _intended_set(recipients) -> Counter[str]:
    """Each intended element must parse to EXACTLY one mailbox, else a single
    list entry could smuggle a second recipient past the exact-set check."""
    counter: Counter[str] = Counter()
    for entry in recipients:
        parsed = _canonical_addresses(str(entry))
        if len(parsed) != 1:
            raise _RecipientParseError(f"intended recipient is not a single address: {entry!r}")
        counter[parsed[0]] += 1
    return counter


def _actual_set(form: dict[str, object], key: str) -> Counter[str]:
    raw = form.get(key, "")
    if isinstance(raw, dict):  # {__truncated: n} sentinel from the probe
        raise _RecipientParseError(f"{key} field was truncated by the probe: {raw!r}")
    return Counter(_canonical_addresses(str(raw)))


def verify_compose_form(
    browser: TriggerBrowser, recipients, body: str, cc=(), bcc=(),
) -> tuple[bool, str]:
    """Probe `_compose.getForm()` and require the REAL outgoing params to
    contain our body and route to EXACTLY our recipients (2026-07-19: a raw-DOM
    body write left getForm() with 'undefined', which was then literally sent;
    2026-07-29: a subset/substring recipient check let extra addresses through
    and a probe truncation hid the tail recipient).

    To, Cc and Bcc must each equal the intended multiset (order-insensitive),
    and any missing / extra / mutated / truncated recipient
    fails closed before the caller is allowed to fire the send trigger."""
    raw = _unwrap(browser.eval_js(_FORM_PROBE_JS))
    try:
        form = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return False, "unparseable form probe" if bcc else f"unparseable form probe: {str(raw)[:200]}"
    if not isinstance(form, dict):
        return False, "non-dict form probe" if bcc else f"non-dict form probe: {str(raw)[:200]}"
    dump = json.dumps({**form, "bcc": "[hidden]"} if bcc or form.get("bcc") else form,
                      ensure_ascii=False)
    if form.get("__probe_error"):
        return False, dump
    marker = _body_marker(body)
    # The editor stores HTML, so '&', '<' and '>' of the marker read as entities there.
    import html as html_module
    spellings = {marker, html_module.escape(marker, quote=False)}
    body_ok = bool(marker) and any(
        spelling in str(v) for spelling in spellings
        for k, v in form.items() if k != "__probe_error")
    if not body_ok:
        return False, dump
    method = str(form.get("method", ""))
    try:
        intended_to = _intended_set(recipients)
        intended_cc = _intended_set(cc)
        intended_bcc = _intended_set(bcc)
        if method == "tome":
            # send() rewrites to := from in tome mode; only safe for a true,
            # single-address self-send with no other recipients.
            if intended_cc or intended_bcc or _actual_set(form, "bcc"):
                return False, dump
            from_addrs = _canonical_addresses(str(form.get("from", "")))
            actual_from = Counter(from_addrs) if len(from_addrs) == 1 else None
            routing_ok = actual_from is not None and actual_from == intended_to
        elif method in ("", "send"):
            # A plain (non-tome) compose. The hidden `method` input is EMPTY on
            # this webmail and stays empty for the whole compose lifetime
            # (measured 2026-08-18: 12s poll, always ""), and the site's own
            # send() only special-cases 'tome' — every other value is a normal
            # send. Demanding literal 'send' here therefore rejected every
            # legitimate mail.
            #
            # This shipped as a latent defect: the check landed 2026-07-29 but
            # the mailon runtime stayed pinned to the 07-30 release for 19 days,
            # so production never ran it. Deploying the vendor tree on 08-18
            # carried it in and every send began failing closed, which tripped
            # the two-strike rule into mail-mode no-go. Logs confirm method was
            # "" on 08-12/13/14 too — while those sends were succeeding.
            actual_to = _actual_set(form, "to")
            actual_cc = _actual_set(form, "cc")
            actual_bcc = _actual_set(form, "bcc")
            routing_ok = (
                actual_to == intended_to
                and actual_cc == intended_cc
                and actual_bcc == intended_bcc
            )
        else:
            return False, dump  # unknown method: fail closed
    except _RecipientParseError as error:
        log.warning("recipient verification failed: %s",
                    type(error).__name__ if bcc or form.get("bcc") else str(error)[:200])
        return False, dump
    return routing_ok, dump


def fill_body_via_editor_api(browser: TriggerBrowser, body: str, html: str | None = None) -> None:
    """Re-commit the body (``html`` when given) through the Namo CrossEditor API / hidden field."""
    import html as html_module
    html_body = html if html else html_module.escape(body).replace("\n", "<br>")
    script = _EDITOR_API_FILL_TEMPLATE.replace("__HTML__", json.dumps(html_body))
    try:
        result = _unwrap(browser.eval_js(script))
        log.info("editor-API body refill: %s", str(result)[:200])
    except Exception as error:
        log.warning("editor-API body refill failed: %s", str(error)[:150])


# 2026-10-26 (owner: "한6줄은되보여", "큰 글씨 폰트로 3줄들어가있어"): with the
# signature placed outside the editor (#signPlaceType 1) the compose form posts
# signUid + signContent beside the body, and the webmail server joins them as
# body + three default-size <p><br/></p> + signature — about six body lines in
# Gmail. The owner's signature setting holds no blank line, so the only lever is
# what we post: append the webmail's own signContent (read here, never stored in
# code) after the body with one body-sized blank line, and set the compose to the
# webmail's own "사용안함" state (signUid -1, signContent empty) so the server
# adds nothing.  One eval does it all and puts everything back if any step does
# not read back, so a failure sends the mail exactly as before.
SIGNATURE_SPACER = '<div style="font-family:\uad74\ub9bc; font-size:10pt; line-height:150%"><br></div>'

_SIGNATURE_INTO_BODY_JS = r"""
JSON.stringify((() => { /* signature into body */
  const spacer = __SPACER__;
  const c = window._compose, ed = window.mail_editor;
  if (!c || typeof c.getForm !== 'function' || !ed || typeof ed.getContent !== 'function') {
    return {moved: false, reason: 'compose-unavailable'};
  }
  const place = document.querySelector('#signPlaceType');
  if (!place || String(place.value) !== '1') return {moved: false, reason: 'signature-not-outside-editor'};
  try { if (typeof ed.getMode === 'function' && ed.getMode() === 'text') return {moved: false, reason: 'text-mode'}; }
  catch (e) { return {moved: false, reason: 'mode-unreadable'}; }
  const sel = document.querySelector('#mcp_wrap select[name="signUid"]');
  const ipt = document.querySelector('#mcp_wrap [name="signContent"]');
  if (!sel || !ipt) return {moved: false, reason: 'signature-fields-missing'};
  const sign = String(ipt.value || '');
  if (String(sel.value) === '-1' || !sign.trim()) return {moved: false, reason: 'no-signature'};
  let base = '';
  try { base = String(ed.getContent()); } catch (e) { return {moved: false, reason: 'content-unreadable'}; }
  if (!base || base.indexOf('undefined') !== -1) return {moved: false, reason: 'content-unreadable'};
  const combined = base + spacer + sign;
  const origGet = ed.getContent, origUid = sel.value;
  const restore = (why) => {
    ed.getContent = origGet; sel.value = origUid; ipt.value = sign;
    return {moved: false, reason: why};
  };
  try {
    ed.getContent = function() { return combined; };
    sel.value = '-1';
    if (typeof c.signSelected === 'function') c.signSelected(sel);
    ipt.value = '';
    const p = c.getForm();
    if (String(p.content) !== combined) return restore('content-not-posted');
    if (String(p.signUid) !== '-1' || String(p.signContent || '') !== '') return restore('signature-still-posted');
  } catch (e) { return restore('error:' + String(e).slice(0, 80)); }
  return {moved: true, signature_chars: sign.length};
})())
"""


def move_signature_into_body(browser: TriggerBrowser) -> dict:
    """Post the webmail signature right under the body instead of letting the
    server put three large blank paragraphs before it; never raises."""
    script = _SIGNATURE_INTO_BODY_JS.replace("__SPACER__", json.dumps(SIGNATURE_SPACER))
    try:
        raw = _unwrap(browser.eval_js(script))
        result = json.loads(raw) if isinstance(raw, str) else raw
    except Exception as error:
        result = {"moved": False, "reason": f"eval-failed:{type(error).__name__}"}
    if not isinstance(result, dict):
        result = {"moved": False, "reason": "unparseable"}
    log.info("signature placement: %s", json.dumps(result, ensure_ascii=False)[:200])
    return result


_RECIPIENT_TOKENIZE_TEMPLATE = r"""
(() => {
  const groups = __GROUPS__;
  const report = [];
  for (const pair of groups) {
    const sel = pair[0], addrs = pair[1];
    const el = document.querySelector(sel);
    if (!el) { report.push(sel + ':missing'); continue; }
    const proto = el.tagName === 'TEXTAREA'
      ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const desc = Object.getOwnPropertyDescriptor(proto, 'value');
    for (const addr of addrs) {
      try { el.focus(); } catch (e) {}
      if (desc && desc.set) desc.set.call(el, addr); else el.value = addr;
      el.dispatchEvent(new Event('input', {bubbles: true}));
      for (const type of ['keydown', 'keyup']) {
        el.dispatchEvent(new KeyboardEvent(type,
          {key: 'Enter', keyCode: 13, which: 13, bubbles: true}));
      }
      el.dispatchEvent(new Event('change', {bubbles: true}));
      try { el.blur(); } catch (e) {}
      el.dispatchEvent(new Event('blur', {bubbles: true}));
    }
    report.push(sel + ':' + addrs.length + ' fed');
  }
  const named = document.querySelectorAll(
    '#mcp_wrap [name="to"], #mcp_wrap [name="cc"]');
  report.push('named-recipient-fields:' + named.length);
  return report.join(' | ');
})()
"""


def commit_recipients(browser: TriggerBrowser, recipients, cc, bcc=()) -> None:
    """Feed addresses ONE AT A TIME with Enter tokenization (2026-07-20:
    a comma-joined multi-recipient fill never registered any chip, so
    getForm() carried zero recipients and the form gate refused to send)."""
    groups = [["#adr-to-ipt_ta", list(recipients)]]
    if cc:
        groups.append(["#adr-cc-ipt_ta", list(cc)])
    if bcc:
        groups.append([BCC_SELECTOR, list(bcc)])
    script = _RECIPIENT_TOKENIZE_TEMPLATE.replace("__GROUPS__", json.dumps(groups))
    try:
        log.info("recipient tokenization: %s", _unwrap(browser.eval_js(script))[:300])
    except Exception as error:  # gate still verifies via getForm afterwards
        log.warning("recipient tokenization failed: %s",
                    type(error).__name__ if bcc else str(error)[:150])
