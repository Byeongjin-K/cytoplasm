from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SKILLS_ROOT = _REPO_ROOT / "skills"


def _is_repo_root_path(entry: str) -> bool:
    return Path(entry or ".").resolve() == _REPO_ROOT


@pytest.mark.parametrize(
    ("module_name", "skill_name"),
    (
        ("mail_preflight", "mail"),
        ("calendar_preflight", "calendar"),
        ("todo_preflight", "todo"),
    ),
)
def test_preflight_module_imports_without_automation_at_load_time(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    skill_name: str,
) -> None:
    # Given: a mounted skill script path without the repository package root.
    automation_modules = tuple(
        name for name in sys.modules if name == "automation" or name.startswith("automation.")
    )
    with monkeypatch.context() as isolated:
        for name in (*automation_modules, module_name):
            isolated.delitem(sys.modules, name, raising=False)
        isolated.delenv("AUTOPHAGY_REPO_ROOT", raising=False)
        isolated.setattr(sys, "path", [entry for entry in sys.path if not _is_repo_root_path(entry)])
        isolated.syspath_prepend(str(_SKILLS_ROOT / skill_name / "scripts"))
        importlib.invalidate_caches()

        # When: the deployed preflight module itself is loaded.
        loaded = importlib.import_module(module_name)

    # Then: importing does not require the unavailable automation package.
    # Then: importing does not require the unavailable automation package.
    assert isinstance(loaded, ModuleType)


@pytest.mark.parametrize(
    ("module_name", "skill_name"),
    (
        ("mail_preflight", "mail"),
        ("calendar_preflight", "calendar"),
        ("todo_preflight", "todo"),
    ),
)
def test_repo_module_resolves_when_partial_automation_already_bound(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    module_name: str,
    skill_name: str,
) -> None:
    """Regression: the deployed runtime binds a PARTIAL ``automation`` first.

    On the node ``~/.hermes/interop_runtime/automation`` is a REGULAR package
    (it has ``__init__.py``) containing only ``interop``. When the mail signed
    -confirm path imports ``automation.interop`` first, ``automation`` binds to
    that runtime with a FIXED ``__path__``. The lazy loader then inserts the repo
    root into ``sys.path``, but a regular package's ``__path__`` does not extend,
    so ``automation.entity_preflight`` stays unresolvable and the guard fails
    closed in production (mail/calendar/todo could not deploy). The loader must
    extend the bound package's ``__path__`` so the real submodules resolve.
    """
    # Given: a partial 'automation' regular package bound first, mimicking interop_runtime.
    partial = tmp_path / "runtime"
    (partial / "automation" / "interop").mkdir(parents=True)
    (partial / "automation" / "__init__.py").write_text("", encoding="utf-8")
    (partial / "automation" / "interop" / "__init__.py").write_text("", encoding="utf-8")

    automation_modules = tuple(
        name for name in sys.modules if name == "automation" or name.startswith("automation.")
    )
    with monkeypatch.context() as isolated:
        for name in (*automation_modules, module_name):
            isolated.delitem(sys.modules, name, raising=False)
        isolated.setenv("AUTOPHAGY_REPO_ROOT", str(_REPO_ROOT))
        isolated.setenv("AUTOPHAGY_RUNTIME_ROOT", str(_REPO_ROOT))
        isolated.setattr(sys, "path", [entry for entry in sys.path if not _is_repo_root_path(entry)])
        isolated.syspath_prepend(str(_SKILLS_ROOT / skill_name / "scripts"))
        isolated.syspath_prepend(str(partial))
        importlib.invalidate_caches()

        # Bind the partial 'automation' first, exactly like the signed-confirm path does.
        import automation  # noqa: F401
        importlib.import_module("automation.interop")

        preflight = importlib.import_module(module_name)

        # When: the loader resolves an entity_preflight submodule at guard time.
        contracts = preflight._repo_module("contracts")
        gate = preflight._repo_module("gate")

    # Then: the real repo submodules resolve despite the partial binding.
    assert contracts.__name__ == "automation.entity_preflight.contracts"
    assert gate.__name__ == "automation.entity_preflight.gate"


@pytest.mark.parametrize(
    ("module_name", "skill_name", "package_name"),
    (
        ("mail_preflight", "mail", "entity_preflight"),
        ("calendar_preflight", "calendar", "entity_preflight"),
        ("todo_preflight", "todo", "entity_preflight"),
        # The approval adapters resolve the same root to reach automation.interop.
        # They were missed when the preflight modules were fixed, so the mounted
        # release kept refusing its own approval surface (2026-08-18: every compose
        # and every mail-triage-watch tick died on GATE-REFUSED).
        ("triage_approval", "mail", "interop"),
        ("triage_binding", "mail", "interop"),
        ("calendar_binding", "calendar", "interop"),
        ("coordination_binding", "coordination", "interop"),
        ("wiki_binding", "wiki", "interop"),
        ("calendar_approval", "calendar", "interop"),
        ("wiki_approval", "wiki", "interop"),
        ("scripts.patent_export_binding", "patent-prep", "interop"),
    ),
)
def test_repo_root_skips_candidates_without_automation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    module_name: str,
    skill_name: str,
    package_name: str,
) -> None:
    """Regression: the mounted release path makes ``parents[3]`` a wrong guess.

    A mounted skill runs from ``/srv/autophagy-skills/releases/<skill>/<hash>/scripts``,
    so ``Path(__file__).parents[3]`` resolves to ``.../releases`` — a directory with no
    ``automation`` package. The loader then failed closed and the post-mount smoke test
    refused every send (``GATE-REFUSED ... AUTOPHAGY_REPO_ROOT=/srv/autophagy-skills/releases``),
    which blocked the mail and calendar deploys. ``repo_root`` must skip a candidate that
    carries no ``automation`` package instead of trusting the depth guess.
    """
    # Given: a mounted-release layout whose parents[3] has no automation package.
    release = tmp_path / "releases" / skill_name / "deadbeef" / "scripts"
    release.mkdir(parents=True)
    real_repo = tmp_path
    (real_repo / "automation" / "entity_preflight").mkdir(parents=True)
    (real_repo / "automation" / "interop").mkdir(parents=True)
    (real_repo / "automation" / "runtime_root.py").write_bytes(
        (_REPO_ROOT / "automation" / "runtime_root.py").read_bytes()
    )

    automation_modules = tuple(
        name for name in sys.modules if name == "automation" or name.startswith("automation.")
    )
    package_modules = tuple(
        name for name in sys.modules if name == "scripts" or name.startswith("scripts.")
    )
    with monkeypatch.context() as isolated:
        for name in (*automation_modules, *package_modules, module_name):
            isolated.delitem(sys.modules, name, raising=False)
        isolated.delenv("AUTOPHAGY_REPO_ROOT", raising=False)
        isolated.setenv("AUTOPHAGY_RUNTIME_ROOT", str(real_repo))
        isolated.setattr(sys, "path", [e for e in sys.path if not _is_repo_root_path(e)])
        import_root = _SKILLS_ROOT / skill_name
        if not module_name.startswith("scripts."):
            import_root /= "scripts"
        isolated.syspath_prepend(str(import_root))
        importlib.invalidate_caches()
        preflight = importlib.import_module(module_name)

        # When: the module resolves its repo root from a mounted-release location.
        # repo_root reads the module-global __file__, so rebind it in the module dict.
        isolated.setitem(
            preflight.__dict__, "__file__", str(release / f"{module_name.rsplit('.', 1)[-1]}.py")
        )
        resolved = preflight.repo_root()

    # Then: it never returns the meaningless depth guess (.../releases).
    assert resolved != release.parents[2], (
        f"repo_root() returned the mounted-release guess {resolved}"
    )
    assert resolved == real_repo, f"repo_root() ignored the fabricated runtime checkout: {resolved}"
    assert (resolved / "automation" / package_name).is_dir(), (
        f"repo_root() returned {resolved}, which cannot load automation.{package_name}"
    )
