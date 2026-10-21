"""Actually *runs* the skill scenarios that are provably side-effect-free.

`tests/unit/test_skill_scenario_drift.py` deliberately executes nothing: it
pins the drift that is visible statically (a scenario naming a path a migration
deleted, a scenario that stopped parsing) and its own guard asserts that the
module never invokes a scenario for real. That file is FS3-pinned — one added
line invalidates the recorded replay evidence — so the runtime half of the same
follow-up lives here instead of widening it.

The follow-up it closes: only 3 of 18 scenarios (calendar, mail, wiki, each in
its own dedicated test) run before deploy, so the remaining 15 first execute on
the peer node during `automation/deploy-skill.sh` stage 1 — the most expensive
place to discover a break. Dumping all 18 into the unit suite is not the fix:
most of them reach a real external-effect transport (Gmail send, Google Tasks
insert, Drive upload, an LLM CLI) and are offline *only* while an env-var stub
seam holds. This module therefore grows the covered set one skill at a time,
each run under a `mktemp` HOME (mode 700) with dummy credentials and a bounded
timeout, exactly the way deploy-skill.sh:853-858 shapes its sandbox. Bubblewrap
adds a process boundary: the repository is read-only, the real user HOME and
`/srv` are hidden, `/run` is empty, networking is unshared, and named service,
mail, browser, Drive, and model clients are refusing stubs that log every call.

Inventory — every `skills/*/scripts/scenario.sh`, classified by reading it.
SAFE means: no external-effect transport is reachable on any leg, every write
lands in a scenario-owned `mktemp -d`, and the executed path does not depend on
what happens to exist on the host. `_EXECUTED` / `_HELD_BACK` below are the
machine-consumed form of this table and must cover the on-disk set.

    | skill           | verdict | evidence                                        |
    |-----------------|---------|-------------------------------------------------|
    | budget          | UNSAFE  | successful mail leg replaces Gmail with a gws stub, so inclusion would only assert the stub call |
    | calendar        | UNSAFE  | successful insert/delete legs replace Calendar with a gws stub; already run by test_calendar_scenario.py |
    | coordination    | UNSAFE  | Discord CLI leg proves only tokenless refusal; no faithful transport execution |
    | doctype         | UNSAFE  | document prose and extraction are answers from a local Codex executable stub |
    | hello-autophagy | SAFE    | no seams at all: runs scripts/hello.sh and asserts its marker |
    | mail            | UNSAFE  | mailon send and Codex responses are local stubs; already run by test_mail_scenario.py |
    | meeting         | UNSAFE  | extraction consumes --offline recorded-response fixtures rather than the model boundary |
    | patent-prep     | UNSAFE  | Drive/age clients are canaries and only the no-manifest refusal leg executes |
    | plaud           | SAFE    | stdlib-only read of a mktemp state fixture; no transport, no writes outside mktemp |
    | procurement     | UNSAFE  | Discord review is stubbed and Drive is disabled, so no successful review delivery is exercised |
    | prompt          | SAFE    | local store only; hidden /srv fixes the repo root and explicit temp overlay/private roots hold writes |
    | proposal        | UNSAFE  | research, image, refine, and Drive success paths all use fake transports |
    | recall          | SAFE    | read-only skill; RECALL_FAKE_RESULTS / RECALL_FAKE_ERROR fixtures; writes confined to mktemp |
    | repair          | SAFE    | reads SKILL.md and asserts three substrings; no subprocess, no writes |
    | report          | SAFE    | recorded response/evidence fixtures exercise outputs; DRIVE_PUBLISH_ENABLED=0 is explicit and deniers stay untouched |
    | speechtotext    | UNSAFE  | transcript is recorded or emitted by fake whisper/ffmpeg; API leg points at a closed port |
    | todo            | UNSAFE  | successful Google Tasks insert/get uses an in-memory fake gws executable |
    | wiki            | SAFE    | no effect transport, but already run by test_wiki_scenario.py with a staged INTEROP_RUNTIME |

Determinism: no sleeps and no polling. The only time bound is the subprocess
timeout, which turns a hung scenario into a failure instead of a hang.
"""

from __future__ import annotations

import os
import pwd
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest

_REPO: Final = Path(__file__).resolve().parents[2]
_SCENARIOS: Final = tuple(sorted(_REPO.glob("skills/*/scripts/scenario.sh")))

# Every included scenario finishes in well under a second on the deploy host;
# the bound exists so a regression that blocks on a socket fails the suite.
_TIMEOUT_S: Final = 30.0
_EFFECT_CLIENTS: Final = (
    "agent-browser", "chromium", "chromium-browser", "codex", "curl", "firefox",
    "google-chrome", "gws", "hermes", "mailon", "ssh", "wget", "xdg-open",
)
_NAMESPACE_REPO: Final = Path("/tmp/workspace/autophagy")
_NAMESPACE_SANDBOX: Final = Path("/tmp/scenario")

# The scenarios this module executes. Growing it is the point of the follow-up:
# add a skill only after reading it end to end and confirming the SAFE criteria
# in the table above.
_EXECUTED: Final = (
    "hello-autophagy", "plaud", "prompt", "recall", "repair", "report",
)

# Everything else, with the effect that keeps it out. Fail-closed: a new
# scenario that is in neither mapping fails `test_every_scenario_is_classified`
# rather than being silently skipped.
_HELD_BACK: Final[Mapping[str, str]] = {
    "budget": "successful Gmail leg substitutes a local gws stub",
    "calendar": "successful Calendar write/delete legs substitute a local gws stub",
    "coordination": "Discord leg stops at tokenless refusal",
    "doctype": "Codex prose and extraction come from a local executable stub",
    "mail": "mailon send and Codex answers come from local stubs",
    "meeting": "LLM extraction consumes recorded-response fixtures",
    "patent-prep": "Drive/age canaries cover only the no-manifest refusal",
    "procurement": "Discord is stubbed and Drive publication is disabled",
    "proposal": "research/image/refine/Drive success paths use fake transports",
    "speechtotext": "transcripts and local tools are recorded/fake; API is a closed port",
    "todo": "Google Tasks insert/get uses an in-memory fake gws executable",
    "wiki": "already executed by tests/unit/test_wiki_scenario.py",
}


@dataclass(frozen=True, slots=True)
class ScenarioOutcome:
    """What one sandboxed scenario run produced."""

    returncode: int
    stdout: str
    stderr: str
    effect_attempts: tuple[str, ...]


def _effect_deniers(home: Path) -> tuple[Path, Path]:
    deny_bin = home / "deny-bin"
    deny_bin.mkdir(mode=0o700)
    log = home / "effect-attempts.log"
    body = "#!/bin/sh\nprintf '%s\\t%s\\n' \"$0\" \"$*\" >> \"$SCENARIO_EFFECT_LOG\"\nexit 97\n"
    for name in _EFFECT_CLIENTS:
        path = deny_bin / name
        _ = path.write_text(body, encoding="utf-8")
        path.chmod(0o700)
    return deny_bin, log


def _namespace_scenario(scenario: Path, sandbox_root: Path) -> Path:
    try:
        return _NAMESPACE_REPO / scenario.resolve().relative_to(_REPO)
    except ValueError:
        return _NAMESPACE_SANDBOX / scenario.resolve().relative_to(sandbox_root.resolve())


def run_scenario_isolated(
    scenario: Path, home: Path, *, timeout_s: float = _TIMEOUT_S
) -> ScenarioOutcome:
    """Run one scenario the way deploy-skill.sh stage 1 does.

    Bubblewrap receives an empty environment and a private network namespace.
    Only the disposable sandbox is writable; the real passwd HOME, host service
    sockets, and deployment roots are hidden. Effect-capable executables resolve
    to logging deniers, so a swallowed invocation is still a test failure.
    """
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    for directory in (home / "tmp", home / "runtime"):
        directory.mkdir(mode=0o700)
    deny_bin, effect_log = _effect_deniers(home)
    sandbox_root = home.parent
    namespace_home = _NAMESPACE_SANDBOX / home.relative_to(sandbox_root)
    bwrap = shutil.which("bwrap")
    assert bwrap is not None, "bubblewrap is required for process-isolated scenario coverage"
    environment = {
        "AUTOPHAGY_DEMO_SECRET": "DUMMY-unit-scenario-execution",
        "AUTOPHAGY_REPO_ROOT": str(_NAMESPACE_REPO),
        "AUTOPHAGY_SKILL_LIVE_ROOT": str(_NAMESPACE_REPO / "skills"),
        "DRIVE_GWS_BIN": str(namespace_home / deny_bin.name / "gws"),
        "DRIVE_PUBLISH_ENABLED": "0",
        "HOME": str(namespace_home),
        "INTEROP_RUNTIME": str(namespace_home / "runtime" / "interop"),
        "PATH": f"{namespace_home / deny_bin.name}:/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "SCENARIO_EFFECT_LOG": str(namespace_home / effect_log.name),
        "TMPDIR": str(namespace_home / "tmp"),
        "XDG_CACHE_HOME": str(namespace_home / "runtime" / "cache"),
        "XDG_CONFIG_HOME": str(namespace_home / "runtime" / "config"),
        "XDG_DATA_HOME": str(namespace_home / "runtime" / "data"),
        "XDG_STATE_HOME": str(namespace_home / "runtime" / "state"),
    }
    command = [
        bwrap, "--die-with-parent", "--unshare-all", "--new-session",
        "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
        "--tmpfs", "/tmp", "--dir", str(_NAMESPACE_REPO.parent),
        "--ro-bind", str(_REPO), str(_NAMESPACE_REPO),
        "--dir", str(_NAMESPACE_SANDBOX), "--bind", str(sandbox_root), str(_NAMESPACE_SANDBOX),
        "--tmpfs", "/run", "--tmpfs", "/srv",
        "--tmpfs", pwd.getpwuid(os.getuid()).pw_dir, "--clearenv",
    ]
    for key, value in environment.items():
        command.extend(("--setenv", key, value))
    command.extend(("--chdir", str(namespace_home), "/bin/bash", str(
        _namespace_scenario(scenario, sandbox_root)
    )))
    completed = subprocess.run(
        command, check=False, capture_output=True, text=True, timeout=timeout_s
    )
    attempts = tuple(effect_log.read_text(encoding="utf-8").splitlines()) if effect_log.exists() else ()
    return ScenarioOutcome(completed.returncode, completed.stdout, completed.stderr, attempts)


@pytest.mark.parametrize("skill", _EXECUTED)
def test_safe_scenario_passes_in_a_disposable_home(skill: str, tmp_path: Path) -> None:
    # Given: a scenario classified SAFE, and a HOME that holds nothing
    scenario = _REPO / "skills" / skill / "scripts" / "scenario.sh"

    # When: it runs with dummy credentials and no inherited environment
    outcome = run_scenario_isolated(scenario, tmp_path / "home")

    # Then: it reaches its own success marker, before deploy rather than during
    assert outcome.returncode == 0, outcome.stderr
    assert "SCENARIO-PASS" in outcome.stdout
    assert outcome.effect_attempts == ()


def test_the_harness_reports_a_mutated_scenario_as_failing(tmp_path: Path) -> None:
    """The harness must bite: a broken skill has to come back non-zero.

    The mutation is the real drift this module exists to catch — the skill's
    own script stops honouring the contract its scenario asserts — applied to a
    throwaway copy so the repository tree is untouched.
    """
    # Given: a copy of a passing skill whose greeting no longer matches
    skill_copy = tmp_path / "skills" / "hello-autophagy"
    _ = shutil.copytree(_REPO / "skills" / "hello-autophagy", skill_copy)
    greeting = skill_copy / "scripts" / "hello.sh"
    _ = greeting.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
echo MUTATED-MARKER
""",
        encoding="utf-8",
    )

    mutated = run_scenario_isolated(skill_copy / "scripts" / "scenario.sh", tmp_path / "m")
    intact = run_scenario_isolated(
        _REPO / "skills" / "hello-autophagy" / "scripts" / "scenario.sh", tmp_path / "i"
    )

    # Then: the mutant fails loudly and the untouched original still passes
    assert mutated.returncode != 0
    assert "SCENARIO-FAIL" in mutated.stderr
    assert "SCENARIO-PASS" not in mutated.stdout
    assert intact.returncode == 0, intact.stderr
    assert mutated.effect_attempts == intact.effect_attempts == ()


def test_the_harness_hands_over_no_inherited_credential(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: a credential in the parent process that no scenario may observe
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "DUMMY-parent-only")
    probe = tmp_path / "probe.sh"
    _ = probe.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
[[ -z "${DISCORD_BOT_TOKEN:-}" ]] || exit 9
[[ "$AUTOPHAGY_DEMO_SECRET" == DUMMY-* ]] || exit 8
[[ "$PWD" == "$HOME" ]] || exit 7
echo PROBE-PASS
""",
        encoding="utf-8",
    )

    # When: the probe runs through the same harness the scenarios use
    outcome = run_scenario_isolated(probe, tmp_path / "home")

    # Then: it saw a dummy secret, a disposable HOME, and no parent token
    assert outcome.returncode == 0, outcome.stderr
    assert "PROBE-PASS" in outcome.stdout
    assert outcome.effect_attempts == ()


def test_process_boundary_denies_and_logs_effect_clients(tmp_path: Path) -> None:
    probe = tmp_path / "boundary.sh"
    _ = probe.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
set +e
gws drive files list
rc=$?
set -e
[[ "$rc" -eq 97 ]] || exit 6
python3 -I - <<'PY'
import os
import pwd
import socket
from pathlib import Path

assert not any(Path(pwd.getpwuid(os.getuid()).pw_dir).iterdir())
with socket.socket() as client:
    try:
        client.connect(("127.0.0.1", 9))
    except OSError:
        pass
    else:
        raise AssertionError("private network reached a listener")
PY
echo BOUNDARY-PASS
""",
        encoding="utf-8",
    )

    outcome = run_scenario_isolated(probe, tmp_path / "home")

    assert outcome.returncode == 0, outcome.stderr
    assert "BOUNDARY-PASS" in outcome.stdout
    assert len(outcome.effect_attempts) == 1
    assert outcome.effect_attempts[0].endswith("gws\tdrive files list")


def test_every_scenario_is_classified() -> None:
    # Given: the scenarios actually on disk right now
    on_disk = {scenario.parents[1].name for scenario in _SCENARIOS}
    assert len(on_disk) >= 10, "empty glob would make this vacuous"

    # Then: each is either executed here or held back for a named effect, and
    # a newly added scenario fails this until somebody reads and classifies it
    classified = set(_EXECUTED) | set(_HELD_BACK)
    assert on_disk == classified
    assert set(_EXECUTED).isdisjoint(_HELD_BACK)
    assert all(reason for reason in _HELD_BACK.values())
