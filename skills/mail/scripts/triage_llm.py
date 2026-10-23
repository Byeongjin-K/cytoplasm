"""Mail model calls through the account-configured shared client and fallback chain."""

from __future__ import annotations

import fcntl
import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import triage_core

CODEX_PROVIDER = "openai-codex"  # legacy requested-route label; served_* records actual model
CALL_TIMEOUT_S = 600.0  # unchanged per-call budget of the mail pipeline
REPO_ROOT_ENV = "AUTOPHAGY_REPO_ROOT"
MOUNTED_REPO_ROOT = Path("/srv/autophagy-agent-current")


class LlmCallError(RuntimeError):
    """Raised when an LLM call fails at the transport level."""


class LlmUnavailableError(LlmCallError):
    """Raised when the Codex OAuth tier itself is unavailable, not this request.

    Missing OAuth credentials, quota exhaustion and transport failures mean every
    following call fails the same way. Before the migration this type selected the
    non-GLM degrade path; now it is the fail-closed signal — the caller refuses the
    run instead of routing the prompt anywhere else.
    """


def _repo_root() -> Path:
    """Locate the repository without importing it (skills stay import-light)."""
    override = os.environ.get(REPO_ROOT_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    for parent in Path(__file__).resolve().parents:
        if (parent / "automation" / "skill_mount.py").is_file():
            return parent
    return MOUNTED_REPO_ROOT


def _codex_module() -> ModuleType:
    """Import the shared Codex client lazily; an ImportError REFUSES the call.

    Fail closed exactly like the approval_lifecycle rule in ``skills/AGENTS.md``:
    a skill that cannot reach the governed client does not improvise its own
    provider call.
    """
    root = str(_repo_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        import automation.codex_llm as codex_llm
    except ImportError:
        raise LlmUnavailableError(
            "automation.codex_llm 임포트 실패 — 공유 모델 경로 없음 (호출 거부)"
        ) from None
    return codex_llm


def codex_model() -> str:
    """The requested-model label: the account's Hermes config picks the model."""
    return _codex_module().CONFIGURED_MODEL


def _log_path() -> Path:
    return Path(
        os.environ.get("TRIAGE_LLM_LOG", "~/.hermes/mail-triage/logs/llm-calls.jsonl")
    ).expanduser()


def _append_record(record: dict) -> None:
    """Append one masked JSON line to the routing log (0600, flock-serialized)."""
    path = _log_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    with path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(line + "\n")
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    path.chmod(0o600)


def _log_call(*, model: str, purpose: str, uid_opaque: str, served: Any) -> None:
    """Append one masked routing line: the requested primary and the route that answered.

    ``provider``/``model`` stay the pinned primary so the QA call-count surface still
    parses; ``served_provider``/``served_model`` come from Hermes' usage report and
    differ when the account's ``fallback_providers`` chain answered (``unknown`` when
    Hermes wrote no report).
    """
    _append_record(
        {
            "model": model,
            "provider": CODEX_PROVIDER,
            "purpose": purpose,
            "served_model": served.model,
            "served_provider": served.provider,
            "timestamp": triage_core.utc_now(),
            "uid": uid_opaque,
        }
    )


def log_failure(*, purpose: str, uid_opaque: str, error: BaseException) -> None:
    """Record one masked ``<purpose>_failed`` line next to the successful calls.

    The digest runs under a no-agent cron that drops stderr, so this line is the
    only forensic trace of a per-mail LLM failure. Only the exception class and a
    redacted, clipped message are kept — never subject, sender, body or addresses.
    """
    _append_record(
        {
            "error": f"{type(error).__name__}: {triage_core.redact(str(error))[:160]}",
            "purpose": f"{purpose}_failed",
            "timestamp": triage_core.utc_now(),
            "uid": uid_opaque,
        }
    )


def call_codex(prompt: str, *, timeout: float = CALL_TIMEOUT_S) -> Any:
    """One completion on the shared route (Codex OAuth first) — no client retry.

    The shared client owns the argv (account config
    honored so Hermes' ``fallback_providers`` chain applies), the child
    environment and the success rule (rc 0 AND non-empty stdout). Returns the shared
    client's ``Served`` (``.text`` plus the provider/model that answered).
    """
    codex = _codex_module()
    try:
        client = codex.CodexClient.from_environment(timeout=timeout)
        return client.complete_served(prompt)
    except codex.CodexUnavailableError as error:
        raise LlmUnavailableError(_failure_text(error)) from None
    except codex.CodexError as error:
        raise LlmCallError(_failure_text(error)) from None


def _failure_text(error: BaseException) -> str:
    return f"codex 호출 실패: {triage_core.redact(str(error))[:200]}"


def classify(
    *, subject: str, sender: str, body: str, uid_opaque: str, prompt_path: Path,
) -> tuple[triage_core.Classification, str]:
    """Classify mail actions on the account-configured shared route."""
    prompt = triage_core.build_prompt(
        triage_core.load_prompt_template(prompt_path),
        subject=subject, sender=sender, body=body,
    )
    served = call_codex(prompt)
    _log_call(model=codex_model(), purpose="classify",
              uid_opaque=uid_opaque, served=served)
    raw = served.text
    return triage_core.parse_classification(raw), CODEX_PROVIDER


def draft_reply(
    *, subject: str, sender: str, body: str, uid_opaque: str,
    prompt_path: Path, instruction: str = "", evidence: str = "",
) -> tuple[str, str, str]:
    """Korean final-text reply through the shared account route."""
    import mail_reply_format

    closing, template = mail_reply_format.closing_template(triage_core.load_prompt_template(prompt_path))
    prompt = triage_core.build_prompt(
        template,
        subject=subject, sender=sender, body=body, instruction=instruction,
        evidence=evidence,
    )
    served = call_codex(prompt)
    _log_call(model=codex_model(), purpose="draft_reply",
              uid_opaque=uid_opaque, served=served)
    raw = served.text
    llm_subject, reply_body = triage_core.parse_reply(raw)
    reply_body = mail_reply_format.closing_body(reply_body, closing)
    return triage_core.reply_subject(llm_subject, subject), reply_body, CODEX_PROVIDER


def summarize(
    *, subject: str, sender: str, body: str, uid_opaque: str, prompt_path: Path,
) -> str:
    """One-line Korean digest summary through the shared account route."""
    prompt = triage_core.build_prompt(
        triage_core.load_prompt_template(prompt_path),
        subject=subject, sender=sender, body=body,
    )
    served = call_codex(prompt)
    _log_call(model=codex_model(), purpose="digest_summary",
              uid_opaque=uid_opaque, served=served)
    raw = served.text
    return triage_core.parse_digest_summary(raw)
