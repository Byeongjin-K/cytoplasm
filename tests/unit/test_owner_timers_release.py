"""Real owner/HEAD approval and offline deploy transport; no cases added to FS3 files."""
from __future__ import annotations

import subprocess
import os
import tarfile
from dataclasses import replace
from pathlib import Path

import pytest

from automation import release_approval
from tests.unit import test_release_authorize as auth

JOBS = ("calendar-confirm-watch", "mail-attachment-drive-watch")


@pytest.mark.parametrize("reaction,covered,head,jobs,expected", [
    ("approve", True, "a" * 40, JOBS, 0),
    ("pending", True, "a" * 40, JOBS, 1),
    ("both", True, "a" * 40, JOBS, 8),
    ("approve", False, "a" * 40, JOBS, 4),
    ("approve", True, "b" * 40, JOBS, 4),
    ("approve", True, "a" * 40, JOBS[:1], 4),
])
def test_authorization_requires_owner_same_head_and_entire_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    reaction: str, covered: bool, head: str, jobs: tuple[str, ...], expected: int,
) -> None:
    # Given
    from automation import owner_timers

    base = auth._spec()
    spec = replace(base, surface_digests=(*base.surface_digests,
                                        *(owner_timers.surfaces(JOBS) if covered else ())))
    monkeypatch.setattr(auth, "_spec", lambda: spec)
    owner = [{"id": auth._OWNER, "bot": False}]
    auth._env(tmp_path, monkeypatch, reactions={
        auth._APPROVE: owner if reaction in ("approve", "both") else [],
        auth._CANCEL: owner if reaction == "both" else [],
    }, render_version=8)
    # When
    result = release_approval.main(["authorize-owner-timers-off", "--jobs", "/".join(jobs), "--head", head])
    # Then
    assert result == expected


def test_unapproved_deploy_refuses_before_touching_node(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    from automation import owner_timers

    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 4)

    monkeypatch.setattr(owner_timers.subprocess, "run", run)
    # When
    result = owner_timers.deploy(tmp_path, "fake-node", "/srv/current", "a" * 40, JOBS)
    # Then
    assert result == 4
    assert len(calls) == 1
    assert calls[0][1:] == ("authorize-owner-timers-off", "--jobs", "/".join(JOBS), "--head", "a" * 40)


@pytest.mark.parametrize("auth_rc", [0, 4])
def test_apply_authorizes_list_before_node_marker_write(tmp_path: Path, auth_rc: int) -> None:
    # Given: only the transport and the approval authority are replaced.
    from tests.unit.test_hermes_gateways_stop import REPO, _host

    repo, env = _host(tmp_path, None, auth_rc=auth_rc)
    for name in ("deploy_declarations.py", "watcher_manifest.py"):
        (repo / "automation" / name).symlink_to(REPO / "automation" / name)
    manifest = repo / "automation/xw/deploy-manifest.txt"
    manifest.parent.mkdir()
    manifest.write_text(
        "agent|automation/xw/x.py|calendar-confirm-watch|v2:cron;runner=systemd;"
        "schedule=17 * * * *;script=x.py;deliver=local;mode=no-agent\n",
    )
    flag = Path(env["HOME"]) / ".hermes/release-flags/owner-timers-off"
    flag.parent.mkdir(parents=True)
    flag.write_bytes(b"calendar-confirm-watch\n")
    # When
    result = subprocess.run(["bash", str(repo / "automation/deploy_all.sh"), "--apply"],
                            env=env, capture_output=True, text=True, check=False, timeout=30)
    # Then
    assert result.returncode == auth_rc, result.stdout + result.stderr
    calls = (tmp_path / "calls").read_text().splitlines()
    authorizations = [i for i, line in enumerate(calls) if line.startswith("authorize authorize-owner-timers-off")]
    writes = [i for i, line in enumerate(calls) if "automation.owner_timers install" in line]
    assert len(authorizations) == 1
    if auth_rc == 0:
        assert writes and authorizations[0] < writes[0]
    else:
        assert not writes


def test_rehearsal_checks_owner_marker_without_authorizing_or_installing(tmp_path: Path) -> None:
    # Given
    from tests.unit.test_release_rehearsal import _rehearse
    # When
    result, calls = _rehearse(tmp_path)
    # Then
    assert result.returncode == 0, result.stdout + result.stderr
    checks = [line for line in calls if "automation.owner_timers check" in line]
    assert len(checks) == 1
    assert "PYTHONPATH=/tmp/deploy-rehearse." in checks[0]
    assert not any("automation.owner_timers install" in line or line.startswith("authorize ") for line in calls)


def test_remote_approval_snapshot_contains_skill_job_declarations(tmp_path: Path) -> None:
    # Given: capture only the archive at the existing SSH boundary, with no live node.
    from tests.unit.test_release_approval_remote import _CLIENT, _REPO

    ssh = tmp_path / "ssh"
    archive = tmp_path / "snapshot.tar"
    ssh.write_text(
        '#!/bin/bash\n'
        '[[ "$*" != *"test"*"-f"* ]] || exit 1\n'
        'if [[ "$*" == *"incoming"* ]]; then cat > "$ARCHIVE"; else cat >/dev/null; fi\n',
    )
    ssh.chmod(0o755)
    # When
    result = subprocess.run(["bash", str(_CLIENT), "authorize-owner-timers-off",
                             "--jobs", "/".join(JOBS), "--head", "a" * 40],
                            cwd=_REPO, env={**os.environ, "RELEASE_APPROVAL_SSH": str(ssh),
                                           "RELEASE_APPROVAL_HOST": "fake-node",
                                           "ARCHIVE": str(archive)},
                            capture_output=True, text=True, check=False, timeout=30)
    # Then
    assert result.returncode == 0, result.stderr
    with tarfile.open(archive) as snapshot:
        names = snapshot.getnames()
    assert "skills/calendar/deploy-manifest.txt" in names
    assert "skills/mail/deploy-manifest.txt" in names
