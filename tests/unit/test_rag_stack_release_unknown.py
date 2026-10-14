"""A missing RAG observation omits its action, not the whole release approval."""
from __future__ import annotations

from pathlib import Path

from tests.unit.test_release_sh import _calls, _origin_with_commits, _origin_tags, _run


def test_rag_stack_unknown_observation_warns_and_proceeds_without_rag_action(tmp_path: Path) -> None:
    _origin, work = _origin_with_commits(tmp_path)
    unavailable = tmp_path / "unavailable-rag"
    unavailable.write_text("#!/bin/sh\necho RUNTIME-PACKAGE-UNKNOWN >&2\nexit 4\n")
    unavailable.chmod(0o755)

    result = _run(tmp_path, work, decisions="2 7 0", rag_plan=str(unavailable))

    assert result.returncode == 0, result.stdout + result.stderr
    assert "RAG-MCP-PLAN-UNKNOWN:" in result.stderr
    assert "--rag-mcp" not in (tmp_path / "plan-args").read_text()
    assert _calls(tmp_path).count("request") == 1
    assert "refs/tags/v1.0.0" in _origin_tags(work)
