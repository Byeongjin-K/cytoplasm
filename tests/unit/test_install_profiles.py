"""The install profile is the installer's answer to "which HEALTHCHECK_SERVICES do I paste".

Before this, the declaration existed only as an environment variable the operator had to
derive from the docs or ask the node for (`healthcheck.sh --suggest`) after the install was
already done. A fresh install should never need either: the profile the operator picked at
install time already says which service groups this node runs, so the installer writes that
answer to a root-owned file and the registry reads it when nothing in the environment
overrides it.
"""
from __future__ import annotations

import base64
import os
import subprocess
from pathlib import Path
from typing import Final

import pytest

from automation.install.assets import build_inputs, render_plan
from automation.install.installer import main as installer_main
from automation.install.plan import SystemState, build_plan
from automation.node_config import default_node_config

_REPO: Final = Path(__file__).resolve().parents[2]
_REGISTRY: Final = _REPO / "automation" / "healthcheck_registry.sh"
_ENV_PATH: Final = Path("/etc/autophagy/healthcheck.env")


def _public_key() -> str:
    algorithm = b"ssh-ed25519"
    material = len(algorithm).to_bytes(4, "big") + algorithm
    material += (32).to_bytes(4, "big") + bytes(range(32))
    return f"ssh-ed25519 {base64.b64encode(material).decode()} profile-test"


def _live_checks(env_file: Path | None, services: str | None) -> subprocess.CompletedProcess[str]:
    environment = {k: v for k, v in os.environ.items() if k != "HEALTHCHECK_SERVICES"}
    if services is not None:
        environment["HEALTHCHECK_SERVICES"] = services
    environment["HEALTHCHECK_SERVICES_FILE"] = (
        str(env_file) if env_file else "/nonexistent/healthcheck.env"
    )
    return subprocess.run(
        ("bash", "-c",
         'set -euo pipefail\n'
         'eval "$(python3 automation/node_config_sh.py --print-env)"\n'
         'PRIMARY_NODE="$NODE_PRIMARY_NODE_NAME"; RAG_NODE="$NODE_RAG_NODE_NAME"\n'
         'PEER_GATEWAY_CONFIG=/pinned/p.json\n'
         f'source "{_REGISTRY}"\n'
         'printf "%s\\n" "${LIVE_CHECKS[@]}"\n'),
        cwd=_REPO, env=environment, capture_output=True, text=True, check=False,
    )


# --- profile -> declaration ----------------------------------------------------------


def test_a_profile_names_the_groups_the_registry_knows() -> None:
    from automation.install.profiles import resolve_profile

    assert resolve_profile("core").healthcheck_services == ("core",)
    assert resolve_profile("rag").healthcheck_services == ("core", "rag")
    assert resolve_profile("report-hub").healthcheck_services == ("core", "report-hub")
    assert resolve_profile("full").healthcheck_services == ("core", "report-hub", "rag")


def test_an_unknown_profile_is_refused_by_name_listing_the_known_ones() -> None:
    from automation.install.profiles import UnknownProfileError, resolve_profile

    with pytest.raises(UnknownProfileError) as error:
        _ = resolve_profile("rga")
    assert "rga" in str(error.value)
    for known in ("core", "rag", "report-hub", "full"):
        assert known in str(error.value)


def test_a_profile_plans_the_root_owned_declaration_file() -> None:
    inputs = build_inputs(_REPO, default_node_config(), _public_key(), profile="rag")
    files = {spec.path: spec for spec in inputs.files}

    spec = files[_ENV_PATH]
    assert spec.content == 'HEALTHCHECK_SERVICES="core rag"\n'
    assert (spec.owner, spec.group, spec.mode) == ("root", "root", 0o644)


def test_no_profile_keeps_the_plan_exactly_as_before() -> None:
    # An install that names no profile must reproduce yesterday's plan byte for byte —
    # narrowing is opted into, never implied.
    inputs = build_inputs(_REPO, default_node_config(), _public_key())

    assert _ENV_PATH not in {spec.path for spec in inputs.files}
    rendered = render_plan(build_plan(inputs, SystemState.empty()))
    assert "healthcheck.env" not in rendered


def test_the_cli_flag_plans_the_file_in_dry_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key = tmp_path / "trust.pub"
    _ = key.write_text(f"{_public_key()}\n", encoding="utf-8")

    code = installer_main(("--update-trust-key", str(key), "--dry-run", "--profile", "rag"))
    out = capsys.readouterr().out

    assert code == 0
    assert f"file {_ENV_PATH}" in out


def test_an_unknown_profile_stops_the_cli(tmp_path: Path) -> None:
    key = tmp_path / "trust.pub"
    _ = key.write_text(f"{_public_key()}\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exit_info:
        _ = installer_main(("--update-trust-key", str(key), "--dry-run", "--profile", "rga"))
    assert exit_info.value.code != 0


# --- the registry reads the file the installer wrote ---------------------------------


def test_the_registry_reads_the_declaration_file_when_the_environment_is_silent(
    tmp_path: Path,
) -> None:
    from automation.install.profiles import healthcheck_env, resolve_profile

    env_file = tmp_path / "healthcheck.env"
    _ = env_file.write_text(healthcheck_env(resolve_profile("rag")), encoding="utf-8")

    result = _live_checks(env_file, services=None)

    assert result.returncode == 0, result.stderr
    rows = [line for line in result.stdout.splitlines() if line]
    assert rows and not [row for row in rows if "report-hub" in row]
    assert [row for row in rows if "qdrant" in row.lower() or "embedding" in row.lower()]


def test_an_explicit_environment_value_wins_over_the_file(tmp_path: Path) -> None:
    env_file = tmp_path / "healthcheck.env"
    _ = env_file.write_text('HEALTHCHECK_SERVICES="core"\n', encoding="utf-8")

    result = _live_checks(env_file, services="core report-hub rag")

    assert result.returncode == 0, result.stderr
    rows = [line for line in result.stdout.splitlines() if line]
    assert not [row for row in rows if "report-hub" in row]
    assert [row for row in rows if "qdrant" in row.lower() or "embedding" in row.lower()]


def test_a_declared_dashboard_url_does_not_restore_retired_report_probes(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / "healthcheck.env"
    _ = env_file.write_text(
        'HEALTHCHECK_SERVICES="core report-hub"\nHEALTHCHECK_REPORT_HUB_DASHBOARD_URL="http://192.0.2.7:8800/"\n',
        encoding="utf-8",
    )

    result = _live_checks(env_file, services=None)

    assert result.returncode == 0, result.stderr
    auth = [line for line in result.stdout.splitlines() if "dashboard auth" in line]
    assert auth == []
    assert "report-hub" not in result.stdout
    assert "http://192.0.2.7:8800/" not in result.stdout


def test_a_missing_file_keeps_all_remaining_service_probes() -> None:
    result = _live_checks(None, services=None)

    assert result.returncode == 0, result.stderr
    rows = [line for line in result.stdout.splitlines() if line]
    assert not [row for row in rows if "report-hub" in row]
    assert [row for row in rows if "qdrant" in row.lower() or "embedding" in row.lower()]
