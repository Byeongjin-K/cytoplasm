"""Apply attempt status is separate from digest convergence.

Use an isolated shell checkout and real digest judgment with a local SSH substitute.
The frozen skill deployer is neither edited nor executed; its post-install failure
is reproduced by a subprocess that installs bytes before returning nonzero.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest

_REPO: Final = Path(__file__).resolve().parents[2]
_SSH: Final = '''#!/usr/bin/env python3
import hashlib
import os
import sys
from pathlib import Path
from automation.deploy_all import parse_observations, render_actions, render_plan, render_receipt

state = Path(os.environ["FAKE_STATE"])
command = sys.argv[-1]
with (state / "calls").open("a") as stream:
    stream.write(command + "\\n")
if command.startswith("readlink "):
    print("/srv/releases/" + os.environ["FAKE_HEAD"])
elif "--format " in command:
    expected = hashlib.sha256((state / "source").read_bytes()).hexdigest()
    mounted = hashlib.sha256((state / "installed").read_bytes()).hexdigest()
    lines = ["OBS|release|" + os.environ["FAKE_HEAD"], "OBS|mounts|judged",
             f"OBS|home|agent|.hermes/scripts/watch.py|automation/watch/watch.py|required|{expected}|{expected}"]
    if expected != mounted:
        lines.append(f"OBS|mount-stale|proposal|{expected}|{mounted}")
    plan = parse_observations([*lines, "OBS|end"])
    form = command.rsplit(" ", 1)[-1]
    if form == "actions":
        print(render_actions(plan))
    elif form == "report":
        print(render_plan(plan))
    elif form == "receipt":
        print(render_receipt(plan, verified_at="2026-09-20T00:00:00+00:00"), end="")
    else:
        sys.exit(97)
    sys.exit(0 if plan.clean else 1)
elif "cat >" in command and "/receipt.json" in command:
    (state / "receipt.json").write_bytes(sys.stdin.buffer.read())
elif "sha256sum" in command and "/receipt.json" in command:
    print(hashlib.sha256((state / "receipt.json").read_bytes()).hexdigest())
else:
    sys.exit("unexpected SSH command: " + command)
'''
_DEPLOYER: Final = '''#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$FAKE_STATE/deployer-calls"
if [[ "$FAKE_INSTALL" == 1 ]]; then
  cp "$FAKE_STATE/source" "$FAKE_STATE/installed"
fi
if [[ "$FAKE_DEPLOY_RC" != 0 ]]; then
  printf 'POST-MOUNT-SMOKE-FAIL\\n' >&2
fi
exit "$FAKE_DEPLOY_RC"
'''


@dataclass(frozen=True, slots=True)
class Deployment:
    root: Path
    state: Path
    env: dict[str, str]

    def run(self, mode: str = "--apply") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ("bash", str(self.root / "automation/deploy_all.sh"), mode),
            env=self.env, capture_output=True, text=True, check=False, timeout=20,
        )


@pytest.fixture
def deployment(tmp_path: Path) -> Deployment:
    root = tmp_path / "checkout"
    automation = root / "automation"
    automation.mkdir(parents=True)
    _ = shutil.copy2(_REPO / "automation/deploy_all.sh", automation / "deploy_all.sh")
    (automation / "node_config_sh.py").symlink_to(_REPO / "automation/node_config_sh.py")
    git_dir = subprocess.check_output(
        ("git", "rev-parse", "--absolute-git-dir"), cwd=_REPO, text=True,
    ).strip()
    (root / ".git").symlink_to(git_dir, target_is_directory=True)
    head = subprocess.check_output(
        ("git", "rev-parse", "HEAD"), cwd=_REPO, text=True,
    ).strip()
    state = tmp_path / "state"
    state.mkdir()
    _ = (state / "source").write_bytes(b"new skill bytes")
    _ = (state / "installed").write_bytes(b"old skill bytes")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for path, content in (
        (fake_bin / "ssh", _SSH),
        (automation / "deploy-skill.sh", _DEPLOYER),
    ):
        _ = path.write_text(content, encoding="utf-8")
        path.chmod(0o755)
    return Deployment(root, state, {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "PYTHONPATH": str(_REPO),
        "DEPLOY_SSH_HOST": "fake-node",
        "DEPLOY_ALL_WRAPPER_REFRESH_CMD": "true",
        "DEPLOY_ALL_LOCK_DIR": str(tmp_path / "locks"),
        "DEPLOY_ALL_RECEIPT_DIR": str(state),
        "HEALTHCHECK_NODE_CONFIG_PATH": str(_REPO / "configs/node.example.toml"),
        "FAKE_STATE": str(state),
        "FAKE_HEAD": head,
        "FAKE_DEPLOY_RC": "23",
        "FAKE_INSTALL": "1",
    })


@pytest.mark.parametrize("previous_receipt", [None, b'{"release_sha":"previous"}\n'])
def test_apply_fails_without_a_new_receipt_when_post_mount_smoke_fails(
    deployment: Deployment, previous_receipt: bytes | None,
) -> None:
    # Given: the deployer installs matching bytes, then fails its smoke.
    receipt = deployment.state / "receipt.json"
    if previous_receipt is not None:
        _ = receipt.write_bytes(previous_receipt)

    # When: the real shell orchestrator invokes that deployer.
    result = deployment.run()

    # Then: converged bytes cannot turn this failed attempt into completion.
    assert (deployment.state / "installed").read_bytes() == b"new skill bytes"
    assert (deployment.state / "deployer-calls").read_text() == "proposal --release-approval\n"
    assert result.returncode == 1, result.stdout + result.stderr
    assert "--format receipt" not in (deployment.state / "calls").read_text()
    if previous_receipt is None:
        assert not receipt.exists()
    else:
        assert receipt.read_bytes() == previous_receipt


@pytest.mark.parametrize("mode", ["--verify", "--apply"])
def test_digest_only_check_succeeds_when_a_previous_attempt_already_installed_bytes(
    deployment: Deployment, mode: str,
) -> None:
    # Given: an earlier failed smoke left matching bytes and no completion receipt.
    _ = shutil.copyfile(deployment.state / "source", deployment.state / "installed")

    # When: verifying digests or starting a new, already-converged apply attempt.
    result = deployment.run(mode)

    # Then: no deployer is invoked, and the existing digest-only contract holds.
    assert result.returncode == 0, result.stdout + result.stderr
    assert (deployment.state / "receipt.json").is_file()
    assert not (deployment.state / "deployer-calls").exists()


def test_apply_retry_succeeds_when_a_failed_attempt_left_drift(
    deployment: Deployment,
) -> None:
    # Given: the previous invocation failed without installing its bytes.
    failed = Deployment(deployment.root, deployment.state, {
        **deployment.env, "FAKE_INSTALL": "0",
    }).run()
    assert failed.returncode == 1, failed.stdout + failed.stderr
    retry = Deployment(deployment.root, deployment.state, {
        **deployment.env, "FAKE_DEPLOY_RC": "0",
    })

    # When: a fresh attempt successfully installs and verifies the same release.
    result = retry.run()

    # Then: the earlier failure is not latched across attempts.
    assert result.returncode == 0, result.stdout + result.stderr
    assert (deployment.state / "receipt.json").is_file()
    assert (deployment.state / "deployer-calls").read_text().splitlines() == [
        "proposal --release-approval", "proposal --release-approval",
    ]


def test_deploy_all_passes_the_release_approval_to_skill_deploys() -> None:
    source = (
        Path(__file__).resolve().parents[2] / "automation" / "deploy_all.sh"
    ).read_text(encoding="utf-8")

    assert (
        '"$repo_root/automation/deploy-skill.sh" "$arg" --release-approval'
        in source
    )


def test_deploy_all_actions_loop_reads_on_a_private_fd() -> None:
    """ssh inside the deployers drains fd 0, so the loop must not live there.

    2026-08-31 실측: `done <<< "$actions"` 로 actions 를 stdin 에 실었더니 첫
    deploy-skill 안의 ssh 가 남은 action 줄을 전부 삼켜 매 실행 한 건만 배포됐다.
    """
    source = (
        Path(__file__).resolve().parents[2] / "automation" / "deploy_all.sh"
    ).read_text(encoding="utf-8")

    assert "read -r -u 9 tag kind arg" in source
    assert 'done 9<<< "$actions"' in source
    assert 'done <<< "$actions"' not in source
