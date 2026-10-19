"""배포 레이아웃(``/srv/autophagy-skills/releases/<skill>/<sha>``)에서 scenario.sh 가 도는가.

샌드박스(peer ``~/.hermes/skills/<skill>``)는 위에 ``skills`` 네임스페이스 패키지가 우연히 있어
``from skills.<skill>.scripts import …`` 가 통과하지만, live 마운트의 부모는
``/srv/autophagy-skills/releases`` 라 그 패키지가 없다. 2026-09-03 v1.1.1 의 post-mount
smoke 에서 prompt·doctype 이 ``ModuleNotFoundError: No module named 'skills'`` 로 실패했다
(마운트는 이미 전환된 뒤라 배포는 통과, 신호만 잃었다).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Final

import pytest
import unit_node

_REPO: Final = Path(__file__).resolve().parents[2]


def _deployed_layout(tmp_path: Path, skill: str) -> tuple[Path, Path]:
    """live/<skill> -> releases/<skill>/<sha> 심링크 팜 + 이웃 스킬만 있는 ``skills`` 를 흉내 낸다."""
    # live/<skill>/scripts/../../.. 는 심링크를 풀지 않은 논리 경로라 <tmp>(노드의
    # /srv/autophagy-skills 자리)다 — 시나리오가 repo_root 로 삼는 그곳에 공유 자원을 둔다.
    release = tmp_path / "releases" / skill / "deadbeef"
    shutil.copytree(_REPO / "skills" / skill, release, symlinks=True)
    neighbours = tmp_path / "skills"
    neighbours.mkdir()
    for other in (_REPO / "skills").iterdir():
        if other.is_dir() and other.name != skill:
            (neighbours / other.name).symlink_to(other)
    for shared in ("configs", "prompts", "automation"):
        (tmp_path / shared).symlink_to(_REPO / shared)
    live = tmp_path / "live"
    live.mkdir()
    (live / skill).symlink_to(release)
    return live, live / skill / "scripts" / "scenario.sh"


@pytest.mark.parametrize("skill", ("prompt", "doctype"))
def test_scenario_runs_from_the_live_mount_layout(tmp_path: Path, skill: str) -> None:
    live, scenario = _deployed_layout(tmp_path, skill)
    home = tmp_path / "home"
    home.mkdir()
    completed = subprocess.run(
        ("bash", str(scenario)),
        env={
            **unit_node.env(),
            "HOME": str(home),
            "PATH": "/usr/bin:/bin",
            "INTEROP_RUNTIME": str(_REPO),
            "AUTOPHAGY_DEMO_SECRET": "DUMMY-deployed-layout",
            "AUTOPHAGY_SKILL_LIVE_ROOT": str(live),
        },
        # cwd 가 체크아웃이면 `python3 -` 의 sys.path[0]='' 로 저장소의 skills/ 가 새어 든다 — 노드처럼 빈 홈에서.
        cwd=home,
        capture_output=True, text=True, timeout=180, check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "SCENARIO-PASS" in completed.stdout
