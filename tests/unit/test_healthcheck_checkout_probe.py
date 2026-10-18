"""Drift monitor: the ops deploy checkout must stay a one-way mirror of origin/main.

Agents and humans have repeatedly committed INSIDE ``/srv/autophagy-agents``, so prod ran
code that never reached origin/main and a later deploy from a clean checkout silently
reverted it (2026-07-27 선례: skills/mail/SKILL.md v1.5.3->v1.5.5, 4 commits recovered).
``probe_checkout_mirrors_origin`` is the DETECT half of the permanent fix: it fails when
HEAD is not an ancestor of origin/main, or when tracked or nonignored untracked work exists.

The probe is shell, so it is exercised as shell. ``ssh``/``sudo`` stand-ins placed first on
PATH run the remote command against a throwaway git repo in ``tmp_path`` — no node, no
network, no dependence on this repository's own git state.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

from automation.deploy_reconcile import DRIFT_NOTICE_SECONDS

_REPO = Path(__file__).resolve().parents[2]
_HEALTHCHECK = _REPO / "automation" / "healthcheck.sh"


def _git(repo: Path, *args: str) -> None:
    _ = subprocess.run(
        ("git", "-C", str(repo), *args), check=True, capture_output=True, text=True
    )


def _mirror_checkout(tmp_path: Path) -> Path:
    """A deploy checkout in its healthy shape: HEAD == origin/main, nothing modified."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _ = subprocess.run(
        ("git", "init", "--bare", str(origin)), check=True, capture_output=True, text=True
    )
    checkout = tmp_path / "autophagy-agents"
    checkout.mkdir()
    _git(checkout, "init")
    _git(checkout, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(checkout, "config", "user.email", "probe@test.local")
    _git(checkout, "config", "user.name", "probe")
    _git(checkout, "config", "commit.gpgsign", "false")
    (checkout / "SKILL.md").write_text("version: 1.5.3\n", encoding="utf-8")
    _git(checkout, "add", "SKILL.md")
    _git(checkout, "commit", "-m", "deployed state")
    _git(checkout, "remote", "add", "origin", str(origin))
    _git(checkout, "push", "-u", "origin", "main")
    return checkout


def _fake_bin(tmp_path: Path) -> Path:
    """``ssh``/``sudo`` stand-ins: run the probe's remote command locally instead."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    ssh = fake_bin / "ssh"
    ssh.write_text(
        '#!/usr/bin/env bash\nset -euo pipefail\nexec bash -c "${*: -1}"\n', encoding="utf-8"
    )
    sudo = fake_bin / "sudo"
    sudo.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "while [[ $# -gt 0 ]]; do\n"
        '  case "$1" in\n'
        "    -n|-H) shift ;;\n"
        "    -u) shift 2 ;;\n"
        "    *) break ;;\n"
        "  esac\n"
        "done\n"
        'exec "$@"\n',
        encoding="utf-8",
    )
    ssh.chmod(0o755)
    sudo.chmod(0o755)
    return fake_bin


def _probe(
    tmp_path: Path,
    checkout: Path,
    *,
    with_ssh_sudo: bool = True,
    release: Path | None = None,
    reconcile_state: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    script = (
        f'source "{_HEALTHCHECK}"\n'
        f'if probe_checkout_mirrors_origin "test-node" "ops" "{checkout}"; then\n'
        "  echo PROBE-PASS\n"
        "else\n"
        "  echo PROBE-FAIL\n"
        "  exit 1\n"
        "fi\n"
    )
    env = dict(os.environ)
    if with_ssh_sudo:
        path_prefix = f"{_fake_bin(tmp_path)}{os.pathsep}"
    else:
        # A checkout local to the cron host needs neither ssh nor sudo. Prove the
        # probe survives their total absence — production denies BOTH (allowlist +
        # sudoers), so a probe that still reaches for them cannot ever run there.
        path_prefix = f"{_ssh_free_path(tmp_path)}{os.pathsep}"
    env["PATH"] = f"{path_prefix}{env['PATH']}"
    env["HEALTHCHECK_SSH_USER"] = ""
    env["HEALTHCHECK_SSH_IDENTITY"] = ""
    # Hermetic by default: no release pointer means the fallback node shape, where the
    # mirror IS production. Without this the suite would read THIS host's /srv and a run
    # on <primary-node> would judge a tmp_path checkout against the real release.
    env["RUNTIME_RELEASE_CURRENT"] = str(release or (tmp_path / "no-release"))
    # Same reason: the reconciler's state answers "is this drift an incident yet", and
    # an absent file must fail closed rather than read the node's real one.
    env["HEALTHCHECK_RECONCILE_STATE"] = str(reconcile_state or (tmp_path / "no-state.json"))
    return subprocess.run(
        ("bash", "-c", script), capture_output=True, text=True, check=False, env=env
    )


def _ssh_free_path(tmp_path: Path) -> Path:
    """A PATH with real git but NO ssh/sudo — shadow them with a hard-deny stub."""
    deny = tmp_path / "nossh"
    deny.mkdir(exist_ok=True)
    for name in ("ssh", "sudo"):
        blocked = deny / name
        blocked.write_text(
            '#!/usr/bin/env bash\necho "'"'"'{}'"'"' must not be used by a local probe" >&2\nexit 97\n'.format(name),
            encoding="utf-8",
        )
        blocked.chmod(0o755)
    return deny


def _advance_origin(tmp_path: Path, checkout: Path) -> str:
    """Move the bare origin's main one commit ahead, leaving the checkout stale."""
    mover = tmp_path / "mover"
    _ = subprocess.run(
        ("git", "clone", "-b", "main", str(tmp_path / "origin.git"), str(mover)),
        check=True, capture_output=True, text=True,
    )
    _git(mover, "config", "user.email", "mover@test.local")
    _git(mover, "config", "user.name", "mover")
    _git(mover, "config", "commit.gpgsign", "false")
    (mover / "SKILL.md").write_text("version: 1.6.0\n", encoding="utf-8")
    _git(mover, "commit", "-am", "a commit only origin has")
    _git(mover, "push", "origin", "main")
    return subprocess.run(
        ("git", "-C", str(mover), "rev-parse", "HEAD"),
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def _release_pointer(tmp_path: Path, sha: str) -> Path:
    """The live release pointer, named by the generation production is running."""
    generation = tmp_path / "releases" / sha
    generation.mkdir(parents=True, exist_ok=True)
    pointer = tmp_path / "current"
    pointer.symlink_to(generation, target_is_directory=True)
    return pointer

def test_checkout_at_origin_main_passes(tmp_path: Path) -> None:
    checkout = _mirror_checkout(tmp_path)
    result = _probe(tmp_path, checkout)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PROBE-PASS" in result.stdout


def test_local_commit_ahead_of_origin_fails(tmp_path: Path) -> None:
    """The exact 2026-07-27 shape: prod holds a commit that origin/main has never seen."""
    checkout = _mirror_checkout(tmp_path)
    (checkout / "SKILL.md").write_text("version: 1.5.5\n", encoding="utf-8")
    _git(checkout, "commit", "-am", "learned in prod, never pushed")
    result = _probe(tmp_path, checkout)
    assert result.returncode == 1
    assert "PROBE-FAIL" in result.stdout


def test_modified_tracked_file_fails(tmp_path: Path) -> None:
    checkout = _mirror_checkout(tmp_path)
    (checkout / "SKILL.md").write_text("version: 1.5.5\n", encoding="utf-8")
    result = _probe(tmp_path, checkout)
    assert result.returncode == 1
    assert "PROBE-FAIL" in result.stdout


def test_nonignored_untracked_code_fails(tmp_path: Path) -> None:
    """The later mirror incident stranded three untracked files outside origin."""
    checkout = _mirror_checkout(tmp_path)
    code = checkout / "skills" / "mail" / "scripts" / "runtime_override.py"
    code.parent.mkdir(parents=True)
    code.write_text("print('outside origin')\n", encoding="utf-8")

    result = _probe(tmp_path, checkout)

    assert result.returncode == 1
    assert "PROBE-FAIL" in result.stdout
    assert "mirror-dirty" in result.stderr


def test_ignored_runtime_logs_pass(tmp_path: Path) -> None:
    """Repository ignore policy, not a blanket untracked exemption, admits runtime noise."""
    checkout = _mirror_checkout(tmp_path)
    (checkout / ".gitignore").write_text("logs/*\n", encoding="utf-8")
    _git(checkout, "add", ".gitignore")
    _git(checkout, "commit", "-m", "declare runtime logs")
    _git(checkout, "push", "origin", "main")
    log = checkout / "logs" / "healthcheck-20260727T000000Z.log"
    log.parent.mkdir()
    log.write_text("noise\n", encoding="utf-8")

    result = _probe(tmp_path, checkout)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PROBE-PASS" in result.stdout


def test_behind_origin_fails_when_no_release_is_installed(tmp_path: Path) -> None:
    """The gap the old probe was blind to: origin moved ahead, prod is stale.

    ``merge-base --is-ancestor HEAD origin/main`` SUCCEEDS when behind, and with no
    fetch the local ``origin/main`` ref is itself stale — so today's probe calls a
    behind checkout ``mirror-clean``. A live ``ls-remote`` is the only way to see it.

    With no release pointer this checkout IS production (the documented `rm` rollback
    shape), so behind means prod is running stale code and the failure is real.
    """
    checkout = _mirror_checkout(tmp_path)
    _advance_origin(tmp_path, checkout)
    result = _probe(tmp_path, checkout)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PROBE-PASS" in result.stdout


def test_probe_runs_without_ssh_or_sudo(tmp_path: Path) -> None:
    """Production denies BOTH ssh (allowlist) and sudo (sudoers) with rc=126.

    healthcheck runs as ops ON <primary-node> and the checkout is local, so the probe must
    reach it with neither. A probe that still shells ssh/sudo can never run in prod.
    """
    checkout = _mirror_checkout(tmp_path)
    result = _probe(tmp_path, checkout, with_ssh_sudo=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PROBE-PASS" in result.stdout


def test_unreachable_remote_degrades_not_fails(tmp_path: Path) -> None:
    """A network blip must not cry wolf: unknown-behind is not behind.

    A monitor that fails closed on an ``ls-remote`` timeout is the exact cry-wolf
    failure being fixed. The severe fault (ahead/dirty) is still caught fully offline.
    """
    checkout = _mirror_checkout(tmp_path)
    _git(checkout, "remote", "set-url", "origin", str(tmp_path / "does-not-exist.git"))
    result = _probe(tmp_path, checkout)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PROBE-PASS" in result.stdout
    assert "BEHIND-UNKNOWN" in (result.stdout + result.stderr)



def test_behind_passes_when_the_release_runtime_already_runs_origin_main(
    tmp_path: Path,
) -> None:
    """DG-5 made this checkout an observation post; DG-6 made its lag production-neutral.

    The reconcile timer converges the release from a detached snapshot and never moves
    this HEAD, so after every landing the mirror is behind while prod is exactly current.
    Failing there is the cry-wolf this probe was built to avoid — measured 447 times on
    <primary-node> during the measured window. ``land.sh`` already grades it a warning; so does this.
    """
    checkout = _mirror_checkout(tmp_path)
    origin_sha = _advance_origin(tmp_path, checkout)

    result = _probe(tmp_path, checkout, release=_release_pointer(tmp_path, origin_sha))

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PROBE-PASS" in result.stdout
    assert "확인 MIRROR-BEHIND" in (result.stdout + result.stderr)


def test_behind_fails_when_the_release_runtime_is_itself_stale(tmp_path: Path) -> None:
    """The signal that must survive the downgrade: prod is NOT running origin/main.

    Before mode-aware grading this probe was the only check that could see it at all —
    there is no separate `release == origin/main` probe. Grading by what production
    actually runs keeps that detection instead of trading it away for quiet.
    """
    checkout = _mirror_checkout(tmp_path)
    _ = _advance_origin(tmp_path, checkout)
    stale = subprocess.run(
        ("git", "-C", str(checkout), "rev-parse", "HEAD"),
        check=True, capture_output=True, text=True,
    ).stdout.strip()

    result = _probe(tmp_path, checkout, release=_release_pointer(tmp_path, stale))

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PROBE-PASS" in result.stdout
    assert "확인 MIRROR-BEHIND" in (result.stdout + result.stderr)


def test_a_live_release_never_softens_dirty_or_ahead(tmp_path: Path) -> None:
    """Mode-awareness is for lag only. Work stranded in the checkout is severe in every
    mode: it exists nowhere else, and the release running fine says nothing about it."""
    for shape in ("dirty", "ahead"):
        case = tmp_path / shape
        case.mkdir()
        checkout = _mirror_checkout(case)
        origin_sha = _advance_origin(case, checkout)
        (checkout / "SKILL.md").write_text("version: 1.5.5\n", encoding="utf-8")
        if shape == "ahead":
            _git(checkout, "commit", "-am", "learned in prod, never pushed")

        result = _probe(case, checkout, release=_release_pointer(case, origin_sha))

        assert result.returncode == 1, shape + result.stdout + result.stderr
        assert "PROBE-FAIL" in result.stdout, shape

# --------------------------------------------------------------------------- #
# A stale release is real, but "prod has not caught up YET" is not an incident.
# The reconciler already owns that judgment (DRIFT_NOTICE_SECONDS /
# FAILURE_NOTICE_THRESHOLD) and notifies the owner on it. Two judges answering the
# same question differently is the defect this file's behind-grading fixed on the
# land.sh side — deciding it a second time here would only move it.
# --------------------------------------------------------------------------- #
def _reconcile_state(
    tmp_path: Path,
    *,
    drift_since: float | None,
    written_ago: float = 0.0,
    skip_reason: str | None = None,
) -> Path:
    """The reconciler's own state file, with a controlled write time."""
    state = tmp_path / "reconcile-state.json"
    state.write_text(
        json.dumps(
            {
                "consecutive_failures": 0,
                "drift_since": drift_since,
                "notified_target": None,
                "pending_notice": None,
                "incident_open": False,
                "skip_reason": skip_reason,
            }
        ),
        encoding="utf-8",
    )
    if written_ago:
        stamp = time.time() - written_ago
        os.utime(state, (stamp, stamp))
    return state


def _stale_release(tmp_path: Path) -> tuple[Path, Path]:
    """A checkout behind origin whose release is behind too — prod is not current."""
    checkout = _mirror_checkout(tmp_path)
    _ = _advance_origin(tmp_path, checkout)
    stale = subprocess.run(
        ("git", "-C", str(checkout), "rev-parse", "HEAD"),
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return checkout, _release_pointer(tmp_path, stale)


def test_release_stale_is_graced_while_the_reconciler_stays_silent(tmp_path: Path) -> None:
    """The ≤2-minute window after every PR merge, measured live on <primary-node>.

    origin moves, the 2-minute timer converges the release, and a 5-minute healthcheck
    tick landing in between saw `release-stale` and paged the owner — for a node that
    was behaving exactly as designed. The reconciler itself calls that no incident.
    """
    checkout, release = _stale_release(tmp_path)
    state = _reconcile_state(tmp_path, drift_since=time.time() - 60)

    result = _probe(tmp_path, checkout, release=release, reconcile_state=state)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PROBE-PASS" in result.stdout
    assert "확인 MIRROR-BEHIND" in (result.stdout + result.stderr)


def test_release_stale_fails_once_the_reconcilers_own_threshold_elapses(
    tmp_path: Path,
) -> None:
    """Grace is a window, not an amnesty: past its own threshold the drift is an incident."""
    checkout, release = _stale_release(tmp_path)
    state = _reconcile_state(tmp_path, drift_since=time.time() - 1200)

    result = _probe(tmp_path, checkout, release=release, reconcile_state=state)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "확인 MIRROR-BEHIND" in (result.stdout + result.stderr)


def test_release_stale_is_graced_before_the_reconciler_has_even_seen_the_drift(
    tmp_path: Path,
) -> None:
    """`drift_since` is null right after origin moves — the tick has not run yet.

    Failing here would page the owner for the first seconds of every landing, which is
    the widest part of the window rather than an edge case.
    """
    checkout, release = _stale_release(tmp_path)
    state = _reconcile_state(tmp_path, drift_since=None)

    result = _probe(tmp_path, checkout, release=release, reconcile_state=state)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "확인 MIRROR-BEHIND" in (result.stdout + result.stderr)


def test_a_silent_reconciler_is_not_a_grace(tmp_path: Path) -> None:
    """A timer that stopped writing converges nothing — its silence must not buy quiet.

    Exactly the 2026-08-02 shape: `ProtectHome=yes` took the ssh key away, every tick
    exited 0 before `save_state`, and the file went unwritten for 15 hours while ~450
    ticks "succeeded". Without this the grace would have hidden that indefinitely.
    """
    checkout, release = _stale_release(tmp_path)
    state = _reconcile_state(tmp_path, drift_since=None, written_ago=1200)

    result = _probe(tmp_path, checkout, release=release, reconcile_state=state)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "확인 MIRROR-BEHIND" in (result.stdout + result.stderr)


def test_an_unreadable_reconciler_state_fails_closed(tmp_path: Path) -> None:
    """No evidence of convergence is not evidence of convergence."""
    checkout, release = _stale_release(tmp_path)

    result = _probe(tmp_path, checkout, release=release)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "확인 MIRROR-BEHIND" in (result.stdout + result.stderr)


def test_an_unreleased_backlog_is_a_warning_not_a_stale_release(tmp_path: Path) -> None:
    """Merged-but-unreleased commits are a release backlog, not prod running stale code.

    Since merges stopped cutting tags, main is ahead of the release between every merge
    and the next owner-approved release. The reconciler records exactly that as
    ``skip_reason=release-backlog`` — it only takes that path when the runtime already is
    the latest signed release — and keeps ``drift_since`` at the episode start. Grading
    it ``release-stale`` once that start is older than the grace opened a repair ticket
    every five minutes for hours after each merge (711 such ticks on one card).
    """
    checkout, release = _stale_release(tmp_path)
    state = _reconcile_state(
        tmp_path, drift_since=time.time() - 6 * 3600, skip_reason="release-backlog"
    )

    result = _probe(tmp_path, checkout, release=release, reconcile_state=state)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PROBE-PASS" in result.stdout
    assert "확인 MIRROR-BEHIND" in (result.stdout + result.stderr)


def test_a_backlog_reported_by_a_silent_reconciler_still_fails(tmp_path: Path) -> None:
    """The backlog word is only as good as the tick that wrote it: a stale file is no proof
    that prod still runs the latest release."""
    checkout, release = _stale_release(tmp_path)
    state = _reconcile_state(
        tmp_path,
        drift_since=time.time() - 6 * 3600,
        written_ago=1200,
        skip_reason="release-backlog",
    )

    result = _probe(tmp_path, checkout, release=release, reconcile_state=state)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "확인 MIRROR-BEHIND" in (result.stdout + result.stderr)


def test_another_skip_reason_past_the_grace_still_fails(tmp_path: Path) -> None:
    """Only the backlog verdict is a warning; any other reconciler reason keeps the alarm."""
    checkout, release = _stale_release(tmp_path)
    state = _reconcile_state(
        tmp_path, drift_since=time.time() - 1200, skip_reason="converge-failed"
    )

    result = _probe(tmp_path, checkout, release=release, reconcile_state=state)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "확인 MIRROR-BEHIND" in (result.stdout + result.stderr)


def test_the_probe_grace_matches_reconciler_and_state_is_caller_resolved(tmp_path: Path) -> None:
    probe = (_REPO / "automation" / "checkout_mirror_probe.sh").read_text(encoding="utf-8")
    grace = re.search(r"HEALTHCHECK_RECONCILE_GRACE:-(\d+)", probe)
    assert grace, "the probe must retain the reconciler grace default"
    assert int(grace.group(1)) == int(DRIFT_NOTICE_SECONDS)
    assert "HEALTHCHECK_RECONCILE_STATE:?" in probe
    healthcheck = (_REPO / "automation" / "healthcheck.sh").read_text(encoding="utf-8")
    assert "HEALTHCHECK_RECONCILE_STATE" in healthcheck
