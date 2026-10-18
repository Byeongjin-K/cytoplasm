"""Drive the real source sync and MCP apply with local SSH/Docker fakes, never a node."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from automation import release_approval, release_card, release_spec
from automation.deploy_receipt import pending_lines, render_receipt
from tests.unit.test_deploy_receipt import _plan

_REPO = Path(__file__).resolve().parents[2]
_BEFORE = "sha256:" + "a" * 64
_AFTER = "sha256:" + "b" * 64

_DOCKER = """#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
p = pathlib.Path(os.environ["DOCKER_STATE"])
state = json.loads(p.read_text())
with open(os.environ["FAKE_CALLS"], "a") as f:
    f.write(json.dumps(args) + "\\n")
if args[:2] == ["image", "tag"]:
    state["tags"][args[3]] = args[2]
elif args[0] == "inspect":
    print("personal_rag" if ".Config.Labels" in args[2] else state["image"])
elif args[0] == "ps":
    print("mcp-container")
elif args[0] == "exec":
    import hashlib
    for file in args[3:]:
        content = pathlib.Path(os.environ["FAKE_IMAGE_SOURCE"], pathlib.Path(file).name).read_bytes()
        print(hashlib.sha256(content).hexdigest(), file)
elif "ps" in args:
    print("mcp-container")
elif "up" in args:
    if "--build" in args:
        state["image"] = os.environ["AFTER"]
    else:
        text = pathlib.Path(args[args.index("up")-1]).read_text()
        state["image"] = state["tags"][text.split("image: ")[1].strip()]
else:
    sys.exit(97)
p.write_text(json.dumps(state))
"""
_SSH = """#!/usr/bin/env bash
[[ "$1" == -n && "$2" == fake-rag && "$3" == sudo* ]] || exit 97
bash -c "$3" < /dev/null
"""
_SCP = """#!/usr/bin/env python3
import shutil, sys
args = [a for a in sys.argv[1:] if a != "-q"]
host, dest = args[-1].split(":", 1)
assert host == "ops@fake-rag"
for src in args[:-1]:
    shutil.copy(src, dest)
"""
_PYTHON = """#!/usr/bin/env bash
if [[ "$1" == */smoke.py ]]; then
  python3.real -c 'import json,os,sys; s=json.load(open(os.environ["DOCKER_STATE"])); sys.exit(1 if s["image"]==os.environ["AFTER"] and os.environ["FAIL_SMOKE"]=="1" else 0)'
else exec python3.real "$@"; fi
"""


def _executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def _fake(tmp_path: Path) -> dict[str, str]:
    """Fake SSH executes only the explicit ops command locally; Docker has image state."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, text in {"ssh": _SSH, "scp": _SCP, "docker": _DOCKER, "python3": _PYTHON,
                       "sudo": '#!/usr/bin/env bash\nshift 4; exec "$@"\n'}.items():
        _executable(bin_dir / name, text)
    (bin_dir / "python3.real").symlink_to(shutil.which("python3") or "/usr/bin/python3")
    home = tmp_path / "ops"
    home.mkdir()
    old = home / "personal-rag"
    old.mkdir()
    (old / ".env.secrets").write_text("RAG_MCP_API_KEY=synthetic\nMCP_BIND_ADDRESS=127.0.0.1\n")
    (old / ".env.secrets").chmod(0o600)
    state = tmp_path / "docker.json"
    state.write_text(json.dumps({"image": _BEFORE, "tags": {}}))
    return {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(home),
            "DOCKER_STATE": str(state), "FAKE_CALLS": str(tmp_path / "calls"),
            "AFTER": _AFTER, "FAIL_SMOKE": "0", "RAG_STACK_SSH_HOST": "fake-rag",
            "DEPLOY_ALLOW_UNPUSHED": "1", "RAG_MCP_AUTHORIZE_CMD": "true",
            "RAG_STACK_LOCAL_NODE": "different-node",
            "HEALTHCHECK_NODE_CONFIG_PATH": str(_REPO / "configs/node.example.toml"),
            "FAKE_IMAGE_SOURCE": str(_REPO / "configs/rag/mcp/src/rag_mcp")}


def _digest() -> str:
    return subprocess.run(
        ("bash", "-c", 'source automation/runtime_package_probe.sh; '
         'LC_ALL=C runtime_package_local_snapshot "$PWD/configs/rag" tree | sha256sum | cut -d" " -f1'),
        cwd=_REPO, capture_output=True, text=True, check=True,
    ).stdout.strip()


def _apply(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    head = subprocess.run(("git", "rev-parse", "HEAD"), cwd=_REPO,
                          capture_output=True, text=True, check=True).stdout.strip()
    return subprocess.run(
        ("bash", "automation/rag_stack/deploy.sh", "--release-approval", head, _digest()),
        cwd=_REPO, env=env, capture_output=True, text=True, check=False, timeout=30,
    )


@pytest.mark.parametrize("fail", ["0", "1"])
def test_rag_stack_apply_rebuilds_only_mcp_and_rolls_back_on_failed_smoke(
    tmp_path: Path, fail: str,
) -> None:
    env = {**_fake(tmp_path), "FAIL_SMOKE": fail}
    secret = Path(env["HOME"]) / "personal-rag/.env.secrets"
    before = secret.read_bytes()

    result = _apply(env)

    assert result.returncode == (1 if fail == "1" else 0), result.stdout + result.stderr
    state = json.loads(Path(env["DOCKER_STATE"]).read_text())
    calls = [json.loads(line) for line in Path(env["FAKE_CALLS"]).read_text().splitlines()]
    up = [c for c in calls if "up" in c]
    assert len(up) == (2 if fail == "1" else 1)
    assert all(c[-1] == "mcp" and "--no-deps" in c for c in up)
    assert not any("embedding" in c for c in calls)
    assert state["image"] == (_BEFORE if fail == "1" else _AFTER)
    assert secret.read_bytes() == before and secret.stat().st_mode & 0o777 == 0o600
    assert f"RAG-MCP-REFRESH|{'failed' if fail == '1' else 'installed'}|{_BEFORE}|{_AFTER}|" in result.stdout
    if fail == "1":
        assert "--no-build" in up[-1] and "--pull" in up[-1]
        assert result.stdout.rstrip().endswith("|rolled-back")


def test_rag_stack_apply_refuses_without_release_authorization(tmp_path: Path) -> None:
    env = {**_fake(tmp_path), "RAG_MCP_AUTHORIZE_CMD": "false"}

    result = _apply(env)

    assert result.returncode == 4
    assert not Path(env["FAKE_CALLS"]).exists()
    assert json.loads(Path(env["DOCKER_STATE"]).read_text())["image"] == _BEFORE


def test_rag_stack_source_sync_does_not_activate(tmp_path: Path) -> None:
    env = _fake(tmp_path)

    result = subprocess.run(("bash", "automation/rag_stack/deploy.sh"), cwd=_REPO, env=env,
                            capture_output=True, text=True, check=False, timeout=30)

    assert result.returncode == 0, result.stdout + result.stderr
    assert not Path(env["FAKE_CALLS"]).exists()
    assert (Path(env["HOME"]) / "personal-rag/mcp/src/rag_mcp/app.py").read_bytes() == (
        _REPO / "configs/rag/mcp/src/rag_mcp/app.py").read_bytes()


def test_rag_stack_card_notice_is_bound_and_absent_card_bytes_are_unchanged() -> None:
    spec = release_spec.ReleaseSpec("v1.2.3", "a" * 40, "b" * 32,
                                    (("repo", "c" * 64),), "changes")
    previous = spec.render()
    rag = replace(spec, surface_digests=(*spec.surface_digests, ("rag-mcp", "d" * 64)))

    rendered = rag.render()

    assert rendered.count("검색 잠깐 끊김(~1분)·되돌림 있음") == 1
    assert spec.render() == previous
    assert rag.action_hash() != spec.action_hash()
    assert "검색 잠깐 끊김" not in previous


@pytest.mark.parametrize("status", ["installed", "failed", "skipped"])
def test_rag_stack_receipt_records_result_and_reports_failure(status: str) -> None:
    plan = _plan("OBS|pending|rag_stack_current|rag|PASS|rag current")

    receipt = json.loads(render_receipt(plan, verified_at="fixed",
        rag_mcp_refresh=f"{status}|{_BEFORE}|{_AFTER}|verified"))

    assert receipt["rag_mcp_refresh"]["image_before"] == _BEFORE
    assert receipt["rag_mcp_refresh"]["status"] == status
    assert bool(receipt["pending_owner_actions"]) == (status != "installed")
    assert bool(pending_lines(receipt)) == (status != "installed")


@pytest.mark.parametrize("sentinel", [True, False])
def test_rag_stack_deploy_all_passes_failed_effect_to_receipt(
    tmp_path: Path, sentinel: bool,
) -> None:
    import shlex

    from tests.unit.test_deploy_all_cli import _run

    refresh = tmp_path / "refresh"
    line = f"RAG-MCP-REFRESH|failed|{_BEFORE}|{_AFTER}|rolled-back"
    _executable(refresh, "#!/bin/sh\n" + (f"echo '{line}'\n" if sentinel else "") + "exit 1\n")

    result = _run(tmp_path, "--apply",
                  extra_env={"DEPLOY_ALL_RAG_MCP_REFRESH_CMD": str(refresh)})

    assert result.returncode == 0, result.stdout + result.stderr
    commands = (tmp_path / "calls.log").read_text().splitlines()
    receipt = next(shlex.split(c) for c in commands if "--format receipt" in c)
    assert receipt[receipt.index("--rag-mcp-refresh") + 1] == (
        line.removeprefix("RAG-MCP-REFRESH|") if sentinel else "failed|-|-|refresh-unfinished"
    )


def test_rag_stack_authorize_cli_reuses_head_and_surface_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = []
    monkeypatch.setattr(release_approval.skill_gate, "cmd_release_authorize",
                        lambda args: captured.append(args) or 4)

    result = release_approval.main(["authorize-rag-mcp", "--head", "a" * 40, "--hash", "b" * 64])

    assert result == 4
    assert captured[0].surface == "rag-mcp" and captured[0].head == "a" * 40
    assert captured[0].hash == "b" * 64


def test_rag_stack_preflight_never_drops_outage_notice_on_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = release_spec.ReleaseSpec("v1.2.3", "a" * 40, "b" * 32,
                                    (("rag-mcp", "c" * 64),), "changes")
    def unavailable(*_args: object, **_kwargs: object) -> str:
        raise release_spec.EnvelopeUnavailable("unavailable")
    monkeypatch.setattr(release_spec, "render_v6", unavailable)
    monkeypatch.setattr(release_spec, "render_v7", unavailable)

    candidate, refusal = release_card.preflight_new_request(spec)

    assert candidate is None and refusal


@pytest.mark.parametrize("verdict", ["PASS", "DIFF", "UNKNOWN"])
def test_rag_stack_plan_adds_action_only_for_observed_stale(
    tmp_path: Path, verdict: str,
) -> None:
    probe = tmp_path / "probe"
    _executable(probe, f'#!/bin/sh\necho RUNTIME-PACKAGE-{verdict}\n'
                f'exit {0 if verdict == "PASS" else 1}\n')
    head = subprocess.run(("git", "rev-parse", "HEAD"), cwd=_REPO,
                          capture_output=True, text=True, check=True).stdout.strip()
    env = {**os.environ, "RAG_MCP_PROBE_CMD": str(probe),
           "HEALTHCHECK_NODE_CONFIG_PATH": str(_REPO / "configs/node.example.toml")}

    result = subprocess.run(("bash", "automation/rag_mcp_release.sh", "plan", head),
                            cwd=_REPO, env=env, capture_output=True, text=True, check=False)

    assert result.returncode == (4 if verdict == "UNKNOWN" else 0), result.stderr
    assert result.stdout.strip() == (_digest() if verdict == "DIFF" else "")
