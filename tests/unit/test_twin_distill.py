"""DT-C2: inferred decision-twin distillation is draft-only."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import pytest

from automation.twin_distill.cli import (
    DistillationDependencies,
    DistillationInvocation,
    DraftEmission,
    LiveDependencies,
    ProcessResult,
    WikiDraftRunner,
    run_distillation,
    run_with_live_llm,
)
from automation.twin_distill.gather import (
    EvidenceExcerpt,
    EvidenceMetadata,
    GatherRequest,
    RecallSearchResult,
)
from automation.twin_distill.llm import CodexLlmClient, LlmConfigurationError
from automation.twin_distill.validate import (
    AuthorityCapError,
    CandidateSpec,
    CandidateValidationError,
    InferredProposal,
    build_proposal,
)


# Absent on every host; the literal /home/agent is a real, unreadable account on the node.
_ABSENT_HOME = str(Path(os.environ["AUTOPHAGY_UNIT_NODE_ROOT"]) / "home" / "agent")

WELL_FORMED_BODY = (
    "## Trigger\n"
    "When selecting a research direction.\n\n"
    "## Rule\n"
    "Prefer reversible experiments before irreversible commitments.\n\n"
    "## Evidence\n"
    "- source_key: wiki:research-principles#c0001\n\n"
    "## Counterexample\n"
    "Do not delay a compliance deadline that has a fixed external due date.\n"
)


@dataclass(frozen=True, slots=True)
class FakeSearchClient:
    results: tuple[RecallSearchResult, ...]
    queries: list[str]

    def search(self, query: str) -> tuple[RecallSearchResult, ...]:
        self.queries.append(query)
        return self.results


@dataclass(frozen=True, slots=True)
class FakeLlm:
    response: str
    prompts: list[str]

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.response


@dataclass(frozen=True, slots=True)
class FakeDraftRunner:
    emitted: list[InferredProposal]

    def emit(self, proposal: InferredProposal) -> DraftEmission:
        self.emitted.append(proposal)
        return DraftEmission(command=("fake-wiki", "draft"), stdout="DRAFT-CREATED", stderr="")


@dataclass(frozen=True, slots=True)
class ProcessCall:
    command: tuple[str, ...]
    stdin: str
    environment: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class FakeProcessRunner:
    calls: list[ProcessCall]

    def run(
        self,
        command: tuple[str, ...],
        *,
        stdin: str,
        env: Mapping[str, str],
    ) -> ProcessResult:
        self.calls.append(ProcessCall(command, stdin, tuple(sorted(env.items()))))
        return ProcessResult(returncode=0, stdout="DRAFT-CREATED", stderr="")


def _invocation(*, excerpts: tuple[EvidenceExcerpt, ...] = ()) -> DistillationInvocation:
    return DistillationInvocation(
        gather_request=GatherRequest(
            query="How should a research direction be selected?",
            conversation_excerpts=excerpts,
        ),
        title="Reversible research direction selection",
        authority="default",
        tags=("decision-twin", "research"),
    )


def _safe_result() -> RecallSearchResult:
    return RecallSearchResult(
        source="wiki:research-principles#c0001",
        content="Prefer a reversible experiment before making a long-term commitment.",
        metadata=EvidenceMetadata(source_type="wiki"),
    )


def _patent_result() -> RecallSearchResult:
    return RecallSearchResult(
        source="obsidian:patent-roadmap#c0001",
        content="특허 patent 기밀 검토 내용.",
        metadata=EvidenceMetadata(source_type="obsidian"),
    )


def test_distillation_includes_all_evidence_before_llm() -> None:
    # Given: a recall/MCP response containing both ordinary and keyword-bearing evidence
    search = FakeSearchClient((_safe_result(), _patent_result()), [])
    llm = FakeLlm(WELL_FORMED_BODY, [])
    runner = FakeDraftRunner([])

    # When
    run_distillation(_invocation(), DistillationDependencies(search, llm, runner))

    # Then: both sources reach the LLM boundary unchanged
    assert len(llm.prompts) == 1
    assert "특허 patent 기밀 검토 내용." in llm.prompts[0]
    assert "wiki:research-principles#c0001" in llm.prompts[0]
    assert len(runner.emitted) == 1


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("## Rule\nA rule without evidence.\n## Counterexample\nAn exception.\n", "Evidence"),
        ("## Evidence\nA source without a citation.\n## Counterexample\nAn exception.\n", "source_key"),
        ("## Evidence\nsource_key: wiki:rule\n", "Counterexample"),
        ("## Evidence\nsource_key: wiki:rule\n## Counterexample\n\n", "Counterexample"),
    ],
)
def test_validator_rejects_missing_required_inferred_evidence_or_counterexample(
    body: str,
    reason: str,
) -> None:
    # Given: a candidate that superficially resembles a rule but misses one SI-3 proof
    spec = CandidateSpec(title="Candidate", authority="default")

    # When / Then
    with pytest.raises(CandidateValidationError, match=reason):
        build_proposal(spec, body)


def test_validator_accepts_well_formed_candidate_and_forces_inferred_meta() -> None:
    # Given: a candidate with its evidence citation and bounded counterexample
    spec = CandidateSpec(title="Candidate", authority="default")

    # When
    proposal = build_proposal(spec, WELL_FORMED_BODY)

    # Then: provenance is canonical and the proposer supplies no active status
    assert proposal.authority == "default"
    assert proposal.provenance == "inferred"
    assert proposal.status is None


def test_builder_rejects_strict_authority_for_inferred_rule() -> None:
    # Given: an otherwise valid inferred candidate requested with forbidden strict authority
    spec = CandidateSpec(title="Candidate", authority="strict")

    # When / Then
    with pytest.raises(AuthorityCapError, match="strict"):
        build_proposal(spec, WELL_FORMED_BODY)


def test_malformed_llm_candidate_emits_no_draft() -> None:
    # Given: the LLM returns text without the deterministic SI-3 sections
    search = FakeSearchClient((_safe_result(),), [])
    llm = FakeLlm("## Rule\nLooks plausible but has no proof.\n", [])
    runner = FakeDraftRunner([])

    # When / Then
    with pytest.raises(CandidateValidationError):
        run_distillation(_invocation(), DistillationDependencies(search, llm, runner))
    assert runner.emitted == []


def test_wiki_draft_runner_uses_draft_subprocess_with_explicit_environment() -> None:
    # Given: a validated inferred proposal and a process fake instead of a real wiki CLI
    process = FakeProcessRunner([])
    environment = {"PATH": "/bin", "WIKI_GATE_DIR": "/tmp/wiki-gate"}
    runner = WikiDraftRunner(
        wiki_cli_path=Path("/repo/skills/wiki/scripts/wiki_cli.py"),
        environment=environment,
        process_runner=process,
    )
    proposal = build_proposal(CandidateSpec(title="Candidate", authority="advisory"), WELL_FORMED_BODY)

    # When
    emission = runner.emit(proposal)

    # Then: only a pending draft command is issued; no status can bypass the gate
    assert emission.stdout == "DRAFT-CREATED"
    assert len(process.calls) == 1
    call = process.calls[0]
    assert call.command[1] == "/repo/skills/wiki/scripts/wiki_cli.py"
    assert call.command[2] == "draft"
    assert "--stdin" in call.command
    assert "--provenance" in call.command
    assert "inferred" in call.command
    assert "confirm" not in call.command
    assert "--status" not in call.command
    assert call.stdin == WELL_FORMED_BODY
    assert dict(call.environment) == environment


def test_unreachable_codex_oauth_fails_before_any_draft_is_emitted() -> None:
    # Given: a production dependency bundle whose environment cannot reach Codex OAuth
    search = FakeSearchClient((_safe_result(),), [])
    runner = FakeDraftRunner([])
    runtime = LiveDependencies(search_client=search, draft_runner=runner, environment={})

    # When / Then: it fails closed on the single tier; there is no second provider to try
    with pytest.raises(LlmConfigurationError, match="Codex"):
        run_with_live_llm(_invocation(), runtime)
    assert runner.emitted == []


def test_live_client_calls_codex_oauth_with_the_user_config_honored() -> None:
    # Given: the live client the CLI builds for an environment that can reach Codex
    client = CodexLlmClient.from_environment(
        {"HOME": _ABSENT_HOME, "AUTOPHAGY_HERMES_BIN": f"{_ABSENT_HOME}/.local/bin/hermes"}
    )

    # When: the argv of one distillation call is inspected
    argv = client.client.argv("prompt")

    # Then: the primary is Codex OAuth and the account's Hermes fallback chain stays live
    assert "--ignore-user-config" not in argv
    assert not {"--provider", "-m"} & set(argv)
