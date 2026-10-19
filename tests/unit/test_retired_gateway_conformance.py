"""No retired gateway code, settings or endpoint may return outside its removal."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
REMOVAL = {"automation/gateway_retirement.py", "automation/gateway_retirement_policy.py"}
FORBIDDEN = re.compile(rb"litellm|127\.0\.0\.1:4000|localhost:4000", re.IGNORECASE)


def test_shipped_runtime_has_no_retired_gateway() -> None:
    listed = subprocess.run(
        ("git", "ls-files", "-co", "--exclude-standard", "-z", "--", "automation", "skills", "configs"),
        cwd=REPO, check=True, capture_output=True, timeout=10,
    ).stdout
    violations = []
    for raw in set(filter(None, listed.split(b"\0"))):
        relative = raw.decode()
        path = REPO / relative
        if relative not in REMOVAL and path.is_file() and FORBIDDEN.search(path.read_bytes()):
            violations.append(relative)
    assert sorted(violations) == []


@pytest.mark.parametrize("value", [b"LiteLLM", b"LITELLM_AGENT_KEY", b"127.0.0.1:4000", b"localhost:4000"])
def test_reintroduction_is_detected(value: bytes) -> None:
    assert FORBIDDEN.search(value)
