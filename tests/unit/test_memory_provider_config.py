"""Release profile convergence preserves stage 1 caps and stage 2 SOUL selection."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

from automation import deploy_all_config, deploy_all_observe, hermes_memory_config, hermes_soul_recall
from automation.deploy_declarations import parse_declaration_file
from automation.memory_provider import config

_REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("text", [
    "model:\n  provider: synthetic\n",
    "memory:\n  memory_char_limit: 2200\n  user_char_limit: 1375\n  nudge_interval: 10\n",
    "memory:\n  provider: builtin\n  metadata:\n    label: synthetic\n",
])
def test_profile_sets_caps_and_provider_in_one_mapping(text: str) -> None:
    # Given
    before = yaml.safe_load(text)
    # When
    result = config.apply_profile(text)
    # Then: use the YAML parser Hermes uses, not only the writer's own reader.
    parsed = yaml.safe_load(result)
    assert parsed["memory"] == {**before.get("memory", {}), **hermes_memory_config.LIMITS,
                                "provider": config.PROVIDER}
    assert {k: v for k, v in parsed.items() if k != "memory"} == {
        k: v for k, v in before.items() if k != "memory"}
    assert config.apply_profile(result) == result


def test_apply_backs_up_config_without_touching_native_memory(tmp_path: Path) -> None:
    # Given
    root = tmp_path / ".hermes"
    (root / "memories").mkdir(parents=True)
    source = "memory:\n  user_profile_enabled: true\nother:\n  key: unchanged\n"
    (root / "config.yaml").write_text(source)
    (root / "memories/USER.md").write_text("합성 사용자 기록")
    (root / "memories/MEMORY.md").write_text("합성 메모리 기록")
    # When
    rc = config.main(["--apply"], home=tmp_path)
    # Then
    assert rc == 0
    assert yaml.safe_load((root / "config.yaml").read_text())["memory"]["provider"] == config.PROVIDER
    assert [p.read_text() for p in root.glob("config.yaml.bak-memory-limits-*")] == [source]
    assert (root / "memories/USER.md").read_text() == "합성 사용자 기록"
    assert (root / "memories/MEMORY.md").read_text() == "합성 메모리 기록"


def test_selected_external_provider_is_refused_without_a_write(tmp_path: Path) -> None:
    # Given
    root = tmp_path / ".hermes"
    root.mkdir()
    text = "memory:\n  provider: synthetic_backend\n"
    path = root / "config.yaml"
    path.write_text(text)
    # When
    rc = config.main(["--apply"], home=tmp_path)
    # Then
    assert rc == 7
    assert path.read_text() == text
    assert not list(root.glob("*.bak-*"))


@pytest.mark.parametrize("text", [
    "memory: {provider: builtin}\n",
    "memory:\n  provider: builtin\n  provider: other\n",
    "memory:\n  provider: [other]\n",
    "memory:\n  user_char_limit: bad\n",
    "---\nmemory:\n  provider: builtin\n---\nother: {}\n",
])
def test_malformed_profile_is_refused(text: str) -> None:
    # Given / When / Then
    with pytest.raises(hermes_memory_config.ConfigShapeError):
        config.apply_profile(text)


@pytest.mark.parametrize("enabled", [False, True])
def test_real_config_observer_detects_provider_drift(tmp_path: Path, enabled: bool) -> None:
    # Given: the actual closed remote script, but only a local synthetic account.
    root = tmp_path / ".hermes"
    root.mkdir()
    text = hermes_memory_config.apply_limits("")
    (root / "config.yaml").write_text(config.apply_profile(text) if enabled else text)
    row = (
        "agent|automation/memory_provider/config.py|.hermes/config.yaml|"
        f"v2:config;algorithm={config.ALGORITHM};activation=gateway"
    )
    (decl,) = parse_declaration_file("automation/memory_curator/deploy-manifest.txt", row)

    def runner(account: str, args: tuple[str, ...]) -> tuple[int, str]:
        result = subprocess.run(
            ["bash", "-c", deploy_all_observe.REMOTE_SCRIPT, "_", *args],
            env={**os.environ, "HOME": str(tmp_path)},
            capture_output=True, text=True, check=False, timeout=10,
        )
        return result.returncode, result.stdout

    # When
    facts = deploy_all_config.observe_config(decl, _REPO, runner)
    # Then
    assert facts[2] == ("ok" if enabled else "stale")
    assert facts[3] == (() if enabled else (("different-provider", "-"),))
    assert hermes_soul_recall.ALGORITHM in deploy_all_observe._CONFIG_MODULES
    assert hermes_memory_config.ALGORITHM in deploy_all_observe._CONFIG_MODULES


def test_lowering_guard_survives_the_composed_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    root = tmp_path / ".hermes"
    (root / "memories").mkdir(parents=True)
    text = hermes_memory_config.apply_limits("")
    (root / "config.yaml").write_text(text)
    (root / "memories/USER.md").write_text("x" * 2500)
    monkeypatch.setattr(hermes_memory_config, "LIMITS", {"memory_char_limit": 3300, "user_char_limit": 2000})
    # When
    rc = config.main(["--apply"], home=tmp_path)
    # Then
    assert rc == 7
    assert (root / "config.yaml").read_text() == text


def test_shared_converge_helper_runs_apply_and_check(tmp_path: Path) -> None:
    # Given: replace only the SSH boundary with a local executor in an isolated HOME.
    root = tmp_path / ".hermes"
    root.mkdir()
    (root / "config.yaml").write_text("memory:\n  nudge_interval: 10\n")
    script = (
        'source "$1"; run_account() { bash -c "$2"; }; '
        f'converge_config agent .hermes/config.yaml {config.ALGORITHM}'
    )
    # When
    result = subprocess.run(
        ["bash", "-c", script, "_", str(_REPO / "automation/deploy_config.sh")],
        env={**os.environ, "HOME": str(tmp_path), "NODE_RELEASE_CURRENT": str(_REPO)},
        capture_output=True, text=True, check=False, timeout=10,
    )
    # Then
    assert result.returncode == 0, result.stdout + result.stderr
    assert yaml.safe_load((root / "config.yaml").read_text())["memory"] == {
        **hermes_memory_config.LIMITS, "provider": config.PROVIDER, "nudge_interval": 10}


def test_nested_provider_is_refused_before_writing(tmp_path: Path) -> None:
    # Given
    root = tmp_path / ".hermes"
    root.mkdir()
    text = "memory:\n  provider:\n    name: synthetic_backend\n"
    path = root / "config.yaml"
    path.write_text(text)
    # When
    rc = config.main(["--apply"], home=tmp_path)
    # Then
    assert rc == 3
    assert path.read_text() == text
    assert not list(root.glob("*.bak-*"))
