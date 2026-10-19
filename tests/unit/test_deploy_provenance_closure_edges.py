"""Provenance 클로저가 위치를 잃은 출처는 거부하고 바깥 함수에는 속지 않는다(RCB todo 58 재작업, 검증 B58-1·B58-2).

B58-1: 상대 경로로 source 한 뒤 cwd 가 바뀌거나, 상대 경로로 부른 배포기가 cd 하거나, extdebug 를 켜지
못하거나, 함수 출처가 사라진 파일이면 클로저가 그 파일을 조용히 빼고 "OK" 를 냈다 — 더러운 함수가 노드에서
돌았다. 이제 그 경우들은 rc 4·원격 호출 0 이다.
B58-2: 셸에 저장소 밖 함수가 이름순 마지막에 있으면 pipefail 아래 클로저가 rc 1 을 내 깨끗한 배포(동결된
`deploy-skill.sh` 포함)를 거부했다. 이제 함수 이름 순서와 무관하게 통과한다.
새 파일인 이유: `test_deploy_provenance_helper_closure.py` 는 순수 LOC 상한에 가깝다.
"""
from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest

from tests.unit.provenance_rig import PROBE_HELPER, SENTINEL_SSH, build_repo, deploy, dirty, git, removed

_EXTDEBUG: Final = {
    "refuses": 'shopt() { case " $* " in *" extdebug "*) return 1 ;; esac; builtin shopt "$@"; }\n',
    "pretends": 'shopt() { case " $* " in *" extdebug "*) return 0 ;; esac; builtin shopt "$@"; }\n',
}
_UNLOCATABLE: Final = {
    "process-substitution": "source <(printf 'ghost() { :; }\\n')\n",
    "deleted-outside": 'printf "ghost() { :; }\\n" > "$RIG/ghost.sh"; source "$RIG/ghost.sh"; rm "$RIG/ghost.sh"\n',
    "deleted-in-root": ('printf "ghost() { :; }\\n" > "$REPO/automation/ghost.sh"; '
                        'source "$REPO/automation/ghost.sh"; rm "$REPO/automation/ghost.sh"\n'),
}
_BOUNDARY_PACKAGE: Final = "automation/closure_boundary"
_BOUNDARY_DEPLOYER: Final = '''#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" "$repo_root/automation/closure_boundary/deploy.sh" "$repo_root/automation/deploy_push.sh" "$repo_root/automation/deploy_cron.sh" "$repo_root/automation/deploy_tree.sh" || exit 4
source "$repo_root/automation/deploy_push.sh"
ssh "$DEPLOY_SSH_HOST" true
'''
_EXTERNAL: Final = {"sorted-last": "zzzz_external() { :; }\n", "sorted-first": "aaaa_external() { :; }\n"}


def _boundary(root: Path) -> Path:
    caller = root / _BOUNDARY_PACKAGE / "deploy.sh"
    caller.parent.mkdir()
    caller.write_text(_BOUNDARY_DEPLOYER, encoding="utf-8")
    git(root, "add", _BOUNDARY_PACKAGE)
    git(root, "commit", "-qm", "test: normal guarded deployment caller")
    git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    return root


@pytest.fixture(scope="module")
def repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _boundary(build_repo(tmp_path_factory.mktemp("edges") / "repo"))


def _probe(repo: Path, rig: Path, name: str, bash_env: str = "") -> tuple[int, str, list[str], bool]:
    return deploy(repo, rig, str(repo / "automation" / name), bash_env=bash_env)


@pytest.mark.parametrize("state", ["clean", "dirty-helper"])
def test_a_relative_function_source_is_refused(repo: Path, tmp_path: Path, state: str) -> None:
    if state == "clean":
        rc, stderr, calls, marker = _probe(repo, tmp_path, "closure_probe_relative.sh")
    else:
        with dirty(repo, PROBE_HELPER):
            rc, stderr, calls, marker = _probe(repo, tmp_path, "closure_probe_relative.sh")

    assert (rc, calls, marker) == (4, [], False), stderr


@pytest.mark.parametrize("decoy", ["repo-root", "cwd-and-repo-root"])
def test_a_same_name_decoy_never_stands_in_for_the_helper(repo: Path, tmp_path: Path, decoy: str) -> None:
    assert (repo / "closure_probe_helper.sh").is_file()
    if decoy == "cwd-and-repo-root":
        tmp_path.mkdir(parents=True, exist_ok=True)
        _ = (tmp_path / "closure_probe_helper.sh").write_text("unrelated_cwd_function() { :; }\n", encoding="utf-8")
    with dirty(repo, PROBE_HELPER):
        rc, stderr, calls, marker = _probe(repo, tmp_path, "closure_probe_decoy.sh")

    assert (rc, calls, marker) == (4, [], False), stderr


@pytest.mark.parametrize("decoy", [False, True])
def test_a_function_that_changes_directory_never_lets_a_decoy_stand_in_for_the_caller(
        repo: Path, tmp_path: Path, decoy: bool) -> None:
    script = "automation/closure_probe_enter.sh"
    if decoy:
        (tmp_path / "automation").mkdir(parents=True)
        _ = (tmp_path / script).write_text("# unrelated same-name decoy\n", encoding="utf-8")
    with dirty(repo, script):
        rc, stderr, calls, marker = deploy(repo, tmp_path, script, cwd=repo)

    assert (rc, calls, marker) == (4, [], False), stderr


@pytest.mark.parametrize("state", ["clean", "dirty-deployer"])
def test_a_relative_deployer_that_changes_directory_fails_closed(repo: Path, tmp_path: Path, state: str) -> None:
    script = "automation/closure_probe_cd.sh"
    if state == "clean":
        rc, stderr, calls, marker = deploy(repo, tmp_path, script, cwd=repo)
    else:
        with dirty(repo, script):
            rc, stderr, calls, marker = deploy(repo, tmp_path, script, cwd=repo)

    assert (rc, calls, marker) == (4, [], False), stderr


def test_a_normal_deployer_run_by_relative_path_still_deploys(repo: Path, tmp_path: Path) -> None:
    package = _BOUNDARY_PACKAGE
    clean = deploy(repo, tmp_path / "clean", f"{package}/deploy.sh", cwd=repo)
    bypass = deploy(repo, tmp_path / "bypass", f"{package}/deploy.sh", cwd=repo, bypass=True)
    with dirty(repo, f"{package}/deploy.sh"):
        rc, stderr, calls, _ = deploy(repo, tmp_path / "dirty", f"{package}/deploy.sh", cwd=repo)

    assert clean[0] == 0, clean[1]
    assert clean[2] == bypass[2] != []
    assert (rc, calls) == (4, []), stderr


@pytest.mark.parametrize("helper", ["automation/deploy_cron.sh", "automation/deploy_push.sh"])
def test_a_deleted_tracked_shared_helper_is_refused_before_transport(repo: Path, tmp_path: Path,
                                                                      helper: str) -> None:
    package = _BOUNDARY_PACKAGE
    with removed(repo, helper):
        rc, stderr, calls, _ = deploy(repo, tmp_path, str(repo / package / "deploy.sh"))

    assert (rc, calls) == (4, []), stderr


def test_a_shared_helper_absent_from_the_reference_is_skipped(tmp_path: Path) -> None:
    repo = _boundary(build_repo(tmp_path / "repo"))
    _ = git(repo, "rm", "-q", "automation/deploy_tree_remote.sh")
    _ = git(repo, "commit", "-q", "-m", "older tree")
    _ = git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    package = _BOUNDARY_PACKAGE

    rc, stderr, calls, _ = deploy(repo, tmp_path / "rig", str(repo / package / "deploy.sh"))
    bypass = deploy(repo, tmp_path / "bypass", str(repo / package / "deploy.sh"), bypass=True)

    assert rc == 0, stderr
    assert calls == bypass[2] != []


@pytest.mark.parametrize("fault", sorted(_EXTDEBUG))
@pytest.mark.parametrize("state", ["clean", "dirty-helper"])
def test_unavailable_extdebug_fails_closed(repo: Path, tmp_path: Path, fault: str, state: str) -> None:
    if state == "clean":
        rc, stderr, calls, marker = _probe(repo, tmp_path, "closure_probe.sh", bash_env=_EXTDEBUG[fault])
    else:
        with dirty(repo, PROBE_HELPER):
            rc, stderr, calls, marker = _probe(repo, tmp_path, "closure_probe.sh", bash_env=_EXTDEBUG[fault])

    assert (rc, calls, marker) == (4, [], False), stderr


@pytest.mark.parametrize("origin", sorted(_UNLOCATABLE))
def test_a_function_whose_source_file_is_gone_fails_closed(repo: Path, tmp_path: Path, origin: str) -> None:
    rc, stderr, calls, marker = _probe(repo, tmp_path, "closure_probe.sh", bash_env=_UNLOCATABLE[origin])

    assert (rc, calls, marker) == (4, [], False), stderr


@pytest.mark.parametrize("order", sorted(_EXTERNAL))
def test_an_external_function_never_refuses_a_clean_deploy(repo: Path, tmp_path: Path, order: str) -> None:
    rc, stderr, calls, _ = _probe(repo, tmp_path, "closure_probe.sh", bash_env=_EXTERNAL[order])

    assert (rc, calls) == (0, ["fake-node"]), stderr


@pytest.mark.parametrize("order", sorted(_EXTERNAL))
def test_the_frozen_skill_deployer_reaches_transport_with_an_external_function(
        repo: Path, tmp_path: Path, order: str) -> None:
    skill = (str(repo / "automation/deploy-skill.sh"), "hello-autophagy")
    _, clean_stderr, clean_calls, _ = deploy(repo, tmp_path / "clean", *skill, ssh=SENTINEL_SSH,
                                             bash_env=_EXTERNAL[order])
    with dirty(repo, "automation/deploy_cron.sh"):
        rc, stderr, calls, _ = deploy(repo, tmp_path / "dirty", *skill, ssh=SENTINEL_SSH, bash_env=_EXTERNAL[order])

    assert clean_calls != [], clean_stderr
    assert (rc, calls) == (4, []), stderr
