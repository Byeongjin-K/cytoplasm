"""Release-selected channel, node-local convergence and deletion tolerance."""
from __future__ import annotations

import importlib
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from automation import hermes_agent_chat_ignore as ignore
from automation.interop import free_response, origin_notice
from automation.memory_provider import agent_config
from tests.unit.test_healthcheck_wrapper_release import _spec

OLD = "123456789012345678"
NEW = "987654321098765432"


def test_release_channel_switch_is_strict_and_absence_is_off(tmp_path: Path) -> None:
    # Given
    module = importlib.import_module("automation.yoodori_channel")
    flag = tmp_path / ".hermes/release-flags/yoodori-channel"
    flag.parent.mkdir(parents=True)
    # When / Then
    assert module.release_switch(tmp_path) is None
    flag.write_text(NEW)
    assert module.release_switch(tmp_path) == NEW
    flag.write_text(NEW + "\n")
    with pytest.raises(ValueError):
        module.release_switch(tmp_path)


def test_channel_convergence_preserves_other_bytes_and_restores(tmp_path: Path) -> None:
    # Given
    module = importlib.import_module("automation.yoodori_channel")
    path = tmp_path / ".hermes/interop/config.json"
    path.parent.mkdir(parents=True)
    before = '{\r\n "agent_chat_channel_id" : ' + OLD + ', "other": { "x": [1,2] }\r\n}\r\n'
    path.write_bytes(before.encode())
    # When
    assert module.main(["--apply", "--yoodori-channel", NEW], home=tmp_path) == 0
    first = path.read_bytes()
    # Then
    assert b'"other": { "x": [1,2] }\r\n' in first
    assert json.loads(first)[module.CHAT + module.BEFORE] == int(OLD)
    assert json.loads(first)[module.NOTICE] == NEW
    assert module.main(["--apply", "--yoodori-channel", NEW], home=tmp_path) == 0
    assert path.read_bytes() == first
    assert len(list(path.parent.glob("*.bak-*"))) == 1
    assert module.main(["--apply", "--yoodori-channel", "off"], home=tmp_path) == 0
    assert path.read_bytes() == before.encode()


def test_no_channel_switch_does_not_require_or_write_config(tmp_path: Path) -> None:
    # Given
    module = importlib.import_module("automation.yoodori_channel")
    # When / Then
    assert module.main(["--apply", "--yoodori-channel", "off"], home=tmp_path) == 0
    assert not list(tmp_path.iterdir())


def test_release_card_binds_target_channel_and_only_adds_line_when_on() -> None:
    # Given
    module = importlib.import_module("automation.release_spec")
    plain = _spec()
    enabled = _spec(module.yoodori_channel_surface(NEW))
    line = f"채널 합치기: 노드의 대화(#agent-chat)·알림(#notifications)을 채널 {NEW}(유돌 채널)로"
    # When / Then
    assert line not in plain.render()
    assert enabled.render().count(line) == 1
    assert plain.action_hash() != enabled.action_hash()
    assert _spec(module.yoodori_channel_surface(OLD)).action_hash() != enabled.action_hash()


def test_profile_ignores_both_channels_and_free_response_cannot_add_them(tmp_path: Path) -> None:
    # Given
    path = tmp_path / ".hermes/config.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(f"discord:\n  ignored_channels: '44'\n  free_response_channels: '{OLD}'\n")
    interop = path.parent / "interop/config.json"
    interop.parent.mkdir()
    interop.write_text(json.dumps({"agent_chat_channel_id": OLD}))
    # When
    code = agent_config.main(
        ["--apply", "--dori-agent-chat", "off", "--yoodori-channel", NEW], home=tmp_path,
    )
    # Then
    assert code == 0
    text = path.read_text()
    assert set(ignore.read_ignored(text).channels) == {"44", OLD, NEW}
    assert OLD not in free_response.configured_channels(text)[0]
    assert free_response.decide(text, NEW).state is free_response.State.IGNORED


@pytest.mark.parametrize("operation", ["close", "rename", "notice"])
def test_deleted_terminal_thread_is_quiet(
    operation: str, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    def missing(*_args, **_kwargs):
        raise HTTPError("https://example.invalid", 404, "Unknown Channel", {}, None)

    def fallback(_body):
        pytest.fail("a deleted terminal destination must not trigger fallback")

    # When
    if operation == "close":
        origin_notice.close_thread(missing, "321", origin_notice.ThreadOutcome.DONE)
    elif operation == "rename":
        origin_notice.rename_thread(missing, "321", "digest")
    else:
        origin_notice.deliver(
            api=missing, transport_factory=lambda _: type("Sender", (), {"send": missing})(),
            record={"id": "record", "approval_thread_id": "321"},
            thread_name="result", content="done", fallback=fallback,
            outcome=origin_notice.ThreadOutcome.DONE,
        )
    # Then
    assert capsys.readouterr().err == ""
