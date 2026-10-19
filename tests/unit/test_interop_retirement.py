"""Retired Hermes hooks must disappear without restarting Hermes or touching cards."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from automation import retired_jobs
from automation.deploy_declarations import all_declarations

ROOT = Path(__file__).resolve().parents[2]
FILES = (
    ".hermes/interop/external-effect-tools.yaml",
    ".hermes/plugins/interop-protocol/__init__.py",
    ".hermes/plugins/interop-protocol/plugin.yaml",
    ".config/systemd/user/hermes-gateway.service.d/20-interop-guard.conf",
    ".hermes/interop/production_guard.sh",
    ".hermes/interop/pin_import_root.sh",
    ".hermes/interop/gate_driver.py",
    ".hermes/interop_runtime/automation/interop/external_effect_gate.py",
    ".hermes/interop_runtime/automation/interop/hermes_plugin/__init__.py",
)


def _stopped(argv: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    assert argv[:2] == ("systemctl", "--user")
    assert argv[2] in ("is-active", "is-enabled", "daemon-reload")
    return subprocess.CompletedProcess(argv, 0 if argv[2] == "daemon-reload" else 3,
                                      "inactive" if argv[2] == "is-active" else "disabled", "")


def test_retired_interop_cleanup_removes_stale_hooks_but_preserves_approval_records(tmp_path: Path) -> None:
    for relative in FILES:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"retired")
    cards = tmp_path / ".hermes/interop/approval-records.json"
    cards.write_bytes(b"mail budget release outside-invite")
    assert retired_jobs.check(tmp_path, "interop-plugin", _stopped) == "stale"
    retired_jobs.install(tmp_path, "interop-plugin", _stopped)
    assert all(not (tmp_path / relative).exists() for relative in FILES)
    assert cards.read_bytes() == b"mail budget release outside-invite"
    assert retired_jobs.check(tmp_path, "interop-plugin", _stopped) == "ok"
    retired_jobs.install(tmp_path, "interop-plugin", _stopped)


def test_running_gateway_prevents_hook_removal(tmp_path: Path) -> None:
    path = tmp_path / FILES[0]
    path.parent.mkdir(parents=True)
    path.write_bytes(b"retired")

    def active(argv: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, "active", "")

    with pytest.raises(ValueError):
        retired_jobs.install(tmp_path, "interop-plugin", active)
    assert path.read_bytes() == b"retired"


def test_symlink_inside_a_retired_plugin_preserves_every_file(tmp_path: Path) -> None:
    rules = tmp_path / FILES[0]
    rules.parent.mkdir(parents=True)
    rules.write_bytes(b"retired")
    plugin = tmp_path / ".hermes/plugins/interop-protocol"
    plugin.mkdir(parents=True)
    outside = tmp_path / "owner-file"
    outside.write_bytes(b"keep")
    (plugin / "unsafe").symlink_to(outside)
    assert retired_jobs.check(tmp_path, "interop-plugin", _stopped) == "unknown"
    with pytest.raises(ValueError):
        retired_jobs.install(tmp_path, "interop-plugin", _stopped)
    assert rules.read_bytes() == b"retired"
    assert outside.read_bytes() == b"keep"


def test_interop_declarations_are_retired_and_have_required_absence_cleanup() -> None:
    rows = all_declarations(ROOT)
    old = [row for row in rows if row.owner == "automation/interop"]
    assert old and all(row.policy == "retired" for row in old)
    cleanup = [row for row in rows if row.attr("algorithm") == "retired-interop-v1"]
    assert {row.account for row in cleanup} == {"agent", "peer"}
    assert all(row.policy == "required" and row.kind == "derived" for row in cleanup)


def test_retired_evaluator_and_plugin_are_not_importable() -> None:
    assert not (ROOT / "automation/interop/external_effect_gate.py").exists()
    assert not (ROOT / "automation/interop/hermes_plugin/__init__.py").exists()
    assert not (ROOT / "configs/external-effect-tools.yaml").exists()
