"""The RAG rollout read-back proves the recall arguments and the core-notes tool, not just a plain search.

Split from test_rag_stack_smoke.py so its existing cases stay byte-identical."""
from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SMOKE = Path(__file__).resolve().parents[2] / "automation/rag_stack/smoke.py"


class _Fatal(Exception):
    pass


def _load(monkeypatch: pytest.MonkeyPatch, *, old_server: bool) -> tuple[ModuleType, list[str]]:
    calls: list[str] = []

    class Client:
        def __init__(self, _url: str, _key: str) -> None:
            pass

        def search_memory(self, _query: str, limit: int = 5, **kwargs: Any) -> list[dict[str, Any]]:
            calls.append("recall-search" if kwargs else "search")
            if kwargs and old_server:
                raise _Fatal("validation error")
            return []

        def core_notes(self) -> list[dict[str, str]]:
            calls.append("core-notes")
            return []

    fake = ModuleType("mcp_client")
    fake.McpMemoryClient = Client  # type: ignore[attr-defined]
    fake.McpFatalError = _Fatal  # type: ignore[attr-defined]
    fake.McpUnreachableError = OSError  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mcp_client", fake)
    monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_k: io.BytesIO(json.dumps({"status": "ok"}).encode()))
    spec = importlib.util.spec_from_file_location("rag_stack_smoke", SMOKE)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module, calls


def _run(module: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *mode: str) -> int:
    env = tmp_path / "env"
    env.write_text("MCP_BIND_ADDRESS=127.0.0.1\nRAG_MCP_API_KEY=synthetic\n")
    monkeypatch.setattr(sys, "argv", ["smoke.py", str(env), "5", *mode])
    return module.main()


def test_a_new_install_must_answer_the_recall_search_and_the_core_notes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    module, calls = _load(monkeypatch, old_server=False)

    assert _run(module, monkeypatch, tmp_path) == 0
    assert calls == ["search", "recall-search", "core-notes"]
    assert "RAG-MCP-SMOKE-PASS" in capsys.readouterr().err


def test_a_server_without_the_recall_arguments_fails_the_install_read_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    module, _calls = _load(monkeypatch, old_server=True)

    assert _run(module, monkeypatch, tmp_path) == 1
    assert "RAG-MCP-SMOKE-FAIL: recall-search" in capsys.readouterr().err


def test_a_rollback_to_the_previous_image_is_checked_with_the_plain_search_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    module, calls = _load(monkeypatch, old_server=True)

    assert _run(module, monkeypatch, tmp_path, "basic") == 0
    assert calls == ["search"]
