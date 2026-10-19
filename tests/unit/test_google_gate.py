"""Real CLI renderer boundaries, not a second copy of its JSON builder."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from automation.doctor.guides import GWS_SCOPES

REPO = Path(__file__).resolve().parents[2]


def render_args() -> list[str]:
    return [
        "--relay-uid", "2011", "--agent-uid", "2012", "--operator-uid", "2013",
        "--owner-id", "12345678901234567", "--owner-guild", "22345678901234567",
        "--owner-email", "owner@example.org", "second@example.org",
        "--approval-channel", "32345678901234567",
        "--bot-token-file", "/var/lib/dori-gate/owner-bot-token",
    ]


def render(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "automation.google_gate", "render-config", *args],
        cwd=REPO, env=os.environ.copy(), capture_output=True, text=True, check=False,
    )


def test_renderer_when_valid_separates_listener_and_grants() -> None:
    # Given
    args = render_args()
    # When
    result = render(args)
    # Then
    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)
    assert config["listen"] == {"connect": None, "google": 3129, "discord": None}
    assert (config["clients"], config["admins"], config["token_clients"]) == ([2011], [2013], [2012])
    assert config["grants"]["jobs"]["scopes"] == GWS_SCOPES.split(",")
    assert len(config["grants"]["jobs"]["scopes"]) == 12
    assert config["grants"]["jobs"]["token_file"] == "jobs-token.json"
    assert config["owner_emails"] == ["owner@example.org", "second@example.org"]
    assert not any(scope.endswith(("gmail.send", "gmail.modify")) for scope in config["google_scopes"])


@pytest.mark.parametrize(("flag", "value", "code"), [
    ("--relay-uid", "-1", "uid-invalid"),
    ("--agent-uid", "0", "uid-invalid"),
    ("--agent-uid", "4294967295", "uid-invalid"),
    ("--operator-uid", "2012", "uids-must-differ"),
    ("--owner-id", "123", "id-invalid"),
    ("--owner-guild", "9" * 21, "id-invalid"),
    ("--approval-channel", "x" * 17, "id-invalid"),
    ("--owner-email", "owner@example.org\nother@example.org", "email-invalid"),
    ("--bot-token-file", "relative", "path-invalid"),
])
def test_renderer_when_invalid_refuses_without_input_echo(flag: str, value: str, code: str) -> None:
    # Given
    args = render_args()
    args[args.index(flag) + 1] = value
    # When
    result = render(args)
    # Then
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.strip() == f"GOOGLE-GATE-REFUSED {code}"


def test_renderer_when_vault_incomplete_refuses() -> None:
    # Given / When
    result = render([*render_args(), "--vault-dir", "/var/lib/dori-gate/vault"])
    # Then
    assert result.returncode == 2
    assert "vault-incomplete" in result.stderr
