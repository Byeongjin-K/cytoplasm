"""Release-written node setting; absence keeps the two-account receipt contract."""
from __future__ import annotations

import json
from pathlib import Path

from .choices import ChoiceError


def peer_retired() -> bool:
    """Only an explicit boolean opts out; unreadable or malformed settings refuse work."""
    path = Path.home() / ".hermes/memory-relocate/peer-mode.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    match value:
        case {"peer_retired": bool() as retired}:
            return retired
        case _:
            raise ChoiceError("peer_mode_invalid")
