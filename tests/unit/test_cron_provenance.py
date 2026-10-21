"""cron 을 등록하는 배포기는 등록 값의 출처까지 provenance 로 막는다(RCB todo 57).

`converge_cron <이름> <주기> <스크립트> <전달>` 과 FS3 고정 날것 줄의 값은 배포기 자신의
`deploy.sh` 에 있고, 그 선언은 옆의 `deploy-manifest.txt` 다. provenance 인자가 옮기는 스크립트만
덮으면 커밋하지 않은 주기를 바꾼 `deploy.sh` 가 그 주기를 노드에 등록한다(todo 27 검증 재현:
cost-report). 그래서 cron 을 등록하는 모든 배포기의 `deploy_provenance_check` 인자는 그 두 파일을
직접 또는 담은 디렉터리로 덮어야 한다. 새 파일인 이유: `test_cron_declarations.py` 가 순수 LOC 상한에 닿는다.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Final

import pytest

from automation.cron_schedule import on_calendar
from tests.unit.cron_fixture import HELPER, declared_cron, listing, timer_env
from tests.unit.test_cron_declarations import registrations

_REPO: Final = Path(__file__).resolve().parents[2]
_OWN: Final = ("deploy.sh", "deploy-manifest.txt")
_CALL: Final = "deploy_provenance_check"
_PREFIX: Final = "$repo_root/"
_OPERATORS: Final = frozenset({"||", "&&", ";", "|", "&"})


def _calls(text: str) -> list[list[str]]:
    logical = text.replace("\\\n", " ")
    found: list[list[str]] = []
    for line in logical.splitlines():
        if not line.lstrip().startswith(_CALL):
            continue
        args: list[str] = []
        for token in shlex.split(line)[1:]:
            if token in _OPERATORS:
                break
            if token.startswith(_PREFIX):
                args.append(token.removeprefix(_PREFIX).rstrip("/"))
        found.append(args)
    return found


def _covers(arg: str, relative: str) -> bool:
    return relative == arg or relative.startswith(f"{arg}/")


def cron_deployers(root: Path) -> tuple[str, ...]:
    registered, _ = registrations(root)
    return tuple(sorted({registration.job.package for registration in registered}))


def uncovered(root: Path, package: str) -> tuple[str, ...]:
    calls = _calls((root / package / "deploy.sh").read_text(encoding="utf-8"))
    wanted = (f"{package}/{name}" for name in _OWN)
    return tuple(path for path in wanted if not any(_covers(arg, path) for args in calls for arg in args))


def test_every_cron_deployer_guards_its_own_deployer_and_declaration() -> None:
    packages = cron_deployers(_REPO)

    assert len(packages) == 19  # skills/wiki deploy retired 2026-10-19, skills/budget 2026-10-22
    assert {"automation/effect_request", "automation/doctor", "skills/mail"} <= set(packages)
    assert {package: uncovered(_REPO, package) for package in packages} == dict.fromkeys(packages, ())


@pytest.mark.parametrize("package", cron_deployers(_REPO))
@pytest.mark.parametrize("own", _OWN)
def test_dropping_an_own_file_from_the_guard_is_caught(tmp_path: Path, package: str, own: str) -> None:
    text = (_REPO / package / "deploy.sh").read_text(encoding="utf-8")
    target = f"{package}/{own}"
    covering = {arg for args in _calls(text) for arg in args if _covers(arg, target)}
    assert covering, f"{package} does not guard {own}"
    mutated = text
    for arg in covering:
        mutated = mutated.replace(f'"{_PREFIX}{arg}"', '""').replace(f'"{_PREFIX}{arg}/"', '""')
    assert mutated != text
    copy = tmp_path / package
    copy.mkdir(parents=True)
    _ = (copy / "deploy.sh").write_text(mutated, encoding="utf-8")

    assert target in uncovered(tmp_path, package)


# 리그는 Hermes cron 에 남은 배포기 하나를 실제로 돌린다 — cost-report 는 P2 에서 사용자 타이머로 옮겼다.
_COST: Final = "automation/effect_request"
_ROW: Final = declared_cron(_COST)
_FILES: Final = (
    "automation/node_config.py", "automation/node_config_sh.py", "automation/deploy_provenance.sh",
    "automation/deploy_push.sh", HELPER, "automation/deploy_timer.sh", f"{_COST}/deploy.sh", f"{_COST}/deploy-manifest.txt", f"{_COST}/cron/effect_request_watch.py",
    "configs/node.example.toml",
)
_HERMES: Final = r'''#!/usr/bin/env bash
printf '%s\n' "$*" >> "$RIG/hermes.log"
state="$RIG/schedule"
if [[ "$*" == "cron list --all" ]]; then
  schedule="$(cat "$state")"
  printf '%s' "${LISTING//@SCHEDULE@/$schedule}"
  exit 0
fi
[[ "$1 $2" == "cron edit" ]] || exit 9
while [[ $# -gt 0 ]]; do
  [[ "$1" == --schedule ]] && printf '%s' "$2" > "$state"
  shift
done
'''


def _rig(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    repo, home, bin_dir = (tmp_path / name for name in ("repo", "home", "bin"))
    for relative in _FILES:
        (repo / relative).parent.mkdir(parents=True, exist_ok=True)
        _ = shutil.copyfile(_REPO / relative, repo / relative)
    for path in (home, bin_dir):
        path.mkdir()
    for name, body in (
        ("ssh", '#!/bin/bash\nprintf "%s\\n" "$1" >> "$RIG/ssh.log"\nshift\nexec bash -c "$*"\n'),
        ("sudo", '#!/bin/bash\nHOME="$NODE_HOME" exec bash -c "${@: -1}"\n'),
        ("hermes", _HERMES),
    ):
        _ = (bin_dir / name).write_text(body, encoding="utf-8")
        (bin_dir / name).chmod(0o755)
    _ = (tmp_path / "schedule").write_text(_ROW.attr("schedule"), encoding="utf-8")
    identity = ("-c", "user.name=rig", "-c", "user.email=rig@example.invalid")
    for args in (("init", "-q"), ("add", "-A"), (*identity, "commit", "-q", "-m", "base"),
                 ("update-ref", "refs/remotes/origin/main", "HEAD")):
        _ = subprocess.run(("git", "-C", str(repo), *args), check=True, capture_output=True, timeout=30)
    skip = {"PYTHONPATH", "DEPLOY_ALLOW_UNPUSHED", "DEPLOY_PROVENANCE_REF", "DEPLOY_SSH_HOST"}
    env = {key: value for key, value in os.environ.items() if key not in skip}
    env.update({
        "HOME": str(tmp_path), "NODE_HOME": str(home), "RIG": str(tmp_path),
        "PATH": f"{bin_dir}:{os.environ['PATH']}", "DEPLOY_SSH_HOST": "fake-node",
        "HEALTHCHECK_NODE_CONFIG_PATH": str(repo / "configs/node.example.toml"),
        "LISTING": listing(_ROW).replace(_ROW.attr("schedule"), "@SCHEDULE@"),
        **timer_env(tmp_path / "timer"),
    })
    return repo, env


def _deploy(tmp_path: Path, repo: Path, env: dict[str, str]) -> tuple[int, str, list[str], str]:
    result = subprocess.run(("bash", str(repo / _COST / "deploy.sh")), env=env, cwd=tmp_path,
                            capture_output=True, text=True, check=False, timeout=120)
    ssh_log = tmp_path / "ssh.log"
    calls = ssh_log.read_text(encoding="utf-8").splitlines() if ssh_log.exists() else []
    return result.returncode, result.stderr, calls, (tmp_path / "schedule").read_text(encoding="utf-8")


def test_a_clean_hermes_cron_deploy_still_converges(tmp_path: Path) -> None:
    repo, env = _rig(tmp_path)

    rc, stderr, calls, schedule = _deploy(tmp_path, repo, env)

    assert rc == 0, stderr
    assert calls == ["fake-node"] * 5
    timer = (tmp_path / "home" / ".config/systemd/user/autophagy-cron-effect-request-watch.timer").read_text()
    assert f"OnCalendar={on_calendar(_ROW.attr('schedule'), '')}\n" in timer


@pytest.mark.parametrize("dirty", ["schedule", "declaration"])
def test_an_uncommitted_registration_source_never_reaches_the_node(tmp_path: Path, dirty: str) -> None:
    repo, env = _rig(tmp_path)
    if dirty == "schedule":
        deployer = repo / _COST / "deploy.sh"
        old = f'converge_timer {_ROW.destination} "{_ROW.attr("schedule")}"'
        text = deployer.read_text(encoding="utf-8")
        assert old in text
        _ = deployer.write_text(text.replace(old, old.replace(_ROW.attr("schedule"), "0 10 * * *")),
                                encoding="utf-8")
    else:
        with (repo / _COST / "deploy-manifest.txt").open("a", encoding="utf-8") as manifest:
            _ = manifest.write("# uncommitted\n")

    rc, stderr, calls, schedule = _deploy(tmp_path, repo, env)

    assert rc == 4, stderr
    assert "DEPLOY-BLOCK" in stderr
    assert calls == []
    assert not (tmp_path / "home" / ".config/systemd/user/autophagy-cron-effect-request-watch.timer").exists()
