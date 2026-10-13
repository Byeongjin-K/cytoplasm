"""The analysis model is chosen by configuration only: Hermes main model by default, a local endpoint by config.

The swap test talks to a real OpenAI-compatible HTTP stub on loopback, so a provider that
ignores the config (and silently falls back to Hermes) fails here.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from automation.project_board.analysis import analyze
from automation.project_board.config import load_config
from automation.project_board.llm import HermesProvider, OpenAICompatibleProvider, provider_from_config
from automation.project_board.models import ProjectBoardError, SourceRef
from automation.project_board.sources import SourceDocument

ANSWER = {
    "relevant": True, "project": "프로젝트A", "project_owner": "", "summary": "자료 요청",
    "todos": [{"title": "자료 제출", "owner": "담당자1", "due": "2026-10-20", "due_reason": "원문"}],
}


@pytest.fixture
def local_llm() -> Iterator[tuple[str, list[dict[str, object]]]]:
    seen: list[dict[str, object]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append({"path": self.path, "body": body, "auth": self.headers.get("Authorization")})
            reply = json.dumps({"model": body["model"], "choices": [{"message": {"content": json.dumps(ANSWER)}}]})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(reply.encode())

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1", seen
    finally:
        server.shutdown()
        server.server_close()


def _write_config(tmp_path: Path, llm: dict[str, object] | None) -> dict[str, str]:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({} if llm is None else {"llm": llm}), encoding="utf-8")
    return {"PROJECT_BOARD_CONFIG": str(path), "HOME": str(tmp_path)}


def _document() -> SourceDocument:
    return SourceDocument(SourceRef("mail", "u-1", "자료 요청", "a@example.invalid", "2026-10-13"), "자료를 20일까지")


def test_no_config_means_the_hermes_main_model(tmp_path: Path) -> None:
    provider = provider_from_config(load_config(_write_config(tmp_path, None)).llm)
    assert isinstance(provider, HermesProvider)


def test_hermes_route_names_no_model_and_reports_what_served(tmp_path: Path) -> None:
    hermes = tmp_path / "hermes"
    record = tmp_path / "argv.json"
    hermes.write_text(
        "#!/usr/bin/env python3\nimport json, sys\n"
        f"json.dump(sys.argv[1:], open({str(record)!r}, 'w'))\n"
        "usage = sys.argv[sys.argv.index('--usage-file') + 1]\n"
        "json.dump({'provider': 'main-route', 'model': 'main-model'}, open(usage, 'w'))\n"
        f"print({json.dumps(ANSWER, ensure_ascii=False)!r})\n",
        encoding="utf-8",
    )
    hermes.chmod(0o755)
    provider = HermesProvider(env={"HOME": str(tmp_path), "AUTOPHAGY_HERMES_BIN": str(hermes)})
    proposal = analyze(_document(), provider, known_projects=(), today=date(2026, 10, 13))
    argv = json.loads(record.read_text(encoding="utf-8"))
    assert "-m" not in argv and "--provider" not in argv
    assert proposal is not None and (proposal.provider, proposal.model) == ("main-route", "main-model")


def test_config_swaps_to_the_local_endpoint(tmp_path: Path, local_llm: tuple[str, list[dict[str, object]]]) -> None:
    base, seen = local_llm
    env = _write_config(tmp_path, {"provider": "openai-compatible", "base_url": base, "model": "local-7b"})
    provider = provider_from_config(load_config(env).llm, env=env)
    assert isinstance(provider, OpenAICompatibleProvider)
    proposal = analyze(_document(), provider, known_projects=("프로젝트A",), today=date(2026, 10, 13))
    assert proposal is not None and proposal.todos[0].due == "2026-10-20"
    assert (proposal.provider, proposal.model) == ("openai-compatible", "local-7b")
    assert seen[0]["path"] == "/v1/chat/completions"
    request = seen[0]["body"]
    assert isinstance(request, dict) and request["model"] == "local-7b"
    assert "- 프로젝트A" in request["messages"][0]["content"]


def test_local_endpoint_key_comes_from_the_named_env(local_llm: tuple[str, list[dict[str, object]]]) -> None:
    base, seen = local_llm
    provider = OpenAICompatibleProvider(base, "local-7b", "LOCAL_LLM_KEY", env={"LOCAL_LLM_KEY": "k-test"})
    provider.complete("x")
    assert seen[0]["auth"] == "Bearer k-test"
    with pytest.raises(ProjectBoardError, match="unset"):
        OpenAICompatibleProvider(base, "local-7b", "LOCAL_LLM_KEY", env={}).complete("x")


@pytest.mark.parametrize(
    "llm",
    [{"provider": "other"}, {"provider": "openai-compatible", "base_url": "file:///x", "model": "m"},
     {"provider": "openai-compatible", "base_url": "http://127.0.0.1:1/v1"}],
)
def test_bad_llm_config_is_refused(tmp_path: Path, llm: dict[str, object]) -> None:
    with pytest.raises(ProjectBoardError):
        load_config(_write_config(tmp_path, llm))


def test_unreachable_local_endpoint_is_retryable() -> None:
    with pytest.raises(ProjectBoardError) as error:
        OpenAICompatibleProvider("http://127.0.0.1:9/v1", "m", timeout=2).complete("x")
    assert error.value.retryable
