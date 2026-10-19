"""A node with no deploy receipt keeps its approved gateway stop across new releases.

PR #104 gap 8 (2026-10-19): with no receipt, the release reconciler read "not stopped" and
restarted both gateways on every new signed release. The approved stop now leaves an ops marker
beside the receipt. New file so the rig cases in test_hermes_gateways_stop.py keep their shape.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from automation.hermes_gateways import stopped_in_receipt
from tests.unit.test_hermes_gateways_stop import _host

ACCOUNTS = ("agent", "peer")


@pytest.mark.parametrize(("marker", "stopped"), [(b"stopped", True), (None, False), (b"running", False)])
def test_no_receipt_falls_back_to_the_ops_marker(tmp_path: Path, marker: bytes | None, stopped: bool) -> None:
    path = tmp_path / "gateways-stopped"
    if marker is not None:
        path.write_bytes(marker)
    assert stopped_in_receipt(tmp_path / "receipt.json", ACCOUNTS, path) is stopped


def test_a_receipt_still_decides_when_one_exists(tmp_path: Path) -> None:
    (tmp_path / "gateways-stopped").write_bytes(b"stopped")
    (tmp_path / "receipt.json").write_text(json.dumps({
        "version": 2, "release_sha": "a" * 40, "hermes_gateways": "running",
        "surfaces": {"gateway_services": {account: "running" for account in ACCOUNTS}},
    }))
    assert stopped_in_receipt(tmp_path / "receipt.json", ACCOUNTS, tmp_path / "gateways-stopped") is False


@pytest.mark.parametrize("switch", ["stopped", None])
def test_the_marker_follows_the_approved_gateway_change(tmp_path: Path, switch: str | None) -> None:
    repo, env = _host(tmp_path, switch)
    subprocess.run(("bash", str(repo / "automation/deploy_all.sh"), "--apply"),
                   env=env, text=True, capture_output=True, check=False)
    calls = (tmp_path / "calls").read_text().splitlines()
    marks = [i for i, line in enumerate(calls) if "/gateways-stopped" in line]
    changes = [i for i, line in enumerate(calls) if "hermes-gateway.service" in line and "sudo -n -u" in line]
    assert len(marks) == 1 and len(changes) == 2
    if switch == "stopped":
        assert "printf stopped" in calls[marks[0]] and marks[0] > max(changes)
    else:
        assert "rm -f" in calls[marks[0]] and marks[0] < min(changes)
