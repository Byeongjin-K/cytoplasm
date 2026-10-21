"""Build an atomic, lint-gated proposal corpus from research and owner summaries."""

from __future__ import annotations

import argparse
import hashlib
import io
import ipaddress
import json
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from . import proposal_config, proposal_knowledge, proposal_research, proposal_version
from .proposal_route_guard import RouteRefused, assert_route_allowed

_BRIEF_ITEM = re.compile(
    r"^- source_key=(.*?); bucket=([^;]+); "
    + r"(?:([^;]+=[^;]+); )?summary=(.*)$"
)


class CorpusError(RuntimeError):
    """Corpus construction or its external converter failed."""


class CorpusLintError(CorpusError):
    """The hard corpus lint gate rejected the candidate web evidence."""


@dataclass(frozen=True, slots=True)
class InvocationResult:
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[tuple[str, ...]], InvocationResult]


def _invoke(argv: tuple[str, ...]) -> InvocationResult:
    """Run an engine subcommand in this process; the engine ships in this repository."""
    from ..engine.pipeline.cli import main as engine_main

    stdout, stderr = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            returncode = engine_main(list(argv))
    except (OSError, ValueError, RuntimeError) as error:
        raise CorpusError(f"engine invocation failed: {error.__class__.__name__}") from error
    return InvocationResult(returncode, stdout.getvalue(), stderr.getvalue())


def _pack_from_brief(path: Path) -> proposal_knowledge.EvidencePack:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise CorpusError(f"owner evidence snapshot is unavailable: {error.__class__.__name__}") from error
    items: list[proposal_knowledge.EvidenceItem] = []
    for line in lines:
        match = _BRIEF_ITEM.fullmatch(line)
        if match is None:
            continue
        source_key, bucket, _, summary = match.groups()
        if bucket not in {"rag", "wiki-twin", "obsidian"}:
            raise CorpusError(f"invalid owner evidence bucket: {bucket}")
        items.append(proposal_knowledge.EvidenceItem(
            source_key,
            cast(proposal_knowledge.Bucket, bucket),
            summary,
            None,
            None,
        ))
    return proposal_knowledge.EvidencePack("snapshot", tuple(items), (), ())


def _safe_source_key(source_key: str) -> str:
    if not source_key or "\n" in source_key or "\r" in source_key:
        raise CorpusError("owner evidence has an invalid source key")
    return source_key


def _write_owner_evidence(
    directory: Path, pack: proposal_knowledge.EvidencePack
) -> tuple[Path, ...]:
    paths: list[Path] = []
    for item in pack.items:
        source_key = _safe_source_key(item.source_key)
        digest = hashlib.sha256(source_key.encode("utf-8")).hexdigest()[:8]
        path = directory / f"owner-{digest}.md"
        content = "".join((
            "---\n",
            f"source_key: {source_key}\n",
            "---\n",
            f"{item.summary}\n",
        ))
        _ = path.write_text(content, encoding="utf-8")
        path.chmod(0o600)
        paths.append(path)
    return tuple(paths)


def _is_public_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or hostname is None:
        return False
    try:
        return ipaddress.ip_address(hostname).is_global
    except ValueError:
        return "." in hostname and not hostname.endswith(".local")


def _check_claims(synthesis: Path) -> tuple[proposal_research.Claim, ...]:
    try:
        claims = proposal_research.parse_claims(synthesis.read_text(encoding="utf-8"))
    except OSError as error:
        raise CorpusError(f"cannot read SYNTHESIS.md: {error.__class__.__name__}") from error
    if not claims:
        raise CorpusError("SYNTHESIS.md has no CONFIRMED claims")
    for claim in claims:
        if not _is_public_url(claim.url):
            raise proposal_research.ResearchValidationError(
                claim.line, f"CONFIRMED URL is not publicly eligible: {claim.url}"
            )
        _ = assert_route_allowed(claim.text, "render", classification="public")
    return claims


PLAN_BRIEF_NAME = "plan-brief.md"


def _write_plan_brief(directory: Path, plan: Path | None) -> None:
    """Carry the author's planning brief (KPI lines, schedule, TRL) into the corpus.

    The engine planner never invents numbers: KPIs, work-package months and TRL come
    only from evidence text. The v000011 reference got them from curated planning
    notes; a corpus of web claims and owner one-liners has none, so ``compose`` stops
    with "No public KPI evidence". The brief renders into the document, so it must
    clear the same render route as any claim.
    """
    if plan is None or not plan.is_file() or plan.is_symlink():
        return
    try:
        text = plan.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as error:
        raise CorpusError(f"planning brief is unreadable: {error.__class__.__name__}") from error
    if not text:
        return
    _ = assert_route_allowed(text, "render")
    body = text.split("\n---\n", 1)[1].lstrip() if text.startswith("---\n") else text
    path = directory / PLAN_BRIEF_NAME
    _ = path.write_text(
        f"---\nsource: proposal-plan\n---\n{body}\n", encoding="utf-8"
    )
    path.chmod(0o600)


def build_corpus(
    synthesis: Path,
    corpus: Path,
    pack: proposal_knowledge.EvidencePack,
    *,
    runner: Runner | None = None,
    plan: Path | None = None,
) -> tuple[Path, ...]:
    """Convert and lint web claims, then atomically publish web and owner summaries."""
    _ = _check_claims(synthesis)
    if corpus.exists():
        if corpus.is_symlink() or not corpus.is_dir():
            raise CorpusError("corpus target is not a regular directory")
        shutil.rmtree(corpus)
    corpus.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = Path(tempfile.mkdtemp(prefix=".corpus-", dir=corpus.parent))
    candidate = temporary / "candidate"
    baseline = temporary / "baseline"
    output = temporary / "output"
    for directory in (candidate, baseline, output):
        directory.mkdir(mode=0o700)
    execute = runner or _invoke
    try:
        converted = execute(
            (
                "research-convert", str(synthesis),
                "--out", str(candidate),
            )
        )
        if converted.returncode != 0:
            raise CorpusError(f"research-convert failed rc={converted.returncode}")
        linted = execute(
            (
                "corpus-lint", "--corpus", str(baseline),
                "--candidate-dir", str(candidate),
            )
        )
        if linted.returncode != 0:
            raise CorpusLintError(f"corpus-lint failed rc={linted.returncode}")
        web = sorted(candidate.glob("research-*.md"))
        if not web:
            raise CorpusError("research-convert emitted no corpus files")
        for path in web:
            destination = output / path.name
            os.replace(path, destination)
            destination.chmod(0o600)
        _ = _write_owner_evidence(output, pack)
        _write_plan_brief(output, plan)
        os.replace(output, corpus)
        corpus.chmod(0o700)
        return tuple(sorted(corpus.glob("*.md")))
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def command(args: argparse.Namespace, *, runner: Runner | None = None) -> int:
    """Execute the proposal CLI corpus subcommand."""
    corpus: Path | None = None
    try:
        slug = cast(str, args.slug)
        json_output = cast(bool, args.json)
        store = proposal_version.VersionStore.from_environment()
        version = store.head(slug)
        if version is None:
            raise CorpusError("proposal has no current version")
        version_dir = store.resolve_slug_dir(slug) / "versions" / version
        inputs = version_dir / "inputs"
        corpus = version_dir / "corpus"
        _ = proposal_research.validate_synthesis(inputs / "SYNTHESIS.md")
        pack = _pack_from_brief(inputs / "RESEARCH_BRIEF.md")
        files = build_corpus(
            inputs / "SYNTHESIS.md", corpus, pack, runner=runner, plan=inputs / "PLAN.md"
        )
        payload = {
            "corpus": str(corpus),
            "files": [str(path) for path in files],
            "slug": slug,
            "version": version,
        }
        print(json.dumps(payload, sort_keys=True) if json_output else payload)
        return 0
    except (CorpusLintError, proposal_research.ResearchValidationError, RouteRefused) as error:
        if corpus is not None and corpus.is_dir() and not corpus.is_symlink():
            shutil.rmtree(corpus)
        print(f"CORPUS-VALIDATION {error}", file=sys.stderr)
        return 3
    except (CorpusError, proposal_config.ConfigError, proposal_version.VersionError) as error:
        print(f"CORPUS-ERROR {error}", file=sys.stderr)
        return 1
