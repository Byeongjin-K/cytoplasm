"""The gateway generation record covers the lazily loaded memory provider plugin.

Live 2026-10-15 (v1.36.0): Hermes loads ``memory.provider`` only when the first
agent is built, so the recall plugin never wrote the record at gateway start and
the release re-judgment failed with ``plugin:autophagy_recall`` after both
gateways had restarted. Kept apart from test_gateway_generation.py so its cases
stay as they were.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from automation import gateway_generation
from automation.deploy_declarations import parse_declaration_file
from automation.memory_provider import config

_REPO = Path(__file__).resolve().parents[2]


def _home(tmp_path: Path, *, provider: str | None) -> Path:
    home = tmp_path / "home"
    hermes = home / ".hermes"
    for name in ("interop-protocol", config.PROVIDER):
        (hermes / "plugins" / name).mkdir(parents=True)
        (hermes / "plugins" / name / "__init__.py").write_text(f"# {name}\n", encoding="utf-8")
    memory = f"memory:\n  provider: {provider}\n" if provider else "memory:\n  memory_char_limit: 3300\n"
    (hermes / "config.yaml").write_text(memory, encoding="utf-8")
    (hermes / "gateway.pid").write_text(f'{{"pid": {os.getpid()}}}', encoding="utf-8")
    return home


def _start_gateway(home: Path) -> None:
    gateway_generation._record(
        "interop-protocol", str(home / ".hermes/plugins/interop-protocol/__init__.py"),
        home=home, argv=["hermes", "gateway", "run"], env={"SYSTEMD_EXEC_PID": str(os.getpid())}, pid=os.getpid(),
    )


def test_gateway_start_records_the_selected_memory_provider_file(tmp_path: Path) -> None:
    # Given: the recall provider is selected and only the interop plugin registers at start.
    home = _home(tmp_path, provider=config.PROVIDER)
    # When
    _start_gateway(home)
    # Then: the release check that requires both plugins passes.
    status, *_ = gateway_generation.check(home, _REPO, ["interop-protocol", config.PROVIDER])
    assert status == "ok"


def test_a_provider_file_replaced_after_start_is_stale(tmp_path: Path) -> None:
    home = _home(tmp_path, provider=config.PROVIDER)
    _start_gateway(home)
    (home / ".hermes/plugins" / config.PROVIDER / "__init__.py").write_text("# newer\n", encoding="utf-8")

    assert gateway_generation.check(home, _REPO, [config.PROVIDER])[3] == f"plugin:{config.PROVIDER}"


@pytest.mark.parametrize("provider", [None, "builtin"])
def test_an_unselected_provider_is_not_claimed_as_loaded(tmp_path: Path, provider: str | None) -> None:
    home = _home(tmp_path, provider=provider)
    _start_gateway(home)

    assert gateway_generation.check(home, _REPO, [config.PROVIDER])[0] == "stale"


def test_every_gateway_plugin_row_is_recorded_at_start_or_is_the_selected_provider() -> None:
    # A gateway plugin row is required by the release re-judgment; it must have a way to be recorded.
    for manifest in sorted(_REPO.glob("**/deploy-manifest.txt")):
        if ".venv" in manifest.parts:
            continue
        relative = manifest.relative_to(_REPO).as_posix()
        for decl in parse_declaration_file(relative, manifest.read_text(encoding="utf-8")):
            parts = decl.destination.split("/")
            if decl.attr("activation") != "gateway" or parts[:2] != [".hermes", "plugins"] or parts[-1] != "__init__.py":
                continue
            source = (_REPO / decl.source).read_text(encoding="utf-8")
            assert "gateway_generation" in source or parts[2] == config.PROVIDER, decl.destination
