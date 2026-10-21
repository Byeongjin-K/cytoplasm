"""Routing logs name the model that actually answered, not only the requested primary.

Since the 2026-09-22 owner decision Hermes may answer a Codex-pinned call from the
account's ``fallback_providers`` chain (xAI Grok). Every masked routing log therefore
keeps the requested primary in ``provider``/``model`` and adds ``served_provider``/
``served_model`` read from Hermes' own ``--usage-file`` report. The stub binary below
writes that report the way Hermes does after a fallback, so each test proves the log
records Grok even though the argv pinned Codex.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "skills" / "mail" / "scripts"))
sys.path.insert(0, str(ROOT / "skills"))
sys.path.insert(0, str(ROOT / "skills" / "patent-prep"))

from automation import codex_llm  # noqa: E402
from skills.doctype.scripts import doctype_llm  # noqa: E402
from skills.proposal.scripts import proposal_llm  # noqa: E402

import triage_llm  # noqa: E402

report_llm = import_module("report.scripts.report_llm")
patent_llm = import_module("scripts.patent_llm")

FALLBACK = {"provider": "xai-oauth", "model": "grok-4.7"}


def _stub(path: Path, stdout: str, report: dict[str, str] | None = FALLBACK) -> Path:
    """A Hermes stand-in that answers and, like Hermes, writes the usage report it was asked for."""
    body = json.dumps(report) if report is not None else None
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "argv = sys.argv[1:]\n"
        f"body = {body!r}\n"
        "if body is not None and '--usage-file' in argv:\n"
        "    open(argv[argv.index('--usage-file') + 1], 'w', encoding='utf-8').write(body)\n"
        f"print({stdout!r})\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _last(log: Path) -> dict[str, object]:
    return json.loads(log.read_text(encoding="utf-8").splitlines()[-1])


def test_complete_served_reports_the_fallback_route_and_removes_the_report(
    tmp_path: Path,
) -> None:
    # Given: Hermes answers from the fallback chain and says so in its usage report.
    binary = _stub(tmp_path / "hermes", "pong")
    client = codex_llm.CodexClient.from_environment(
        {"HOME": str(tmp_path), "AUTOPHAGY_HERMES_BIN": str(binary)}
    )
    before = set(Path(os.environ.get("TMPDIR", "/tmp")).glob("autophagy-usage-*.json"))

    # When
    served = client.complete_served("ping")

    # Then: the answer comes back with the route that produced it, and no report is left behind.
    assert served == codex_llm.Served(text="pong", provider="xai-oauth", model="grok-4.7")
    assert set(Path(os.environ.get("TMPDIR", "/tmp")).glob("autophagy-usage-*.json")) == before


def test_complete_served_without_a_report_says_unknown_instead_of_codex(tmp_path: Path) -> None:
    # Given: a binary that answers but writes no usage report.
    binary = _stub(tmp_path / "hermes", "pong", report=None)
    client = codex_llm.CodexClient.from_environment(
        {"HOME": str(tmp_path), "AUTOPHAGY_HERMES_BIN": str(binary)}
    )

    # When
    served = client.complete_served("ping")

    # Then: the log must not claim Codex answered when nobody said so.
    assert (served.provider, served.model) == (codex_llm.UNKNOWN, codex_llm.UNKNOWN)


def test_mail_log_keeps_the_primary_and_adds_the_served_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "llm-calls.jsonl"
    monkeypatch.setenv("AUTOPHAGY_HERMES_BIN", str(_stub(tmp_path / "hermes", '{"summary": "ok"}')))
    monkeypatch.setenv("TRIAGE_LLM_LOG", str(log))

    summary = triage_llm.summarize(
        subject="S", sender="X", body="B", uid_opaque="sha256:u",
        prompt_path=ROOT / "skills" / "mail" / "prompts" / "digest-summary-v1.md",
    )

    record = _last(log)
    assert summary == "ok"
    assert record["provider"] == "openai-codex"
    assert (record["served_provider"], record["served_model"]) == ("xai-oauth", "grok-4.7")


def test_doctype_log_records_the_served_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "llm-calls.jsonl"
    monkeypatch.setenv("DOCTYPE_HERMES_BIN", str(_stub(tmp_path / "hermes", "draft")))
    monkeypatch.setenv("DOCTYPE_LLM_LOG", str(log))

    text = doctype_llm.call_codex("prompt", purpose="narrative", opaque_id="d1")

    record = _last(log)
    assert text == "draft"
    assert record["provider"] == "openai-codex"
    assert (record["served_provider"], record["served_model"]) == ("xai-oauth", "grok-4.7")


def test_proposal_log_records_the_served_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AUTOPHAGY_HERMES_BIN", str(_stub(tmp_path / "hermes", "Review comments.")))
    monkeypatch.setenv("PROPOSAL_LLM_LOG_ROOT", str(tmp_path / "logs"))

    review = proposal_llm.run_final_review("# Proposal")

    record = _last(tmp_path / "logs" / "llm-calls.jsonl")
    assert review == "Review comments."
    assert record["provider"] == "openai-codex"
    assert (record["served_provider"], record["served_model"]) == ("xai-oauth", "grok-4.7")


def test_report_log_records_the_served_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".local" / "bin").mkdir(parents=True)
    _stub(tmp_path / ".local" / "bin" / "hermes", "draft")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("AUTOPHAGY_HERMES_BIN", raising=False)

    text = report_llm.generate("prompt")

    record = _last(tmp_path / ".hermes" / "report" / "logs" / "llm-calls.jsonl")
    assert text == "draft"
    assert record["provider"] == "openai-codex"
    assert (record["served_provider"], record["served_model"]) == ("xai-oauth", "grok-4.7")


@dataclass(frozen=True, slots=True)
class _Result:
    returncode: int
    stdout: str


def test_patent_log_records_the_served_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATENT_LLM_LOG_ROOT", str(tmp_path / "logs"))

    def invoke(command: tuple[str, ...]) -> _Result:
        # Hermes writes the usage report to the path it was given.
        usage = Path(command[command.index("--usage-file") + 1])
        usage.write_text(json.dumps(FALLBACK), encoding="utf-8")
        return _Result(0, "Synthetic draft.")

    response = patent_llm.generate_draft("private material", (), invoke)

    record = _last(tmp_path / "logs" / "llm-calls.jsonl")
    assert response.text == "Synthetic draft."
    assert record["provider"] == "openai-codex"
    assert (record["served_provider"], record["served_model"]) == ("xai-oauth", "grok-4.7")
