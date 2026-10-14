"""Per-entry verdict cache for the relocation watcher (owner decision Q138 ②: wider classification, same call count)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from automation.memory_curator.model import MemoryEntry
from automation.memory_relocate.verdict_cache import classify_with_cache
from automation.twin_distill.llm import LlmInvocationError

PERSON = "김가상의 호칭은 ‘선임님’이고 메일은 정중한 문체로 쓴다."
OPS = "The scheduled worker reads /srv/example/state.sqlite at startup and writes its journal next to it daily."


@dataclass
class _Llm:
    calls: int = 0
    fail: bool = False

    def complete(self, prompt: str) -> str:
        self.calls += 1
        if self.fail:
            raise LlmInvocationError("down")
        if "김가상" in prompt:
            return json.dumps({"route": "PERSON_REFERENCE", "evidence": "김가상의 호칭은", "reason": "r"})
        return json.dumps({"route": "OPS_REFERENCE", "evidence": "/srv/example/state.sqlite", "reason": "r"})


def test_a_second_tick_reuses_every_verdict_in_order(tmp_path: Path) -> None:
    entries = {"memory": (MemoryEntry(OPS),), "user": (MemoryEntry(PERSON),)}
    llm, cache = _Llm(), tmp_path / "classify-cache.json"
    first = classify_with_cache(entries, client=llm, path=cache)
    second = classify_with_cache(entries, client=llm, path=cache)
    assert llm.calls == 2
    assert [v.route for v in first] == [v.route for v in second] == ["OPS_REFERENCE", "PERSON_REFERENCE"]


def test_a_changed_entry_is_asked_again_and_a_removed_one_drops_out(tmp_path: Path) -> None:
    llm, cache = _Llm(), tmp_path / "classify-cache.json"
    _ = classify_with_cache({"memory": (MemoryEntry(OPS),), "user": ()}, client=llm, path=cache)
    _ = classify_with_cache({"memory": (MemoryEntry(OPS + " now"),), "user": ()}, client=llm, path=cache)
    assert llm.calls == 2
    assert len(json.loads(cache.read_text(encoding="utf-8"))) == 1


def test_a_model_failure_is_not_cached(tmp_path: Path) -> None:
    cache = tmp_path / "classify-cache.json"
    down = _Llm(fail=True)
    (failed,) = classify_with_cache({"memory": (MemoryEntry(OPS),), "user": ()}, client=down, path=cache)
    assert failed.veto == "llm_error"
    up = _Llm()
    (answered,) = classify_with_cache({"memory": (MemoryEntry(OPS),), "user": ()}, client=up, path=cache)
    assert (up.calls, answered.route) == (1, "OPS_REFERENCE")
