"""`v2:config` 선언의 노드 관측 — 새 파일인 이유: 기존 관측 테스트 파일은 FS3 정산이 출력 해시로 고정할 수 있다.

원격 고정 스크립트(`REMOTE_SCRIPT`)의 `config` 갈래를 sudo 없이 같은 인자로 로컬 bash 에서 실제로 돌린다 —
계정 홈은 임시 디렉터리, 런타임 루트는 이 저장소의 `automation/` 을 담은 임시 트리다.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Final

from automation import deploy_all_kinds, deploy_all_observe, hermes_memory_config

_REPO: Final = Path(__file__).resolve().parents[2]
_ROWS: Final = (
    "agent|automation/hermes_memory_config.py|.hermes/config.yaml|"
    "v2:config;algorithm=hermes-memory-limits-v1;activation=gateway\n"
    "peer|automation/hermes_memory_config.py|.hermes/config.yaml|"
    "v2:config;algorithm=hermes-memory-limits-v1;activation=gateway\n"
)


def _runtime(tmp_path: Path) -> Path:
    root = tmp_path / "runtime"
    package = root / "automation"
    package.mkdir(parents=True)
    for name in ("__init__.py", "hermes_memory_config.py"):
        _ = shutil.copy(_REPO / "automation" / name, package / name)
    (package / "pkg").mkdir()
    _ = (package / "pkg" / "deploy-manifest.txt").write_text(_ROWS, encoding="utf-8")
    return root


def _runner(homes: Path) -> deploy_all_observe.Runner:
    def run(account: str, args: tuple[str, ...]) -> tuple[int, str]:
        home = homes / account
        home.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            ("bash", "-c", deploy_all_observe.REMOTE_SCRIPT, "_", *args),
            env={"HOME": str(home), "PATH": os.environ["PATH"]},
            capture_output=True, text=True, check=False, timeout=60,
        )
        return proc.returncode, proc.stdout

    return run


def _config(homes: Path, account: str, text: str) -> None:
    path = homes / account / ".hermes" / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(text, encoding="utf-8")


def _artifacts(root: Path, homes: Path) -> dict[str, deploy_all_kinds.ArtifactState]:
    lines = deploy_all_observe.collect(root, _runner(homes), "", gateway_only=False)
    parsed = deploy_all_kinds.parse_artifact_lines([line for line in lines if line.split("|")[1] != "gateway"])
    return {state.account: state for state in parsed.artifacts}


def test_default_caps_are_stale_and_released_caps_are_ok(tmp_path: Path) -> None:
    root, homes = _runtime(tmp_path), tmp_path / "homes"
    _config(homes, "agent", "memory:\n  memory_char_limit: 2200\n  user_char_limit: 1375\n")
    _config(homes, "peer", hermes_memory_config.apply_limits("model:\n  provider: x\n"))
    seen = _artifacts(root, homes)
    assert seen["agent"].status == "stale"
    assert seen["agent"].activation == "gateway"
    assert seen["agent"].owner == "automation/pkg"
    assert seen["agent"].details == (("different-memory_char_limit", "-"), ("different-user_char_limit", "-"))
    assert seen["peer"].status == "ok"
    assert seen["peer"].want == seen["agent"].want


def test_account_without_a_memory_block_is_stale_not_ok(tmp_path: Path) -> None:
    root, homes = _runtime(tmp_path), tmp_path / "homes"
    for account in ("agent", "peer"):
        _config(homes, account, "model:\n  provider: x\n")
    assert {a: s.status for a, s in _artifacts(root, homes).items()} == {"agent": "stale", "peer": "stale"}


def test_unreadable_config_is_unknown_not_absent(tmp_path: Path) -> None:
    root, homes = _runtime(tmp_path), tmp_path / "homes"
    _config(homes, "agent", "memory: {memory_char_limit: 2200}\n")
    seen = _artifacts(root, homes)
    assert seen["agent"].status == "unknown"
    assert seen["agent"].details == (("unreadable-config", "-"),)
    assert seen["peer"].status == "unknown"
