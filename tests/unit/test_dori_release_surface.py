"""The optional takeover rides the existing release surface and owner decision."""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from automation import release_approval, skill_gate
from automation.release_spec import dori_surface
from tests.unit import test_release_authorize as auth
from tests.unit.test_healthcheck_wrapper_release import _fake_plan, _plan_args, _spec

BOT = "555555555555555555"
LINE = "도리 전환 켬: 노드 agent 게이트웨이가 #agent-chat 무시(유돌이 받음)"


def test_no_switch_keeps_the_existing_card_and_hash() -> None:
    # Given: goldens captured from the pre-opt-in v6 renderer.
    plain = _spec()
    # When / Then
    assert plain.action_hash() == "fb17cdbb60ccb11840684527a1daeffc94c7b69b1e6035c10662c7f7143b6dfc"
    assert hashlib.sha256(plain.render().encode()).hexdigest() == (
        "a3d23a31938565414513f51e72115cf36b2161c618223bcdf56b3d279127478d"
    )


def test_switch_card_renders_one_line_and_binds_the_bot() -> None:
    # Given
    plain = _spec()
    enabled = _spec(dori_surface(BOT))
    # When
    card = enabled.render()
    # Then
    assert card.count(LINE) == 1
    assert f"> {LINE}" in card.splitlines()
    assert "dori-agent-chat:" not in card
    assert enabled.action_hash() != plain.action_hash()
    assert _spec(dori_surface("666")).action_hash() != enabled.action_hash()
    print(next(line for line in card.splitlines() if LINE in line))


@pytest.mark.parametrize("bot", [None, BOT, "invalid"])
def test_plan_adds_only_an_explicit_valid_switch_surface(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], bot: str | None,
) -> None:
    # Given
    _fake_plan(monkeypatch)
    args = _plan_args()
    args.dori_agent_chat = bot
    # When
    code = release_approval.cmd_plan(args)
    # Then
    output = capsys.readouterr()
    if bot == "invalid":
        assert code == 4
        assert output.out == ""
        assert "DORI-SWITCH-REFUSED" in output.err
    else:
        assert code == 0
        assert json.loads(output.out)["surface_digests"] == sorted(
            [["repo", "c" * 64], *([list(dori_surface(BOT))] if bot else [])]
        )


@pytest.mark.parametrize("reaction, covered_bot, requested_head, code", [
    ("approve", BOT, "a" * 40, 0),
    ("pending", BOT, "a" * 40, 1),
    ("cancel", BOT, "a" * 40, 8),
    ("both", BOT, "a" * 40, 8),
    ("approve", "666", "a" * 40, 4),
    ("approve", None, "a" * 40, 4),
    ("approve", BOT, "f" * 40, 4),
])
def test_dori_authorization_uses_the_real_owner_and_surface_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    reaction: str, covered_bot: str | None, requested_head: str, code: int,
) -> None:
    # Given: reuse the existing API fake, not a stubbed authorization predicate.
    original_spec = auth._spec
    base = original_spec()
    bound = replace(base, surface_digests=(
        *base.surface_digests, *([dori_surface(covered_bot)] if covered_bot else []),
    ))
    monkeypatch.setattr(auth, "_spec", lambda: bound)
    owner = [{"id": auth._OWNER, "bot": False}]
    reactions = {
        auth._APPROVE: owner if reaction in ("approve", "both") else [],
        auth._CANCEL: owner if reaction in ("cancel", "both") else [],
    }
    api_calls: list[str] = []
    auth._env(tmp_path, monkeypatch, reactions=reactions, api_log=api_calls, render_version=6)
    # When
    result = release_approval.cmd_authorize_dori(
        argparse.Namespace(bot_id=BOT, head=requested_head)
    )
    # Then
    assert result == code
    if code == skill_gate.RELEASE_NOT_COVERED_EXIT:
        assert api_calls == []
