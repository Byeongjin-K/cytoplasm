from __future__ import annotations

import hashlib
import json
import mimetypes
import logging
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Protocol

from . import folders, send_trigger, send_verify
from .browser import BrowserError

log = logging.getLogger(__name__)

# Retry idempotency: a matching mail already in Sent within this window means
# a previous (possibly "failed-to-verify") attempt actually delivered.
DUPLICATE_SUPPRESS_WINDOW_MS = 20 * 60 * 1000
# Sent-folder confirmation window starts this much before beforeSend() fires
# (client/server clock skew allowance).
SENT_CONFIRM_SKEW_MS = 120 * 1000
_EMPTY_ATTACHMENT_MANIFEST_SHA256 = hashlib.sha256(b"[]").hexdigest()


class SendValidationError(ValueError):
    """Invalid caller input that must be corrected before retrying."""

    error_code = "validation_error"
    stage = "validation"
    retryable = False


class SendSafetyError(RuntimeError):
    """Fail-closed refusal while preparing, sending, or verifying a mail.

    ``cause`` is a fixed code naming the check that refused. It is safe to
    record (unlike the message) so a failed send says why it stopped."""

    error_code = "send_failed"
    stage = "send"
    retryable = True
    cause = "unclassified"
    step = ""

    def __init__(self, message: str = "", *, cause: str | None = None) -> None:
        super().__init__(message)
        if cause is not None:
            self.cause = cause


class SendStepError(SendSafetyError, BrowserError):
    """A browser command failed at ``step`` of the send (2026-10-13).

    It stays a BrowserError for callers that catch that, and carries the failing
    agent-browser subcommand and a fixed error kind, never the page's own text."""

    error_code = "external_service_error"
    stage = "provider"

    def __init__(self, step: str, error: BrowserError) -> None:
        super().__init__(
            f"{step}: {error}",
            cause="compose_not_ready" if step == "compose_open" else "browser_command_failed",
        )
        self.step = step
        self.command = getattr(error, "command", "")
        self.error_kind = browser_error_kind(str(error))


_SAFE_CODE = re.compile(r"[a-z][a-z0-9_-]{2,47}")


def failure_details(error: BaseException) -> dict[str, str]:
    """Where a send stopped, as fixed codes safe to record (2026-10-13): the step,
    the agent-browser subcommand that failed, and what kind of failure it was.
    ``browser_command_failed`` alone could not say which command of the send failed."""
    browser = isinstance(error, BrowserError)
    details = {
        "step": getattr(error, "step", "") or ("session" if browser else ""),
        "browser_command": getattr(error, "command", ""),
        "error_kind": getattr(error, "error_kind", "")
        or (browser_error_kind(str(error)) if browser else ""),
    }
    return {key: value for key, value in details.items()
            if isinstance(value, str) and _SAFE_CODE.fullmatch(value)}


def browser_error_kind(message: str) -> str:
    """Fixed name for what went wrong inside a failed browser command."""
    if "CDP command timed out" in message:
        return "cdp_timeout"
    if "timed out after" in message:
        return "command_timeout"
    if "TypeError" in message:
        return "js_type_error"
    if "Evaluation error" in message:
        return "js_error"
    if "not found on PATH" in message:
        return "browser_missing"
    return "browser_error"


class AttachmentValidationError(SendValidationError):
    """An attachment path does not identify a readable regular file."""

    error_code = "attachment_invalid"


class AttachmentUnsupportedError(SendSafetyError):
    """The configured provider adapter cannot safely upload attachments."""

    error_code = "attachment_unsupported"
    stage = "attachment_upload"
    retryable = False


class AttachmentUploadError(SendSafetyError):
    """The provider upload step failed before the final send trigger."""

    error_code = "attachment_upload_failed"
    stage = "attachment_upload"
    retryable = True


class ComposeBrowser(Protocol):
    def eval_js(self, script: str) -> str: ...
    def eval_json(self, script: str) -> dict | list | str | int | float | bool | None: ...
    def fill(self, selector: str, value: str) -> None: ...
    def upload(self, selector: str, paths: tuple[Path, ...]) -> None: ...
    def wait_ms(self, milliseconds: int) -> None: ...
    def clear_network_requests(self) -> None: ...
    def network_post_count(self) -> int: ...
    def network_requests(self) -> str: ...


@dataclass(frozen=True)
class SendRequest:
    recipients: tuple[str, ...]
    cc: tuple[str, ...]
    subject: str
    body: str
    attachments: tuple[Path, ...]
    bcc: tuple[str, ...] = ()
    # The editor content when set; ``body`` stays the plain text the form gate checks.
    body_html: str = ""

    def __post_init__(self) -> None:
        if not self.recipients:
            raise SendValidationError("at least one recipient is required")
        if not self.subject.strip():
            raise SendValidationError("a subject is required")
        if not self.body.strip():
            raise SendValidationError("a body is required")
        for attachment in self.attachments:
            try:
                valid = attachment.exists() and attachment.is_file()
            except OSError:
                valid = False
            if not valid:
                raise AttachmentValidationError("attachment does not exist or is not a regular file")


@dataclass(frozen=True)
class SendResult:
    status: str
    csrf_present: bool
    attachment_count: int
    network_post_count: int
    verified: bool = False
    attachment_manifest_sha256: str = _EMPTY_ATTACHMENT_MANIFEST_SHA256
    # How the mail list was found before sending (rows/reload-N/sidebar).
    # Only the attempt log records it; the stdout JSON contract stays fixed.
    list_path: str = ""
    # How the mail page finished building itself (see folders.finish_page_boot).
    page_boot: str = ""

    def to_json(self) -> str:
        payload = asdict(self)
        del payload["list_path"]
        del payload["page_boot"]
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)

    def attempt_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)


class ComposeSender:
    def __init__(
        self,
        browser: ComposeBrowser,
        *,
        verify_timeout_s: float = 45.0,
        verify_poll_s: float = 3.0,
        fastfail_timeout_s: float = 10.0,
        resolve_timeout_s: float = 90.0,
        clock: Callable[[], float] = time.monotonic,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self._browser = browser
        self._verify_timeout_s = verify_timeout_s
        self._verify_poll_s = verify_poll_s
        self._fastfail_timeout_s = fastfail_timeout_s
        self._resolve_timeout_s = resolve_timeout_s
        self._clock = clock
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))

    def send(self, request: SendRequest, *, dry_run: bool) -> SendResult:
        """Send, naming the step that stopped it when it fails."""
        self._step = "mailbox_check"
        try:
            return self._send(request, dry_run=dry_run)
        except SendSafetyError as error:
            if not error.step:
                error.step = self._step
            raise
        except BrowserError as error:
            raise SendStepError(self._step, error) from error

    def _send(self, request: SendRequest, *, dry_run: bool) -> SendResult:
        attachment_manifest_sha256 = _attachment_manifest_sha256(request.attachments)
        self._step = "page_boot"
        page_boot = folders.finish_page_boot(self._browser)
        self._step = "mailbox_check"
        folder_uid = list_path = ""
        if not dry_run:
            try:
                folder_uid, list_path = send_verify.resolve_current_folder_uid(
                    self._browser, timeout_s=self._resolve_timeout_s,
                    poll_interval_s=self._verify_poll_s, clock=self._clock)
                if send_verify.find_mail_match(
                    self._browser, folder_uid, request.subject,
                    request.recipients,
                    self._now_ms() - DUPLICATE_SUPPRESS_WINDOW_MS,
                    timeout_s=0, poll_interval_s=0, clock=self._clock,
                ):
                    log.warning(
                        "duplicate-suppressed: matching mail already in the "
                        "mailbox within window; not sending again")
                    return SendResult(
                        status="submitted", csrf_present=False,
                        attachment_count=len(request.attachments),
                        network_post_count=0, verified=True, list_path=list_path,
                        page_boot=page_boot,
                    )
            except send_verify.SendVerifyError as error:
                raise SendSafetyError(str(error), cause=error.cause) from error

        self._step = "compose_open"
        _open_compose_with_recovery(self._browser, clock=self._clock)
        # The retry proves only that _tbar.compose() became callable (~1.9s
        # measured). Nothing has measured when the compose form (CSRF token,
        # #adr-to-ipt_ta, editor iframe) finishes rendering, and every downstream
        # probe fails closed, so keep the settle wait rather than trade a measured
        # fix for an unmeasured race.
        self._browser.wait_ms(3000)
        self._step = "compose_fields"
        self._browser.clear_network_requests()
        compose_metadata = self._browser.eval_json(
            """JSON.stringify((function() {
              var csrf = document.querySelector('#sendCSRFToken');
              var fileInput = document.querySelector('input[type=file]');
              return {csrf: csrf ? {name: csrf.name, value: csrf.value} : null,
                      file_input: fileInput ? '#' + fileInput.id : null};
            })())"""
        )
        csrf = compose_metadata.get("csrf") if isinstance(compose_metadata, dict) else None
        if not csrf:
            raise SendSafetyError("compose page did not expose send CSRF token",
                                  cause="compose_csrf_missing")

        self._browser.eval_js(_field_fill_script(
            ",".join(request.recipients), ",".join(request.cc), request.subject,
            **({"bcc": ",".join(request.bcc)} if request.bcc else {}),
        ))
        self._step = "editor_fill"
        _fill_editor_when_ready(self._browser, request.body, clock=self._clock,
                                html=request.body_html)

        post_count = self._browser.network_post_count()
        if dry_run:
            if post_count:
                raise SendSafetyError(f"dry-run observed {post_count} POST request(s)",
                                      cause="dry_run_posted")
            return SendResult(
                status="dry_run",
                csrf_present=True,
                attachment_count=len(request.attachments),
                network_post_count=0,
                verified=False,
                attachment_manifest_sha256=attachment_manifest_sha256,
                page_boot=page_boot,
            )

        if request.attachments:
            self._step = "attachment_upload"
            file_input = (
                compose_metadata.get("file_input")
                if isinstance(compose_metadata, dict) else None
            )
            if not file_input:
                raise AttachmentUnsupportedError(
                    "compose page did not expose an attachment file input",
                    cause="attachment_input_missing")
            try:
                self._browser.upload(file_input, request.attachments)
            except BrowserError as error:
                raise AttachmentUploadError("attachment upload could not be started",
                                            cause="attachment_upload_not_started") from error
            queue_state = self._browser.eval_json(
                """JSON.stringify((function(expected) {
                  if (!window._compose || !window.uploader ||
                      typeof window.uploader.upload !== 'function') {
                    return {ready: false, queued: 0, records: 0};
                  }
                  window._compose.command = '';
                  var records = window.uploadGrid && window.uploadGrid.objRecords
                    ? window.uploadGrid.objRecords.records : [];
                  return {ready: true, queued: window.uploader.files.length,
                          records: records.length, expected: expected};
                })(""" + str(len(request.attachments)) + "))"
            )
            if not isinstance(queue_state, dict) or not queue_state.get("ready"):
                raise AttachmentUnsupportedError("mail attachment uploader API is unavailable",
                                                 cause="attachment_uploader_missing")
            if queue_state.get("queued") != len(request.attachments):
                raise AttachmentUploadError(
                    "not all attachment files entered the upload queue",
                    cause="attachment_queue_incomplete")
            self._browser.eval_js(
                "window._compose.command=''; window.uploader.upload(); 'upload-started';"
            )
            _verify_attachment_upload(self._browser, request.attachments)
        self._step = "recipient_commit"
        bcc_options = {"bcc": request.bcc} if request.bcc else {}
        send_trigger.commit_recipients(self._browser, request.recipients, request.cc, **bcc_options)
        send_trigger.sync_field_events(self._browser)
        log.info("compose discovery: %s", send_trigger.discover_compose(self._browser))
        form_ok, form_dump = send_trigger.verify_compose_form(
            self._browser, request.recipients, request.body, cc=request.cc, **bcc_options)
        if not form_ok:
            log.warning("compose form incomplete (%s); refilling body via editor API",
                        form_dump[:800])
            send_trigger.fill_body_via_editor_api(self._browser, request.body,
                                                  html=request.body_html or None)
            form_ok, form_dump = send_trigger.verify_compose_form(
                self._browser, request.recipients, request.body, cc=request.cc, **bcc_options)
        log.info("compose form state: %s", form_dump[:2500])
        if not form_ok:
            raise SendSafetyError(
                "compose form missing body/recipients (would send garbage) — "
                "form dump logged", cause="compose_form_incomplete")
        if send_trigger.move_signature_into_body(self._browser).get("moved"):
            form_ok, form_dump = send_trigger.verify_compose_form(
                self._browser, request.recipients, request.body, cc=request.cc, **bcc_options)
            if not form_ok:
                raise SendSafetyError(
                    "compose form changed after the signature move — form dump logged",
                    cause="compose_form_incomplete")
        self._step = "send_trigger"
        baseline_ids = send_verify.request_ids(self._browser.network_requests())
        send_started_ms = self._now_ms()
        triggered = (send_trigger.call_compose_send(self._browser)
                     or send_trigger.click_send_button(self._browser))
        if not triggered:
            raise SendSafetyError(
                "no send trigger available (_compose.send missing AND button "
                "not found) — discovery logged; refusing to fire blind",
                cause="send_trigger_missing")
        self._browser.wait_ms(500)
        self._step = "sent_verify"
        send_verify.log_network_forensics(self._browser, baseline_ids)
        try:
            send_verify.check_network_fast_fail(
                self._browser, baseline_ids,
                timeout_s=self._fastfail_timeout_s, clock=self._clock,
            )
            if not send_verify.find_mail_match(
                self._browser, folder_uid, request.subject,
                request.recipients, send_started_ms - SENT_CONFIRM_SKEW_MS,
                timeout_s=self._verify_timeout_s,
                poll_interval_s=self._verify_poll_s, clock=self._clock,
            ):
                raise send_verify.SendVerifyError(
                    "send not confirmed in mailbox (allFolder) within "
                    f"{self._verify_timeout_s:.0f}s", cause="sent_mail_unconfirmed")
        except send_verify.SendVerifyError as error:
            raise SendSafetyError(str(error), cause=error.cause) from error
        return SendResult(
            status="submitted",
            csrf_present=True,
            attachment_count=len(request.attachments),
            network_post_count=self._browser.network_post_count(),
            verified=True,
            attachment_manifest_sha256=attachment_manifest_sha256,
            list_path=list_path,
            page_boot=page_boot,
        )


def _open_compose_with_recovery(
    browser: ComposeBrowser, *, clock: Callable[[], float],
    reloads: int = folders.RELOAD_ATTEMPTS,
) -> None:
    """Open compose; when the page never built itself, reload it, finish its boot
    and try again — the recovery the mail list already uses (2026-10-13)."""
    try:
        send_trigger.open_compose_when_ready(browser, clock=clock)
        return
    except BrowserError as error:
        last = error
    for attempt in range(1, reloads + 1):
        log.warning("compose did not open (%s); reload %d/%d", str(last)[:200],
                    attempt, reloads)
        try:
            browser.eval_js(folders._RELOAD_JS)
        except BrowserError as error:
            last = error
            break
        browser.wait_ms(3000)
        folders.finish_page_boot(browser)
        try:
            send_trigger.open_compose_when_ready(browser, clock=clock)
            log.warning("compose opened after reload %d/%d", attempt, reloads)
            return
        except BrowserError as error:
            last = error
    raise last


def _verify_attachment_upload(
    browser: ComposeBrowser,
    attachments: tuple[Path, ...],
    *,
    attempts: int = 45,
) -> None:
    """Wait for server file UIDs and fail closed if any upload is incomplete."""
    names = [path.name for path in attachments]
    encoded_names = json.dumps(names)
    for _ in range(attempts):
        state = browser.eval_json(
            """JSON.stringify((function(expected) {
              var attach = document.querySelector('#uploaderAttach');
              var records = window.uploadGrid && window.uploadGrid.objRecords
                ? window.uploadGrid.objRecords.records : [];
              var represented = expected.filter(function(name) {
                return records.some(function(record) {
                  return record.fileName === name && record.file && record.file.fileUid;
                });
              });
              var uids = attach && attach.value
                ? attach.value.split(';').filter(Boolean) : [];
              return {represented: represented, uid_count: uids.length,
                      record_count: records.length};
            })(""" + encoded_names + "))"
        )
        if isinstance(state, dict):
            represented = set(state.get("represented", []))
            if represented == set(names) and state.get("uid_count") == len(names):
                return
        browser.wait_ms(1000)
    raise AttachmentUploadError(
        "attachment upload did not receive verified server file IDs — refusing to send",
        cause="attachment_upload_unverified",
    )


def _attachment_manifest_sha256(attachments: tuple[Path, ...]) -> str:
    records: list[dict[str, str | int]] = []
    for attachment in attachments:
        try:
            size_bytes = attachment.stat().st_size
            with attachment.open("rb") as attachment_file:
                content_sha256 = hashlib.file_digest(attachment_file, "sha256").hexdigest()
        except OSError as error:
            raise AttachmentValidationError("attachment does not exist or is not a regular file") from error
        records.append({
            "display_name": attachment.name,
            "size_bytes": size_bytes,
            "mime_type": mimetypes.guess_type(attachment.name)[0] or "application/octet-stream",
            "sha256": content_sha256,
        })
    canonical = json.dumps(
        records, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

def _editor_fill_script(body: str, html: str = "") -> str:
    encoded_body = json.dumps(html or body)
    target = "innerHTML" if html else "innerText"
    return (
        "(function(value) {"
        "var frame = document.querySelector('#NamoSE_Ifr__mail_editor_nm');"
        "if (!frame || !frame.contentDocument || !frame.contentDocument.body) {"
        f"throw new Error({json.dumps(_EDITOR_UNAVAILABLE)});"
        "}"
        f"frame.contentDocument.body.{target} = value;"
        "frame.contentDocument.body.dispatchEvent(new Event('input', {bubbles: true}));"
        f"}})({encoded_body});"
    )


# The Namo editor iframe reloads itself after compose opens. Measured 2026-09-28
# on the node with a 1s poll after compose: its body exists at t+1.4s, is gone
# (document without body) from t+2.5s to t+5.9s, and is back for good from
# t+7.8s. The 3s form settle lands inside that window, so a single fill attempt
# threw "compose editor frame unavailable" on nearly every watch tick and two
# approved mails waited hours. Retry the real fill and let its success be the
# readiness signal, as open_compose_when_ready() does for compose itself.
# 2026-10-19: on a slow webmail (page 'load' never fired, finish_page_boot ran) the
# editor was still gone 20s after compose opened and an approved mail stopped at
# editor_fill. The budget now covers a slow page; the fill still stops before any send.
_EDITOR_UNAVAILABLE = "compose editor frame unavailable"
EDITOR_READY_TIMEOUT_S = 60.0


def _fill_editor_when_ready(
    browser: ComposeBrowser,
    body: str,
    *,
    clock: Callable[[], float],
    timeout_s: float = EDITOR_READY_TIMEOUT_S,
    html: str = "",
) -> None:
    script = _editor_fill_script(body, html)
    deadline = clock() + timeout_s
    while True:
        try:
            browser.eval_js(script)
            return
        except BrowserError as error:
            if _EDITOR_UNAVAILABLE not in str(error) or clock() >= deadline:
                raise
        browser.wait_ms(500)


def _field_fill_script(recipients: str, cc: str, subject: str, bcc: str = "") -> str:
    encoded = json.dumps([recipients, cc, subject])
    selectors = "['#adr-to-ipt_ta','#adr-cc-ipt_ta','#compose_subject']"
    if bcc:
        encoded = json.dumps([recipients, cc, subject, bcc])
        selectors = json.dumps([
            "#adr-to-ipt_ta", "#adr-cc-ipt_ta", "#compose_subject", send_trigger.BCC_SELECTOR,
        ])
    return (
        "(function(values) {"
        f"{selectors}.forEach(function(selector, index) {{"
        "var element = document.querySelector(selector);"
        "if (!element) { throw new Error('compose field unavailable'); }"
        "element.value = values[index];"
        "});"
        f"}})({encoded});"
    )


def record_send_failure(logs_dir: Path, payload: dict) -> None:
    """Failed attempts go to the same log as sent ones, with the safe error fields
    only — before 2026-10-13 a failed send left no line there at all."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    line = {key: value for key, value in payload.items() if key != "message"}
    line["at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with (logs_dir / "send-attempts.jsonl").open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(line, separators=(",", ":"), sort_keys=True) + "\n")


def record_send_result(logs_dir: Path, result: SendResult) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    with (logs_dir / "send-attempts.jsonl").open("a", encoding="utf-8") as log_file:
        log_file.write(result.attempt_json() + "\n")
