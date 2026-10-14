"""Hermes 메모리 상한의 단일 출처와 계정 config.yaml 수렴(`automation.hermes_memory_config`)."""
from __future__ import annotations

from pathlib import Path

import pytest

from automation import hermes_memory_config as hmc
from automation.memory_curator.model import CAPS

AGENT_CONFIG = """model:
  provider: openai-codex
memory:
  memory_enabled: true
  user_profile_enabled: true
  memory_char_limit: 2200
  user_char_limit: 1375  # Hermes default
  nudge_interval: 10
  flush_min_turns: 6
delegation:
  max_depth: 1
"""

PEER_CONFIG = """model:
  provider: openai-codex
timezone: Asia/Seoul
plugins:
  enabled:
  - interop
"""


def _home(tmp_path: Path, config: str, user_md: str = "") -> Path:
    hermes = tmp_path / ".hermes"
    (hermes / "memories").mkdir(parents=True)
    _ = (hermes / "config.yaml").write_text(config, encoding="utf-8")
    if user_md:
        _ = (hermes / "memories" / "USER.md").write_text(user_md, encoding="utf-8")
    return tmp_path


def test_curator_caps_are_the_released_limits() -> None:
    assert CAPS == {"memory": hmc.LIMITS["memory_char_limit"], "user": hmc.LIMITS["user_char_limit"]}
    assert hmc.LIMITS == {"memory_char_limit": 3300, "user_char_limit": 3000}


def test_apply_rewrites_only_the_two_limit_lines() -> None:
    after = hmc.apply_limits(AGENT_CONFIG)
    assert hmc.read_limits(after) == hmc.LIMITS
    changed = [(a, b) for a, b in zip(AGENT_CONFIG.splitlines(), after.splitlines(), strict=True) if a != b]
    assert changed == [
        ("  memory_char_limit: 2200", "  memory_char_limit: 3300"),
        ("  user_char_limit: 1375  # Hermes default", "  user_char_limit: 3000"),
    ]
    assert hmc.apply_limits(after) == after


def test_account_without_a_memory_block_gets_one_appended() -> None:
    assert hmc.read_limits(PEER_CONFIG) == {"memory_char_limit": None, "user_char_limit": None}
    after = hmc.apply_limits(PEER_CONFIG)
    assert after.startswith(PEER_CONFIG)
    assert after.endswith("memory:\n  memory_char_limit: 3300\n  user_char_limit: 3000\n")
    assert hmc.read_limits(after) == hmc.LIMITS


def test_missing_key_is_inserted_inside_the_block_not_after_the_next_section() -> None:
    config = "memory:\n    memory_enabled: true\n    memory_char_limit: 2200\nother:\n  x: 1\n"
    after = hmc.apply_limits(config)
    assert after == "memory:\n    memory_enabled: true\n    memory_char_limit: 3300\n    user_char_limit: 3000\nother:\n  x: 1\n"


@pytest.mark.parametrize("config", [
    "memory: {memory_char_limit: 2200}\n",
    "memory:\n  user_char_limit: 1375\n  user_char_limit: 2000\n",
    "memory:\n  user_char_limit: lots\n",
    "memory:\n  a: 1\nmemory:\n  b: 2\n",
])
def test_unreadable_shapes_are_refused_not_guessed(config: str) -> None:
    with pytest.raises(hmc.ConfigShapeError):
        _ = hmc.apply_limits(config)


def test_cli_apply_backs_up_writes_and_checks(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = _home(tmp_path, AGENT_CONFIG)
    assert hmc.main(["--check"], home=home) == 1
    assert hmc.main(["--apply"], home=home) == 0
    assert "HERMES-MEMORY-LIMITS applied have=memory_char_limit=3300,user_char_limit=3000" in capsys.readouterr().out
    config = home / ".hermes" / "config.yaml"
    backups = list(config.parent.glob("config.yaml.bak-memory-limits-*"))
    assert [b.read_text(encoding="utf-8") for b in backups] == [AGENT_CONFIG]
    assert oct(config.stat().st_mode & 0o777) == "0o600"
    assert hmc.main(["--check"], home=home) == 0
    assert hmc.main(["--apply"], home=home) == 0
    assert "unchanged" in capsys.readouterr().out
    assert len(list(config.parent.glob("config.yaml.bak-memory-limits-*"))) == 1


def test_lowering_below_the_current_file_size_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    home = _home(tmp_path, hmc.apply_limits(AGENT_CONFIG), user_md="x" * 2500)
    monkeypatch.setattr(hmc, "LIMITS", {"memory_char_limit": 3300, "user_char_limit": 2000})
    before = (home / ".hermes" / "config.yaml").read_text(encoding="utf-8")
    assert hmc.main(["--apply"], home=home) == 7
    assert "USER.md 2500>2000" in capsys.readouterr().err
    assert (home / ".hermes" / "config.yaml").read_text(encoding="utf-8") == before


def test_unreadable_config_is_an_error_not_a_default(tmp_path: Path) -> None:
    assert hmc.main(["--show"], home=tmp_path) == 3
