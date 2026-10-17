"""RCB todo 16: the research_trends runtime and prompt copies are declared.

The flat runtime copy stays (the watcher must work whatever the release state is); what was
missing is that the release can see the copy and put it back. Three lists must agree: the
declaration's `files=`, the deployer's `deploy_tree_swap` arguments and the standing
runtime-package table row.
"""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
from pathlib import Path

from automation.deploy_declarations import Declaration, parse_declaration_file
from tests.unit.cron_fixture import timer_env

_REPO = Path(__file__).resolve().parents[2]
_PKG = _REPO / "automation" / "research_trends"
_DEPLOYER = _PKG / "deploy.sh"
_DECLARATION = "automation/research_trends/deploy-manifest.txt"
_TABLE = _REPO / "configs" / "runtime-package-manifest.txt"
_RUNTIME_DEST = ".hermes/research_trends_runtime"
_FILES = ("research_trends.py", "research_trends_core.py", "topics_import.py")
_SWAP = re.compile(
    r'deploy_tree_swap\s+"\$repo_root/automation/research_trends"\s+(?P<dest>\S+)(?P<files>(?:\s+[\w.]+\.py)+)'
)
_PUSH = re.compile(r'push_file\s+"\$repo_root/(?P<src>[^"]+)"\s+\'(?P<dest>[^\']+)\'')


def _declarations() -> tuple[Declaration, ...]:
    return parse_declaration_file(_DECLARATION, (_REPO / _DECLARATION).read_text(encoding="utf-8"))


def _tree_declaration() -> Declaration:
    rows = [d for d in _declarations() if d.destination == _RUNTIME_DEST]
    assert len(rows) == 1, rows
    return rows[0]


def _swap_call() -> tuple[str, tuple[str, ...]]:
    match = _SWAP.search(_DEPLOYER.read_text(encoding="utf-8"))
    assert match is not None, "deploy_tree_swap call not found in the research_trends deployer"
    return match.group("dest"), tuple(match.group("files").split())


def _table_files() -> tuple[str, ...]:
    for line in _TABLE.read_text(encoding="utf-8").splitlines():
        fields = line.split("|")
        if len(fields) == 7 and fields[2] == _RUNTIME_DEST:
            return tuple(fields[6].split(","))
    raise AssertionError("runtime-package table has no research_trends row")


def test_runtime_tree_declaration_lists_exactly_the_shipped_files() -> None:
    declaration = _tree_declaration()
    assert (declaration.account, declaration.source, declaration.kind) == (
        "agent", "automation/research_trends", "tree",
    )
    assert declaration.policy == "required"
    declared = tuple(declaration.attr("files").split(","))
    dest, swapped = _swap_call()
    assert dest == _RUNTIME_DEST
    assert declared == swapped == _table_files() == _FILES


def test_prompt_copy_is_declared_where_the_deployer_pushes_it() -> None:
    pushed = {m.group("dest"): m.group("src") for m in _PUSH.finditer(_DEPLOYER.read_text(encoding="utf-8"))}
    file_rows = {d.destination: d for d in _declarations() if d.kind == "file"}
    for source, dest in (
        ("prompts/research-trends-v1.md", ".hermes/research-trends/research-trends-v1.md"),
    ):
        assert pushed.get(dest) == source
        row = file_rows[dest]
        assert (row.account, row.source, row.policy, row.legacy) == ("agent", source, "required", False)


def _load_runtime_modules_test() -> object:
    spec = importlib.util.spec_from_file_location(
        "rt_runtime_modules", _REPO / "tests/unit/test_research_trends_runtime_modules.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_runtime_import_missing_from_the_shipped_list_is_rejected(tmp_path: Path) -> None:
    module = _load_runtime_modules_test()
    source = (_PKG / "research_trends.py").read_text(encoding="utf-8")
    mutated = source + "\nfrom automation.research_trends.extra import x\n"
    imported = module._runtime_self_imports(mutated)
    declared = set(_tree_declaration().attr("files").split(","))
    assert sorted(f"{name}.py" for name in imported if f"{name}.py" not in declared) == ["extra.py"]
    clean = module._runtime_self_imports(source)
    assert all(f"{name}.py" in declared for name in clean)


def _rig(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    home, bin_dir = tmp_path / "home", tmp_path / "bin"
    (home / ".hermes").mkdir(parents=True)
    bin_dir.mkdir()
    (home / ".hermes/config.yaml").write_text("timezone: Asia/Seoul\n", encoding="utf-8")
    (bin_dir / "ssh").write_text('#!/usr/bin/env bash\nshift\nexec bash -c "$*"\n', encoding="utf-8")
    (bin_dir / "sudo").write_text(
        '#!/usr/bin/env bash\nwhile [[ "$1" == -* ]]; do case "$1" in -u) shift 2;; *) shift;; esac; done\n'
        'if [[ "${FAULT:-}" == drop-prepare && "$*" == *_deploy_tree_remote_prepare* ]]; then cat >/dev/null; exit 0; fi\n'
        'HOME="$NODE_HOME" exec "$@"\n',
        encoding="utf-8",
    )
    (bin_dir / "hermes").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    for name in ("ssh", "sudo", "hermes"):
        (bin_dir / name).chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}", "NODE_HOME": str(home), "HOME": str(tmp_path / "ops"),
        "DEPLOY_SSH_HOST": "fake-host", "DEPLOY_ALLOW_UNPUSHED": "1", "LANG": "C.UTF-8",
        **timer_env(tmp_path / "timer"),
    }
    (tmp_path / "ops").mkdir()
    return home, env


def _deploy(env: dict[str, str], fault: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("bash", str(_DEPLOYER)), env={**env, "FAULT": fault}, cwd=_REPO,
        capture_output=True, text=True, check=False, timeout=120,
    )


def test_research_trends_deploy_uses_the_swap_helper(tmp_path: Path) -> None:
    text = _DEPLOYER.read_text(encoding="utf-8")
    assert text.count("rm -rf") == 0 and "deploy_tree_swap" in text
    home, env = _rig(tmp_path)
    runtime = home / _RUNTIME_DEST
    runtime.mkdir()
    (runtime / "research_trends.py").write_text("old\n", encoding="utf-8")
    before = (runtime / "research_trends.py").read_bytes()

    failed = _deploy(env, "drop-prepare")
    assert failed.returncode != 0, failed.stdout + failed.stderr
    assert sorted(p.name for p in runtime.iterdir()) == ["research_trends.py"]
    assert (runtime / "research_trends.py").read_bytes() == before
    assert not list((home / ".hermes").glob("research_trends_runtime.staging.*"))

    done = _deploy(env)
    assert done.returncode == 0, done.stdout + done.stderr
    assert runtime.is_dir() and not runtime.is_symlink()
    for name in _FILES:
        assert (runtime / name).read_bytes() == (_PKG / name).read_bytes()
    assert sorted(p.name for p in runtime.iterdir()) == sorted(_FILES)
    assert not (home / ".hermes/sensitivity-rules.yaml").exists()
    assert (home / ".hermes/research-trends/research-trends-v1.md").read_bytes() == (
        _REPO / "prompts/research-trends-v1.md"
    ).read_bytes()
    assert list((home / ".hermes").glob("research_trends_runtime.old.*"))
