"""New shell regressions avoid changing FS3-frozen deployment harness test files."""
import shlex
from pathlib import Path

import pytest

from automation.cron_timer_owner_off import FLAG
from tests.unit import test_deploy_all_cli as shell

_SSH = r"""#!/usr/bin/env bash
set -euo pipefail
cmd="${*: -1}"
case "$cmd" in
  *authorize-cron-timer*)
    printf '%s\n' "$cmd" >> "$FAKE_CALLS"
    exit "$FAKE_APPROVAL_RC" ;;
  *".archive-sha256"*) exit 0 ;;
  *"automation.cron_timer_owner_off install"*)
    printf '%s\n' "$cmd" >> "$FAKE_CALLS"
    touch "$FAKE_MARKER_REMOVED"
    exit 0 ;;
  *"--format actions"*)
    if [[ "$FAKE_OLD_MARKER" == 1 && ! -e "$FAKE_MARKER_REMOVED" ]]; then
      printf '%s\n' "$cmd" >> "$FAKE_CALLS"
      printf 'ACT|cron-timers|converge\n'
      exit 1
    fi ;;
esac
exec "$(dirname "$0")/ssh-base" "$@"
"""


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    original = shell._stub_ssh

    def stub(path):
        binary, calls, receipt = original(path)
        (binary / "ssh").rename(binary / "ssh-base")
        (binary / "ssh").write_text(_SSH)
        (binary / "ssh").chmod(0o755)
        return binary, calls, receipt

    monkeypatch.setattr(shell, "_stub_ssh", stub)
    home = tmp_path / "host-home"
    home.mkdir()
    return {
        "HOME": str(home),
        "FAKE_APPROVAL_RC": "0",
        "FAKE_OLD_MARKER": "0",
        "FAKE_MARKER_REMOVED": str(tmp_path / "marker-removed"),
        "RELEASE_APPROVAL_HOST": "example-node",
        "RELEASE_APPROVAL_ACCOUNT": "agent",
        "RELEASE_APPROVAL_HOME": "/home/example-agent",
    }


@pytest.mark.parametrize("approved", [True, False])
def test_apply_authorizes_stop_before_timer_deployers_when_host_flag_is_present(
    tmp_path, deployment, approved,
):
    # Given: an isolated release host selecting one currently declared timer.
    flag = Path(deployment["HOME"]) / FLAG
    flag.parent.mkdir(parents=True)
    flag.write_bytes(b"budget-watch\n")
    deployment["FAKE_APPROVAL_RC"] = "0" if approved else "4"
    # When: the real shell applies this release through the real approval transport.
    result = shell._run(tmp_path, "--apply", extra_env=deployment)
    calls = (tmp_path / "calls.log").read_text()
    # Then: only approval permits install, and install precedes timer observation.
    assert result.returncode == (0 if approved else 4), result.stdout + result.stderr
    authorization = next(line for line in calls.splitlines() if "authorize-cron-timer" in line)
    arguments = shlex.split(shlex.split(authorization)[-1])
    assert arguments[-5:-1] == ["authorize-cron-timer", "--name", "budget-watch", "--head"]
    if approved:
        assert calls.index("authorize-cron-timer") < calls.index("install --jobs budget-watch")
        assert calls.index("install --jobs budget-watch") < calls.index("--format actions")
    else:
        assert "install --jobs" not in calls
        assert "--format actions" not in calls


def test_apply_removes_old_marker_before_reprobe_when_host_flag_is_deleted(tmp_path, deployment):
    # Given: no host flag, but the node still has the preceding approved selection.
    deployment["FAKE_OLD_MARKER"] = "1"
    # When: the real shell receives the node marker mismatch action.
    result = shell._run(tmp_path, "--apply", extra_env=deployment)
    calls = (tmp_path / "calls.log").read_text()
    # Then: removing the marker precedes the probe that finds normal restoration work.
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls.count("--format actions") == 2
    assert calls.index("--format actions") < calls.index("install --jobs off") < calls.rindex("--format actions")
    assert "authorize-cron-timer" not in calls
