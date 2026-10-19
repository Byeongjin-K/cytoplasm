"""The v2 pipeline must not let a hand-built bundle pass for an engine-built one.

2026-09-28: with no command for the engine draft stage the node agent hand-wrote
``drafts.json``, drew placeholder figures and rendered them; the render then could not
correct terms because ``automation`` was not importable from the mounted skill. These
cases pin the three fixes together, apart from the per-module suites they cross.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from skills.proposal.engine.pipeline import cli as engine_cli
from skills.proposal.engine.pipeline import orchestrator
from skills.proposal.scripts import (
    proposal_cli,
    proposal_compose,
    proposal_corpus,
    proposal_knowledge,
    proposal_render,
)
from skills.proposal.scripts.proposal_config import ProposalConfig
from skills.proposal.scripts.proposal_ir import FigureSpec, figures_to_json
from skills.proposal.scripts.proposal_route_guard import RouteRefused
from skills.proposal.scripts.proposal_version import Staging, VersionStore

_REPO = Path(__file__).resolve().parents[2]


def _config(tmp_path: Path) -> ProposalConfig:
    return ProposalConfig(
        profile="10-page",
        image_model="gpt-image-2",
        image_monthly_cap_usd=10,
        refine_pin="b" * 40,
        drive_root="outputs",
        state_root=tmp_path / "state",
    )


def _version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "proposals"
    store = VersionStore(root)
    staging = store.begin("demo", hashlib.sha256(str(root).encode()).hexdigest())
    assert isinstance(staging, Staging)
    version = store.promote("demo", staging, {"parent": None, "schema_version": 1})
    monkeypatch.setenv("PROPOSAL_ROOT", str(root))
    return root / "demo" / "versions" / version


def _figures(version: Path, *, model: str | None) -> None:
    png = b"png bytes"
    _ = (version / "images" / "fig-s1-01.png").write_bytes(png)
    figure = FigureSpec(
        "fig-s1-01", "s1", ("public:c",), "diagram", "caption", hashlib.sha256(png).hexdigest(), 0
    )
    records = cast(list[dict[str, object]], json.loads(figures_to_json((figure,))))
    if model is not None:
        records[0]["model"] = model
    _ = (version / "figures.json").write_text(json.dumps(records), encoding="utf-8")
    drafts = json.dumps({"sections": [{"section_id": "s1", "body": "public research body"}]})
    _ = (version / "out" / "drafts.json").write_text(drafts, encoding="utf-8")
    _ = (version / "out" / "refine-report.json").write_text(
        json.dumps({"refined": False, "reason": "host-unauthenticated"}), encoding="utf-8"
    )


class _Renderer:
    def __init__(self) -> None:
        self.automation_importable: bool | None = None

    def __call__(self, **kwargs: Any) -> object:
        self.automation_importable = importlib.util.find_spec("automation.term_correction") is not None
        _ = Path(str(kwargs["out_path"])).write_bytes(b"hwpx")
        return object()


def test_render_refuses_figures_the_images_stage_did_not_make(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    version = _version(tmp_path, monkeypatch)
    _figures(version, model=None)

    with pytest.raises(SystemExit) as raised:
        _ = proposal_render.run_render("demo", renderer=_Renderer(), config=_config(tmp_path))

    assert raised.value.code == 5
    assert "UNGENERATED-FIGURES: fig-s1-01" in capsys.readouterr().err
    assert not (version / "out" / "proposal.hwpx").exists()


def test_ungenerated_figures_render_only_as_a_draft_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = _version(tmp_path, monkeypatch)
    _figures(version, model=None)

    result = proposal_render.run_render(
        "demo", allow_missing_figures=True, renderer=_Renderer(), config=_config(tmp_path)
    )

    assert result.draft_preview is True


def test_generated_figures_render_normally(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    version = _version(tmp_path, monkeypatch)
    _figures(version, model="gpt-image-2")

    result = proposal_render.run_render("demo", renderer=_Renderer(), config=_config(tmp_path))

    assert result.draft_preview is False


def test_render_makes_automation_importable_from_a_mounted_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = _version(tmp_path, monkeypatch)
    _figures(version, model="gpt-image-2")
    repo_entries = {str(_REPO), "", "."}
    # A cron bootstrap imported earlier in the session may have put an installed runtime
    # (/srv/autophagy-agent-current on the node) on sys.path; drop every package root.
    monkeypatch.setattr(sys, "path", [
        entry for entry in sys.path
        if entry not in repo_entries and not (Path(entry) / "automation").is_dir()
    ])
    for name in [name for name in sys.modules if name == "automation" or name.startswith("automation.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(_REPO))
    assert importlib.util.find_spec("automation") is None
    renderer = _Renderer()

    _ = proposal_render.run_render("demo", renderer=renderer, config=_config(tmp_path))

    assert renderer.automation_importable is True


def _seed_corpus(version: Path) -> None:
    gold = Path(orchestrator.PROJECT_ROOT) / "resource" / "gold"
    for source in gold.iterdir():
        if source.is_file():
            _ = shutil.copy(source, version / "corpus" / source.name)


def test_compose_drafts_the_version_from_its_corpus_and_drops_stale_refinement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    version = _version(tmp_path, monkeypatch)
    _seed_corpus(version)
    stale = version / "out" / "drafts.refined.json"
    _ = stale.write_text("{}", encoding="utf-8")
    _ = (version / "out" / "refine-report.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(engine_cli, "_build_llm", lambda **_kwargs: None)

    rc = proposal_cli.main(["compose", "--slug", "demo", "--profile", "10-page", "--json"])

    payload = cast(dict[str, object], json.loads(capsys.readouterr().out.splitlines()[-1]))
    assert rc == 0
    assert payload["version"] == version.name
    assert cast(int, payload["sections"]) > 0
    for name in ("drafts.json", "drafts.json.planspec.json", "drafts.json.pms.json"):
        assert (version / "out" / name).stat().st_mode & 0o777 == 0o600
    assert not stale.exists()
    assert not (version / "out" / "refine-report.json").exists()


def test_compose_refuses_an_empty_corpus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _ = _version(tmp_path, monkeypatch)

    rc = proposal_cli.main(["compose", "--slug", "demo", "--json"])

    assert rc == 1
    assert "corpus is empty" in capsys.readouterr().err


def test_a_failed_compose_leaves_the_previous_bundle_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = _version(tmp_path, monkeypatch)
    _seed_corpus(version)
    previous = {
        name: (version / "out" / name).write_bytes(name.encode()) and name.encode()
        for name in ("drafts.json", "drafts.json.planspec.json", "drafts.json.pms.json")
    }

    def failing_llm(**_kwargs: object) -> object:
        raise RuntimeError("hermes completion failed rc=1: 401")

    monkeypatch.setattr(engine_cli, "_build_llm", failing_llm)

    with pytest.raises(proposal_compose.ComposeError, match="401"):
        _ = proposal_compose.compose("demo", profile="10-page")

    assert {name: (version / "out" / name).read_bytes() for name in previous} == previous
    assert not list((version / "out").glob(".compose-*"))


def test_missing_kpi_evidence_names_the_planning_brief(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = _version(tmp_path, monkeypatch)
    _ = (version / "corpus" / "claim.md").write_text(
        "---\nsource_url: https://example.org/a\n---\n"
        + "굴착 작업의 실패 원인을 관측·토사·유압으로 나누어 진단한다.\n",
        encoding="utf-8",
    )

    class _EmptyPhrasing:
        def complete(self, _role: str, _prompt: str) -> str:
            return "{}"

    monkeypatch.setattr(engine_cli, "_build_llm", lambda **_kwargs: _EmptyPhrasing())

    with pytest.raises(proposal_compose.ComposeError, match="inputs/PLAN.md"):
        _ = proposal_compose.compose("demo", profile="10-page")


class _ConvertRunner:
    def __call__(self, argv: tuple[str, ...]) -> proposal_corpus.InvocationResult:
        if "research-convert" in argv:
            out = Path(argv[argv.index("--out") + 1])
            _ = (out / "research-00000001.md").write_text(
                "---\nsource_url: https://example.org/a\n---\nclaim\n",
                encoding="utf-8",
            )
        return proposal_corpus.InvocationResult(0, "", "")


def _synthesis(path: Path) -> Path:
    _ = path.write_text(
        "# Synthesis\n\n## Detailed Findings\n\nF.\n\n## External Sources\n\n"
        + "1. https://example.org/a\n\n## Verified Claims\n\n| Claim | Status | Source |\n"
        + "| --- | --- | --- |\n| C01: claim | CONFIRMED | https://example.org/a |\n",
        encoding="utf-8",
    )
    return path


def test_corpus_carries_the_planning_brief_as_public_evidence(tmp_path: Path) -> None:
    plan = tmp_path / "PLAN.md"
    _ = plan.write_text(
        "# 계획\n\n원인 분류 정확도; baseline: 60%; target: 80%; unit: %; weight: 40%; "
        + "method: 검증세트; env: 시험장으로 설정한다\n",
        encoding="utf-8",
    )
    corpus = tmp_path / "corpus"
    pack = proposal_knowledge.EvidencePack("goal", (), (), ())

    files = proposal_corpus.build_corpus(
        _synthesis(tmp_path / "SYNTHESIS.md"), corpus, pack, runner=_ConvertRunner(), plan=plan
    )

    brief = corpus / proposal_corpus.PLAN_BRIEF_NAME
    assert brief in files
    text = brief.read_text(encoding="utf-8")
    assert text.startswith("---\nsource: proposal-plan\n---\n")
    assert "baseline: 60%" in text


def test_owner_private_planning_brief_is_refused(tmp_path: Path) -> None:
    plan = tmp_path / "PLAN.md"
    _ = plan.write_text("obsidian: Projects/private.md\n비공개 메모\n", encoding="utf-8")
    pack = proposal_knowledge.EvidencePack("goal", (), (), ())

    with pytest.raises(RouteRefused):
        _ = proposal_corpus.build_corpus(
            _synthesis(tmp_path / "SYNTHESIS.md"),
            tmp_path / "corpus",
            pack,
            runner=_ConvertRunner(),
            plan=plan,
        )


def test_the_parallel_reviewer_pass_waits_as_long_as_one_hermes_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import inspect

    from skills.proposal.engine.agents.hermes_client import HermesLLMClient

    hermes_limit = inspect.signature(HermesLLMClient.__init__).parameters["timeout_s"].default
    monkeypatch.delenv(orchestrator.REVIEWER_PASS_TIMEOUT_ENV, raising=False)
    assert orchestrator._reviewer_pass_timeout() >= hermes_limit

    monkeypatch.setenv(orchestrator.REVIEWER_PASS_TIMEOUT_ENV, "90")
    assert orchestrator._reviewer_pass_timeout() == 90.0

    monkeypatch.setenv(orchestrator.REVIEWER_PASS_TIMEOUT_ENV, "not-a-number")
    assert orchestrator._reviewer_pass_timeout() >= hermes_limit



def test_render_drops_the_bare_section_marker_line() -> None:
    from skills.proposal.engine.contracts.models import SectionDraft

    marked = SectionDraft("1", "배경·필요성", "배경·필요성\n### 1-1. 기술적 배경\n본문이다.", [])
    kept = SectionDraft("0", "요약문", "요약문은 과제를 요약한다.", [])

    assert orchestrator._without_section_marker(marked).body == "### 1-1. 기술적 배경\n본문이다."
    assert orchestrator._without_section_marker(kept).body == "요약문은 과제를 요약한다."
