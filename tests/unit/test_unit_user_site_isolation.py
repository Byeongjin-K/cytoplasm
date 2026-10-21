"""Pins conftest's user-site pin: children keep the suite's packages, never the real home.

conftest moves HOME to a throwaway directory so no test reads the developer's ~/.hermes.
Python finds the user site (``pip install --user``) through HOME, so on a host where
pytest and pytest-xdist live only there, every child python3 a test started died with
"No module named pytest": 16 tests failed that way on the primary node on 2026-10-19 and
twice on 2026-10-21, though main had not changed. conftest now pins PYTHONUSERBASE to the
base this interpreter already used. These checks fail on any host when the pin is
removed, and when a fix restores the real HOME instead.
"""
from __future__ import annotations

import os
import pwd
import site
import subprocess
import sys
from pathlib import Path

_PROBE = (
    "import site, pathlib, os;"
    "print(site.getuserbase());"
    "print(pathlib.Path.home());"
    "print(os.path.expanduser('~/.hermes'))"
)


def _child() -> list[str]:
    result = subprocess.run(
        (sys.executable, "-c", _PROBE), env=dict(os.environ), capture_output=True, text=True, check=True,
    )
    return result.stdout.splitlines()


def test_a_child_python_resolves_the_user_base_this_interpreter_already_used() -> None:
    # Given: the suite runs with HOME moved to the unit home
    unit_home = Path(os.environ["HOME"])
    # When: a child python3 asks where its user site lives
    user_base = Path(_child()[0])
    # Then: it is the parent's base, not one under the throwaway home
    assert user_base == Path(site.getuserbase())
    assert not user_base.is_relative_to(unit_home)


def test_a_child_python_still_imports_pytest_with_the_unit_home() -> None:
    # When: a child started the way the failing tests start one imports the test runner
    result = subprocess.run(
        (sys.executable, "-c", "import pytest"), env=dict(os.environ), capture_output=True, text=True,
    )
    # Then: it is found wherever this interpreter found it
    assert result.returncode == 0, result.stderr


def test_the_child_home_stays_the_unit_home_not_the_real_one() -> None:
    # Given: the account's real home from passwd
    real_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    # When: the child resolves its home and the state directory under it
    _, home, hermes = _child()
    # Then: both stay inside the throwaway home; the pin opened no path to real home state
    assert Path(home) == Path(os.environ["HOME"])
    assert Path(home) != real_home
    assert not Path(hermes).is_relative_to(real_home)
