"""Standalone legacy ACKs may survive, but must never bypass an active sender override."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from automation import owner_notice
from automation.deploy_declarations import all_declarations
from tests.unit.test_notice_bot import installed

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("facade", [owner_notice.notify_owner, owner_notice.notify_owner_dm])
def test_owner_notice_never_uses_old_token_when_resolver_fails(
    facade, tmp_path, monkeypatch, capsys,
):
    installed(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "legacy credential")
    monkeypatch.setenv("AUTOPHAGY_OWNER_ID", "123")

    def broken():
        raise ImportError("unavailable")

    def forbidden(*args, **kwargs):
        pytest.fail("resolver failure reached the legacy sender")

    monkeypatch.setattr(owner_notice, "resolve_token", broken)
    monkeypatch.setattr(owner_notice, "send_notice", forbidden)
    assert facade("test") is False
    assert capsys.readouterr().err == "[owner-notice] NOTIFY-FAILED: ImportError\n"


def test_isolated_meeting_plugin_refuses_legacy_token_after_cutover(tmp_path):
    installed(tmp_path)
    plugin = tmp_path / "plugin.py"
    shutil.copyfile(REPO / "skills/meeting/plugin/__init__.py", plugin)
    script = """
import importlib.util, sys
assert importlib.util.find_spec("automation") is None
spec = importlib.util.spec_from_file_location("meeting_plugin", sys.argv[1])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
def forbidden(*args, **kwargs):
    raise AssertionError("legacy credential reached HTTP")
module.urlopen = forbidden
assert module._discord_post("/channels/123/messages", {"content": "test"}) is None
print("blocked")
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", script, str(plugin)],
        env={"HOME": str(tmp_path), "PATH": os.environ["PATH"], "DISCORD_BOT_TOKEN": "legacy credential"},
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "blocked\n"
    assert result.stderr == "NOTICE-BOT-UNAVAILABLE\n"


def test_declared_repair_runtime_resolves_override_without_checkout(tmp_path):
    home, runtime = tmp_path / "home", tmp_path / "runtime"
    installed(home)
    rows = all_declarations(REPO)
    tree = next(row for row in rows if row.destination == ".hermes/repair-report-runtime/.current")
    for name in tree.attr("files").split(","):
        target = runtime / tree.attr("prefix") / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / tree.source / name, target)
    destination = tree.destination + "/automation/node.example.toml"
    seed = next(row for row in rows if row.destination == destination)
    shutil.copyfile(REPO / seed.source, runtime / "automation/node.example.toml")
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c",
         "import sys; sys.path.insert(0, sys.argv[1]); "
         "from automation.notice_bot import read_override; "
         "assert read_override() is not None; print('override-used')", str(runtime)],
        env={"HOME": str(home), "PATH": os.environ["PATH"]},
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "override-used\n"
