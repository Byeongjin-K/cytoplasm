from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills/recall/scripts"
sys.path.insert(0, str(SCRIPTS))

import recall_cli  # noqa: E402
import recall_core  # noqa: E402
import recall_pick  # noqa: E402

from automation import codex_llm  # noqa: E402


class _OldServerError(Exception):
    pass


def _row(source: str, score: float = 0.3) -> dict[str, Any]:
    return {"source": source, "content": source, "score": score, "metadata": {}, "document_id": source}


class _Client:
    def __init__(self, *, reject_per_note: bool = False) -> None:
        self.calls: list[tuple[int, bool, int]] = []
        self.reject_per_note = reject_per_note

    def search_memory(
        self, query: str, limit: int = 5, *, one_per_note: bool = False, per_note: int = 1,
        exclude_notes: object = (),
    ) -> list[dict[str, Any]]:
        self.calls.append((limit, one_per_note, per_note))
        if per_note != 1 and self.reject_per_note:
            raise _OldServerError("validation error: unexpected per_note")
        return [_row("wiki:a.md"), _row("wiki:b.md")]


def _model(monkeypatch: pytest.MonkeyPatch, *answers: str | Exception) -> None:
    replies = iter(answers)

    class Client:
        def complete(self, _prompt: str) -> str:
            answer = next(replies)
            if isinstance(answer, Exception):
                raise answer
            return answer

    monkeypatch.setattr(codex_llm.CodexClient, "from_environment", classmethod(lambda _cls, *a, **k: Client()))


def test_the_pick_can_be_turned_off() -> None:
    assert recall_pick.picker({"RECALL_PICK": "0"}) is None


def test_the_agent_call_searches_the_whole_store_two_passages_per_note_and_keeps_the_pick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _model(monkeypatch, '{"ids": ["c002"]}', '{"ids": ["c001"]}')
    client = _Client()
    pick = recall_pick.picker({})
    assert pick is not None

    rows, picked = pick(client, "질문", 5, _OldServerError)

    assert picked is True
    assert [row["source"] for row in rows] == ["wiki:b.md"]
    assert client.calls == [(60, True, 2)]


@pytest.mark.parametrize("failure", ["model", "old-server"])
def test_a_failed_pick_falls_back_to_the_plain_search(monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    _model(monkeypatch, codex_llm.CodexUnavailableError("down") if failure == "model" else '{"ids": []}')
    client = _Client(reject_per_note=failure == "old-server")
    pick = recall_pick.picker({})
    assert pick is not None

    rows, picked = pick(client, "질문", 5, _OldServerError)

    assert picked is False
    assert client.calls[-1] == (5, False, 1)
    assert len(rows) == 2


def test_picked_rows_are_not_gated_by_score() -> None:
    response = recall_core.build_response("전혀 다른 낱말", [_row("wiki:a.md", 0.1)], picked=True)

    assert response["status"] == "hit"
    assert [result["source"] for result in response["results"]] == ["wiki:a.md"]
    assert response["search"]["picked"] is True


def test_a_picked_search_skips_the_entity_fallback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    calls: list[str] = []

    def fake_search(query: str, limit: int, picker: object = None) -> tuple[list[dict[str, Any]], None, list[str], str]:
        calls.append(query)
        return [_row("wiki:a.md", 0.1)], None, ["fake://pick", recall_cli._PICKED], "fake://pick"

    monkeypatch.setattr(recall_cli, "_mcp_search", fake_search)
    monkeypatch.setenv("RECALL_LOG_DIR", str(tmp_path))
    monkeypatch.delenv("RECALL_FAKE_RESULTS", raising=False)
    monkeypatch.delenv("RECALL_FAKE_ERROR", raising=False)

    assert recall_cli.main(["search", "최근 김민준 박사와 함께 진행한 업무 협업 내역", "--json", "--entity-fallback"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert calls == ["최근 김민준 박사와 함께 진행한 업무 협업 내역"]
    assert payload["status"] == "hit"
    assert payload["search"]["picked"] is True
