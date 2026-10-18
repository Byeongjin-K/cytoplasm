"""RCB todo 18: a repair-report watcher never imports half of one runtime and half of another.

The real `automation/repair/deploy.sh` runs against a temp HOME through fake `ssh`/`sudo`
shims, and the real wrapper file runs as a child process. Each runtime generation is ten stub
modules that record `<generation>:<file>` when imported, so a watcher's marker file names
exactly which trees it read. Deploy steps park on FIFOs; nothing waits on a sleep. A new file
because the repair consumer tests are FS3-pinned.

Reader-state invariants pinned here:
  I1 a watcher started at any step of a deploy imports all ten files from ONE generation;
  I2 a watcher that resolved the runtime keeps that generation even if the link flips mid-import;
  I3 during the first deploy the old wrapper reads only the root copy, whose bytes never change;
  I4 a failure at any step leaves a whole runtime the current wrapper can import.
"""
from __future__ import annotations

import os
import re
import select
import shutil
import signal
import subprocess
import sys
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest

from automation.cron_schedule import on_calendar
from tests.unit.cron_fixture import HELPER, TIMER_HELPER, declared_cron, listing_only_hermes, timer_env

_REPO: Final = Path(__file__).resolve().parents[2]
_RUNTIME: Final = ".hermes/repair-report-runtime"
_LINK: Final = f"{_RUNTIME}/.current"
_WRAPPER: Final = ".hermes/scripts/repair_report_consume_watch.py"
_WRAPPER_SOURCE: Final = "automation/repair/cron/repair_report_consume_watch.py"
_DEPLOYER: Final = "automation/repair/deploy.sh"
_CONSUMER: Final = "repair/repair_report_consumer.py"
_FILES: Final = (
    "__init__.py", "interop/__init__.py", "interop/chunker.py", "interop/discord_transport.py",
    "interop/report.py", "repair/__init__.py", "repair/repair_capability.py", _CONSUMER,
    "repair/repair_report_queue.py", "repair/repair_report_send.py",
)
_SOURCED: Final = re.compile(r'^\s*source\s+"\$\w+/([\w.-]+\.sh)"', re.MULTILINE)


def _tree_helper_closure() -> tuple[str, ...]:
    """`deploy_tree.sh` and every sibling it sources, transitively, so a split never drops one."""
    seen: list[str] = []
    pending = ["deploy_tree.sh"]
    while pending:
        name = pending.pop()
        if name not in seen:
            seen.append(name)
            text = (_REPO / "automation" / name).read_text(encoding="utf-8")
            pending.extend(_SOURCED.findall(text))
    return tuple(f"automation/{name}" for name in seen)


_INFRA: Final = tuple(dict.fromkeys((
    "automation/node_config.py", "automation/node_config_sh.py", "automation/notice_bot.py",
    "automation/deploy_provenance.sh", *_tree_helper_closure(), "automation/deploy_push.sh", TIMER_HELPER,
    _DEPLOYER, "automation/repair/deploy-manifest.txt", _WRAPPER_SOURCE,
    "configs/runtime-package-manifest.txt", "configs/node.example.toml",
)))
_CRON: Final = declared_cron("automation/repair")
_STEPS: Final = ("prepare", "switch", "verify", "push", "readback", "push", "readback",
                 f"timer:{_CRON.destination}", f"timer-check:{_CRON.destination}")

_SUDO: Final = r'''#!/bin/bash
account=""
while [[ $# -gt 0 && "$1" == -* ]]; do
  if [[ "$1" == -u ]]; then account="$2"; shift 2; else shift; fi
done
[[ "$1" == bash ]] && shift
[[ "$1" == -lc || "$1" == -c ]] && shift
script="$1"
last="${script##*$'\n'}"
case "$last" in
  _deploy_tree_remote_prepare) tag=prepare ;;
  _deploy_tree_remote_switch) tag=switch ;;
  _deploy_tree_remote_verify) tag=verify ;;
  _deploy_tree_remote_rollback*) tag=rollback ;;
  *'rm -rf -- "$staging"'*) tag=cleanup ;;
  sha256sum*) tag=readback ;;
  *) if [[ "$script" == *"automation.cron_timer converge --name "* ]]; then
       name="${script##*converge --name }"; tag="timer:${name%% *}"
     elif [[ "$script" == *"automation.cron_timer check --name "* ]]; then
       name="${script##*check --name }"; tag="timer-check:${name%% *}"
     elif [[ "$script" == *deploy-tmp* ]]; then tag=push; else tag=other; fi ;;
esac
printf '%s %s\n' "$account" "$tag" >> "$RIG/log"
rc=0
if [[ "$FAULT" == push && "$tag" == push ]]; then
  out="$(HOME="$NODE_HOME" bash -c "$script" < /dev/null)" || rc=$?
elif [[ "$FAULT" == switch && "$tag" == switch ]]; then
  out="$(FAIL_LN=1 HOME="$NODE_HOME" bash -c "$script")" || rc=$?
else
  out="$(HOME="$NODE_HOME" bash -c "$script")" || rc=$?
fi
[[ "$FAULT" == prepare && "$tag" == prepare ]] && out="misleading-success"
if [[ "$PAUSE" == 1 ]]; then
  printf '%s\n' "$tag" > "$RIG/deploy-arrived"
  read -r _ < "$RIG/deploy-release"
fi
printf '%s\n' "$out"
exit "$rc"
'''


def _stub(generation: str, name: str) -> str:
    lines = [
        "import os as _os",
        f"GEN = {generation!r}",
        "_marker = _os.environ.get('REPAIR_MARKER')",
        "if _marker:",
        "    with open(_marker, 'a', encoding='utf-8') as _out:",
        f"        _out.write(GEN + ':{name}\\n')",
    ]
    if name == _CONSUMER:
        lines += [
            "_pause = _os.environ.get('REPAIR_PAUSE_DIR')",
            "if _pause:",
            "    with open(_pause + '/arrived', 'w', encoding='utf-8') as _out:",
            "        _out.write('import\\n')",
            "    with open(_pause + '/release', encoding='utf-8') as _in:",
            "        _in.readline()",
            "from automation.interop import chunker, discord_transport, report",
            "from automation.repair import repair_capability, repair_report_queue, repair_report_send",
            "def consume_once():",
            "    return GEN",
        ]
    return "\n".join(lines) + "\n"


def _put_generation(root: Path, generation: str) -> None:
    for name in _FILES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(_stub(generation, name), encoding="utf-8")


def _tree(root: Path) -> dict[str, bytes]:
    assert root.is_dir(), root
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts
    }


def _generation(lines: list[str]) -> str:
    generations = {line.split(":", 1)[0] for line in lines}
    assert len(generations) == 1, sorted(lines)
    generation = generations.pop()
    assert sorted(lines) == sorted(f"{generation}:{name}" for name in _FILES)
    return generation


def _line(stream: int) -> bytes:
    assert select.select([stream], [], [], 30)[0], "handshake deadline expired"
    return os.read(stream, 4096)


def _old_wrapper() -> str:
    text = (_REPO / _WRAPPER_SOURCE).read_text(encoding="utf-8")
    old = text.replace(
        '"~/.hermes/repair-report-runtime/.current"', '"~/.hermes/repair-report-runtime"',
    ).replace(
        "sys.path.insert(0, os.path.realpath(RUNTIME_ROOT))", "sys.path.insert(0, str(RUNTIME_ROOT))",
    )
    assert old != text and old.count("str(RUNTIME_ROOT)") == 1, "the wrapper lost its two-line change"
    return old


@dataclass(frozen=True)
class Rig:
    root: Path
    repo: Path
    home: Path
    env: dict[str, str]

    @property
    def link(self) -> Path:
        return self.home / _LINK

    @property
    def root_copy(self) -> Path:
        return self.home / _RUNTIME / "automation"

    def commit(self, generation: str) -> dict[str, bytes]:
        _put_generation(self.repo / "automation", generation)
        identity = ("-c", "user.name=rig", "-c", "user.email=rig@example.invalid")
        for args in (("add", "-A"), (*identity, "commit", "-q", "-m", generation),
                     ("update-ref", "refs/remotes/origin/main", "HEAD")):
            _ = subprocess.run(("git", "-C", str(self.repo), *args), check=True,
                               capture_output=True, env=self.env, timeout=30)
        return {f"automation/{name}": (self.repo / "automation" / name).read_bytes() for name in _FILES}

    def seed_root(self) -> None:
        _put_generation(self.root_copy, "root")
        wrapper = self.home / _WRAPPER
        wrapper.parent.mkdir(parents=True, exist_ok=True)
        _ = wrapper.write_text(_old_wrapper(), encoding="utf-8")

    def deploy(self, fault: str = "") -> subprocess.CompletedProcess[str]:
        _ = (self.root / "log").write_text("", encoding="utf-8")
        return subprocess.run(
            ("bash", str(self.repo / _DEPLOYER)), env={**self.env, "FAULT": fault},
            cwd=self.root, capture_output=True, text=True, check=False, timeout=120,
        )

    def log(self) -> list[str]:
        return (self.root / "log").read_text(encoding="utf-8").splitlines()

    def watch_env(self, marker: Path, pause: Path | None = None) -> dict[str, str]:
        env = {"HOME": str(self.home), "PATH": os.environ["PATH"], "REPAIR_MARKER": str(marker)}
        if pause is not None:
            env["REPAIR_PAUSE_DIR"] = str(pause)
        return env

    def watch(self, wrapper: Path | None = None) -> list[str]:
        marker = self.root / "marker"
        marker.unlink(missing_ok=True)
        result = subprocess.run(
            (sys.executable, str(wrapper or self.home / _WRAPPER)), env=self.watch_env(marker),
            cwd=self.root, capture_output=True, text=True, check=False, timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return marker.read_text(encoding="utf-8").splitlines()

    def watch_across_a_deploy(self, wrapper: Path) -> set[str]:
        """Park a watcher inside the consumer import, run a whole deploy, then let it finish."""
        pause = self.root / "watch-pause"
        pause.mkdir(exist_ok=True)
        for name in ("arrived", "release"):
            os.mkfifo(pause / name)
        marker = self.root / "marker"
        marker.unlink(missing_ok=True)
        with (
            os.fdopen(os.open(pause / "arrived", os.O_RDWR), "rb", buffering=0) as arrived,
            os.fdopen(os.open(pause / "release", os.O_RDWR), "wb", buffering=0) as release,
            subprocess.Popen(
                (sys.executable, str(wrapper)), env=self.watch_env(marker, pause), cwd=self.root,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
            ) as process,
        ):
            try:
                assert _line(arrived.fileno()) == b"import\n"
                deployed = self.deploy()
                assert deployed.returncode == 0, deployed.stderr
                _ = os.write(release.fileno(), b"go\n")
                _stdout, stderr = process.communicate(timeout=60)
                assert process.returncode == 0, stderr
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                _ = process.communicate(timeout=30)
                for name in ("arrived", "release"):
                    (pause / name).unlink()
        lines = marker.read_text(encoding="utf-8").splitlines()
        assert sorted(line.split(":", 1)[1] for line in lines) == sorted(_FILES)
        return {line.split(":", 1)[0] for line in lines}

    @contextmanager
    def paused_deploy(self) -> Iterator[tuple[subprocess.Popen[str], int, int]]:
        for name in ("deploy-arrived", "deploy-release"):
            os.mkfifo(self.root / name)
        _ = (self.root / "log").write_text("", encoding="utf-8")
        with (
            os.fdopen(os.open(self.root / "deploy-arrived", os.O_RDWR), "rb", buffering=0) as arrived,
            os.fdopen(os.open(self.root / "deploy-release", os.O_RDWR), "wb", buffering=0) as release,
            subprocess.Popen(
                ("bash", str(self.repo / _DEPLOYER)), env={**self.env, "PAUSE": "1"},
                cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                start_new_session=True,
            ) as process,
        ):
            try:
                yield process, arrived.fileno(), release.fileno()
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                _ = process.communicate(timeout=30)
                for name in ("deploy-arrived", "deploy-release"):
                    (self.root / name).unlink()


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    repo, home, operator, bin_dir = (tmp_path / p for p in ("repo", "home", "operator", "bin"))
    for path in (home, operator, bin_dir):
        path.mkdir()
    for relative in _INFRA:
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        _ = shutil.copyfile(_REPO / relative, target)
    _ = shutil.copyfile(_REPO / HELPER, repo / HELPER)
    (home / ".local/bin").mkdir(parents=True)
    _ = (home / ".local/bin/hermes").write_text(listing_only_hermes(_CRON), encoding="utf-8")
    (home / ".local/bin/hermes").chmod(0o755)
    _ = subprocess.run(("git", "init", "-q", str(repo)), check=True, timeout=30)
    real_ln = shutil.which("ln")
    assert real_ln
    for name, body in (
        ("ssh", '#!/bin/bash\nshift\nexec bash -c "$*"\n'),
        ("sudo", _SUDO),
        ("ln", f'#!/bin/bash\n[[ "${{FAIL_LN:-}}" == 1 ]] && exit 1\nexec {real_ln} "$@"\n'),
    ):
        _ = (bin_dir / name).write_text(body, encoding="utf-8")
        (bin_dir / name).chmod(0o755)
    skip = {"PYTHONPATH", "DEPLOY_ALLOW_UNPUSHED", "DEPLOY_PROVENANCE_REF", "REPAIR_REPORT_RUNTIME",
            "REPAIR_MARKER", "REPAIR_PAUSE_DIR", "DEPLOY_SSH_HOST"}
    env = {key: value for key, value in os.environ.items() if key not in skip}
    env.update({
        "HOME": str(operator), "NODE_HOME": str(home), "RIG": str(tmp_path),
        "PATH": f"{bin_dir}:{os.environ['PATH']}", "DEPLOY_SSH_HOST": "fake-node",
        "HEALTHCHECK_NODE_CONFIG_PATH": str(repo / "configs/node.example.toml"),
        "FAULT": "", "PAUSE": "0",
        **timer_env(tmp_path / "timer"),
    })
    return Rig(tmp_path, repo, home, env)


def test_repair_deployer_ships_the_declared_closure(rig: Rig) -> None:
    shipped = rig.commit("A")
    result = rig.deploy()
    assert result.returncode == 0, result.stderr
    with (_REPO / "configs/node.example.toml").open("rb") as stream:
        account = tomllib.load(stream)["agent_account"]
    assert rig.log() == [f"{account} {step}" for step in _STEPS]
    units = rig.home / ".config/systemd/user"
    timer = (units / f"autophagy-cron-{_CRON.destination}.timer").read_text()
    assert f"OnCalendar={on_calendar(_CRON.attr('schedule'), '')}\n" in timer
    service = (units / f"autophagy-cron-{_CRON.destination}.service").read_text()
    assert f"--name {_CRON.destination} --script {_CRON.attr('script')}\n" in service
    assert rig.link.is_symlink()
    assert os.readlink(rig.link).startswith(".current.d/")
    generation = rig.link.resolve()
    assert generation.parent == rig.home / f"{_LINK}.d"
    real = {f"automation/{name}": (rig.repo / "automation" / name).read_bytes()
            for name in ("node_config.py", "notice_bot.py")}
    real["automation/node.example.toml"] = (rig.repo / "configs/node.example.toml").read_bytes()
    assert _tree(generation) == shipped | real
    assert (rig.home / _WRAPPER).read_bytes() == (_REPO / _WRAPPER_SOURCE).read_bytes()
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in generation.rglob("*.py"))
    assert all(p.stat().st_mode & 0o777 == 0o700 for p in generation.rglob("*") if p.is_dir())
    assert not (rig.home / _RUNTIME / "automation").exists()
    assert _generation(rig.watch()) == "A"


@pytest.mark.parametrize("first", [False, True], ids=["later", "first"])
def test_a_watcher_started_at_any_point_of_a_deploy_sees_one_whole_generation(
    rig: Rig, first: bool,
) -> None:
    if first:
        rig.seed_root()
    else:
        _ = rig.commit("A")
        assert rig.deploy().returncode == 0
    before = "root" if first else "A"
    root_before = _tree(rig.root_copy) if first else {}
    _ = rig.commit("B")
    assert _generation(rig.watch()) == before
    flipped = "root" if first else "B"
    # The config push precedes the wrapper push, so the old wrapper still reads the old copy until then.
    expected = (before, flipped, flipped, flipped, flipped, "B", "B", "B", "B")
    assert len(expected) == len(_STEPS)
    with rig.paused_deploy() as (process, arrived, release):
        for step, want in zip(_STEPS, expected, strict=True):
            assert _line(arrived) == f"{step}\n".encode()
            assert _generation(rig.watch()) == want, step
            _ = os.write(release, b"go\n")
        _stdout, stderr = process.communicate(timeout=60)
        assert process.returncode == 0, stderr
    assert _generation(rig.watch()) == "B"
    if first:
        assert _tree(rig.root_copy) == root_before


def test_a_watcher_mid_import_keeps_its_generation_across_the_flip(rig: Rig) -> None:
    _ = rig.commit("A")
    assert rig.deploy().returncode == 0
    _ = rig.commit("B")
    assert rig.watch_across_a_deploy(rig.home / _WRAPPER) == {"A"}
    assert _generation(rig.watch()) == "B"

    # Control: without the realpath the same rig must catch a mixed import.
    wrapper = (rig.home / _WRAPPER).read_text(encoding="utf-8")
    mutant_text = wrapper.replace("os.path.realpath(RUNTIME_ROOT)", "str(RUNTIME_ROOT)")
    assert mutant_text != wrapper
    mutant = rig.home / ".hermes/scripts/mutant_watch.py"
    _ = mutant.write_text(mutant_text, encoding="utf-8")
    _ = rig.commit("C")
    assert rig.watch_across_a_deploy(mutant) == {"B", "C"}


def test_an_old_wrapper_mid_import_during_the_first_deploy_keeps_the_root_copy(rig: Rig) -> None:
    rig.seed_root()
    _ = rig.commit("B")
    before = _tree(rig.root_copy)
    assert rig.watch_across_a_deploy(rig.home / _WRAPPER) == {"root"}
    assert _tree(rig.root_copy) == before
    assert (rig.home / _WRAPPER).read_bytes() == (_REPO / _WRAPPER_SOURCE).read_bytes()
    assert _generation(rig.watch()) == "B"


@pytest.mark.parametrize("fault", ["prepare", "switch", "push"])
@pytest.mark.parametrize("first", [False, True], ids=["later", "first"])
def test_a_failure_mid_transfer_leaves_a_whole_runtime(rig: Rig, first: bool, fault: str) -> None:
    previous = None
    if first:
        rig.seed_root()
    else:
        _ = rig.commit("A")
        assert rig.deploy().returncode == 0
        previous = rig.link.resolve()
    earlier = _tree(previous) if previous else {}
    root_before = _tree(rig.root_copy) if first else {}
    wrapper_before = (rig.home / _WRAPPER).read_bytes()
    shipped = rig.commit("B")

    result = rig.deploy(fault=fault)
    assert result.returncode != 0
    assert fault in {line.split()[1] for line in rig.log()}

    generations = rig.home / f"{_LINK}.d"
    names = sorted(p.name for p in generations.iterdir()) if generations.exists() else []
    if fault == "push":
        # The first push is the config file, so the swapped tree holds only the shipped Python sources.
        real = {f"automation/{name}": (rig.repo / "automation" / name).read_bytes()
                for name in ("node_config.py", "notice_bot.py")}
        assert rig.link.is_symlink() and _tree(rig.link.resolve()) == shipped | real
    elif first:
        assert not rig.link.is_symlink() and not rig.link.exists()
        assert names == []
    else:
        assert previous is not None
        assert rig.link.resolve() == previous and _tree(previous) == earlier
        assert names == [previous.name]
    assert (rig.home / _WRAPPER).read_bytes() == wrapper_before
    if first:
        assert _tree(rig.root_copy) == root_before
    assert not any(name.startswith(".staging.") for name in names)
    assert not list((rig.home / _RUNTIME).glob(".current.link.*"))
    assert not list((rig.home / ".hermes/scripts").glob("*.deploy-tmp.*"))
    expected = "root" if first else ("B" if fault == "push" else "A")
    assert _generation(rig.watch()) == expected
