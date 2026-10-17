"""doctor 배포는 계정마다 래퍼를 검증된 rename 으로 옮긴다(RCB todo 28, GAP-10).

전에는 `tar -xzf - -C ~/.hermes/scripts; chmod 600 …` 한 줄이었다. 전송이 비어도 원격 셸의
마지막 명령(`chmod`)이 옛 파일에 성공해 배포가 rc 0 으로 끝났고, 그 계정은 낡은 래퍼를 계속 돌렸다.
`push_file` 은 임시 파일의 해시가 맞을 때만 rename 하고 원격에서 다시 읽어 대조한다 — 착지하지 않으면
배포가 실패하고 그 계정의 기존 래퍼는 바이트 그대로 남는다.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Final

from automation.deploy_declarations import all_declarations
from tests.unit.cron_fixture import HELPER, listing_only_hermes

_REPO: Final = Path(__file__).resolve().parents[2]
_PACKAGE: Final = "automation/doctor"
_WRAPPER: Final = ".hermes/scripts/doctor_watch.py"
_INFRA: Final = (
    "automation/node_config.py", "automation/node_config_sh.py", "automation/deploy_provenance.sh",
    "automation/deploy_push.sh", HELPER, "automation/deploy_timer.sh", "configs/node.example.toml",
)
_SUDO: Final = r'''#!/bin/bash
account="$3"; script="${@: -1}"
export HOME="$RIG/home-$account"
if [[ "$account" == "$DROP" && ( "$script" == *deploy-tmp* || "$script" == *"tar -xzf"* ) ]]; then
  exec bash -c "$script" < /dev/null
fi
exec bash -c "$script"
'''


def _tracked(package: str) -> list[str]:
    listed = subprocess.run(("git", "-C", str(_REPO), "ls-files", package), capture_output=True,
                            text=True, check=True, timeout=30)
    return listed.stdout.split()


def _rig(tmp_path: Path, drop: str) -> tuple[Path, dict[str, str]]:
    repo, bin_dir = tmp_path / "repo", tmp_path / "bin"
    for relative in (*_INFRA, *_tracked(_PACKAGE)):
        (repo / relative).parent.mkdir(parents=True, exist_ok=True)
        _ = shutil.copyfile(_REPO / relative, repo / relative)
    bin_dir.mkdir()
    row = next(r for r in all_declarations(_REPO) if r.owner == _PACKAGE and r.kind == "cron")
    for name, body in (("ssh", '#!/bin/bash\nshift\nexec bash -c "$*"\n'), ("sudo", _SUDO),
                       ("hermes", listing_only_hermes(row))):
        _ = (bin_dir / name).write_text(body, encoding="utf-8")
        (bin_dir / name).chmod(0o755)
    for account in ("agent", "peer"):
        wrapper = tmp_path / f"home-{account}" / _WRAPPER
        wrapper.parent.mkdir(parents=True)
        _ = wrapper.write_text("old wrapper\n", encoding="utf-8")
        wrapper.chmod(0o600)
    identity = ("-c", "user.name=rig", "-c", "user.email=rig@example.invalid")
    for args in (("init", "-q"), ("add", "-A"), (*identity, "commit", "-q", "-m", "base"),
                 ("update-ref", "refs/remotes/origin/main", "HEAD")):
        _ = subprocess.run(("git", "-C", str(repo), *args), check=True, capture_output=True, timeout=30)
    skip = {"PYTHONPATH", "DEPLOY_ALLOW_UNPUSHED", "DEPLOY_PROVENANCE_REF", "DEPLOY_SSH_HOST"}
    env = {key: value for key, value in os.environ.items() if key not in skip}
    env.update({
        "HOME": str(tmp_path), "RIG": str(tmp_path), "DROP": drop, "DEPLOY_SSH_HOST": "fake-node",
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HEALTHCHECK_NODE_CONFIG_PATH": str(repo / "configs/node.example.toml"),
    })
    return repo, env


def test_a_push_that_does_not_land_keeps_the_old_wrapper(tmp_path: Path) -> None:
    repo, env = _rig(tmp_path, drop="peer")

    result = subprocess.run(("bash", str(repo / _PACKAGE / "deploy.sh")), env=env, cwd=tmp_path,
                            capture_output=True, text=True, check=False, timeout=120)

    new = (_REPO / _PACKAGE / "cron/doctor_watch.py").read_bytes()
    peer = tmp_path / "home-peer" / _WRAPPER
    assert result.returncode != 0, result.stdout + result.stderr
    assert (tmp_path / "home-agent" / _WRAPPER).read_bytes() == new
    assert peer.read_text(encoding="utf-8") == "old wrapper\n"
    assert not [path.name for path in peer.parent.iterdir() if ".deploy-tmp." in path.name]
