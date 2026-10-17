"""RCB todo 17: the poller's two runtime files change as ONE generation behind a link.

The real `automation/reminder_poller/deploy.sh` runs from a throwaway git copy. Fake `ssh`
and `sudo` shims stand in for the remote account: each remote script runs in a local shell
under a temp HOME. When asked, the shims also stop after every remote call on a FIFO, so a
poller can be started at each of those points. Each generation's modules are the real two
files with one extra line that records `<generation>:<module>` when the module is imported.
Pollers run under `env -i` without PYTHONPATH, so the package import fails and the wrapper
takes its flat fallback, just as the cron sandbox does. Handshakes only; there are no sleeps.
"""
from __future__ import annotations

import os
import re
import select
import shlex
import shutil
import signal
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest

from automation.deploy_declarations import all_declarations
from automation.cron_schedule import on_calendar
from tests.unit.cron_fixture import HELPER, TIMER_HELPER, declared_cron, listing_only_hermes, timer_env

_REPO = Path(__file__).resolve().parents[2]
_PKG = "automation/reminder_poller"
_RUNTIME = ".hermes/reminder_poller_runtime"
_LINK = f"{_RUNTIME}/.current"
_WRAPPER = ".hermes/scripts/poll_reminders.py"
_MODULES = ("poller_core", "reminder_store")
_SOURCED = re.compile(r'^\s*source\s+"\$\w+/([\w.-]+\.sh)"', re.MULTILINE)


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


_COPIED = tuple(dict.fromkeys((
    f"{_PKG}/__init__.py", f"{_PKG}/poll_reminders.py", f"{_PKG}/poller_core.py",
    f"{_PKG}/reminder_store.py", f"{_PKG}/deploy.sh", f"{_PKG}/deploy-manifest.txt",
    "automation/__init__.py", *_tree_helper_closure(), "automation/deploy_push.sh",
    "automation/deploy_provenance.sh", TIMER_HELPER, "automation/node_config_sh.py",
    "automation/node_config.py", "configs/node.example.toml",
    "configs/runtime-package-manifest.txt",
)))
_NEW_PATH = (
    'sys.path.insert(0, os.path.realpath(Path.home() / ".hermes" / "reminder_poller_runtime"'
    ' / ".current"))'
)
_OLD_PATH = 'sys.path.insert(0, str(Path.home() / ".hermes" / "reminder_poller_runtime"))'
_FUTURE = "from __future__ import annotations\n"
_CRON = declared_cron(_PKG)
_FULL_RUN = ["prepare", "switch", "verify", "push", "readback",
             f"timer:{_CRON.destination}", f"timer-check:{_CRON.destination}"]

_SSH = '#!/bin/bash\nexec bash -c "$2"\n'
_SUDO = r'''#!/bin/bash
script="${@: -1}"
last="${script##*$'\n'}"
case "$last" in
  _deploy_tree_remote_prepare) kind=prepare ;;
  _deploy_tree_remote_switch) kind=switch ;;
  _deploy_tree_remote_verify) kind=verify ;;
  _deploy_tree_remote_rollback*) kind=rollback ;;
  'rm -rf -- "$staging"') kind=cleanup ;;
  *) case "$script" in
       *"automation.cron_timer converge --name "*) name="${script##*converge --name }"; kind="timer:${name%% *}" ;;
       *"automation.cron_timer check --name "*) name="${script##*check --name }"; kind="timer-check:${name%% *}" ;;
       sha256sum*) kind=readback ;; *deploy-tmp*) kind=push ;; *) kind=other ;; esac ;;
esac
printf '%s\n' "$kind" >> "$RIG/calls.log"
rc=0
if [[ "$FAULT" == push && "$kind" == push ]]; then
  cat >/dev/null; out=""; rc=1
else
  out="$(HOME="$NODE_HOME" bash -c "$script")" || rc=$?
fi
[[ "$FAULT" == prepare && "$kind" == prepare ]] && out="misleading-success"
if [[ -n "${PAUSE:-}" ]]; then
  printf '%s\n' "$kind" > "$RIG/arrived"
  read -r _ < "$RIG/release"
fi
printf '%s\n' "$out"
exit "$rc"
'''
_MV = r'''#!/bin/bash
if [[ "$FAULT" == switch && "${@: -1}" == */.current ]]; then exit 1; fi
exec /bin/mv "$@"
'''


def _marked(module: str, label: str) -> str:
    """The real module with one line that records its import (after the __future__ line)."""
    source = (_REPO / _PKG / f"{module}.py").read_text(encoding="utf-8")
    assert source.count(_FUTURE) == 1
    mark = (
        "import os as _mark_os\n"
        "with open(_mark_os.environ['REMINDER_MARK_FILE'], 'a', encoding='utf-8') as _mark:\n"
        f"    _ = _mark.write('{label}:{module}\\n')\n"
        f"if _mark_os.environ.get('REMINDER_PAUSE_IN') == '{module}':\n"
        "    with open(_mark_os.environ['REMINDER_PAUSE_DIR'] + '/arrived', 'w') as _a:\n"
        "        _ = _a.write('in\\n')\n"
        "    with open(_mark_os.environ['REMINDER_PAUSE_DIR'] + '/release') as _r:\n"
        "        _ = _r.readline()\n"
    )
    return source.replace(_FUTURE, _FUTURE + mark)


def _old_wrapper() -> str:
    """The wrapper as it was before this change: the flat runtime directory on sys.path."""
    source = (_REPO / _PKG / "poll_reminders.py").read_text(encoding="utf-8")
    assert source.count(_NEW_PATH) == 1, "the wrapper does not pin the generation link"
    return source.replace(_NEW_PATH, _OLD_PATH)


def _line(stream: int) -> str:
    assert select.select([stream], [], [], 20)[0], "handshake deadline expired"
    return os.read(stream, 4096).decode()


@dataclass(frozen=True)
class Rig:
    root: Path
    repo: Path
    home: Path
    env: dict[str, str]

    def label(self, label: str) -> None:
        for module in _MODULES:
            (self.repo / _PKG / f"{module}.py").write_text(_marked(module, label), encoding="utf-8")

    def seed_flat(self) -> dict[str, bytes]:
        runtime, scripts = self.home / _RUNTIME, self.home / ".hermes/scripts"
        runtime.mkdir(parents=True)
        scripts.mkdir(parents=True)
        for module in _MODULES:
            (runtime / f"{module}.py").write_text(_marked(module, "flat"), encoding="utf-8")
        (runtime / "poll_reminders.py").write_text(_old_wrapper(), encoding="utf-8")
        (scripts / "poll_reminders.py").write_text(_old_wrapper(), encoding="utf-8")
        return self.snapshot()

    def snapshot(self) -> dict[str, bytes]:
        paths = [f"{_RUNTIME}/{name}.py" for name in (*_MODULES, "poll_reminders")] + [_WRAPPER]
        return {path: (self.home / path).read_bytes() for path in paths}

    def deploy(self, *, fault: str = "") -> subprocess.CompletedProcess[str]:
        (self.root / "calls.log").write_text("", encoding="utf-8")
        return subprocess.run(
            ("bash", str(self.repo / _PKG / "deploy.sh")), env={**self.env, "FAULT": fault},
            capture_output=True, text=True, check=False, timeout=60,
        )

    def calls(self) -> list[str]:
        return (self.root / "calls.log").read_text(encoding="utf-8").split()

    def poller(self, **extra: str) -> subprocess.Popen[str]:
        marks = self.root / "marks"
        marks.write_text("", encoding="utf-8")
        assignments = {
            "HOME": str(self.home), "PATH": "/usr/bin:/bin", "REMINDER_DRY_RUN": "1",
            "REMINDER_EVENTS_FILE": str(self.root / "events.json"),
            "REMINDER_MILESTONES_FILE": str(self.root / "no-milestones.yaml"),
            "REMINDER_DB": str(self.root / "reminders.db"), "REMINDER_MARK_FILE": str(marks),
            **extra,
        }
        argv = ["env", "-i", *(f"{k}={v}" for k, v in assignments.items()),
                str(self.env["REAL_PYTHON"]), str(self.home / _WRAPPER)]
        return subprocess.Popen(argv, cwd=self.root, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)

    def run_poller(self) -> tuple[int, list[str]]:
        with self.poller() as process:
            out, err = process.communicate(timeout=30)
        assert process.returncode == 0, out + err
        return process.returncode, (self.root / "marks").read_text(encoding="utf-8").split()

    def generations(self) -> list[Path]:
        root = self.home / f"{_LINK}.d"
        return sorted(root.iterdir()) if root.is_dir() else []


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    repo, home, bin_dir, workstation = (tmp_path / p for p in ("repo", "home", "bin", "ws"))
    for path in (home, bin_dir, workstation):
        path.mkdir()
    for relative in _COPIED:
        (repo / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_REPO / relative, repo / relative)
    shutil.copyfile(_REPO / HELPER, repo / HELPER)
    (home / ".local/bin").mkdir(parents=True)
    (home / ".local/bin/hermes").write_text(listing_only_hermes(_CRON), encoding="utf-8")
    (home / ".local/bin/hermes").chmod(0o755)
    subprocess.run(("git", "init", "-q", str(repo)), check=True)
    subprocess.run(("git", "-C", str(repo), "add", "."), check=True)
    for name, text in (("ssh", _SSH), ("sudo", _SUDO), ("mv", _MV)):
        (bin_dir / name).write_text(text, encoding="utf-8")
        (bin_dir / name).chmod(0o755)
    (tmp_path / "events.json").write_text('{"items": []}\n', encoding="utf-8")
    real_python = shutil.which("python3")
    assert real_python
    rig = Rig(tmp_path, repo, home, {
        **os.environ, "HOME": str(workstation), "NODE_HOME": str(home), "RIG": str(tmp_path),
        "PATH": f"{bin_dir}:{os.environ['PATH']}", "DEPLOY_SSH_HOST": "fake-node",
        "DEPLOY_ALLOW_UNPUSHED": "1", "REAL_PYTHON": real_python, "FAULT": "",
        **timer_env(tmp_path / "timer"),
    })
    rig.label("A")
    return rig


def _assert_one_generation(marks: list[str]) -> str:
    labels = {mark.split(":")[0] for mark in marks}
    assert sorted(mark.split(":")[1] for mark in marks) == sorted(_MODULES), marks
    assert len(labels) == 1, f"one poller imported two generations: {marks}"
    return labels.pop()


def _assert_whole_generations(rig: Rig, labels: tuple[str, ...]) -> None:
    for generation in rig.generations():
        assert not generation.name.startswith(".staging."), f"leftover staging {generation}"
        names = sorted(p.name for p in generation.iterdir())
        assert names == [f"{m}.py" for m in _MODULES], (generation, names)
        found = [
            label for label in labels
            if all((generation / f"{m}.py").read_text(encoding="utf-8") == _marked(m, label)
                   for m in _MODULES)
        ]
        assert len(found) == 1, f"{generation} mixes generations"
    leftovers = [p.name for p in (rig.home / _RUNTIME).iterdir() if ".link." in p.name]
    leftovers += [p.name for p in (rig.home / ".hermes/scripts").iterdir() if "deploy-tmp" in p.name]
    assert not leftovers, leftovers


def _link_label(rig: Rig) -> str:
    link = rig.home / _LINK
    assert link.is_symlink()
    target = os.readlink(link)
    assert target.startswith(".current.d/") and "/" not in target[len(".current.d/"):]
    return next(
        label for label in ("A", "B")
        if (link / "poller_core.py").read_text(encoding="utf-8") == _marked("poller_core", label)
    )


def test_the_runtime_lands_as_one_generation_before_the_wrapper(rig: Rig) -> None:
    flat = rig.seed_flat()

    result = rig.deploy()

    assert result.returncode == 0, result.stdout + result.stderr
    assert rig.calls() == _FULL_RUN
    units = rig.home / ".config/systemd/user"
    timer = (units / f"autophagy-cron-{_CRON.destination}.timer").read_text()
    assert f"OnCalendar={on_calendar(_CRON.attr('schedule'), '')}\n" in timer
    service = (units / f"autophagy-cron-{_CRON.destination}.service").read_text()
    assert f"--name {_CRON.destination} --script {_CRON.attr('script')}\n" in service
    [generation] = rig.generations()
    assert os.readlink(rig.home / _LINK) == f".current.d/{generation.name}"
    for module in _MODULES:
        assert (generation / f"{module}.py").read_bytes() == (rig.repo / _PKG / f"{module}.py").read_bytes()
    assert (rig.home / _WRAPPER).read_bytes() == (_REPO / _PKG / "poll_reminders.py").read_bytes()
    after = rig.snapshot()
    assert {k: v for k, v in after.items() if k != _WRAPPER} == {
        k: v for k, v in flat.items() if k != _WRAPPER
    }
    _assert_whole_generations(rig, ("A",))
    assert _assert_one_generation(rig.run_poller()[1]) == "A"


@contextmanager
def _fifos(rig: Rig) -> Iterator[tuple[int, int]]:
    for name in ("arrived", "release"):
        os.mkfifo(rig.root / name)
    with (
        os.fdopen(os.open(rig.root / "arrived", os.O_RDWR), "rb", buffering=0) as arrived,
        os.fdopen(os.open(rig.root / "release", os.O_RDWR), "wb", buffering=0) as release,
    ):
        yield arrived.fileno(), release.fileno()
    for name in ("arrived", "release"):
        (rig.root / name).unlink()


@pytest.mark.parametrize("first", [True, False], ids=["first-deploy", "next-deploy"])
def test_a_poller_started_at_any_point_of_a_deploy_imports_one_generation(
    rig: Rig, first: bool,
) -> None:
    _ = rig.seed_flat()
    if not first:
        assert rig.deploy().returncode == 0
        rig.label("B")
    seen: list[tuple[str, str]] = []
    with _fifos(rig) as (arrived, release), subprocess.Popen(
        ("bash", str(rig.repo / _PKG / "deploy.sh")), env={**rig.env, "PAUSE": "1"},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
    ) as deployer:
        try:
            seen.append(("start", _assert_one_generation(rig.run_poller()[1])))
            for _ in _FULL_RUN:
                stopped_at = _line(arrived).strip()
                seen.append((stopped_at, _assert_one_generation(rig.run_poller()[1])))
                _ = os.write(release, b"go\n")
            out, err = deployer.communicate(timeout=60)
        finally:
            if deployer.poll() is None:
                os.killpg(deployer.pid, signal.SIGKILL)
                _ = deployer.communicate(timeout=10)
    assert deployer.returncode == 0, out + err
    seen.append(("end", _assert_one_generation(rig.run_poller()[1])))

    assert [point for point, _ in seen] == ["start", *_FULL_RUN, "end"]
    old, new = ("flat", "A") if first else ("A", "B")
    expected_switch = "push" if first else "switch"
    flipped = [point for point, label in seen if label == new]
    assert flipped[0] == expected_switch, seen
    assert {label for _, label in seen} == {old, new}


def test_an_old_poller_mid_import_during_the_first_deploy_keeps_the_flat_files(rig: Rig) -> None:
    flat = rig.seed_flat()
    with _fifos(rig) as (arrived, release), rig.poller(
        REMINDER_PAUSE_IN="poller_core", REMINDER_PAUSE_DIR=str(rig.root),
    ) as poller:
        try:
            assert _line(arrived).strip() == "in"
            result = rig.deploy()
            _ = os.write(release, b"go\n")
            out, err = poller.communicate(timeout=30)
        finally:
            if poller.poll() is None:
                poller.kill()
                _ = poller.communicate(timeout=10)

    assert result.returncode == 0, result.stdout + result.stderr
    assert poller.returncode == 0, out + err
    assert (rig.root / "marks").read_text(encoding="utf-8").split() == [
        "flat:poller_core", "flat:reminder_store",
    ]
    after = rig.snapshot()
    assert {k: after[k] for k in flat if k != _WRAPPER} == {k: v for k, v in flat.items() if k != _WRAPPER}


@pytest.mark.parametrize("fault", ["prepare", "switch", "push"])
@pytest.mark.parametrize("first", [True, False], ids=["first-deploy", "next-deploy"])
def test_a_failure_mid_transfer_never_leaves_a_mixed_runtime(rig: Rig, first: bool, fault: str) -> None:
    flat = rig.seed_flat()
    if not first:
        assert rig.deploy().returncode == 0
        wrapper_before = (rig.home / _WRAPPER).read_bytes()
        rig.label("B")

    result = rig.deploy(fault=fault)

    assert result.returncode != 0, result.stdout + result.stderr
    assert fault in rig.calls() and ("push" in rig.calls()) == (fault == "push")
    after = rig.snapshot()
    assert {k: after[k] for k in flat if k != _WRAPPER} == {k: v for k, v in flat.items() if k != _WRAPPER}
    _assert_whole_generations(rig, ("A", "B"))
    if first and fault != "push":
        link = rig.home / _LINK
        assert not link.exists() and not link.is_symlink()
        assert after[_WRAPPER] == flat[_WRAPPER]
        assert _assert_one_generation(rig.run_poller()[1]) == "flat"
        return
    if first:
        assert _link_label(rig) == "A"
        assert after[_WRAPPER] == flat[_WRAPPER]
        assert _assert_one_generation(rig.run_poller()[1]) == "flat"
        return
    assert after[_WRAPPER] == wrapper_before
    expected = "B" if fault == "push" else "A"
    assert _link_label(rig) == expected
    assert _assert_one_generation(rig.run_poller()[1]) == expected


def test_a_poller_held_mid_import_while_the_next_deploy_flips_the_link_reads_one_generation(
    rig: Rig,
) -> None:
    """The wrapper resolves `.current` once, so a link flip between its two imports is invisible.

    The poller stops inside the generation A `poller_core` import. A whole second deploy moves
    the link to generation B before the poller goes on to import `reminder_store`. A wrapper
    that imports through the link name would take `reminder_store` from B.
    """
    _ = rig.seed_flat()
    assert rig.deploy().returncode == 0
    assert _link_label(rig) == "A"
    rig.label("B")

    with _fifos(rig) as (arrived, release), rig.poller(
        REMINDER_PAUSE_IN="poller_core", REMINDER_PAUSE_DIR=str(rig.root),
    ) as poller:
        try:
            assert _line(arrived).strip() == "in"
            result = rig.deploy()
            flipped_to = _link_label(rig)
            _ = os.write(release, b"go\n")
            out, err = poller.communicate(timeout=30)
        finally:
            if poller.poll() is None:
                poller.kill()
                _ = poller.communicate(timeout=10)

    assert result.returncode == 0, result.stdout + result.stderr
    assert rig.calls() == _FULL_RUN
    assert flipped_to == "B", "the link did not move while the poller was held"
    assert poller.returncode == 0, out + err
    marks = (rig.root / "marks").read_text(encoding="utf-8").split()
    assert marks == ["A:poller_core", "A:reminder_store"], f"one poller imported two generations: {marks}"


def test_the_flat_leftovers_are_declared_retired() -> None:
    rows = {row.destination: row for row in all_declarations(_REPO) if row.owner == _PKG}

    for name in ("poller_core", "reminder_store", "poll_reminders"):
        row = rows[f"{_RUNTIME}/{name}.py"]
        assert (row.kind, row.policy, row.legacy) == ("file", "retired", False)
        assert row.reason
    tree = rows[_LINK]
    assert (tree.kind, tree.policy, tree.source) == ("tree", "required", _PKG)
    assert tree.attr("files").split(",") == [f"{m}.py" for m in _MODULES]
    deployer = (_REPO / _PKG / "deploy.sh").read_text(encoding="utf-8")
    [call] = [line for line in deployer.splitlines() if line.startswith("deploy_tree_swap ")]
    args = shlex.split(call.split("||")[0])
    assert args[1] == "--link" and args[3] == _LINK
    assert args[4:] == tree.attr("files").split(",")
    assert rows[_WRAPPER].source == f"{_PKG}/poll_reminders.py"
