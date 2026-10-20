"""Retired Hermes hooks must disappear without restarting Hermes or touching cards."""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
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
    for name in ("external_effect_gate", "policy_paths", "plugin_shim"):
        with pytest.raises(ImportError, match="retired"):
            importlib.import_module(f"automation.interop.{name}")
    plugin = importlib.import_module("automation.interop.hermes_plugin")
    assert not hasattr(plugin, "evaluate_tool_call") and not hasattr(plugin, "load_denylist")
    assert not (ROOT / "configs/external-effect-tools.yaml").exists()


# Issue #150: a node that last converged on v2.6.6 keeps the retired drop-in, whose home
# copy of pin_import_root.sh demands four retired files from the new tree (exit 78 there
# made the old reconcile tick roll every release back). The verbatim v2.6.6 copies are
# fixtures; the tree must satisfy them and leave the legacy gateway inert.
LEGACY = Path(__file__).resolve().parent / "fixtures/legacy_v2_6_6"
_SHIM_PROBE = """
import importlib.util, json, sys
hooks = {}
class Ctx:
    def register_hook(self, name, fn):
        hooks[name] = fn
spec = importlib.util.spec_from_file_location("interop_protocol_home", sys.argv[1])
shim = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shim)
shim.register(Ctx())
print(json.dumps({"hooks": sorted(hooks),
                  "tool": hooks["pre_tool_call"]("terminal", {"command": "true"}),
                  "dispatch": hooks["pre_gateway_dispatch"](object(), None, None)}))
"""


def _legacy_pin(home: Path) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if key not in ("PYTHONPATH", "PIN_RELEASE_POINTER", "PIN_MIRROR_ROOT")}
    env.update(HOME=str(home), AUTOPHAGY_RUNTIME_ROOT=str(ROOT))
    return subprocess.run(["bash", str(LEGACY / "pin_import_root.sh")], env=env, capture_output=True,
                          text=True, check=False, timeout=60)


def test_v266_pin_hook_accepts_the_current_tree(tmp_path: Path) -> None:
    result = _legacy_pin(tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["PIN-IMPORT-ROOT-OK", ROOT.name]


def test_v266_plugin_shim_leaves_the_gateway_inert(tmp_path: Path) -> None:
    assert _legacy_pin(tmp_path).returncode == 0
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    env.update(HOME=str(tmp_path), PYTHONPATH=str(tmp_path / ".hermes/autophagy-import"))
    probe = subprocess.run([sys.executable, "-B", "-c", _SHIM_PROBE, str(LEGACY / "plugin_shim.py")],
                           env=env, cwd=tmp_path, capture_output=True, text=True, check=False, timeout=60)
    assert probe.returncode == 0, probe.stderr
    result = json.loads(probe.stdout)
    assert result["hooks"] == ["pre_gateway_dispatch", "pre_tool_call"]
    assert result["tool"]["action"] == "block"
    assert result["dispatch"]["action"] == "skip"
