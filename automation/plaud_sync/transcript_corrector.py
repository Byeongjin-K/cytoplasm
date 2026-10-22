"""Opt-in node command boundary; raw transcript evidence is never written here."""

from __future__ import annotations

import difflib
import json
import math
import os
import re
import signal
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol, runtime_checkable

from .mcp_transport import JsonValue

DEFAULT_TIMEOUT_SECONDS: Final = 300.0
MAX_TIMEOUT_SECONDS: Final = 3600.0
SETTINGS_RELPATH: Final = Path(".hermes/plaud-sync/transcript-corrector.json")
#: The command may write its evidence here (JSON); the table itself is always the byte diff.
REPORT_ENV: Final = "PLAUD_CORRECTION_REPORT"
REPORT_LIMIT: Final = 1024 * 1024
_STRUCTURE_PREFIXES: Final = ("#", "[", "- 화자:")
#: `[00:01:02]` — a turn's time, as speechtotext and the dori meeting path write it.
_TIMESTAMP: Final = re.compile(r"\[(?:\d{1,2}|--)(?::(?:\d{2}|--)){1,2}(?:[.,]\d+)?\]")
#: A speaker header after the time (`화자1`, `화자1 · 이름`, `speaker_1`): the whole line is structure.
_SPEAKER_HEADER: Final = re.compile(r"[^\s:\[\]]+(?:[ \t]+·[ \t]+[^:\[\]\n]+)?")
#: An inline label before the words (`화자1: `) stays with the frozen prefix.
_INLINE_LABEL: Final = re.compile(r"[^:\[\]\n]{1,40}:[ \t]?")


@dataclass(frozen=True, slots=True)
class TranscriptContext:
    recording_id: str
    note_title: str


@dataclass(frozen=True, slots=True)
class TranscriptChange:
    """One changed run of words: the turn's time, the raw and corrected words, and why."""

    at: str
    before: str
    after: str
    evidence: str


@dataclass(frozen=True, slots=True)
class CorrectedTranscript:
    """The transcript to freeze and what differs from the raw one (empty when nothing changed)."""

    text: str
    changes: tuple[TranscriptChange, ...] = ()
    rejected: int = 0


class Corrector(Protocol):
    def __call__(self, markdown: str, context: TranscriptContext) -> CorrectedTranscript: ...


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


def header_end(lines: list[str]) -> int:
    """Index after the first `---` line (0 when there is none): the header is never edited."""
    return next((i + 1 for i, line in enumerate(lines) if line.strip() == "---"), 0)


def editable_prefix(line: str) -> str | None:
    """The frozen start of a body line, or None when the whole line is structure.

    Words after a turn's time (`[00:01:02] 화자1: …` or `[00:01:02] …`) are speech and may be
    corrected; the time and label stay byte-identical. A bare speaker header, a heading, a
    legend or any other bracket line stays whole.
    """
    if not line.strip() or line.startswith(("#", "- 화자:")):
        return None
    if not line.startswith("["):
        return ""
    stamp = _TIMESTAMP.match(line)
    if stamp is None:
        return None
    rest = line[stamp.end():]
    gap = len(rest) - len(rest.lstrip(" \t"))
    words = rest[gap:].rstrip("\r\n")
    if not words or _SPEAKER_HEADER.fullmatch(words):
        return None
    label = _INLINE_LABEL.match(words)
    prefix = line[: stamp.end() + gap + (label.end() if label else 0)]
    return prefix if line[len(prefix):].strip() else None


def preserves_structure(raw: str, corrected: str) -> bool:
    """Keep line count, header through first rule, blank lines, turn markers and labels."""
    before, after = raw.splitlines(keepends=True), corrected.splitlines(keepends=True)
    if not corrected.strip() or len(before) != len(after):
        return False
    header = header_end(before)
    if before[:header] != after[:header]:
        return False
    for original, replacement in zip(before[header:], after[header:], strict=True):
        prefix = editable_prefix(original)
        if prefix is None:
            if original != replacement:
                return False
            continue
        if not replacement.startswith(prefix) or not replacement[len(prefix):].strip():
            return False
        if not prefix and replacement.startswith(_STRUCTURE_PREFIXES):
            return False
    return True


@dataclass(frozen=True, slots=True)
class _Evidence:
    before: str
    after: str
    reason: str


def _parse_report(text: str) -> tuple[tuple[_Evidence, ...], int]:
    value: JsonValue = json.loads(text)
    if not isinstance(value, dict):
        raise ConfigError("invalid-report")
    items, rejected = value.get("evidence", []), value.get("rejected", 0)
    if not isinstance(items, list) or isinstance(rejected, bool) or not isinstance(rejected, int):
        raise ConfigError("invalid-report")
    parsed: list[_Evidence] = []
    for item in items:
        if not isinstance(item, dict):
            raise ConfigError("invalid-report")
        fields = (item.get("before"), item.get("after"), item.get("evidence"))
        if not all(isinstance(field, str) for field in fields):
            raise ConfigError("invalid-report")
        before, after, reason = (str(field) for field in fields)
        parsed.append(_Evidence(before, after, reason))
    return tuple(parsed), max(rejected, 0)


def _reason(before: str, after: str, evidence: tuple[_Evidence, ...]) -> str:
    """The reported reason for this change; the applied word may differ (a fitted particle)."""
    exact = (
        item.reason for item in evidence
        if item.before and item.after and item.before in before and item.after in after
    )
    same_original = (item.reason for item in evidence if item.before and item.before in before)
    return next(exact, "") or next(same_original, "")


def changes_between(
    raw: str, corrected: str, evidence: tuple[_Evidence, ...] = ()
) -> tuple[TranscriptChange, ...]:
    """Every changed word run, read from the bytes — a command cannot hide a change.

    Call only after `preserves_structure`, which guarantees the same line count.
    """
    changes: list[TranscriptChange] = []
    at = ""
    for original, replacement in zip(
        raw.splitlines(), corrected.splitlines(), strict=True
    ):
        stamp = _TIMESTAMP.match(original)
        if stamp is not None:
            at = stamp.group(0)[1:-1]
        if original == replacement:
            continue
        left, right = original.split(), replacement.split()
        matcher = difflib.SequenceMatcher(None, left, right, autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue
            before, after = " ".join(left[i1:i2]), " ".join(right[j1:j2])
            changes.append(TranscriptChange(at, before, after, _reason(before, after, evidence)))
    return tuple(changes)


@dataclass(frozen=True, slots=True)
class CommandCorrector:
    """One bounded local command; failures expose codes, never child output."""

    env: Mapping[str, str]

    def __call__(self, markdown: str, context: TranscriptContext) -> CorrectedTranscript:
        home = Path(self.env.get("HOME", str(Path.home())))
        try:
            text = (home / SETTINGS_RELPATH).read_text(encoding="utf-8")
        except FileNotFoundError:
            return CorrectedTranscript(markdown)
        except (OSError, UnicodeError):
            return self._fallback(markdown, context, reason="config-read")
        try:
            config = _parse_config(text)
        except (ValueError, ConfigError, OverflowError):
            return self._fallback(markdown, context, reason="invalid-config")
        with tempfile.TemporaryDirectory(prefix="plaud-correction-") as scratch:
            report = Path(scratch) / "report.json"
            child_env = {
                **self.env,
                "PLAUD_RECORDING_ID": context.recording_id,
                "PLAUD_NOTE_TITLE": context.note_title,
                REPORT_ENV: str(report),
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
                        return self._fallback(
                            markdown, context, reason=f"exit:{process.returncode}"
                        )
                corrected = stdout.decode("utf-8")
            except (OSError, UnicodeError, ValueError):
                return self._fallback(markdown, context, reason="command-error")
            if not preserves_structure(markdown, corrected):
                return self._fallback(markdown, context, reason="invalid-output")
            try:
                evidence, rejected = (
                    _parse_report(report.read_text(encoding="utf-8")[:REPORT_LIMIT])
                    if report.exists() else ((), 0)
                )
            except (OSError, UnicodeError, ValueError, ConfigError):
                return self._fallback(markdown, context, reason="invalid-report")
        return CorrectedTranscript(
            corrected, changes_between(markdown, corrected, evidence), rejected
        )

    @staticmethod
    def _fallback(markdown: str, context: TranscriptContext, *, reason: str) -> CorrectedTranscript:
        print(
            f"PLAUD-TRANSCRIPT-CORRECTOR recording_id={context.recording_id} fallback reason={reason}",
            file=sys.stderr,
        )
        return CorrectedTranscript(markdown)
