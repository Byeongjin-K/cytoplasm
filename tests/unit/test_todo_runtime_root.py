"""Runtime-root SSOT regressions for the mounted todo skill."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest
import unit_node


_REPO = Path(__file__).resolve().parents[2]
_TODO_SCRIPTS = _REPO / "skills" / "todo" / "scripts"
sys.path.insert(0, str(_TODO_SCRIPTS))


def test_preflight_repo_root_uses_only_loaded_resolver_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the locator loads a resolver whose answer differs from every local candidate.
    preflight = import_module("todo_preflight")
    sentinel = tmp_path / "resolver-sentinel"
    sentinel.mkdir()
    seen: list[Mapping[str, str]] = []

    def resolve_runtime_root(env: Mapping[str, str]) -> Path:
        seen.append(env)
        return sentinel

    monkeypatch.setattr(
        preflight,
        "_load_runtime_root_module",
        lambda: SimpleNamespace(resolve_runtime_root=resolve_runtime_root),
        raising=False,
    )
    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(tmp_path / "legacy-override"))

    # When: preflight resolves the repository root.
    resolved = preflight.repo_root()

    # Then: it returns only the shared resolver's answer and passes the process environment.
    assert resolved == sentinel
    assert seen == [os.environ]


def test_todo_cli_repo_root_delegates_to_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: preflight's SSOT seam returns a sentinel unrelated to todo_cli's own path.
    todo = import_module("todo_cli")
    preflight = import_module("todo_preflight")
    sentinel = tmp_path / "delegated-root"
    monkeypatch.setattr(preflight, "repo_root", lambda: sentinel)
    monkeypatch.delenv("AUTOPHAGY_REPO_ROOT", raising=False)

    # When / Then: the CLI caller returns the delegated result unchanged.
    assert todo.repo_root() == sentinel


def test_runtime_root_diagnostic_works_from_isolated_mounted_layout(tmp_path: Path) -> None:
    # Given: only mounted todo scripts plus a runtime root are visible from an outside cwd.
    mounted_scripts = tmp_path / "releases" / "todo" / "digest" / "scripts"
    mounted_scripts.mkdir(parents=True)
    for name in ("todo_cli.py", "todo_cli_model.py", "todo_preflight.py"):
        shutil.copy2(_TODO_SCRIPTS / name, mounted_scripts / name)
    runtime_root = tmp_path / "runtime"
    automation = runtime_root / "automation"
    automation.mkdir(parents=True)
    shutil.copy2(_REPO / "automation" / "runtime_root.py", automation / "runtime_root.py")
    outside = tmp_path / "outside"
    outside.mkdir()
    env = {
        **unit_node.env(),
        "AUTOPHAGY_RUNTIME_ROOT": str(runtime_root),
        "HOME": str(tmp_path / "home"),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": "",
    }

    # When: the diagnostic runs with an empty PYTHONPATH outside both trees.
    result = subprocess.run(
        (sys.executable, str(mounted_scripts / "todo_cli.py"), "runtime-root"),
        cwd=outside,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    # Then: it prints the shared resolver's injected root as one line.
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{runtime_root}\n"


def test_staged_scenario_preserves_explicit_deploy_repo_root(tmp_path: Path) -> None:
    # Given: the todo skill is staged alone while deployment supplies a full runtime root.
    mounted_scripts = tmp_path / "staging" / "todo" / "scripts"
    shutil.copytree(_TODO_SCRIPTS, mounted_scripts)
    outside = tmp_path / "outside"
    outside.mkdir()
    env = {
        **unit_node.env(),
        "AUTOPHAGY_DEMO_SECRET": "DUMMY-staged-scenario",
        "AUTOPHAGY_REPO_ROOT": str(_REPO),
        "HOME": str(tmp_path / "home"),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": "",
    }

    # When: the staged scenario runs under the deploy wrapper's minimal environment.
    result = subprocess.run(
        ("bash", str(mounted_scripts / "scenario.sh")),
        cwd=outside,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    # Then: it uses the supplied runtime for the complete offline approval cycle.
    assert result.returncode == 0, result.stderr
    assert "APPROVAL-CYCLE-PASS" in result.stdout
    assert "SCENARIO-PASS" in result.stdout


def test_preflight_fails_closed_when_runtime_root_module_is_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: every locator candidate points at an absent runtime_root.py.
    preflight = import_module("todo_preflight")
    missing = tuple(tmp_path / name / "automation" / "runtime_root.py" for name in ("a", "b"))
    monkeypatch.setattr(
        preflight,
        "_runtime_root_module_candidates",
        lambda: missing,
        raising=False,
    )
    monkeypatch.delenv("AUTOPHAGY_REPO_ROOT", raising=False)
    monkeypatch.delenv("AUTOPHAGY_RUNTIME_ROOT", raising=False)

    # When / Then: preflight refuses instead of returning a guessed legacy checkout.
    with pytest.raises(preflight.TodoPreflightError) as caught:
        preflight.repo_root()
    assert caught.value.exit_code == 3


def test_scenario_asks_cli_for_runtime_root_instead_of_resolving_candidates() -> None:
    # Given / When: the offline scenario's bootstrap is inspected as executable policy.
    scenario = (_TODO_SCRIPTS / "scenario.sh").read_text(encoding="utf-8")

    # Then: it delegates root choice and carries no release/mirror fallback of its own.
    assert 'todo_cli.py" runtime-root' in scenario
    assert "for candidate in" not in scenario
    assert "/srv/autophagy-agents" not in scenario


def test_staged_scenario_keeps_an_inherited_runtime_root_when_deploy_root_is_absent(
    tmp_path: Path,
) -> None:
    """peer 검토는 `AUTOPHAGY_REPO_ROOT` 없이 시나리오를 직접 재실행한다.

    2026-08-17 실측: stage 1 은 `AUTOPHAGY_REPO_ROOT=<릴리스>` 를 주지만
    `skill_review._scenario_passes(dir, None)` 는 `HOME`/`PATH`/`AUTOPHAGY_DEMO_SECRET` 만
    준 채 같은 스크립트를 다시 돌린다. 그때 부트스트랩이 자기 위치에서 루트를 지어내
    공유 리졸버의 최우선 입력(`AUTOPHAGY_RUNTIME_ROOT`)을 덮어써, peer staged 경로
    `~peer/.hermes/skills/todo/scripts` 기준 `../../..` = `~peer/.hermes` 로 풀렸다:
    `SCENARIO-FAIL AUTOPHAGY_REPO_ROOT has no automation/entity_preflight`.
    승인이 끝난 배포가 `PEER-ATTEST-BLOCK` 으로 마운트되지 못했다.
    """
    # Given: staged alone, with a runtime root discoverable ONLY through the shared variable
    mounted_scripts = tmp_path / "staging" / "todo" / "scripts"
    shutil.copytree(_TODO_SCRIPTS, mounted_scripts)
    outside = tmp_path / "outside"
    outside.mkdir()
    env = {
        **unit_node.env(),
        "AUTOPHAGY_DEMO_SECRET": "DUMMY-peer-review",
        "AUTOPHAGY_RUNTIME_ROOT": str(_REPO),
        "HOME": str(tmp_path / "home"),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": "",
    }

    # When: the scenario runs the way the peer reviewer runs it — no deploy-supplied root
    result = subprocess.run(
        ("bash", str(mounted_scripts / "scenario.sh")),
        cwd=outside,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    # Then: it honours the inherited runtime instead of inventing one from its own path
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SCENARIO-PASS" in result.stdout
