"""Single shared Hermes LLM client — the only model call path in this repository.

Every automation, skill, cron and batch caller goes through :class:`CodexClient`.
The model is not chosen here. The argv carries neither ``--provider`` nor ``-m``, so
Hermes answers with the account's own ``~/.hermes/config.yaml``: ``model.provider`` /
``model.default`` as the main model and, when that cannot answer (quota, rate limit,
auth, transport), the ``fallback_providers`` chain — the same pair the Discord gateway
uses (owner decision 2026-09-29: one place decides every model). Pinning a model in
code once left three skills asking for a model the subscription had stopped serving,
and every call fell through to the fallback without anyone choosing that.
That is why the call does NOT pass ``--ignore-user-config``: that flag drops the
user config and, with it, the fallback chain. This client makes one subprocess call
and never retries on its own; when the whole Hermes chain fails, the error propagates.
The release-installed omo-relay mode instead uses workstation SSH stdin and one
transport retry. Its model is selected solely by the workstation's omo settings.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

__all__ = [
    "CONFIGURED_MODEL",
    "DEFAULT_TIMEOUT",
    "PROVIDER",
    "CodexClient",
    "CodexError",
    "CodexUnavailableError",
    "Served",
    "UNKNOWN",
    "complete",
    "complete_served",
    "served_route",
]

#: Label of the one shared route (the account's Hermes config), not a model choice.
PROVIDER: Final = "openai-codex"
#: What routing logs record as the requested model: the config decides, the usage
#: report (``served_model``) says what actually answered.
CONFIGURED_MODEL: Final = "hermes-config"
DEFAULT_TIMEOUT: Final = 180.0

BINARY_ENV: Final = "AUTOPHAGY_HERMES_BIN"

#: Recorded when Hermes wrote no readable usage report — never a guess.
UNKNOWN: Final = "unknown"

_USAGE_FLAG: Final = "--usage-file"
_TASK_MODE: Final = "todo"
#: Safe default the child subprocess always gets, regardless of the caller's own PATH.
_CHILD_PATH: Final = "/usr/bin:/bin"
_RELATIVE_BINARY: Final = (".local", "bin", "hermes")
_STDERR_TAIL_LIMIT: Final = 200
_SECRET: Final = re.compile(
    r"(?:sk-[A-Za-z0-9_-]+|Bearer\s+\S+|eyJ[A-Za-z0-9_.-]{16,}|[A-Za-z0-9_-]{32,})"
)


@dataclass(frozen=True, slots=True)
class Served:
    """One answer plus the route that actually produced it.

    The account config names the main model, but Hermes may answer from its
    ``fallback_providers`` chain. Routing logs that record only the requested
    primary therefore cannot say where a prompt really went;
    ``provider``/``model`` here come from Hermes' own ``--usage-file`` report.
    """

    text: str
    provider: str
    model: str


def served_route(usage_path: Path) -> tuple[str, str]:
    """``(provider, model)`` from a Hermes ``--usage-file`` report; ``unknown`` when absent."""
    try:
        report = json.loads(usage_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return UNKNOWN, UNKNOWN
    if not isinstance(report, dict):
        return UNKNOWN, UNKNOWN
    provider = str(report.get("provider") or "").strip() or UNKNOWN
    model = str(report.get("model") or "").strip() or UNKNOWN
    return provider, model


class CodexError(RuntimeError):
    """This Codex request failed."""


class CodexUnavailableError(CodexError):
    """No route could answer: Codex OAuth and the configured fallback chain all failed."""


@dataclass(frozen=True, slots=True)
class CodexClient:
    """Non-interactive Hermes caller bound to one binary and home; the config picks the model."""

    binary: str
    home: str
    timeout: float = DEFAULT_TIMEOUT
    relay: bool = False

    @classmethod
    def from_environment(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> CodexClient:
        source: Mapping[str, str] = os.environ if env is None else env
        home = (source.get("HOME") or "").strip()
        if not home:
            raise CodexUnavailableError("HOME is unset; Codex OAuth credentials cannot be located")
        from automation.omo_relay_policy import enabled

        try:
            relay = enabled(Path(home))
        except (OSError, ValueError):
            raise CodexUnavailableError("omo relay mode is unreadable or invalid") from None
        return cls(binary="ssh" if relay else _resolve_binary(source, home),
                   home=home, timeout=timeout, relay=relay)

    def argv(self, prompt: str) -> list[str]:
        if self.relay:
            from automation.omo_relay.client import argv

            return argv(Path(self.home))
        return [self.binary, "-z", prompt, "-t", _TASK_MODE]

    def complete(self, prompt: str, *, timeout: float | None = None) -> str:
        """Run one completion (the config's main model first) and return its stripped stdout.

        Hermes may answer from the configured ``fallback_providers`` chain when Codex
        cannot; this client itself never retries. Raises :class:`CodexUnavailableError`
        when no route answers and :class:`CodexError` when it answers with nothing.
        """
        if self.relay:
            return self.complete_served(prompt, timeout=timeout).text
        return self._run(self.argv(prompt), timeout)

    def complete_served(self, prompt: str, *, timeout: float | None = None) -> Served:
        """Like :meth:`complete`, plus the provider and model that actually answered.

        The report lives in a private temp file that is removed after the call; a
        missing or unreadable report yields ``unknown`` instead of assuming Codex.
        """
        if self.relay:
            from automation.omo_relay.client import complete

            return complete(self.home, prompt, self.timeout if timeout is None else timeout)
        handle, name = tempfile.mkstemp(prefix="autophagy-usage-", suffix=".json")
        os.close(handle)
        usage = Path(name)
        try:
            text = self._run([*self.argv(prompt), _USAGE_FLAG, str(usage)], timeout)
            provider, model = served_route(usage)
        finally:
            usage.unlink(missing_ok=True)
        return Served(text=text, provider=provider, model=model)

    def _run(self, argv: list[str], timeout: float | None) -> str:
        limit = self.timeout if timeout is None else timeout
        try:
            completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
                argv,
                cwd=tempfile.gettempdir(),
                env={"HOME": self.home, "PATH": _child_path(self.home)},
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=limit,
            )
        except subprocess.TimeoutExpired:
            raise CodexUnavailableError(f"Codex call timed out after {limit:g}s") from None
        except OSError as error:
            raise CodexUnavailableError(
                f"Codex binary could not be executed: {error.__class__.__name__}"
            ) from None
        if completed.returncode != 0:
            tail = _redacted_tail(completed.stderr)
            raise CodexUnavailableError(f"Codex call failed (rc={completed.returncode}): {tail}")
        answer = (completed.stdout or "").strip()
        if not answer:
            raise CodexError("Codex returned an empty completion")
        return answer


def complete(
    prompt: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    env: Mapping[str, str] | None = None,
) -> str:
    """Convenience one-shot completion for callers that hold no client."""
    return CodexClient.from_environment(env, timeout=timeout).complete(prompt)


def complete_served(
    prompt: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    env: Mapping[str, str] | None = None,
) -> Served:
    """One-shot :meth:`CodexClient.complete_served` for callers that hold no client."""
    return CodexClient.from_environment(env, timeout=timeout).complete_served(prompt)


def _child_path(home: str) -> str:
    """Child subprocess PATH: the account's own ``~/.local/bin`` ahead of the safe default.

    ``hermes`` itself (see ``_RELATIVE_BINARY``) and the ``claude`` CLI that the
    account's configured ``fallback_providers`` chain shells out to both live under
    ``{home}/.local/bin``. A bare ``/usr/bin:/bin`` cannot find either, so the fallback
    silently fails to a ``CodexUnavailableError`` even when it is fully configured and
    would otherwise work. This prepends only that one account-owned directory — never
    the caller's full inherited PATH and never any other system directory.
    """
    return f"{home}/.local/bin:{_CHILD_PATH}"


def _resolve_binary(env: Mapping[str, str], home: str) -> str:
    override = (env.get(BINARY_ENV) or "").strip()
    if override:
        return override
    candidate = Path(home, *_RELATIVE_BINARY)
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    found = shutil.which("hermes", path=env.get("PATH"))
    if found:
        return found
    raise CodexUnavailableError("hermes binary not found; Codex OAuth is unavailable")


def _redacted_tail(stderr: str | None) -> str:
    collapsed = " ".join((stderr or "").split())
    if not collapsed:
        return "<no stderr>"
    return _SECRET.sub("<redacted>", collapsed)[-_STDERR_TAIL_LIMIT:]
