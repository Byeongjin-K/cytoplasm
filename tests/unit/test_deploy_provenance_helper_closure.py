"""배포 provenance 가 배포기와 그것이 실제로 실행하는 공용 헬퍼까지 덮는다(RCB todo 58).

git 체크아웃 경로의 `deploy_provenance_check` 는 인자로 받은 파일만 대조했다. 그래서 커밋하지 않은
`deploy_cron.sh` 의 `_converge_cron_remote` 나 `deploy_tree_remote.sh` 의 `_deploy_tree_remote_prepare`
(둘 다 `declare -f` 로 노드에 실려 실행된다)가 "OK" 를 받고 노드에서 돌았다(todo 57 검증 R1·R4).
대부분의 배포기는 검사 **뒤에** `deploy_cron.sh` 를 source 하므로 검사 순간 셸에 정의된 함수만 보아서는
모자란다 — 공용 헬퍼 목록은 검사가 직접 들고, 호출 스택(배포기 자신)과 이미 정의된 함수의 파일을 더한다.
새 파일인 이유: 기존 provenance 시험들은 FS3 가 출력 해시를 고정했다. 경계 사례는
`test_deploy_provenance_closure_edges.py`, 정적 source 순서는 `test_deploy_provenance_source_order.py`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest

from automation.deploy_declarations import all_declarations
from tests.unit.cron_fixture import declared_cron, listing_only_hermes
from tests.unit.provenance_rig import PROBE_HELPER, REMOTE_HEADS, build_repo, deploy, dirty

_HELPERS: Final = (
    "automation/deploy_cron.sh", "automation/deploy_push.sh", "automation/deploy_tree.sh",
    "automation/deploy_tree_remote.sh", "automation/deploy_provenance.sh",
)
_DEPLOYERS: Final = ("automation/effect_request", "automation/rag_ingest", "automation/repair")
_PROBE: Final = "automation/closure_probe.sh"


@pytest.fixture(scope="module")
def repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_repo(tmp_path_factory.mktemp("closure") / "repo")


def _run(repo: Path, rig: Path, package: str, *, bypass: bool = False) -> tuple[int, str, list[str], bool]:
    hermes = listing_only_hermes(declared_cron(package))
    return deploy(repo, rig, str(repo / package / "deploy.sh"), hermes=hermes, bypass=bypass)


@pytest.mark.parametrize("package", _DEPLOYERS)
def test_clean_helpers_deploy_exactly_as_without_the_guard(repo: Path, tmp_path: Path, package: str) -> None:
    rc, stderr, calls, marker = _run(repo, tmp_path / "guarded", package)
    bypass_rc, _, bypass_calls, _ = _run(repo, tmp_path / "bypass", package, bypass=True)

    assert rc == 0, stderr
    assert (rc, calls) == (bypass_rc, bypass_calls)
    assert calls
    assert not marker


@pytest.mark.parametrize("helper", _HELPERS)
@pytest.mark.parametrize("package", _DEPLOYERS)
def test_a_dirty_shared_helper_never_reaches_the_node(repo: Path, tmp_path: Path, package: str,
                                                      helper: str) -> None:
    with dirty(repo, helper):
        rc, stderr, calls, marker = _run(repo, tmp_path, package)

    assert rc == 4, stderr
    assert calls == []
    assert not marker


@pytest.mark.parametrize("helper,package,account", [
    ("automation/deploy_cron.sh", "automation/doctor", "peer"),
    ("automation/deploy_tree_remote.sh", "automation/repair", "agent"),
])
def test_the_bypass_still_ships_a_dirty_helper(
    repo: Path, tmp_path: Path, helper: str, package: str, account: str,
) -> None:
    assert helper in REMOTE_HEADS
    # doctor keeps the peer Hermes path; repair now uses only the timer helper.
    row = next(row for row in all_declarations(repo)
               if (row.owner, row.kind, row.account) == (package, "cron", account))
    with dirty(repo, helper):
        rc, stderr, calls, marker = deploy(
            repo, tmp_path, str(repo / package / "deploy.sh"),
            hermes=listing_only_hermes(row), bypass=True,
        )

    assert rc == 0, stderr
    assert calls
    assert marker


@pytest.mark.parametrize("target", [_PROBE, PROBE_HELPER])
def test_a_dirty_deployer_or_sourced_helper_outside_the_list_is_blocked(repo: Path, tmp_path: Path,
                                                                        target: str) -> None:
    clean_rc, clean_stderr, clean_calls, _ = deploy(repo, tmp_path / "clean", str(repo / _PROBE))
    with dirty(repo, target):
        rc, stderr, calls, marker = deploy(repo, tmp_path / "dirty", str(repo / _PROBE))

    assert (clean_rc, clean_calls) == (0, ["fake-node"]), clean_stderr
    assert rc == 4, stderr
    assert calls == []
    assert not marker
