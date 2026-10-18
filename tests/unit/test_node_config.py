from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from automation import node_config
from automation import node_config_state

from automation.node_config import NodeConfigError, default_node_config, load_node_config


ROOT = Path(__file__).resolve().parents[2]


def test_an_explicitly_named_missing_file_fails_closed(tmp_path: Path) -> None:
    """"Read THIS file" and "read whatever, fall back to the seed" are different contracts.

    Silently answering the first with the second is how the reconciler's two verification
    paths came to enforce different policies on 2026-08-21: the root helper named
    `~ops/.hermes/node.toml`, that file did not exist, and the seed answered in its place.
    A caller that names a path has already decided which file is authoritative, so a
    missing one is an error — not an invitation to guess.
    """
    with pytest.raises(NodeConfigError):
        _ = load_node_config(tmp_path / "missing.toml")


def test_unnamed_lookup_still_falls_back_to_the_public_safe_seed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The no-argument lookup keeps its search: system path, then home, then the seed."""
    monkeypatch.setattr(node_config, "SYSTEM_NODE_CONFIG_PATH", tmp_path / "absent.toml")
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))

    config = load_node_config()

    assert config == default_node_config()
    assert config.primary_node_name == "example-primary-node"
    assert config.rag_node_name == "example-rag-node"
    assert config.operator_account == "operator"
    assert config.deploy_checkout == Path("/srv/autophagy-agents")
    assert config.require_signed_updates is True


def test_runtime_file_without_update_policy_requires_signatures_by_default(tmp_path: Path) -> None:
    # Given: a third-party runtime file predating the optional update policy field.
    path = tmp_path / "node.toml"
    _ = path.write_text('primary_node_name = "third-party"\n', encoding="utf-8")

    # When: the partial runtime override is loaded.
    config = load_node_config(path)

    # Then: omission preserves the seed's secure signed-update policy.
    assert config.require_signed_updates is True


def test_runtime_file_replaces_every_default(tmp_path: Path) -> None:
    seed = default_node_config()
    values = {
        field: f"custom-{field}"
        for field in seed.__dataclass_fields__
        if field != "require_signed_updates" and field not in {
            "service_root", "deploy_checkout", "release_current", "release_store",
            "private_root", "skill_store", "repair_work", "repair_report_queue",
            "repair_report_ack", "repair_capability", "libexec_dir", "agent_home",
            "peer_home", "ops_home",
        }
    }
    for field in seed.__dataclass_fields__:
        if field != "require_signed_updates" and field not in values:
            values[field] = f"/custom/{field}"
    values["origin_url"] = "ssh://git.example.invalid/team/project.git"
    values["peer_attest_mode"] = "signed"
    values["agent_gateway_unit"] = "custom-agent.service"
    values["peer_gateway_unit"] = "custom-peer.service"
    path = tmp_path / "node.toml"
    text = "\n".join(f'{key} = "{value}"' for key, value in values.items())
    _ = path.write_text(f"{text}\nrequire_signed_updates = true\n", encoding="utf-8")

    config = load_node_config(path)

    assert config.primary_node_name == "custom-primary_node_name"
    assert config.agent_account == "custom-agent_account"
    assert config.release_current == Path("/custom/release_current")
    assert config.require_signed_updates is True
    assert config.peer_attest_mode == "signed"


@pytest.mark.parametrize(
    "text",
    [
        "not = [valid",
        'unknown = "field"',
        'origin_url = ""',
        'deploy_checkout = "relative/path"',
        'agent_account = "bad account"',
        'agent_gateway_unit = "not-a-service"',
        'require_signed_updates = "false"',
        "require_signed_updates = 1",
        'peer_attest_mode = "automatic"',
    ],
)
def test_malformed_or_invalid_runtime_file_fails_closed(tmp_path: Path, text: str) -> None:
    path = tmp_path / "node.toml"
    _ = path.write_text(text, encoding="utf-8")

    with pytest.raises(NodeConfigError):
        _ = load_node_config(path)


def test_shell_bridge_prints_sourceable_resolved_values(tmp_path: Path) -> None:
    home = tmp_path / "home"
    config_dir = home / ".hermes"
    config_dir.mkdir(parents=True)
    _ = (config_dir / "node.toml").write_text(
        'deploy_ssh_host = "third party host"\nagent_account = "runner"\n',
        encoding="utf-8",
    )

    result = subprocess.run(
        (sys.executable, "-m", "automation.node_config_sh", "--print-env"),
        cwd=ROOT,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin", "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    shell = subprocess.run(
        ("bash", "-c", 'eval "$1"; printf "%s|%s" "$NODE_DEPLOY_SSH_HOST" "$NODE_AGENT_ACCOUNT"', "bash", result.stdout),
        capture_output=True,
        text=True,
        check=False,
    )
    assert shell.stdout == "third party host|runner"


def test_node_config_when_the_system_path_exists_then_it_wins_over_the_home_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """설정은 HOME 이 살아 있는 환경에서만 읽혀서는 안 된다.

    2026-08-16 하루에 세 번 같은 얼굴로 나타났다 — `deploy-reconcile` 은 `ProtectHome=tmpfs`
    라 `/home/ops` 가 빈 tmpfs 로 보였고, 승인 재개는 `env -i HOME=/root` 로 돌아
    `/root/.hermes` 를 봤으며, 둘 다 시드 플레이스홀더(`example-primary-node`)로 떨어져
    조용히 실패했다. HOME 마다 사본을 두는 것은 두더지잡기라, 어느 환경에서도 같은
    한 곳을 먼저 본다.
    """
    # Given: both a system config and a home config exist, disagreeing
    system = tmp_path / "etc" / "node.toml"
    system.parent.mkdir(parents=True)
    _ = system.write_text('primary_node_name = "from-system"\n', encoding="utf-8")
    home = tmp_path / "home"
    (home / ".hermes").mkdir(parents=True)
    _ = (home / ".hermes" / "node.toml").write_text(
        'primary_node_name = "from-home"\n', encoding="utf-8"
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(node_config, "SYSTEM_NODE_CONFIG_PATH", system)

    # When
    config = node_config.load_node_config()

    # Then
    assert config.primary_node_name == "from-system"


def test_node_config_when_the_system_path_is_absent_then_the_home_path_still_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: no system config, only the historical per-account location
    home = tmp_path / "home"
    (home / ".hermes").mkdir(parents=True)
    _ = (home / ".hermes" / "node.toml").write_text(
        'primary_node_name = "from-home"\n', encoding="utf-8"
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(node_config, "SYSTEM_NODE_CONFIG_PATH", tmp_path / "absent.toml")

    # When / Then
    assert node_config.load_node_config().primary_node_name == "from-home"


def test_node_config_when_it_is_still_the_seed_then_it_reports_itself_unconfigured() -> None:
    """틀린 값보다 조용한 것이 더 오래 숨는다.

    2026-08-16: 시드 폴백이 `example-primary-node` 로 ssh 를 시도해 승인된 배포가
    exit 4 로 죽었고, 리컨실러는 같은 이유로 매 틱 rc 0 으로 나가 실패로 세지도 않았다.
    값을 고치는 것과 별개로, 설정되지 않은 상태가 스스로 드러나야 한다.
    """
    assert node_config_state.unconfigured_reason(node_config.default_node_config()) is not None


def test_node_config_when_the_operator_supplied_real_names_then_it_is_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    system = tmp_path / "node.toml"
    _ = system.write_text(
        'primary_node_name = "real-node"\n'
        'rag_node_name = "real-rag"\n'
        'deploy_ssh_host = "real-node"\n'
        'origin_url = "https://github.com/example-org/example-repo.git"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(node_config, "SYSTEM_NODE_CONFIG_PATH", system)
    monkeypatch.setattr(node_config_state, "SYSTEM_NODE_CONFIG_PATH", system)

    # When / Then
    assert node_config_state.unconfigured_reason(node_config.load_node_config()) is None


def test_node_config_state_cli_rejects_the_seed_before_a_deploy_can_reach_dns(
    tmp_path: Path,
) -> None:
    # Given: an operator HOME with no node override, so only the shipped seed exists.
    home = tmp_path / "home"
    home.mkdir()

    # When: a deploy entry point runs the configuration preflight.
    result = subprocess.run(
        (sys.executable, "-m", "automation.node_config_state"),
        cwd=ROOT,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin", "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
        check=False,
    )

    # Then: it fails with the actual omission and a concrete configuration remedy.
    assert result.returncode != 0
    assert "node is not configured" in result.stderr
    assert "node.toml" in result.stderr
