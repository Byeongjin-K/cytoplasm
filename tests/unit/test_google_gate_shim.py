"""Install/restore and execute the real isolated shim against a loopback gate."""
from __future__ import annotations

import json
import os
import socket
import subprocess
from pathlib import Path

import pytest

from automation.google_gate.mode import marker
from google_gate_helpers import fake_gws, gate, payload, shim_cli


def test_shim_when_round_trip_restores_original_bytes_and_mode(tmp_path: Path) -> None:
    # Given
    paths = fake_gws(tmp_path)
    original, mode = paths.binary.read_bytes(), paths.binary.stat().st_mode
    # When
    first = shim_cli(tmp_path, "install")
    again = shim_cli(tmp_path, "install")
    status = shim_cli(tmp_path, "status")
    removed = shim_cli(tmp_path, "remove")
    # Then
    assert [r.returncode for r in (first, again, status, removed)] == [0] * 4
    assert first.stdout == again.stdout == status.stdout
    assert paths.binary.read_bytes() == original
    assert paths.binary.stat().st_mode == mode
    assert not marker(tmp_path).exists()
    assert shim_cli(tmp_path, "remove").stdout.strip() == "absent"


def test_shim_when_gate_mints_passes_token_only_in_environment(tmp_path: Path) -> None:
    # Given
    paths = fake_gws(tmp_path)
    record = tmp_path / "result.json"
    with gate(payload()) as port:
        assert shim_cli(tmp_path, "install", "--port", str(port)).returncode == 0
        assert marker(tmp_path).stat().st_mode & 0o777 == 0o600
        # When
        result = subprocess.run(
            [str(paths.binary), "drive", "--params", '{"x": "a b"}'],
            env={**os.environ, "HOME": str(tmp_path), "GWS_RECORD": str(record),
                 "GOOGLE_WORKSPACE_CLI_TOKEN": "stale-input"},
            capture_output=True, text=True, check=False,
        )
    # Then
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ""
    assert json.loads(record.read_text()) == {
        "present": True, "length": 42, "argv": ["drive", "--params", '{"x": "a b"}'],
    }


@pytest.mark.parametrize(("status", "body", "reason"), [
    (403, b'{"error":"token-client-required"}', "http-403"),
    (503, b'{"error":"google-auth-required"}', "http-503"),
    (200, b"{}", "response-invalid"),
    (200, b'{"access_token":"x","expires_in":false,"scope":"a"}', "response-invalid"),
    (200, b'{"access_token":"x","expires_in":0,"scope":"a"}', "response-invalid"),
    (200, b"not-json", "response-invalid"),
])
def test_shim_when_gate_refuses_never_executes_gws(
    tmp_path: Path, status: int, body: bytes, reason: str,
) -> None:
    # Given
    paths = fake_gws(tmp_path)
    record = tmp_path / "result.json"
    with gate(body, status) as port:
        assert shim_cli(tmp_path, "install", "--port", str(port)).returncode == 0
        # When
        result = subprocess.run(
            [str(paths.binary), "auth", "status"],
            env={**os.environ, "HOME": str(tmp_path), "GWS_RECORD": str(record)},
            capture_output=True, text=True, check=False,
        )
    # Then
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.strip() == f"GOOGLE-GATE-TOKEN-FAIL {reason}"
    assert not record.exists()


def test_shim_when_gate_down_never_uses_old_credentials(tmp_path: Path) -> None:
    # Given: a bound but non-listening socket cannot accept a connection.
    paths = fake_gws(tmp_path)
    record = tmp_path / "result.json"
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        assert shim_cli(tmp_path, "install", "--port", str(reserved.getsockname()[1])).returncode == 0
        # When
        result = subprocess.run(
            [str(paths.binary), "calendar"],
            env={**os.environ, "HOME": str(tmp_path), "GWS_RECORD": str(record),
                 "GOOGLE_WORKSPACE_CLI_TOKEN": "stale-input"},
            capture_output=True, text=True, check=False,
        )
    # Then
    assert result.returncode == 2
    assert result.stderr.strip() == "GOOGLE-GATE-TOKEN-FAIL unreachable"
    assert not record.exists()


@pytest.mark.parametrize("action", ["install", "remove"])
def test_shim_when_foreign_replacement_refuses_to_overwrite(tmp_path: Path, action: str) -> None:
    # Given
    paths = fake_gws(tmp_path)
    assert shim_cli(tmp_path, "install").returncode == 0
    paths.binary.write_bytes(b"foreign-file\n")
    # When
    result = shim_cli(tmp_path, action)
    # Then
    assert result.returncode == 2
    assert paths.binary.read_bytes() == b"foreign-file\n"
    assert paths.real.exists()


def test_shim_when_real_binary_changed_refuses_restore(tmp_path: Path) -> None:
    # Given
    paths = fake_gws(tmp_path)
    assert shim_cli(tmp_path, "install").returncode == 0
    paths.real.write_bytes(b"changed-binary")
    # When
    result = shim_cli(tmp_path, "remove")
    # Then
    assert result.returncode == 2
    assert result.stderr.strip() == "GOOGLE-GATE-REFUSED real-sha-mismatch"
    assert marker(tmp_path).exists()


def test_shim_when_foreign_dangling_backup_refuses_install(tmp_path: Path) -> None:
    # Given
    paths = fake_gws(tmp_path)
    paths.real.parent.mkdir(parents=True)
    paths.real.symlink_to(tmp_path / "absent")
    original = paths.binary.read_bytes()
    # When
    result = shim_cli(tmp_path, "install")
    # Then
    assert result.returncode == 2
    assert paths.real.is_symlink()
    assert paths.binary.read_bytes() == original
