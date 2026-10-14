"""The release host may be the RAG node: local ops work must not self-SSH."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.unit.test_rag_stack_deploy import _AFTER, _BEFORE, _REPO, _apply, _digest, _executable, _fake

_SUDO = """#!/usr/bin/env python3
import json, os, pathlib, stat, subprocess, sys
assert sys.argv[1:5] == ["-n", "-u", "ops", "-H"]
args = sys.argv[5:]
assert os.environ["HOME"] == os.environ["FAKE_OPS_HOME"]
assert "/home/ops/personal-rag" not in " ".join(args)
with open(os.environ["OPS_CALLS"], "a") as f:
    f.write(json.dumps(args) + "\\n")
if args[0] == "cp":
    for src in args[2:-1]:
        p = pathlib.Path(src)
        assert stat.S_IMODE(p.stat().st_mode) & stat.S_IROTH
        assert stat.S_IMODE(p.parent.stat().st_mode) & stat.S_IXOTH
sys.exit(subprocess.run(args, check=False).returncode)
"""
_REFUSE_TRANSPORT = """#!/usr/bin/env bash
printf '%s\\n' "$0 $*" >> "$TRANSPORT_CALLS"
exit 97
"""
_OBSERVE_SSH = """#!/usr/bin/env bash
[[ "${*: -2:1}" == example-rag-node && "${*: -1}" == sudo* ]] || exit 97
printf 'ssh\\n' >> "$TRANSPORT_CALLS"
bash -c "${*: -1}" < /dev/null
"""


def _local_fake(tmp_path: Path) -> dict[str, str]:
    env = _fake(tmp_path)
    bin_dir = tmp_path / "bin"
    for name in ("ssh", "scp"):
        _executable(bin_dir / name, _REFUSE_TRANSPORT)
    _executable(bin_dir / "sudo", _SUDO)
    return {**env, "RAG_STACK_LOCAL_NODE": "example-rag-node",
            "FAKE_OPS_HOME": env["HOME"], "OPS_CALLS": str(tmp_path / "ops-calls"),
            "TRANSPORT_CALLS": str(tmp_path / "transport-calls"), "HEALTHCHECK_SSH_USER": ""}


@pytest.mark.parametrize("fail", ["0", "1"])
def test_rag_stack_local_apply_uses_readable_staging_and_no_self_ssh(
    tmp_path: Path, fail: str,
) -> None:
    env = {**_local_fake(tmp_path), "FAIL_SMOKE": fail}
    secret = Path(env["HOME"]) / "personal-rag/.env.secrets"
    before = secret.read_bytes()

    result = _apply(env)

    assert result.returncode == (1 if fail == "1" else 0), result.stdout + result.stderr
    assert not Path(env["TRANSPORT_CALLS"]).exists()
    state = json.loads(Path(env["DOCKER_STATE"]).read_text())
    assert state["image"] == (_BEFORE if fail == "1" else _AFTER)
    assert secret.read_bytes() == before
    calls = [json.loads(line) for line in Path(env["OPS_CALLS"]).read_text().splitlines()]
    copied = next(c for c in calls if c[0] == "cp")
    executed = next(c for c in calls if c[0] == "bash" and c[1].endswith("/remote.sh"))
    assert copied[-1].rstrip("/") == executed[-1]
    assert all(not Path(path).exists() for path in copied[2:])
    assert not list(Path(env["HOME"]).glob(".personal-rag.source.*"))
    assert result.stdout.rstrip().endswith("|rolled-back" if fail == "1" else "|verified")


def test_rag_stack_local_node_defaults_to_short_hostname(tmp_path: Path) -> None:
    env = _local_fake(tmp_path)
    env.pop("RAG_STACK_LOCAL_NODE")
    _executable(tmp_path / "bin/hostname", '#!/bin/sh\n[ "$1" = -s ] || exit 97\necho example-rag-node\n')

    result = _apply(env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert not Path(env["TRANSPORT_CALLS"]).exists()


@pytest.mark.parametrize("transport", ["local", "remote"])
@pytest.mark.parametrize("state", ["current", "source-stale", "image-stale", "unknown"])
def test_rag_stack_staleness_observes_sources_and_image_on_both_transports(
    tmp_path: Path, transport: str, state: str,
) -> None:
    env = _local_fake(tmp_path)
    home = Path(env["HOME"])
    shutil.copytree(_REPO / "configs/rag", home / "personal-rag", dirs_exist_ok=True)
    if transport == "remote":
        env["RAG_STACK_LOCAL_NODE"] = "different-node"
        _executable(tmp_path / "bin/ssh", _OBSERVE_SSH)
    if state == "source-stale":
        (home / "personal-rag/mcp/src/rag_mcp/store.py").write_text("# stale source\n")
    elif state == "image-stale":
        image = tmp_path / "image"
        shutil.copytree(_REPO / "configs/rag/mcp/src/rag_mcp", image)
        (image / "store.py").write_text("# stale image\n")
        env["FAKE_IMAGE_SOURCE"] = str(image)
    elif state == "unknown":
        _executable(tmp_path / "bin/docker", "#!/bin/sh\nexit 17\n")
    head = subprocess.run(("git", "rev-parse", "HEAD"), cwd=_REPO,
                          capture_output=True, text=True, check=True).stdout.strip()

    result = subprocess.run(("bash", "automation/rag_mcp_release.sh", "plan", head),
                            cwd=_REPO, env=env, capture_output=True, text=True,
                            check=False, timeout=30)

    assert result.returncode == (4 if state == "unknown" else 0), result.stdout + result.stderr
    assert result.stdout.strip() == (_digest() if state.endswith("stale") else "")
    assert Path(env["TRANSPORT_CALLS"]).exists() == (transport == "remote")
    assert any("docker" in str(c) for c in [
        json.loads(line) for line in Path(env["OPS_CALLS"]).read_text().splitlines()
    ])
