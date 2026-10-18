"""Relay coordinates stay out of node_config so the node's root helper copy never changes for them."""
from __future__ import annotations

from pathlib import Path

import pytest

from automation import node_config
from automation.omo_relay import config


GOOD = 'omo_relay_target = "worker@relay.invalid"\nomo_relay_key = "/home/agent/.ssh/omo_relay"\n'


def test_release_host_reads_its_own_relay_file(tmp_path: Path) -> None:
    # Given: the release host keeps the coordinates in its relay file.
    path = tmp_path / ".hermes/omo-relay.toml"
    path.parent.mkdir(parents=True)
    path.write_text(GOOD)
    # When/Then: they load without touching the node config.
    loaded = config.release_host(tmp_path)
    assert loaded == config.RelayConfig("worker@relay.invalid", Path("/home/agent/.ssh/omo_relay"))


@pytest.mark.parametrize("text", [
    'omo_relay_target = "worker@relay.invalid"\n',
    GOOD + 'extra = "x"\n',
    GOOD.replace("worker@relay.invalid", "-oProxyCommand=x"),
    GOOD.replace("/home/agent/.ssh/omo_relay", "relative/key"),
])
def test_malformed_relay_config_is_refused(text: str) -> None:
    with pytest.raises(config.RelayConfigError):
        config.parse(text)


def test_node_config_has_no_relay_fields() -> None:
    # The root convergence helper keeps a static copy of node_config.py; relay fields there
    # would force an owner root step before every release that touches the relay.
    assert not {name for name in node_config._FIELD_NAMES if name.startswith("omo_relay")}
    assert not hasattr(node_config.default_node_config(), "omo_relay_target")
