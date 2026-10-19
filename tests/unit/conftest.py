"""Redirect unit-test HOME and the ``gws`` binary before collection.

Both run at import because production modules resolve ``Path.home()`` and
``shutil.which("gws")`` during their own imports.

``gws`` on this PATH carries the owner's Google credentials in the OS keyring, so
the HOME redirect never revoked them — on 2026-09-11 the suite was measured
inserting real tasks into the owner's list. All four skills and ``DriveClient``
resolve the binary through ``shutil.which``, so PATH is the one choke point that
also covers child processes; ``test_gws_path_guard.py`` pins it.
"""
from __future__ import annotations

import atexit
import importlib.abc
import importlib.machinery
import os
import shutil
import sys
import tempfile
import types
from collections.abc import Iterator
from typing import SupportsIndex
from pathlib import Path

import pytest

# One temporary root for the whole run; its private tmp is TMPDIR for every later temp
# directory (HOME included) and for children. See the node-state block below.
_UNIT_NODE = Path(tempfile.mkdtemp(prefix="autophagy-unit-node-"))
(_UNIT_NODE / "tmp").mkdir()
os.environ["TMPDIR"] = tempfile.tempdir = str(_UNIT_NODE / "tmp")

_UNIT_HOME = Path(tempfile.mkdtemp(prefix="autophagy-unit-home-"))
os.environ["HOME"] = str(_UNIT_HOME)
for _xdg_home_var in (
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
):
    _ = os.environ.pop(_xdg_home_var, None)

_ = atexit.register(shutil.rmtree, _UNIT_HOME, ignore_errors=True)

_GWS_DIR = Path(tempfile.mkdtemp(prefix="autophagy-unit-gws-"))
_GWS_GUARD = _GWS_DIR / "gws"
_gws_log_override = os.environ.get("AUTOPHAGY_UNIT_GWS_GUARD_LOG", "")
_GWS_GUARD_LOG = Path(_gws_log_override) if _gws_log_override else _GWS_DIR / "calls.log"
_ = _GWS_GUARD.write_text(
    f"""#!/bin/sh
printf '%s\\t%s\\n' "${{PYTEST_CURRENT_TEST:-<no-test>}}" "$*" >> '{_GWS_GUARD_LOG}'
echo 'UNIT-TEST-GWS-BLOCKED: a unit test tried to run the real gws binary.' >&2
echo 'Inject a runner (FakeGws) or set the skill *_GWS_BIN override.' >&2
exit 97
""",
    encoding="utf-8",
)
_GWS_GUARD.chmod(0o755)
os.environ["AUTOPHAGY_UNIT_GWS_GUARD"] = str(_GWS_GUARD)
os.environ["AUTOPHAGY_UNIT_GWS_GUARD_LOG"] = str(_GWS_GUARD_LOG)
os.environ["PATH"] = f"{_GWS_DIR}{os.pathsep}{os.environ.get('PATH', '')}"

_ = atexit.register(shutil.rmtree, _GWS_DIR, ignore_errors=True)

# Node state (2026-10-19). The release host became the primary node itself, so the
# suite now runs where /srv/autophagy-skills/live, /etc/autophagy/node.toml and the
# service accounts are real. A workstation without them had always passed; on the
# node 324 tests failed against main because production defaults resolved to the live
# mount (STALE-SKILL-COPY-BLOCK) and the installed node config, and because the
# operator account may `sudo -n -u agent|ops` without a password. Redirect every
# default the suite reaches to an absent path under one temporary root, and refuse
# sudo the way gws is refused: a test that meant a fake already puts it first on PATH.
# test_unit_node_isolation.py pins this.
_REPO_ROOT = Path(__file__).resolve().parents[2]
# Each value is an existing production override; the paths under _UNIT_NODE stay absent,
# which is exactly what a workstation without an install answers.
_NODE_ENV = {
    "AUTOPHAGY_UNIT_NODE_ROOT": str(_UNIT_NODE),
    # governed live mount (skill_mount.LIVE_ROOT_ENV)
    "AUTOPHAGY_SKILL_LIVE_ROOT": str(_UNIT_NODE / "skills-live"),
    # knowledge's wiki adapter reads the live wiki scripts unless told otherwise
    "WIKI_SCRIPTS": str(_UNIT_NODE / "skills-live" / "wiki" / "scripts"),
    # cron wrappers and bootstraps otherwise put /srv/autophagy-agent-current on sys.path
    "AUTOPHAGY_REPO_ROOT": str(_REPO_ROOT),
    # the shell bridge's /etc/autophagy/node.toml (node_config_sh.SYSTEM_PATH_ENV)
    "AUTOPHAGY_SYSTEM_NODE_CONFIG": str(_UNIT_NODE / "etc" / "node.toml"),
    # the shared approvals log under the node mirror, under each gate's own name
    **dict.fromkeys(
        (
            "APPROVAL_LOG_PATH",
            "EXTERNAL_EFFECT_APPROVAL_LOG",
            "BUDGET_APPROVAL_LOG",
            "CALENDAR_APPROVAL_LOG",
            "TODO_APPROVAL_LOG",
            "TRIAGE_APPROVAL_LOG",
        ),
        str(_UNIT_NODE / "mirror" / "logs" / "approvals.jsonl"),
    ),
    # healthcheck_registry owner-notice probe target
    "HEALTHCHECK_OWNER_NOTICE_CREDENTIAL": str(_UNIT_NODE / "etc" / "repair-approval.env"),
    # shared /tmp locks (origin_snapshot) that a node service account may already own
    "TMPDIR": str(_UNIT_NODE / "tmp"),
}
os.environ.update(_NODE_ENV)
# unit_node.env() hands these to children a test starts with a hand-built env.
os.environ["AUTOPHAGY_UNIT_NODE_ENV_KEYS"] = ",".join(_NODE_ENV)

_SUDO_GUARD = _GWS_DIR / "sudo"
_ = _SUDO_GUARD.write_text(
    f"""#!/bin/sh
printf '%s\\tsudo %s\\n' "${{PYTEST_CURRENT_TEST:-<no-test>}}" "$*" >> '{_GWS_GUARD_LOG}'
echo 'UNIT-TEST-SUDO-BLOCKED: a unit test tried to run the real sudo.' >&2
echo 'Put a fake sudo first on PATH or inject the runner.' >&2
exit 1
""",
    encoding="utf-8",
)
_SUDO_GUARD.chmod(0o755)
os.environ["AUTOPHAGY_UNIT_SUDO_GUARD"] = str(_SUDO_GUARD)

import automation.node_config as _node_config  # noqa: E402  (after HOME moves)

_UNIT_SYSTEM_NODE_CONFIG = Path(_NODE_ENV["AUTOPHAGY_SYSTEM_NODE_CONFIG"])
_node_config.SYSTEM_NODE_CONFIG_PATH = _UNIT_SYSTEM_NODE_CONFIG  # pyright: ignore[reportConstantRedefinition]


class _NodeConfigReimport(importlib.abc.MetaPathFinder):
    """Tests that drop ``automation`` from sys.modules re-import a fresh node_config."""

    def find_spec(self, name: str, path: object, target: object = None) -> importlib.machinery.ModuleSpec | None:
        if name != "automation.node_config":
            return None
        spec = importlib.machinery.PathFinder.find_spec(name, path)  # pyright: ignore[reportArgumentType]
        if spec is None or spec.loader is None:
            return spec
        loader = spec.loader

        class _Loader(importlib.abc.Loader):
            def create_module(self, spec: importlib.machinery.ModuleSpec) -> None:
                return None

            def exec_module(self, module: types.ModuleType) -> None:
                loader.exec_module(module)
                module.SYSTEM_NODE_CONFIG_PATH = _UNIT_SYSTEM_NODE_CONFIG  # pyright: ignore[reportAttributeAccessIssue]

        spec.loader = _Loader()
        return spec


sys.meta_path.insert(0, _NodeConfigReimport())

_ = atexit.register(shutil.rmtree, _UNIT_NODE, ignore_errors=True)

# The guard behind the redirects: an in-process open/listdir of a node path that exists on
# this host fails the test that did it, naming the path. A workstation has none of these,
# so a missing redirect used to surface only on the node, as an unrelated assertion.
_NODE_PREFIXES = ("/srv/autophagy", "/etc/autophagy", "/var/lib/autophagy", "/usr/local/libexec/autophagy")
_NODE_HOMES = tuple(f"/home/{name}" for name in ("agent", "peer", "ops", "yoodori", "okdori", "moodori"))
_node_reads: list[str] = []


def _node_path(text: str) -> bool:
    return text.startswith(_NODE_PREFIXES) or any(
        text == home or text.startswith(home + "/") for home in _NODE_HOMES
    )


def _node_read_audit(event: str, args: tuple[object, ...]) -> None:
    if event not in ("open", "os.listdir", "os.scandir") or not args:
        return
    path = args[0]
    if not isinstance(path, (str, bytes, os.PathLike)):
        return
    text = os.fsdecode(os.fspath(path))
    if _node_path(text) and os.path.lexists(text):
        _node_reads.append(f"{event} {text}")


sys.addaudithook(_node_read_audit)


class _NodeFreePath(list[str]):
    """sys.path that drops installed-runtime entries a module inserts on import.

    Cron wrappers put /srv/autophagy-agent-current and live skill scripts on sys.path
    when they are imported. On the node those exist, so a later test's import resolves
    or scans the installed copy instead of this checkout; elsewhere they are inert.
    """

    def insert(self, index: SupportsIndex, entry: str) -> None:
        if not (isinstance(entry, str) and _node_path(entry)):
            super().insert(index, entry)

    def append(self, entry: str) -> None:
        if not (isinstance(entry, str) and _node_path(entry)):
            super().append(entry)


sys.path = _NodeFreePath(entry for entry in sys.path if not _node_path(entry))


# Tests that still read this host's install on purpose, each with why. An entry is a
# debt, not a pass: it names a reader that has no injectable root yet.
_INSTALLER_PLANS_THE_HOST = (
    "the installer dry run plans against this host's /etc/autophagy and "
    "/usr/local/libexec files by design; it has no host-root override yet"
)
_NODE_READ_EXEMPT: dict[str, str] = {
    "test_doctor_install_component.py::test_the_real_dry_run_plans_the_doctor_units": _INSTALLER_PLANS_THE_HOST,
    "test_install_apply.py::test_cli_dry_run_is_read_only_and_returns_zero": _INSTALLER_PLANS_THE_HOST,
    "test_install_config_required.py::test_dry_run_states_that_checks_are_not_executed": _INSTALLER_PLANS_THE_HOST,
    "test_install_config_required.py::test_dry_run_without_config_stays_allowed": _INSTALLER_PLANS_THE_HOST,
    "test_install_config_required.py::test_real_run_with_explicit_config_passes_the_config_gate": _INSTALLER_PLANS_THE_HOST,
    "test_install_group_trust.py::test_installer_when_group_roster_and_oob_fingerprint_are_given_then_plans_group_trust": _INSTALLER_PLANS_THE_HOST,
    "test_install_healthcheck_probe_asset.py::test_dry_run_shows_probe_when_host_is_empty": _INSTALLER_PLANS_THE_HOST,
    "test_install_opt_in_components.py::test_dry_run_with_the_flag_plans_the_units_and_the_timer": _INSTALLER_PLANS_THE_HOST,
    "test_install_opt_in_components.py::test_dry_run_without_the_flag_never_mentions_the_component": _INSTALLER_PLANS_THE_HOST,
    "test_install_profiles.py::test_the_cli_flag_plans_the_file_in_dry_run": _INSTALLER_PLANS_THE_HOST,
    "test_install_report_hub_component.py::test_unreadable_hub_symlink_is_planned_as_not_converged": _INSTALLER_PLANS_THE_HOST,
    "test_install_third_party_boundary.py::test_plain_dry_run_when_pyyaml_is_absent_then_still_prints_the_plan": _INSTALLER_PLANS_THE_HOST,
    "test_install_wizard.py::test_a_dry_run_only_never_runs_doctor": _INSTALLER_PLANS_THE_HOST,
    "test_install_wizard.py::test_a_failed_install_never_runs_doctor": _INSTALLER_PLANS_THE_HOST,
    "test_install_wizard.py::test_a_mistyped_profile_at_the_prompt_is_asked_again_not_crashed": _INSTALLER_PLANS_THE_HOST,
    "test_install_wizard.py::test_after_install_the_wizard_rechecks_on_enter_until_nothing_is_broken": _INSTALLER_PLANS_THE_HOST,
    "test_install_wizard.py::test_an_unreached_account_keeps_the_loop_open": _INSTALLER_PLANS_THE_HOST,
    "test_install_wizard.py::test_anything_but_yes_ends_the_run_before_any_sudo": _INSTALLER_PLANS_THE_HOST,
    "test_install_wizard.py::test_non_interactive_runs_doctor_once_and_never_waits": _INSTALLER_PLANS_THE_HOST,
    "test_install_wizard.py::test_q_leaves_the_connection_loop_without_changing_the_install_result": _INSTALLER_PLANS_THE_HOST,
    "test_install_wizard.py::test_the_approvals_a_person_must_give_are_listed_before_the_install_starts": _INSTALLER_PLANS_THE_HOST,
    "test_install_wizard.py::test_the_plan_summary_is_counted_from_the_real_dry_run_output": _INSTALLER_PLANS_THE_HOST,
    "test_install_wizard.py::test_the_wizard_asks_for_what_the_flags_did_not_give": _INSTALLER_PLANS_THE_HOST,
    "test_install_wizard.py::test_yes_runs_the_apply_once_with_the_same_arguments": _INSTALLER_PLANS_THE_HOST,
}


@pytest.fixture(autouse=True)
def _no_real_node_reads(request: pytest.FixtureRequest) -> Iterator[None]:
    # hermes_compat_boot merges an installed runtime into automation.__path__; drop it
    # before each test as sys.path does, so package imports never scan the node's copy.
    package = sys.modules.get("automation")
    search = getattr(package, "__path__", None)
    if isinstance(search, list):
        search[:] = [entry for entry in search if not _node_path(str(entry))]
    _node_reads.clear()
    yield
    key = f"{Path(str(request.node.path)).name}::{request.node.originalname}"
    if _node_reads and key in _NODE_READ_EXEMPT:
        _node_reads.clear()
    if _node_reads:
        seen = sorted(set(_node_reads))
        _node_reads.clear()
        pytest.fail(
            "UNIT-TEST-NODE-READ: this test read real node state: " + "; ".join(seen[:5])
            + " — inject the path or use unit_node.env() (tests/unit/conftest.py)",
            pytrace=False,
        )
