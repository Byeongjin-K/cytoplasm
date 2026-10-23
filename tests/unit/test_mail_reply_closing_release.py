"""Reply-closing opt-out at the release boundary, mirroring the HTML-reply switch tests."""
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
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_release_authorize as auth  # noqa: E402
from test_healthcheck_wrapper_release import _fake_plan, _plan_args, _spec  # noqa: E402
from test_mail_html_reply_release import _deploy, _executable  # noqa: E402

SURFACE = ("mail-reply-closing:omit", hashlib.sha256(b"omit").hexdigest())
FLAG = ".hermes/release-flags/mail-reply-closing"
MARKER = ".hermes/mail-reply-closing.mode"


@pytest.mark.parametrize("value,code,mode", [
    (None, 0, "keep"), (b"omit", 0, "omit"), (b"omit\n", 4, ""), (b"keep", 4, ""),
    (b"", 4, ""), (b"OMIT", 4, ""), (b"off", 4, ""),
])
def test_host_switch_accepts_only_exact_omit(tmp_path, value, code, mode):
    # Given: only a private host flag, no node or external account.
    flag = tmp_path / FLAG
    if value is not None:
        flag.parent.mkdir(parents=True)
        flag.write_bytes(value)
    # When: use the actual switch CLI.
    result = subprocess.run(
        ["python3", "-B", "-m", "automation.mail_reply_closing", "switch"], cwd=REPO,
        env={**os.environ, "HOME": str(tmp_path)}, capture_output=True, text=True, check=False,
    )
    # Then: invalid bytes cannot silently become either mode.
    assert result.returncode == code
    assert result.stdout.strip() == mode


def test_plan_binds_omit_to_release_hash_and_card(monkeypatch, capsys):
    # Given: a real release plan over stubbed repository facts.
    _fake_plan(monkeypatch)
    args = _plan_args()
    args.mail_reply_closing = "omit"
    # When
    assert release_approval.cmd_plan(args) == 0
    payload = json.loads(capsys.readouterr().out)
    # Then: the surface joins the existing approval preimage and the card names it.
    assert list(SURFACE) in payload["surface_digests"]
    spec = replace(_spec(SURFACE), render_version=8)
    from automation.mail_reply_closing import CARD_LINE

    assert CARD_LINE in spec.render()
    assert spec.action_hash() != replace(spec, surface_digests=()).action_hash()


def test_plan_without_switch_adds_no_surface(monkeypatch, capsys):
    _fake_plan(monkeypatch)
    assert release_approval.cmd_plan(_plan_args()) == 0
    names = [name for name, _digest in json.loads(capsys.readouterr().out)["surface_digests"]]
    assert not any(name.startswith("mail-reply-closing") for name in names)


@pytest.mark.parametrize("reaction,covered,head,code", [
    ("approve", True, "a" * 40, 0), ("pending", True, "a" * 40, 1),
    ("both", True, "a" * 40, 8), ("approve", False, "a" * 40, 4),
    ("approve", True, "b" * 40, 4),
])
def test_omit_requires_owner_approval_for_exact_head_and_surface(
    tmp_path, monkeypatch, reaction, covered, head, code,
):
    # Given: keep the real release gate; replace Discord transport only.
    base = auth._spec()
    spec = replace(base, surface_digests=(*base.surface_digests, *([SURFACE] if covered else [])))
    monkeypatch.setattr(auth, "_spec", lambda: spec)
    owner = [{"id": auth._OWNER, "bot": False}]
    auth._env(tmp_path, monkeypatch, reactions={
        auth._APPROVE: owner if reaction in ("approve", "both") else [],
        auth._CANCEL: owner if reaction == "both" else [],
    }, render_version=8)
    # When
    result = release_approval.main(["authorize-mail-reply-closing", "--head", head])
    # Then
    assert result == code


@pytest.mark.parametrize("mode,observed,expected", [
    ("keep", None, "ok"), ("omit", None, "absent"), ("omit", b"omit", "ok"),
    ("keep", b"omit", "stale"), ("keep", b"keep", "ok"), ("omit", b"keep", "stale"),
    ("omit", b"broken", "stale"),
])
def test_declared_marker_tracks_host_mode(tmp_path, mode, observed, expected):
    # Given: the declared destination and a node hash from the read-only boundary.
    declaration, = parse_declaration_file(
        "automation/mail_reply_closing/deploy-manifest.txt",
        (REPO / "automation/mail_reply_closing/deploy-manifest.txt").read_text(encoding="utf-8"),
    )
    def run(account, argv):
        assert (account, argv) == ("agent", ("sha", MARKER))
        return (3, "") if observed is None else (0, hashlib.sha256(observed).hexdigest())
    # When
    seen = deploy_all_observe._derived(declaration, tmp_path, run, {"mail_reply_closing": mode})
    # Then
    assert seen.state == expected


def _deployment(tmp_path: Path, *, approval: int = 0, pointer: str = "a" * 40):
    repo = tmp_path / "repo"
    automation = repo / "automation"
    package = automation / "mail_reply_closing"
    package.mkdir(parents=True)
    for name in ("__init__.py", "mail_reply_closing.py", "node_config.py", "deploy_push.sh"):
        (automation / name).symlink_to(REPO / "automation" / name)
    # The transport/provenance seams are local fakes; push_file's write + read-back is real.
    (package / "deploy.sh").symlink_to(REPO / "automation/mail_reply_closing/deploy.sh")
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
    home = tmp_path / "host"
    node = tmp_path / "node"
    home.mkdir()
    node.mkdir()
    return package / "deploy.sh", {
        **os.environ, "HOME": str(home), "NODE_HOME": str(node),
        "HEAD": "a" * 40, "POINTER": pointer, "CALLS": str(tmp_path / "calls"),
        "DEPLOY_SSH_HOST": "fake-node", "PATH": f"{bin_dir}:{os.environ['PATH']}",
    }


@pytest.mark.parametrize("approval,pointer", [(4, "a" * 40), (0, "b" * 40)])
def test_deployer_refuses_before_marker_write_without_authorized_head(tmp_path, approval, pointer):
    # Given
    script, env = _deployment(tmp_path, approval=approval, pointer=pointer)
    flag = Path(env["HOME"]) / FLAG
    flag.parent.mkdir(parents=True)
    flag.write_bytes(b"omit")
    # When
    result = _deploy(script, env)
    # Then
    assert result.returncode == 4, result.stdout + result.stderr
    assert not (Path(env["NODE_HOME"]) / MARKER).exists()


def test_removing_host_switch_deploys_keep_through_same_declared_writer(tmp_path):
    # Given: omit once with the same-head authorization, using the actual push_file helper.
    script, env = _deployment(tmp_path)
    flag = Path(env["HOME"]) / FLAG
    flag.parent.mkdir(parents=True)
    flag.write_bytes(b"omit")
    first = _deploy(script, env)
    assert first.returncode == 0, first.stdout + first.stderr
    marker = Path(env["NODE_HOME"]) / MARKER
    assert marker.read_bytes() == b"omit"
    assert Path(env["CALLS"]).read_text() == f"authorize-mail-reply-closing --head {'a' * 40}\n"
    flag.unlink()
    # When: a later release no longer selects the host switch.
    result = _deploy(script, env)
    # Then: keep is atomically written and verified without the omit authorization.
    assert result.returncode == 0, result.stdout + result.stderr
    assert marker.read_bytes() == b"keep"
    assert marker.stat().st_mode & 0o777 == 0o600


def test_release_shell_passes_host_omit_to_plan(tmp_path):
    # Given: the existing sandbox runs the actual release shell, with no real node.
    from test_release_sh import _origin_with_commits, _run

    _origin, work = _origin_with_commits(tmp_path)
    flag = tmp_path / FLAG
    flag.parent.mkdir(parents=True)
    flag.write_bytes(b"omit")
    # When
    result = _run(tmp_path, work, decisions="2 0", arguments=("--no-deploy",))
    # Then
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--mail-reply-closing omit" in (tmp_path / "plan-args").read_text()
