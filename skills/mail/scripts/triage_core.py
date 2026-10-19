"""Pure logic for the W4-2 mail triage pipeline: LLM response parsing, prompt
building, masking, sanitized approval rendering, mailon send argv building,
and gate-parity external-effect action hashing.

No I/O, no subprocess, no network — everything here is pytest-able.

"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from automation.entity_preflight.contracts import JsonValue
from triage_card_legacy import ApprovalRenderDestination as ApprovalRenderDestination, render_approvals_message as _render_v1
from triage_attachments import (
    AttachmentPolicyError as AttachmentPolicyError,
    MAX_ATTACHMENT_COUNT as MAX_ATTACHMENT_COUNT, MAX_ATTACHMENT_BYTES as MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENT_TOTAL_BYTES as MAX_ATTACHMENT_TOTAL_BYTES,
    BLOCKED_ATTACHMENT_SUFFIXES as BLOCKED_ATTACHMENT_SUFFIXES,
    build_attachment_manifest as build_attachment_manifest,
    attachment_manifest_sha256 as attachment_manifest_sha256,
    verify_attachment_manifest as verify_attachment_manifest,
)

PIPELINE_VERSION = "mail-triage-v1"
CATEGORIES = ("important", "normal", "spam")
FLAG_KEYS = ("reply_needed", "schedule_needed", "budget")
PROMPT_BODY_LIMIT = 6000
_PROMPT_MARKER = "<<<PROMPT>>>"
INSTRUCTION_DEFAULT = "(별도 지시 없음)"

# Canonical ToolCall parity with the deployed pre_tool_call gate for a
# terminal `… python -m mailon.main send …` invocation (rule id mailon_send):
# tool_name "python3", arguments {"command": shlex.join(argv)}.
EXTERNAL_EFFECT_TOOL = "python3"
EXTERNAL_EFFECT_RULE_ID = "mailon_send"
EXTERNAL_EFFECT_TARGET_ID = f"tool:{EXTERNAL_EFFECT_RULE_ID}:{EXTERNAL_EFFECT_TOOL}"

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LONG_DIGITS = re.compile(r"\d{5,}")
_ADDR = re.compile(r"<?([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})>?")


class LlmParseError(ValueError):
    """The LLM response does not contain the required JSON contract."""


@dataclass(frozen=True, slots=True)
class Classification:
    """Validated triage verdict for one mail."""

    category: str
    reply_needed: bool
    schedule_needed: bool
    budget: bool
    schedule_text: str
    reason: str

    def flags(self) -> tuple[str, ...]:
        pairs = zip(FLAG_KEYS, (self.reply_needed, self.schedule_needed, self.budget))
        return tuple(key for key, value in pairs if value)


def first_json_object(raw: str) -> dict:
    """Extract the first balanced JSON object from raw LLM text."""
    start = raw.find("{")
    if start < 0:
        raise LlmParseError("no JSON object in LLM response")
    depth, end, in_string, escape = 0, -1, False, False
    for index in range(start, len(raw)):
        char = raw[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index
                break
    if end < 0:
        raise LlmParseError("unbalanced JSON object in LLM response")
    try:
        payload = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as error:
        raise LlmParseError(f"invalid JSON: {error}") from error
    if not isinstance(payload, dict):
        raise LlmParseError("LLM response JSON is not an object")
    return payload


def _json_bool(value: object) -> bool:
    """Coerce an LLM JSON field to bool WITHOUT Python truthiness traps.

    The former GLM tier sometimes emitted booleans as strings; ``bool("false")`` is ``True``,
    which would spuriously trip a flag (e.g. delegate a calendar draft). Only a
    real JSON ``true`` or the string ``"true"`` (case-insensitive) is True.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False


def parse_classification(raw: str) -> Classification:
    payload = first_json_object(raw)
    category = str(payload.get("category") or "").strip().lower()
    if category not in CATEGORIES:
        raise LlmParseError(f"category must be one of {CATEGORIES}, got {category!r}")
    return Classification(
        category=category,
        reply_needed=_json_bool(payload.get("reply_needed")),
        schedule_needed=_json_bool(payload.get("schedule_needed")),
        budget=_json_bool(payload.get("budget")),
        schedule_text=str(payload.get("schedule_text") or "").strip(),
        reason=str(payload.get("reason") or "").strip(),
    )


def parse_reply(raw: str) -> tuple[str, str]:
    """Return (subject, body) from the reply-draft LLM response."""
    payload = first_json_object(raw)
    subject = str(payload.get("subject") or "").strip()
    body = str(payload.get("body") or "").strip()
    if not body:
        raise LlmParseError("reply draft has an empty body")
    return subject, body


def parse_digest_summary(raw: str) -> str:
    """Return the one-line summary from the digest-summary LLM response."""
    payload = first_json_object(raw)
    summary = str(payload.get("summary") or "").strip()
    if not summary:
        raise LlmParseError("digest summary is empty")
    return summary


def load_prompt_template(path) -> str:
    """Prompt body below the marker LINE (line-anchored — W2-3 lesson)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line.strip() == _PROMPT_MARKER:
            return "\n".join(lines[index + 1 :]).strip()
    raise ValueError(f"prompt file missing {_PROMPT_MARKER} line: {path}")


def build_prompt(
    template: str, *, subject: str, sender: str, body: str, instruction: str = "",
    evidence: str = "",
) -> str:
    for placeholder in ("{{SUBJECT}}", "{{SENDER}}", "{{BODY}}"):
        if placeholder not in template:
            raise ValueError(f"prompt template missing {placeholder}")
    if "{{INSTRUCTION}}" in template:
        template = template.replace(
            "{{INSTRUCTION}}", instruction.strip() or INSTRUCTION_DEFAULT
        )
    elif instruction.strip():
        # Fail closed: an owner instruction must never be silently dropped.
        raise ValueError("prompt template missing {{INSTRUCTION}} for a non-empty instruction")
    prompt = (
        template.replace("{{SUBJECT}}", subject)
        .replace("{{SENDER}}", sender)
        .replace("{{BODY}}", body[:PROMPT_BODY_LIMIT])
    )
    if not evidence:
        return prompt
    return (
        f"{prompt}\n\n{evidence}\n\n"
        "Use only MATERIAL/EVIDENCE, cite [En], do not invent. "
        "For the recipient-facing mail body, emit no [En] citations and mention no private sources."
    )


def mask_value(value: str, salt: str = "") -> str:
    """Opaque id parity with the W4-1 wrapper masking (`sha256:<hex16>`)."""
    digest = hashlib.sha256((salt + value).encode("utf-8")).hexdigest()
    return f"sha256:{digest[:16]}"


def redact(text: str) -> str:
    """Mask emails and long digit runs in error/report lines."""
    return _LONG_DIGITS.sub("[MASKED-NUM]", _EMAIL.sub("[MASKED-EMAIL]", text))


def extract_reply_address(sender: str) -> str:
    """Bare address from a `Name <addr>` / `addr` sender string ('' if none)."""
    match = _ADDR.search(sender or "")
    return match.group(1) if match else ""


def reply_subject(llm_subject: str, mail_subject: str) -> str:
    if llm_subject:
        return llm_subject
    base = (mail_subject or "").strip()
    return base if base.lower().startswith("re:") else f"Re: {base}".strip()


def build_send_argv(
    python: str, to: str, subject: str, body: str,
    attachments: tuple[str | Path, ...] = (),
    cc: str = "",
    bcc: tuple[str, ...] = (),
    body_html: str = "",
) -> tuple[str, ...]:
    """The exact mailon send argv frozen into a draft (W0-7b contract)."""
    argv = (python, "-m", "mailon.main", "send", "--to", to)
    if cc:
        argv += ("--cc", cc)
    if bcc:
        argv += ("--bcc", ",".join(bcc))
    argv += ("--subject", subject, "--body", body)
    if body_html:  # the reply in the owner's font + the quote (mail_quote.html_body)
        argv += ("--body-html", body_html)
    for path in attachments:
        argv += ("--attachment", str(path))
    return argv + ("--confirm-send", "--json")


def external_effect_action_hash(argv: tuple[str, ...]) -> str:
    """Hash-parity with automation.interop.external_effect_gate._action_hash."""
    payload = {
        "action": "external_effect.tool_call",
        "arguments": {"command": shlex.join(argv)},
        "target_id": EXTERNAL_EFFECT_TARGET_ID,
        "tool_name": EXTERNAL_EFFECT_TOOL,
    }
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def draft_sha256(record: dict) -> str:
    """Content hash binding a draft to the exact reply it will send."""
    bound = {
        key: record[key]
        for key in ("argv", "body", "subject", "to", "uid")
    }
    # Old approved hashes keep their original fields; new drafts do not write them.
    if "sensitive" in record:
        bound["sensitive"] = record["sensitive"]
    if "cc" in record:
        bound["cc"] = record["cc"]
    if "bcc" in record:
        bound["bcc"] = record["bcc"]
    if "quote" in record:  # the answered mail sent below the body (mail_quote)
        bound["quote"] = record["quote"]
    for key in ("quote_html", "quote_format"):  # the HTML quote and whether it kept the format
        if key in record:
            bound[key] = record[key]
    if "forward" in record:  # a forward's source marker (mail_forward)
        bound["forward"] = record["forward"]
    if "drive_share" in record:  # file sha256, Drive folder, people, role (mail_drive_share)
        bound["drive_share"] = record["drive_share"]
    # Keep legacy/no-attachment draft hashes byte-for-byte compatible while
    # binding every new attachment draft to its full manifest.
    if "attachments" in record:
        bound["attachments"] = record["attachments"]
        bound["attachment_manifest_sha256"] = record["attachment_manifest_sha256"]
    if record.get("provider") == "gmail":
        bound["approval_action_hash"] = record["approval_action_hash"]
        bound["gmail_approval_snapshot"] = record["gmail_approval_snapshot"]
        bound["provider"] = "gmail"
        bound["reply_target"] = record["reply_target"]
        bound["sender_account"] = record["sender_account"]
    canonical = json.dumps(bound, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def render_approvals_message(
    draft: dict[str, JsonValue], *, destination: ApprovalRenderDestination = ApprovalRenderDestination.CONSOLE,
    instruction: str = "",
) -> str:
    """Replay stored wording. Missing versions are frozen v1, never upgraded in place."""
    version = draft.get("render_version", "1")
    if version == "1":
        return _render_v1(draft, destination=destination, instruction=instruction)
    if version == "4":
        import mail_card_compact

        return mail_card_compact.render(_render_v1(draft, destination=destination), instruction)
    from automation.interop.approval_card import CardRenderError
    if version not in ("2", "3"):
        raise CardRenderError("unknown mail card render version")
    from automation.interop import owner_message
    from triage_card import message
    if not callable(getattr(owner_message, "render", None)):
        raise CardRenderError("owner envelope unavailable")
    try:
        body = owner_message.render(
            message(str(draft["id"]), _render_v1(draft, destination=destination), instruction,
                    render_version="owner-ko-v2" if version == "3" else "owner-ko-v1"),
            destination=owner_message.Ref(scope="self"),
        )
    except owner_message.OwnerMessageError as error:
        raise CardRenderError("mail envelope cannot render") from error
    binding = f"- draft: `{draft['id']}` sha256: `{draft['sha256']}`"
    if draft.get("provider") == "gmail":
        binding += f"\n- action hash: `{draft['approval_action_hash']}`"
    return f"{body}\n{binding}"


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
