"""One declared config profile: existing memory caps plus the recall provider."""
from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final

from automation import hermes_memory_config as limits

ALGORITHM: Final = "hermes-memory-profile-with-recall-v1"
MARKER: Final = "HERMES-MEMORY-PROFILE"
PROVIDER: Final = "autophagy_recall"
_NAME: Final = re.compile(r"[a-z][a-z0-9_-]{0,99}")


@dataclass(frozen=True, slots=True)
class Profile:
    limits: Mapping[str, int | None]
    provider: str | None

    def identity(self) -> str:
        return f"{limits.identity(self.limits)},provider={self.provider or '-'}"


def read_profile(text: str) -> Profile:
    """Parse the same flat memory mapping as the caps writer, without a second YAML parser."""
    values = limits.read_limits(text)
    lines = text.splitlines()
    if any(line.strip() in {"---", "..."} for line in lines):
        raise limits.ConfigShapeError("multiple YAML documents are unsupported")
    span = limits._section(lines)
    provider = None
    if span is not None:
        _, found = limits._children(lines, span)
        if "provider" in found:
            child = limits._CHILD.match(lines[found["provider"]])
            raw = child[3].split(" #", 1)[0].strip() if child else ""
            if raw not in {"", "null", "~"}:
                provider = limits._value(raw)
                if not _NAME.fullmatch(provider):
                    raise limits.ConfigShapeError("memory.provider is not a provider name")
    return Profile(MappingProxyType(values), provider)


def wanted_identity() -> str:
    return Profile(limits.LIMITS, PROVIDER).identity()


def apply_profile(text: str) -> str:
    """Compose both selections before one existing atomic config write."""
    before = read_profile(text)
    if before.identity() == wanted_identity():
        return text
    return limits.apply_memory_values(text, {**limits.LIMITS, "provider": PROVIDER})


def main(argv: Sequence[str] | None = None, *, home: Path | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m automation.memory_provider.config")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--show", action="store_true")
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    root = (home or Path.home()) / ".hermes"
    path = root / "config.yaml"
    try:
        text = path.read_text(encoding="utf-8")
        current = read_profile(text)
    except (OSError, UnicodeError, limits.ConfigShapeError) as error:
        print(f"{MARKER}-ERROR reason={error.__class__.__name__}", file=sys.stderr)
        return 3
    if args.show:
        print(f"{MARKER} have={current.identity()}")
        return 0
    in_sync = current.identity() == wanted_identity()
    if args.check:
        print(f"{MARKER} {'ok' if in_sync else 'drift'} have={current.identity()} want={wanted_identity()}")
        return 0 if in_sync else 1
    if current.provider not in {None, "builtin", PROVIDER}:
        print(f"{MARKER}-REFUSED reason=external-provider-selected", file=sys.stderr)
        return 7
    if in_sync:
        print(f"{MARKER} unchanged have={current.identity()}")
        return 0
    try:
        if limits.lowering_blocked(current.limits, root / "memories"):
            print(f"{MARKER}-REFUSED reason=memory-above-lowered-cap", file=sys.stderr)
            return 7
        limits._write(path, apply_profile(text))
        after = read_profile(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, limits.ConfigShapeError) as error:
        print(f"{MARKER}-ERROR reason={error.__class__.__name__}", file=sys.stderr)
        return 3
    if after.identity() != wanted_identity():
        print(f"{MARKER}-ERROR reason=readback", file=sys.stderr)
        return 5
    print(f"{MARKER} applied have={after.identity()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
