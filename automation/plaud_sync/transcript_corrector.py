"""Opt-in node command boundary; raw transcript evidence is never written here."""

from __future__ import annotations

import json
import math
import os
import signal
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol, runtime_checkable

from .mcp_transport import JsonValue

DEFAULT_TIMEOUT_SECONDS: Final = 300.0
MAX_TIMEOUT_SECONDS: Final = 3600.0
SETTINGS_RELPATH: Final = Path(".hermes/plaud-sync/transcript-corrector.json")


@dataclass(frozen=True, slots=True)
class TranscriptContext:
    recording_id: str
    note_title: str


class Corrector(Protocol):
    def __call__(self, markdown: str, context: TranscriptContext) -> str: ...


@runtime_checkable
class CorrectionEffects(Protocol):
    """Optional transcription effect, independent of existing pure-step fakes."""

    @property
    def transcript_corrector(self) -> Corrector: ...


@dataclass(frozen=True, slots=True)
class CommandConfig:
    argv: tuple[str, ...]
    timeout_seconds: float


@dataclass(frozen=True, slots=True)
class ConfigError(Exception):
    reason: str


def _parse_config(
    text: str, *, decode: Callable[[str], JsonValue] = json.loads,
) -> CommandConfig:
    value = decode(text)
    if not isinstance(value, dict) or set(value) - {"argv", "timeout_seconds"}:
        raise ConfigError("invalid-config")
    argv = value.get("argv")
    if not isinstance(argv, list) or not argv:
        raise ConfigError("invalid-argv")
    arguments: list[str] = []
    for argument in argv:
        if not isinstance(argument, str) or not argument.strip() or "\0" in argument:
            raise ConfigError("invalid-argv")
        arguments.append(argument)
    timeout = value.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
        or timeout > MAX_TIMEOUT_SECONDS
    ):
        raise ConfigError("invalid-timeout")
    return CommandConfig(tuple(arguments), float(timeout))


def preserves_structure(raw: str, corrected: str) -> bool:
    """Keep line count, header through first rule, blank lines and turn markers."""
    before, after = raw.splitlines(keepends=True), corrected.splitlines(keepends=True)
    if not corrected.strip() or len(before) != len(after):
        return False
    header = next((i + 1 for i, line in enumerate(before) if line.strip() == "---"), 0)
    if before[:header] != after[:header]:
        return False
    for original, replacement in zip(before[header:], after[header:], strict=True):
        if (
            not original.strip()
            or original.startswith(("#", "[", "- 화자:"))
            or replacement.startswith(("#", "[", "- 화자:"))
        ):
            if original != replacement:
                return False
        elif not replacement.strip():
            return False
    return True


@dataclass(frozen=True, slots=True)
class CommandCorrector:
    """One bounded local command; failures expose codes, never child output."""

    env: Mapping[str, str]

    def __call__(self, markdown: str, context: TranscriptContext) -> str:
        home = Path(self.env.get("HOME", str(Path.home())))
        try:
            text = (home / SETTINGS_RELPATH).read_text(encoding="utf-8")
        except FileNotFoundError:
            return markdown
        except (OSError, UnicodeError):
            return self._fallback(markdown, context, reason="config-read")
        try:
            config = _parse_config(text)
        except (ValueError, ConfigError, OverflowError):
            return self._fallback(markdown, context, reason="invalid-config")
        child_env = {
            **self.env,
            "PLAUD_RECORDING_ID": context.recording_id,
            "PLAUD_NOTE_TITLE": context.note_title,
        }
        try:
            with subprocess.Popen(  # noqa: S603 - explicit node-owned argv, no shell
                config.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, env=child_env, start_new_session=True,
            ) as process:
                try:
                    stdout, _ = process.communicate(
                        markdown.encode("utf-8"), timeout=config.timeout_seconds
                    )
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    _ = process.communicate()
                    return self._fallback(markdown, context, reason="timeout")
                if process.returncode != 0:
                    return self._fallback(markdown, context, reason=f"exit:{process.returncode}")
            corrected = stdout.decode("utf-8")
        except (OSError, UnicodeError, ValueError):
            return self._fallback(markdown, context, reason="command-error")
        if not preserves_structure(markdown, corrected):
            return self._fallback(markdown, context, reason="invalid-output")
        return corrected

    @staticmethod
    def _fallback(markdown: str, context: TranscriptContext, *, reason: str) -> str:
        print(
            f"PLAUD-TRANSCRIPT-CORRECTOR recording_id={context.recording_id} fallback reason={reason}",
            file=sys.stderr,
        )
        return markdown
