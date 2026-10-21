from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from skills.proposal.scripts import proposal_corpus, proposal_knowledge  # noqa: E402


def _synthesis(*, broken_url: bool = False, first_url: str | None = None) -> str:
    rows: list[str] = []
    sources: list[str] = []
    for index in range(3):
        url = first_url if index == 0 and first_url is not None else (
            f"https://source-{index}.example.org/item/{index}"
        )
        sources.append(url)
        rows.append(f"| C{index + 1:02d}: claim {index + 1} | CONFIRMED | {url} |")
    if broken_url:
        rows[1] = "| C02: claim 2 | CONFIRMED | broken-url |"
    return "\n".join((
        "# Synthesis", "", "## Detailed Findings", "", "Findings.", "",
        "## External Sources", "", *(f"{i}. {url}" for i, url in enumerate(sources, 1)),
        "", "## Verified Claims", "", "| Claim | Status | Source |",
        "| --- | --- | --- |", *rows, "",
    ))


class FakeRunner:
    def __init__(self, lint_rc: int = 0) -> None:
        self.lint_rc = lint_rc
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv: tuple[str, ...]) -> proposal_corpus.InvocationResult:
        self.calls.append(argv)
        if "research-convert" in argv:
            synthesis = Path(argv[argv.index("research-convert") + 1])
            out = Path(argv[argv.index("--out") + 1])
            out.mkdir(parents=True, exist_ok=True)
            for claim in proposal_corpus.proposal_research.parse_claims(
                synthesis.read_text(encoding="utf-8")
            ):
                digest = hashlib.sha256(claim.url.encode()).hexdigest()[:8]
                (out / f"research-{digest}.md").write_text(
                    "---\n"
                    f"source_url: {claim.url}\n"
                    "---\n"
                    f"{claim.text}\n",
                    encoding="utf-8",
                )
            return proposal_corpus.InvocationResult(0, "", "")
        return proposal_corpus.InvocationResult(self.lint_rc, "", "lint failed")


def _pack() -> proposal_knowledge.EvidencePack:
    return proposal_knowledge.EvidencePack(
        "goal",
        (
            proposal_knowledge.EvidenceItem(
                "obsidian:Projects/excavator.md", "obsidian", "summary only",
                0.9, None,
            ),
            proposal_knowledge.EvidenceItem(
                "wiki:invention/42", "wiki-twin", "patent summary",
                0.8, None,
            ),
            proposal_knowledge.EvidenceItem(
                "note:personal/weekly.md", "rag", "weekly summary", 0.7, None,
            ),
        ),
        (),
        (),
    )


def test_three_confirmed_claims_become_public_corpus_files(tmp_path: Path) -> None:
    synthesis = tmp_path / "SYNTHESIS.md"
    synthesis.write_text(_synthesis(), encoding="utf-8")
    corpus = tmp_path / "corpus"

    proposal_corpus.build_corpus(
        synthesis, corpus, _pack(), runner=FakeRunner()
    )

    web = sorted(corpus.glob("research-*.md"))
    assert len(web) == 3
    assert all("source_url: https://" in path.read_text(encoding="utf-8") for path in web)


def test_lint_failure_exits_three_and_removes_corpus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = tmp_path / "excavator" / "versions" / "v000001"
    inputs = version / "inputs"
    corpus = version / "corpus"
    inputs.mkdir(parents=True)
    corpus.mkdir()
    (tmp_path / "excavator" / "HEAD").write_text("v000001\n", encoding="utf-8")
    (version / "manifest.json").write_text(
        '{"parent":null,"run_key":"' + "a" * 64
        + '","schema_version":1,"version":"v000001"}\n',
        encoding="utf-8",
    )
    (inputs / "SYNTHESIS.md").write_text(_synthesis(), encoding="utf-8")
    (inputs / "RESEARCH_BRIEF.md").write_text("# Deep Research Brief\n", encoding="utf-8")
    (corpus / "stale.md").write_text("stale", encoding="utf-8")
    monkeypatch.setenv("PROPOSAL_ROOT", str(tmp_path))
    monkeypatch.setattr(proposal_corpus.proposal_research, "validate_synthesis", lambda path: None)

    rc = proposal_corpus.command(
        argparse.Namespace(slug="excavator", json=True), runner=FakeRunner(3)
    )

    assert rc == 3
    assert not corpus.exists()


def test_owner_files_keep_source_keys_and_only_summaries(tmp_path: Path) -> None:
    synthesis = tmp_path / "SYNTHESIS.md"
    synthesis.write_text(_synthesis(), encoding="utf-8")
    corpus = tmp_path / "corpus"
    pack = _pack()

    proposal_corpus.build_corpus(
        synthesis, corpus, pack, runner=FakeRunner()
    )

    owner_text = "\n".join(path.read_text(encoding="utf-8") for path in corpus.glob("owner-*.md"))
    assert all(item.source_key in owner_text for item in pack.items)
    assert all(item.summary in owner_text for item in pack.items)
    assert "RAW-NOTE-SENTINEL" not in owner_text


def test_lint_never_uses_warn_only_and_rerun_is_idempotent(tmp_path: Path) -> None:
    synthesis = tmp_path / "SYNTHESIS.md"
    synthesis.write_text(_synthesis(), encoding="utf-8")
    corpus = tmp_path / "corpus"
    runner = FakeRunner()

    for _ in range(2):
        proposal_corpus.build_corpus(
            synthesis, corpus, _pack(), runner=runner
        )

    lint_calls = [call for call in runner.calls if "corpus-lint" in call]
    assert lint_calls
    assert all("--warn-only" not in call for call in lint_calls)
    assert len(tuple(corpus.glob("*.md"))) == 6


@pytest.mark.parametrize(
    "url",
    (
        "http://127.0.0.1/source",
        "http://192.168.1.5/source",
        "http://[::1]/source",
        "http://intranet/source",
        "https://x.local/source",
    ),
)
def test_command_rejects_non_public_urls_with_claim_line(
    url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    version = tmp_path / "excavator" / "versions" / "v000001"
    inputs = version / "inputs"
    inputs.mkdir(parents=True)
    (tmp_path / "excavator" / "HEAD").write_text("v000001\n", encoding="utf-8")
    (version / "manifest.json").write_text(
        '{"parent":null,"run_key":"' + "a" * 64
        + '","schema_version":1,"version":"v000001"}\n',
        encoding="utf-8",
    )
    synthesis = inputs / "SYNTHESIS.md"
    synthesis.write_text(_synthesis(first_url=url), encoding="utf-8")
    (inputs / "RESEARCH_BRIEF.md").write_text("# Deep Research Brief\n", encoding="utf-8")
    expected_line = next(
        index
        for index, line in enumerate(synthesis.read_text(encoding="utf-8").splitlines(), 1)
        if "C01:" in line
    )
    monkeypatch.setenv("PROPOSAL_ROOT", str(tmp_path))
    monkeypatch.setattr(proposal_corpus.proposal_research, "validate_synthesis", lambda path: None)

    rc = proposal_corpus.command(
        argparse.Namespace(slug="excavator", json=True), runner=FakeRunner()
    )
    error = capsys.readouterr().err

    assert rc == 3
    assert f"line {expected_line}" in error
    assert not (version / "corpus").exists()


def test_public_domain_url_is_eligible(tmp_path: Path) -> None:
    synthesis = tmp_path / "SYNTHESIS.md"
    synthesis.write_text(
        _synthesis(first_url="https://example.com/paper"),
        encoding="utf-8",
    )

    claims = proposal_corpus._check_claims(synthesis)

    assert claims[0].url == "https://example.com/paper"


def test_command_malformed_synthesis_exits_three_without_partial_corpus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = tmp_path / "excavator" / "versions" / "v000001"
    inputs = version / "inputs"
    inputs.mkdir(parents=True)
    (tmp_path / "excavator" / "HEAD").write_text("v000001\n", encoding="utf-8")
    (version / "manifest.json").write_text(
        '{"parent":null,"run_key":"' + "a" * 64 + '","schema_version":1,"version":"v000001"}\n',
        encoding="utf-8",
    )
    (inputs / "SYNTHESIS.md").write_text(_synthesis(broken_url=True), encoding="utf-8")
    (inputs / "RESEARCH_BRIEF.md").write_text("# Deep Research Brief\n", encoding="utf-8")
    monkeypatch.setenv("PROPOSAL_ROOT", str(tmp_path))

    rc = proposal_corpus.command(argparse.Namespace(slug="excavator", json=True))

    assert rc == 3
    assert not (version / "corpus").exists()
