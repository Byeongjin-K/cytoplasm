"""The turn's wait for automatic recall is one config value the release writes (owner Q173: 15 s).

Split from test_memory_provider_config.py so its existing cases stay as they were.
"""
from __future__ import annotations

from pathlib import Path
from types import ModuleType

import pytest

from automation.memory_provider import config

from test_memory_provider import host_plugin_fixture as host_plugin_fixture


def test_the_release_writes_fifteen_seconds_next_to_the_caps_and_provider() -> None:
    written = config.apply_profile("model:\n  default: x\n")

    assert "  recall_wait_seconds: 15\n" in written
    assert config.read_profile(written).recall_wait == 15
    assert config.read_profile(written).identity() == config.wanted_identity()
    assert config.wanted_identity().endswith(",provider=autophagy_recall,recall_wait=15")


def test_a_config_without_the_wait_is_drift_until_the_release_writes_it() -> None:
    old = "memory:\n  memory_char_limit: 3300\n  user_char_limit: 3000\n  provider: autophagy_recall\n"

    assert config.read_profile(old).identity() != config.wanted_identity()
    assert config.read_profile(config.apply_profile(old)).identity() == config.wanted_identity()


@pytest.mark.parametrize("raw", ["0", "61", "abc", "1.5"])
def test_a_wait_outside_one_to_sixty_seconds_is_refused(raw: str) -> None:
    with pytest.raises(config.limits.ConfigShapeError):
        config.read_profile(f"memory:\n  recall_wait_seconds: {raw}\n")


def test_the_provider_reads_its_wait_from_the_account_config(plugin: ModuleType, tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text("memory:\n  provider: autophagy_recall\n  recall_wait_seconds: 9\n")
    provider = plugin.AutophagyRecallProvider(lambda _query: "")

    provider.initialize("s1", hermes_home=str(tmp_path), platform="discord")

    assert provider._wait_seconds == 9.0


@pytest.mark.parametrize("text", [None, "memory:\n  recall_wait_seconds: nope\n"])
def test_a_missing_or_broken_config_falls_back_to_the_declared_fifteen_seconds(
    plugin: ModuleType, tmp_path: Path, text: str | None,
) -> None:
    if text is not None:
        (tmp_path / "config.yaml").write_text(text)
    provider = plugin.AutophagyRecallProvider(lambda _query: "")

    provider.initialize("s1", hermes_home=str(tmp_path), platform="discord")

    assert provider._wait_seconds == float(config.RECALL_WAIT_SECONDS) == 15.0
