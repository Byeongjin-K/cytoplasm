"""release.sh 의 전제 ④는 노드의 현재 릴리스가 아니라 자르려는 후보와 도우미를 비교한다.

2026-10-16: v1.40.1 이 root 도우미에 복사되는 update_trust.py 를 바꿨고, 그 바이트를
되돌린 v1.40.2 는 전제 ④가 노드의 현재 트리(v1.40.1)와 비교하는 바람에 카드조차 올리지
못했다. 후보가 도우미와 같으면 통과하고, 후보가 다르면 예전처럼 막혀야 한다.

FS3 정산 레코드가 tests/unit/test_healthcheck_helper_drift.py 의 출력을 고정하므로
그 파일에 케이스를 더하지 않고 여기 둔다(tests/AGENTS.md). 가짜 노드는 그 파일의
픽스처를 그대로 쓴다.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from tests.unit.test_healthcheck_helper_drift import _matching_files

_REPO = Path(__file__).resolve().parents[2]
_PROBE = _REPO / "automation" / "release_helper_probe.sh"


def _git(*args: str, cwd: Path) -> str:
    done = subprocess.run(("git", *args), cwd=cwd, capture_output=True, text=True, check=True)
    return done.stdout.strip()


def _candidate_repo(tmp_path: Path, release_root: Path) -> tuple[Path, str]:
    repo = tmp_path / "candidate-repo"
    _ = shutil.copytree(release_root, repo)
    _ = _git("init", "-q", cwd=repo)
    _ = _git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "add", "-A", cwd=repo)
    _ = _git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "candidate", cwd=repo)
    return repo, _git("rev-parse", "HEAD", cwd=repo)


def _run_candidate_check(helper: Path, provenance: Path, current: Path, repo: Path, commit: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.update(
        {
            "HEALTHCHECK_RELEASE_HELPER": str(helper),
            "HEALTHCHECK_RELEASE_PROVENANCE": str(provenance),
            "HEALTHCHECK_RELEASE_SOURCE_ROOT": str(current),
            "HEALTHCHECK_LIBEXEC_DIR": str(helper.parent),
            "HEALTHCHECK_UNIT_DIR": str(helper.parent.parent / "units"),
        }
    )
    script = subprocess.run(
        ("bash", "-c", f'source "{_PROBE}"; release_helper_probe_candidate_script "{repo}" "{commit}"'),
        capture_output=True, text=True, check=True, env=env,
    ).stdout
    # release.sh sends this script to the node as `bash -s`; run it the same way here.
    return subprocess.run(("bash", "-s"), input=script, capture_output=True, text=True, check=False, env=env)


def test_candidate_matching_the_helper_passes_even_when_the_node_release_differs(tmp_path: Path) -> None:
    helper, provenance, release_root = _matching_files(tmp_path)
    repo, commit = _candidate_repo(tmp_path, release_root)
    # The node's current release changed a helper-copied file; the candidate restores it.
    _ = (release_root / "automation" / "update_trust.py").write_text("changed-in-current\n", encoding="utf-8")

    result = _run_candidate_check(helper, provenance, release_root, repo, commit)

    assert result.returncode == 0, result.stderr
    assert "HELPER-DRIFT-PASS" in result.stderr


def test_candidate_differing_from_the_helper_still_blocks_even_when_the_node_release_matches(tmp_path: Path) -> None:
    helper, provenance, release_root = _matching_files(tmp_path)
    candidate_root = tmp_path / "candidate-tree"
    _ = shutil.copytree(release_root, candidate_root)
    _ = (candidate_root / "automation" / "update_trust.py").write_text("changed-in-candidate\n", encoding="utf-8")
    repo, commit = _candidate_repo(tmp_path, candidate_root)

    result = _run_candidate_check(helper, provenance, release_root, repo, commit)

    assert result.returncode != 0
    assert "HELPER-DRIFT: update_trust.py differs from the rendered release source" in result.stderr
