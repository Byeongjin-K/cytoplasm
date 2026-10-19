"""A shadowed-skill notice goes to the pair fault channel, not the owner's channel.

2026-10-19 11:48 KST the owner saw "자가 스킬 이름 가림 (mail)" in his own channel; system alerts
belong to the pair channel that YooDori and ZzangDori work from. New file so the recorded SC-1
cases keep their shape.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from automation import supply_chain_shadow_watch as watch
from tests.unit.test_supply_chain_shadow_watch import _governed, _skills_home


def test_the_real_delivery_path_asks_for_the_fault_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake(content: str, **kwargs: object) -> bool:
        calls.append(kwargs)
        return True

    monkeypatch.setattr(watch, "notify_owner", fake)
    monkeypatch.setenv("SUPPLY_CHAIN_SHADOW_STATE", str(tmp_path / "shadows.json"))
    monkeypatch.delenv("AUTOPHAGY_OWNER_ID", raising=False)
    home = _skills_home(tmp_path)
    watch.run_shadow_check(home=home, governed_root=_governed(tmp_path, "recall"), notify=fake)
    assert calls and all(call.get("fault") is True for call in calls)
