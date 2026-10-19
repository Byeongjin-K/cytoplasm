"""Non-secret per-account shim marker."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from automation.google_gate.token_client import GateError, port_number


@dataclass(frozen=True, slots=True)
class Mode:
    port: int
    installed_at: str
    sha256: str


def marker(home: Path) -> Path:
    return home / ".config/autophagy/google-gate.json"


def enabled(home: Path) -> bool:
    try:
        marker(home).lstat()
    except FileNotFoundError:
        return False
    except OSError:
        # An unreadable configured mode must not fall back to account credentials.
        return True
    return True


def read_mode(home: Path) -> Mode:
    try:
        raw = json.loads(marker(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise GateError("marker-invalid") from None
    match raw:
        case {"port": int() as port, "installed_at": str() as installed,
              "sha256": str() as digest} if (
                  installed and re.fullmatch("[0-9a-f]{64}", digest)
              ):
            return Mode(port_number(port), installed, digest)
        case _:
            raise GateError("marker-invalid")
