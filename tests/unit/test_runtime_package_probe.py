"""Runtime package drift must be visible when release convergence does not deploy it."""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_PROBE = _REPO / "automation" / "runtime_package_probe.sh"
_MANIFEST = _REPO / "configs" / "runtime-package-manifest.txt"
_HEALTHCHECK = _REPO / "automation" / "healthcheck.sh"
_WRAPPER_GENERATOR = _REPO / "automation" / "healthcheck_probe_wrapper.sh"


def _manifest(tmp_path: Path, *rows: str) -> Path:
    path = tmp_path / "manifest.txt"
    _ = path.write_text("# fixture\n\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def _put(root: Path, relative: str, content: str = "pass\n") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(content, encoding="utf-8")


def _run(tmp_path: Path, manifest: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (
            "bash",
            "-c",
            f'source "{_PROBE}"; probe_runtime_packages_current node ops "{manifest}"',
        ),
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "HEALTHCHECK_RELEASE_SOURCE_ROOT": str(tmp_path / "release"),
            "HEALTHCHECK_RUNTIME_PACKAGE_ROOT": str(tmp_path / "home"),
            "NODE_RAG_NODE_NAME": "example-rag-node",
        },
    )


def _fixture(tmp_path: Path, row: str = "agent|automation/pkg|.hermes/pkg_runtime/pkg|required") -> tuple[Path, Path]:
    fields = row.split("|")
    release = tmp_path / "release" / fields[1]
    runtime = tmp_path / "home" / fields[2]
    _ = _manifest(tmp_path, row)
    return release, runtime


def test_matching_recursive_python_tree_passes(tmp_path: Path) -> None:
    release, runtime = _fixture(tmp_path)
    _put(release, "__init__.py")
    _put(release, "sources/item.py", "VALUE = 1\n")
    _put(runtime, "__init__.py")
    _put(runtime, "sources/item.py", "VALUE = 1\n")

    result = _run(tmp_path, tmp_path / "manifest.txt")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "RUNTIME-PACKAGE-PASS" in result.stderr


def test_deployed_file_list_filters_only_release_files(tmp_path: Path) -> None:
    """A flat partial deploy must not report its intentionally unshipped files absent."""
    listed = (
        "agent|automation/pkg|.hermes/pkg_runtime|required|default|python|"
        "first.py,second.py"
    )
    release, runtime = _fixture(tmp_path, listed)
    for relative in ("first.py", "second.py", "third.py", "fourth.py"):
        _put(release, relative, f"{relative}\n")
    for relative in ("first.py", "second.py"):
        _put(runtime, relative, f"{relative}\n")

    filtered = _run(tmp_path, tmp_path / "manifest.txt")
    unfiltered = _manifest(
        tmp_path, "agent|automation/pkg|.hermes/pkg_runtime|required|default|python"
    )
    unfiltered_result = _run(tmp_path, unfiltered)

    assert filtered.returncode == 0, filtered.stdout + filtered.stderr
    assert "RUNTIME-PACKAGE-PASS" in filtered.stderr
    assert unfiltered_result.returncode != 0
    assert "ABSENT third.py" in unfiltered_result.stderr


def test_local_snapshot_ignores_an_inaccessible_inherited_working_directory(
    tmp_path: Path,
) -> None:
    # Given: the probe inherited a directory it can no longer traverse, as ops does over SSH.
    release, _runtime = _fixture(tmp_path)
    _put(release, "item.py")
    inherited = tmp_path / "operator-home"
    inherited.mkdir()
    script = f'''
cd "{inherited}"
chmod 000 "{inherited}"
source "{_PROBE}"
runtime_package_local_snapshot "{release}" python
status=$?
chmod 700 "{inherited}"
exit "$status"
'''

    # When: the release snapshot reads its absolute source root.
    result = subprocess.run(("bash", "-c", script), capture_output=True, text=True, check=False)

    # Then: the unrelated inherited cwd cannot turn a readable release into UNKNOWN.
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("SNAPSHOT-V1\n")


def test_diff_fails_and_names_deployer(tmp_path: Path) -> None:
    release, runtime = _fixture(tmp_path)
    _put(release, "sources/item.py", "NEW = 1\n")
    _put(runtime, "sources/item.py", "OLD = 1\n")

    result = _run(tmp_path, tmp_path / "manifest.txt")

    assert result.returncode != 0
    assert "DIFF sources/item.py" in result.stderr
    assert "automation/pkg/deploy.sh" in result.stderr


def test_absent_file_fails(tmp_path: Path) -> None:
    release, runtime = _fixture(tmp_path)
    _put(release, "sources/new.py")
    runtime.mkdir(parents=True)

    result = _run(tmp_path, tmp_path / "manifest.txt")

    assert result.returncode != 0
    assert "ABSENT sources/new.py" in result.stderr


def test_unreadable_runtime_fails_closed_as_unknown(tmp_path: Path) -> None:
    release, runtime = _fixture(tmp_path)
    _put(release, "item.py")
    runtime.mkdir(parents=True)
    runtime.chmod(0)
    try:
        result = _run(tmp_path, tmp_path / "manifest.txt")
    finally:
        runtime.chmod(0o700)

    assert result.returncode != 0
    assert "RUNTIME-PACKAGE-UNKNOWN" in result.stderr
    assert "ABSENT" not in result.stderr


def test_cache_directories_are_ignored(tmp_path: Path) -> None:
    release, runtime = _fixture(tmp_path)
    _put(release, "item.py")
    _put(runtime, "item.py")
    for cache in ("__pycache__", ".venv", ".ruff_cache", ".pytest_cache"):
        _put(runtime, f"{cache}/stale.py", "stale cache content\n")

    result = _run(tmp_path, tmp_path / "manifest.txt")

    assert result.returncode == 0, result.stdout + result.stderr


def test_cron_watcher_wrapper_is_not_part_of_the_runtime_package(tmp_path: Path) -> None:
    """cron/ wrappers deploy to ~/.hermes/scripts (watcher manifest), never into the package."""
    release, runtime = _fixture(tmp_path)
    _put(release, "item.py")
    _put(release, "cron/pkg_watch.py", "WATCH = 1\n")
    _put(runtime, "item.py")

    result = _run(tmp_path, tmp_path / "manifest.txt")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "ABSENT cron/pkg_watch.py" not in result.stderr
    assert "RUNTIME-PACKAGE-PASS" in result.stderr


def test_tree_profile_hashes_build_inputs_but_excludes_node_secret(tmp_path: Path) -> None:
    release, runtime = _fixture(
        tmp_path,
        "ops|configs/rag|personal-rag|required|rag|tree",
    )
    for relative in ("compose.yaml", "mcp/Dockerfile", "mcp/pyproject.toml", "mcp/uv.lock", "mcp/src/app.py"):
        _put(release, relative, f"release {relative}\n")
        _put(runtime, relative, f"release {relative}\n")
    _put(runtime, ".env.secrets", "node-only\n")

    matching = _run(tmp_path, tmp_path / "manifest.txt")
    _put(runtime, "mcp/Dockerfile", "stale build\n")
    stale = _run(tmp_path, tmp_path / "manifest.txt")

    assert matching.returncode == 0, matching.stdout + matching.stderr
    assert stale.returncode != 0
    assert "DIFF mcp/Dockerfile" in stale.stderr


def test_rag_node_column_is_resolved_for_remote_snapshot(tmp_path: Path) -> None:
    release = tmp_path / "release" / "configs" / "rag"
    _put(release, "item.py", "same\n")
    manifest = _manifest(tmp_path, "ops|configs/rag|personal-rag|required|rag|python")
    journal = tmp_path / "node.txt"
    digest = hashlib.sha256(b"same\n").hexdigest()
    script = f'''source "{_PROBE}"
NODE_RAG_NODE_NAME=example-rag-node
capture_on_node() {{ printf '%s' "$1" > "{journal}"; printf 'SNAPSHOT-V1\\n%s|item.py\\n' "{digest}"; }}
HEALTHCHECK_RELEASE_SOURCE_ROOT="{tmp_path / 'release'}" probe_runtime_packages_current example-primary-node ops "{manifest}"
'''

    result = subprocess.run(("bash", "-c", script), capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr
    assert journal.read_text(encoding="utf-8") == "example-rag-node"


def test_running_mcp_image_diff_fails_closed(tmp_path: Path) -> None:
    release = tmp_path / "rag"
    _put(release, "mcp/src/rag_mcp/app.py", "new app\n")
    _put(release, "mcp/src/rag_mcp/store.py", "new store\n")
    stale = "0" * 64
    script = f'''source "{_PROBE}"
capture_on_node() {{ printf '%s  /app/src/rag_mcp/app.py\\n%s  /app/src/rag_mcp/store.py\\n' "{stale}" "{stale}"; }}
runtime_package_verify_rag_image example-rag-node ops "{release}" automation/rag_stack/deploy.sh
'''

    result = subprocess.run(("bash", "-c", script), capture_output=True, text=True, check=False)

    assert result.returncode != 0
    assert "IMAGE-DIFF mcp/src/rag_mcp/app.py" in result.stderr
    assert "automation/rag_stack/deploy.sh" in result.stderr


def test_extra_python_file_is_reported(tmp_path: Path) -> None:
    release, runtime = _fixture(tmp_path)
    _put(release, "item.py")
    _put(runtime, "item.py")
    _put(runtime, "stale.py")

    result = _run(tmp_path, tmp_path / "manifest.txt")

    assert result.returncode != 0
    assert "EXTRA stale.py" in result.stderr


def test_manifest_parser_accepts_comments_and_blank_lines() -> None:
    rows: list[tuple[str, ...]] = []
    for line in _MANIFEST.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = line.split("|")
        assert len(fields) in (4, 6, 7), line
        rows.append(tuple(fields))

    assert rows == [
        ("agent", "automation/rag_ingest", ".hermes/rag_ingest_runtime/rag_ingest", "required"),
        ("agent", "automation/regression_bank", ".hermes/regression_bank_runtime", "required", "default", "python", "bank_state.py,weekly_bank.py"),
        ("ops", "configs/rag", "personal-rag", "required", "rag", "tree"),
    ]


def test_healthcheck_wires_runtime_probe_remotely() -> None:
    text = _HEALTHCHECK.read_text(encoding="utf-8")
    assert "runtime_package_probe.sh" in text
    assert "primary_runtime_packages_current" in text
    assert "rag_stack_current" in text
    assert "$RAG_NODE personal RAG source and MCP image match the release" in text
    local = next(line for line in text.splitlines() if line.startswith("readonly LOCAL_PROBES="))
    assert "runtime_packages_current" not in local


def test_allowlist_provenance_moves_with_runtime_manifest(tmp_path: Path) -> None:
    base_env = {**os.environ, "HEALTHCHECK_SSH_USER": "", "HEALTHCHECK_SSH_IDENTITY": ""}
    before = subprocess.run(
        ("bash", str(_WRAPPER_GENERATOR), "--inputs-digest"),
        capture_output=True, text=True, check=True, env=base_env,
    ).stdout.strip()
    changed = _manifest(tmp_path, "agent|automation/pkg|.hermes/other/pkg|required")
    after = subprocess.run(
        ("bash", str(_WRAPPER_GENERATOR), "--inputs-digest"),
        capture_output=True, text=True, check=True,
        env={**base_env, "HEALTHCHECK_RUNTIME_PACKAGE_MANIFEST": str(changed)},
    ).stdout.strip()

    assert before != after
