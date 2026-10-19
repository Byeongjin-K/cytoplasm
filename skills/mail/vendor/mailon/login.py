"""mailon.kr login flow.

Based on reverse-engineering the login form at https://mailon.kr/integrated/login:

  textbox[name=ipt-id]   -> email id
  textbox[name=ipt-pw]   -> password
  textbox[name=ipt-otp]  -> 6-digit TOTP
  button "로그인"         -> submit

The form does RSA encryption of id/pw client-side and POSTs to
/integrated/login. On success, the server responds with JSON containing
`redirectURL`; the page then navigates to the mailbox.

We do NOT try to bypass the form (would require reproducing RSA in Python).
We let the browser's own JS run; we just fill fields and click.
"""
from __future__ import annotations

import logging
import time

from .browser import AgentBrowser, BrowserError
from .config import Config
from .totp import generate_code, seconds_until_next_code


log = logging.getLogger(__name__)


class LoginError(RuntimeError):
    pass


def _find_login_refs(browser: AgentBrowser) -> dict[str, str]:
    """Inspect the login form, return refs for {id, pw, otp, submit}.

    The snapshot shows three textboxes with ref e17/e18/e19 and an OTP
    placeholder. We map them by order + the known OTP placeholder.
    """
    snap = browser.snapshot_json(interactive_only=True)
    # snap is a tree; walk it
    refs: list[tuple[str, str, dict]] = []  # (role, ref, node)
    buttons: list[tuple[str, dict]] = []

    def walk(node):
        if isinstance(node, dict):
            role = node.get("role") or node.get("type") or ""
            ref = node.get("ref") or node.get("id") or ""
            if role in ("textbox", "input"):
                refs.append((role, ref, node))
            if role == "button":
                buttons.append((ref, node))
            for child in node.get("children") or []:
                walk(child)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(snap)

    if len(refs) < 3:
        raise LoginError(
            f"expected 3 input fields on login form, found {len(refs)}. "
            "Page structure may have changed."
        )

    # Heuristic mapping: the form has id, pw, otp in that order.
    # OTP field has placeholder/name containing 'otp'.
    id_ref = pw_ref = otp_ref = None

    # Prefer explicit name/placeholder/aria hints
    for role, ref, node in refs:
        text = json_text_blob(node).lower()
        if "otp" in text:
            otp_ref = ref
            break

    remaining = [(ref, node) for role, ref, node in refs if ref != otp_ref]
    if len(remaining) >= 2:
        id_ref = remaining[0][0]
        pw_ref = remaining[1][0]

    if not (id_ref and pw_ref and otp_ref):
        raise LoginError(
            f"could not identify id/pw/otp fields: id={id_ref} pw={pw_ref} otp={otp_ref}"
        )

    # Find login button by name "로그인"
    submit_ref = None
    for ref, node in buttons:
        if "로그인" in json_text_blob(node):
            submit_ref = ref
            break
    if not submit_ref:
        # fallback: first button
        submit_ref = buttons[0][0] if buttons else None
    if not submit_ref:
        raise LoginError("login button not found")

    return {"id": id_ref, "pw": pw_ref, "otp": otp_ref, "submit": submit_ref}


def json_text_blob(node) -> str:
    """Concatenate all stringy fields of a node for keyword search."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return " ".join(json_text_blob(x) for x in node)
    if isinstance(node, dict):
        parts = []
        for k, v in node.items():
            if k == "children":
                continue
            parts.append(json_text_blob(v))
        return " ".join(parts)
    return str(node)


_LOGIN_PATH = "/integrated/login"


def _on_login_page(url: str) -> bool:
    """Return True while the browser is still sitting on the login form.

    Matches on the *path*, never the host: the host itself contains "mail"
    (mailon.kr), which is exactly what made the old ``wait_url("**/mail**")``
    match instantly and report a rejected login as a success.
    """
    return _LOGIN_PATH in url


_OPEN_TIMED_OUT = "Operation timed out"

# Slow-login budget (node sync logs, 178 logins in 2026-10): opening the page took p50 5.3 s,
# p90 27 s, max 57.6 s, and `wait --load domcontentloaded` never resolved (it timed out at 25 s
# every time, so it proved nothing about the form). On 2026-10-19 an approved send failed because
# the first fill hit agent-browser's 30 s CDP limit while the page was still busy. So: wait until
# the login fields actually exist, and retry a timed-out fill a couple of times. Everything here
# runs before the login form is submitted, so a retry can never repeat a send.
_FORM_READY_SECONDS = 90.0
_FORM_POLL_SECONDS = 1.0
_FILL_ATTEMPTS = 3
_FILL_RETRY_DELAY_SECONDS = 5.0
_FORM_READY_JS = (
    "document.querySelector('input[name=\"ipt-id\"]') && "
    "document.querySelector('input[name=\"ipt-pw\"]') && "
    "document.querySelector('input[name=\"ipt-otp\"]') ? 'ready' : 'wait'"
)


def _timed_out(error: BrowserError) -> bool:
    return "timed out" in str(error).lower()


def _wait_for_form(browser: AgentBrowser) -> None:
    """Return once the three login fields exist; a busy page's CDP timeout counts as not yet."""
    deadline = time.monotonic() + _FORM_READY_SECONDS
    while True:
        try:
            if browser.eval_js(_FORM_READY_JS).strip().strip('"') == "ready":
                return
        except BrowserError as error:
            if not _timed_out(error):
                raise
            log.warning("login form check timed out; the page is still busy")
        if time.monotonic() >= deadline:
            raise LoginError(f"login form did not appear within {_FORM_READY_SECONDS:.0f}s")
        browser.wait_ms(int(_FORM_POLL_SECONDS * 1000))


def _fill_credentials(browser: AgentBrowser, cfg: Config) -> None:
    """Fill id, password and a fresh TOTP; retry only a timed-out fill, before any submit."""
    for attempt in range(1, _FILL_ATTEMPTS + 1):
        # Wait until current TOTP window has at least 10 seconds left.
        # The network round-trip + RSA encryption can eat several seconds;
        # if the code expires before the server validates it, the response
        # is indistinguishable from a wrong-password error. Give ourselves
        # a generous margin.
        remaining = seconds_until_next_code()
        if remaining < 10:
            log.info("TOTP window has %ds left (<10); waiting for next window", remaining)
            time.sleep(remaining + 1)

        code = generate_code(cfg.totp_secret)
        log.info("generated TOTP code (length=%d); valid for %ds",
                 len(code), seconds_until_next_code())

        # Use CSS selectors instead of @eN refs because:
        #   1. Form field names on mailon.kr are stable (ipt-id, ipt-pw, ipt-otp)
        #   2. @eN refs require a fresh snapshot AND can become invalid if
        #      anything modifies the DOM between snapshot and fill
        log.info("filling credentials via CSS selectors")
        try:
            browser.fill('input[name="ipt-id"]', cfg.mailon_id)
            browser.fill('input[name="ipt-pw"]', cfg.mailon_pw)
            browser.fill('input[name="ipt-otp"]', code)
            return
        except BrowserError as error:
            if not _timed_out(error) or attempt == _FILL_ATTEMPTS:
                raise
            log.warning("credential fill timed out (attempt %d/%d); retrying in %.0fs",
                        attempt, _FILL_ATTEMPTS, _FILL_RETRY_DELAY_SECONDS)
            time.sleep(_FILL_RETRY_DELAY_SECONDS)
            _wait_for_form(browser)


def _open_login_page(browser: AgentBrowser, cfg: Config) -> None:
    """Open the login page, tolerating only a load timeout that already reached it.

    agent-browser's `open` waits for the full page load under a 25 s action
    limit, and mailon.kr regularly takes longer while the login form itself is
    already rendered (2026-10-02: 3 of 6 opens timed out, the form was there
    6 of 6). Any other failure, or a timeout that never reached the login
    path, still raises.
    """
    log.info("opening login page: %s", cfg.login_url)
    try:
        browser.open(cfg.login_url)
    except BrowserError as error:
        if _OPEN_TIMED_OUT not in str(error):
            raise
        url = browser.current_url()
        if not _on_login_page(url):
            raise
        log.warning("login page open timed out after reaching %s; continuing", url)


def login(browser: AgentBrowser, cfg: Config) -> None:
    """Perform a fresh login. Raises LoginError on failure.

    Strategy:
      1. Navigate to login URL and wait until the login fields exist
      2. Close any "notice" popups
      3. Wait for the TOTP window to have >=5 seconds left so code doesn't
         expire mid-flight
      4. Fill id, pw, otp
      5. Submit
      6. Wait for URL change away from /integrated/login
    """
    _open_login_page(browser, cfg)
    log.info("opened; waiting for the login fields")
    _wait_for_form(browser)

    log.info("dismissing popups")
    _dismiss_popups(browser)
    log.info("popups dismissed; locating fields")

    _fill_credentials(browser, cfg)

    # Submit by calling the page's own login() JS function - this bypasses
    # any visual overlay (popups) that might be blocking the click.
    log.info("submitting login form")
    # agent-browser prints eval results JSON-quoted ('"ok"'), so unwrap before comparing.
    submitted = browser.eval_js(
        "typeof login === 'function' ? (login(), 'ok') : 'no-login-fn'"
    ).strip().strip('"')
    if submitted == "no-login-fn":
        raise LoginError(
            "login form was never submitted: the page exposes no login() function "
            "(eval returned 'no-login-fn'); refusing to spend the 25s navigation "
            "budget on a navigation that cannot happen"
        )
    if submitted != "ok":
        # An unrecognised shape is not evidence of failure: fall through to the URL
        # poll below rather than block a login that may well have gone through.
        log.warning("login submit returned %r; falling back to the URL wait", submitted)

    # Wait for navigation away from the login page (up to 25s).
    #
    # This used to wait on the glob ``**/mail**`` and validate the URL only
    # inside the ``except`` branch.  That glob matches the *host* — mailon.kr
    # itself contains "mail" — so the wait returned immediately (32ms measured)
    # and the except branch never ran.  A rejected login was therefore logged as
    # "login succeeded", and the real failure surfaced much later as a
    # BrowserError from the next page interaction, which exit code 2 folds into
    # "auth_or_browser_error".  Poll the *path* instead and validate on every
    # way out of the wait, so a rejected credential fails here as LoginError.
    deadline = time.monotonic() + 25.0
    url = browser.current_url()
    while _on_login_page(url) and time.monotonic() < deadline:
        browser.wait_ms(500)
        url = browser.current_url()

    if _on_login_page(url):
        # Still sitting on the login form -> credential / OTP rejection.
        try:
            body = browser.eval_json(
                "document.body.innerText.substring(0, 500)"
            )
        except Exception:
            body = ""
        raise LoginError(
            f"login did not leave {_LOGIN_PATH}. url={url} "
            f"snippet={str(body)[:200]!r}"
        )

    log.info("login succeeded; url=%s", url)


def _dismiss_popups(browser: AgentBrowser) -> None:
    """Close any announcement popups that appear on the login page."""
    # Up to 3 popups (mailon sometimes chains them)
    for _ in range(3):
        try:
            # The close button is consistently labeled "닫기"
            browser.find_click("text", "닫기", exact=True)
            browser.wait_ms(400)
        except BrowserError:
            break  # no more popups
