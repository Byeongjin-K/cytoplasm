"""Release color boundary in a new file; historical FS3 test files stay frozen."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from automation import deploy_all_observe, release_approval
from automation.deploy_declarations import parse_declaration_file

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests/unit"))
import test_release_authorize as auth  # noqa: E402
from test_healthcheck_wrapper_release import _fake_plan, _plan_args, _spec  # noqa: E402

SURFACE = ("calendar-default-color:2", hashlib.sha256(b"2").hexdigest())


@pytest.mark.parametrize("value,code,mode", [
    (None, 0, "off"), (b"2", 0, "2"), (b"1", 0, "1"), (b"11", 0, "11"),
    (b"", 4, ""), (b"0", 4, ""), (b"12", 4, ""), (b"02", 4, ""),
    (b"2\n", 4, ""), (b"off", 4, ""),
])
def test_host_switch_cli(tmp_path, value, code, mode):
    # Given
    flag = tmp_path / ".hermes/release-flags/calendar-default-color"
    if value is not None:
        flag.parent.mkdir(parents=True)
        flag.write_bytes(value)
    # When
    result = subprocess.run(
        [sys.executable, "-B", "-m", "automation.calendar_default_color", "switch"],
        cwd=ROOT, env={**os.environ, "HOME": str(tmp_path)},
        text=True, capture_output=True, check=False,
    )
    # Then
    assert result.returncode == code
    assert result.stdout.strip() == mode


def test_plan_binds_actual_color_to_release_hash_and_card(monkeypatch, capsys):
    # Given
    _fake_plan(monkeypatch)
    args = _plan_args()
    args.calendar_default_color = "2"
    # When
    assert release_approval.cmd_plan(args) == 0
    payload = json.loads(capsys.readouterr().out)
    # Then
    assert list(SURFACE) in payload["surface_digests"]
    spec = replace(_spec(SURFACE), render_version=8)
    assert "세이지" in spec.render()
    assert spec.action_hash() != replace(spec, surface_digests=()).action_hash()


@pytest.mark.parametrize("reaction,covered,head,color,code", [
    ("approve", True, "a" * 40, "2", 0),
    ("pending", True, "a" * 40, "2", 1),
    ("both", True, "a" * 40, "2", 8),
    ("approve", False, "a" * 40, "2", 4),
    ("approve", True, "b" * 40, "2", 4),
    ("approve", True, "a" * 40, "3", 4),
])
def test_enable_requires_exact_head_color_and_owner(
    tmp_path, monkeypatch, reaction, covered, head, color, code,
):
    # Given: real gate, only the Discord transport is fake.
    base = auth._spec()
    spec = replace(base, surface_digests=(*base.surface_digests, *([SURFACE] if covered else [])))
    monkeypatch.setattr(auth, "_spec", lambda: spec)
    owner = [{"id": auth._OWNER, "bot": False}]
    auth._env(tmp_path, monkeypatch, reactions={
        auth._APPROVE: owner if reaction in ("approve", "both") else [],
        auth._CANCEL: owner if reaction == "both" else [],
    }, render_version=8)
    # When
    result = release_approval.main([
        "authorize-calendar-default-color", "--head", head, "--color", color,
    ])
    # Then
    assert result == code


@pytest.mark.parametrize("mode,observed,expected", [
    ("off", None, "ok"), ("2", None, "absent"), ("2", b"2", "ok"),
    ("off", b"2", "stale"), ("off", b"off", "ok"), ("2", b"11", "stale"),
])
def test_declared_color_marker_is_observed(tmp_path, mode, observed, expected):
    # Given
    declaration, = parse_declaration_file(
        "automation/calendar_default_color/deploy-manifest.txt",
        "agent|automation/calendar_default_color|.hermes/calendar-default-color.mode"
        "|v2:derived;algorithm=calendar-default-color-v1\n",
    )
    def run(account, argv):
        assert (account, argv) == ("agent", ("sha", ".hermes/calendar-default-color.mode"))
        return (3, "") if observed is None else (0, hashlib.sha256(observed).hexdigest())
    # When
    seen = deploy_all_observe._derived(declaration, tmp_path, run, {"calendar_default_color": mode})
    # Then
    assert seen.state == expected


def _executable(path, text):
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def _deployment(tmp_path, *, approval=0, pointer="a" * 40):
    repo = tmp_path / "repo"
    automation = repo / "automation"
    package = automation / "calendar_default_color"
    package.mkdir(parents=True)
    for name in ("__init__.py", "calendar_default_color.py", "node_config.py", "deploy_push.sh"):
        (automation / name).symlink_to(ROOT / "automation" / name)
    (package / "deploy.sh").symlink_to(ROOT / "automation/calendar_default_color/deploy.sh")
    (automation / "deploy_provenance.sh").write_text("deploy_provenance_check() { return 0; }\n")
    _executable(automation / "release_approval_remote.sh",
                f'#!/bin/bash\nprintf "%s\\n" "$*" >> "$CALLS"\nexit {approval}\n')
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _executable(bin_dir / "git", '#!/bin/bash\nprintf "%s\\n" "$HEAD"\n')
    _executable(bin_dir / "sudo", '#!/bin/bash\nshift 4\nexec "$@"\n')
    _executable(bin_dir / "ssh", '#!/usr/bin/env python3\n'
                'import os, subprocess, sys\n'
                'cmd = sys.argv[-1]\n'
                'if cmd.startswith("readlink "):\n'
                '    print("/srv/releases/" + os.environ["POINTER"])\n'
                '    sys.exit(0)\n'
                'assert cmd.startswith("sudo -n -u agent -H bash ")\n'
                'sys.exit(subprocess.run(["bash", "-c", cmd], env={**os.environ, "HOME": os.environ["NODE_HOME"]}).returncode)\n')
    home, node = tmp_path / "host", tmp_path / "node"
    home.mkdir()
    node.mkdir()
    return package / "deploy.sh", {
        **os.environ, "HOME": str(home), "NODE_HOME": str(node),
        "HEAD": "a" * 40, "POINTER": pointer, "CALLS": str(tmp_path / "calls"),
        "DEPLOY_SSH_HOST": "fake-node", "PATH": f"{bin_dir}:{os.environ['PATH']}",
    }


@pytest.mark.parametrize("approval,pointer", [(4, "a" * 40), (0, "b" * 40)])
def test_deployer_refuses_before_marker_write(tmp_path, approval, pointer):
    # Given
    script, env = _deployment(tmp_path, approval=approval, pointer=pointer)
    flag = Path(env["HOME"]) / ".hermes/release-flags/calendar-default-color"
    flag.parent.mkdir(parents=True)
    flag.write_bytes(b"2")
    # When
    result = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, check=False)
    # Then
    assert result.returncode == 4, result.stdout + result.stderr
    assert not (Path(env["NODE_HOME"]) / ".hermes/calendar-default-color.mode").exists()


def test_deployer_writes_verified_color_and_removal_restores_off(tmp_path):
    # Given
    script, env = _deployment(tmp_path)
    flag = Path(env["HOME"]) / ".hermes/release-flags/calendar-default-color"
    flag.parent.mkdir(parents=True)
    flag.write_bytes(b"2")
    # When: actual declared push_file writes and reads back through fake SSH.
    first = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, check=False)
    # Then
    assert first.returncode == 0, first.stdout + first.stderr
    marker = Path(env["NODE_HOME"]) / ".hermes/calendar-default-color.mode"
    assert marker.read_bytes() == b"2"
    assert marker.stat().st_mode & 0o777 == 0o600
    assert Path(env["CALLS"]).read_text() == f"authorize-calendar-default-color --head {'a' * 40} --color 2\n"
    # Given: a subsequent release removes the host flag.
    flag.unlink()
    # When
    second = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, check=False)
    # Then
    assert second.returncode == 0, second.stdout + second.stderr
    assert marker.read_bytes() == b"off"


def test_release_shell_passes_exact_color_to_plan(tmp_path):
    # Given: actual release shell, sandbox transport only.
    from test_release_sh import _origin_with_commits, _run

    _origin, work = _origin_with_commits(tmp_path)
    flag = tmp_path / ".hermes/release-flags/calendar-default-color"
    flag.parent.mkdir(parents=True)
    flag.write_bytes(b"2")
    # When
    result = _run(tmp_path, work, decisions="2 0", arguments=("--no-deploy",))
    # Then
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--calendar-default-color 2" in (tmp_path / "plan-args").read_text()
