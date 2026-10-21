"""Thin proposal adapter for the read-only knowledge facade."""

from __future__ import annotations

import importlib
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, ClassVar, Literal, Protocol, cast

if TYPE_CHECKING:
    from automation.knowledge.pack import EvidencePack as FacadeEvidencePack
    from automation.knowledge.pack import KnowledgeQuery


_FACADE_SOURCES = frozenset({"rag", "wiki", "twin"})


class _QueryFactory(Protocol):
    def __call__(
        self, text: str, *, purpose: str,
        sources: frozenset[str] = _FACADE_SOURCES,
        limit: int, caller: str,
    ) -> KnowledgeQuery: ...


class _Facade(Protocol):
    KnowledgeQuery: _QueryFactory

    def collect_evidence(self, query: KnowledgeQuery) -> FacadeEvidencePack: ...


@dataclass(frozen=True, slots=True)
class _UnavailableQuery:
    text: str
    purpose: str = "synthesize"
    sources: frozenset[str] = frozenset({"rag", "wiki", "twin"})
    tags: frozenset[str] = frozenset()
    limit: int = 8
    caller: str = "proposal"


@dataclass(frozen=True, slots=True)
class _UnavailablePack:
    version: str
    query: _UnavailableQuery
    verdict: str
    items: tuple[()]
    layers: dict[str, str]
    notes: tuple[str, ...]


Bucket = Literal["rag", "wiki-twin", "obsidian"]
_BUCKETS: tuple[Bucket, ...] = ("rag", "wiki-twin", "obsidian")


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    source_key: str
    bucket: Bucket
    summary: str
    score: float | None
    week: str | None
    doc_date: str | None = None
    date_basis: str | None = None
    source_sha256: str | None = None
    content: str | None = None


@dataclass(frozen=True, slots=True)
class EvidencePack:
    goal: str
    items: tuple[EvidenceItem, ...]
    unavailable: tuple[str, ...]
    notes: tuple[str, ...]

    def by_bucket(self) -> dict[str, tuple[EvidenceItem, ...]]:
        return {
            bucket: tuple(item for item in self.items if item.bucket == bucket)
            for bucket in _BUCKETS
        }

    def has_evidence(self) -> bool:
        return bool(self.items)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True, slots=True)
class _FakeFacadeItem:
    store: str
    source_type: str
    ref: str
    content: str
    score: float | None = 0.9


@dataclass(frozen=True, slots=True)
class _FakeFacadePack:
    items: tuple[_FakeFacadeItem, ...]
    layers: dict[str, str]


class _BuiltInFakeFacade:
    KnowledgeQuery: ClassVar[type[_UnavailableQuery]] = _UnavailableQuery

    @staticmethod
    def collect_evidence(query: _UnavailableQuery) -> _FakeFacadePack:
        if query.sources == frozenset({"rag"}):
            items = (
                _FakeFacadeItem("rag", "note", "FAKE/personal.md", "FAKE 개인 RAG 근거"),
                _FakeFacadeItem("obsidian", "obsidian", "FAKE/demo.md", "FAKE Obsidian 근거"),
            )
            return _FakeFacadePack(items, {"rag": "hit", "wiki": "skipped", "twin": "skipped"})
        items = (_FakeFacadeItem("wiki", "twin", "FAKE/decision", "FAKE wiki 트윈 판단"),)
        return _FakeFacadePack(items, {"rag": "skipped", "wiki": "hit", "twin": "hit"})


def _repo_root() -> Path:
    override = os.environ.get("AUTOPHAGY_REPO_ROOT")
    if override:
        return Path(override).expanduser()
    here = Path(__file__).resolve()
    candidates = (*here.parents[2:6], Path("/srv/autophagy-agent-current"), Path("/srv/autophagy-agents"))
    for candidate in candidates:
        if (candidate / "automation" / "knowledge").is_dir():
            return candidate
    return Path("/srv/autophagy-agent-current")


def ensure_repo_on_path() -> Path:
    """Make the checkout carrying ``automation`` importable and return it.

    A mounted skill lives at ``releases/proposal/<digest>``, where no parent holds the
    repository, so a bare ``import automation`` fails there (2026-09-28: every render
    printed ``TERM-CORRECTION-SKIP reason=ImportError``).
    """
    root = _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


def module(name: str) -> ModuleType:
    """Load one shared knowledge module from the configured repository checkout."""
    _ = ensure_repo_on_path()
    return importlib.import_module(name)


def collect(section_title: str, brief: str, proposal_title: str, *, limit: int = 8) -> FacadeEvidencePack | _UnavailablePack:
    """Build the bounded proposal query and collect exactly one facade pack."""
    text = "\n".join((section_title.strip(), brief.strip(), proposal_title.strip()))
    try:
        facade = cast(_Facade, cast(object, module("automation.knowledge.facade")))
        query = facade.KnowledgeQuery(text, purpose="synthesize", limit=min(limit, 8), caller="proposal")
        return facade.collect_evidence(query)
    except ImportError:
        query = _UnavailableQuery(text, limit=min(limit, 8))
        layers = {"rag": "unavailable", "wiki": "unavailable", "twin": "unavailable"}
        return _UnavailablePack(
            "knowledge-v1", query, "unavailable", (), layers, ("knowledge facade import unavailable",)
        )


def _source_key(item: object) -> str:
    ref = str(getattr(item, "ref", "") or "")
    store = str(getattr(item, "store", "") or "")
    source_type = str(getattr(item, "source_type", "") or "")
    if ref.startswith(("obsidian:", "wiki:", "note:")):
        return ref
    if store == "obsidian":
        return f"obsidian:{ref}"
    if store == "wiki" or source_type in {"twin", "decision", "principle"}:
        return f"wiki:{ref}"
    if source_type and source_type != "rag":
        return f"{source_type}:{ref}"
    return ref


def _bucket(source_key: str, item: object) -> Bucket:
    if source_key.startswith("obsidian:"):
        return "obsidian"
    if source_key.startswith("wiki:") or str(getattr(item, "source_type", "")) in {
        "twin", "decision", "principle",
    }:
        return "wiki-twin"
    return "rag"


def _normalize(items: object) -> tuple[EvidenceItem, ...]:
    if not isinstance(items, (list, tuple)):
        return ()
    normalized: list[EvidenceItem] = []
    for item in cast(list[object] | tuple[object, ...], items):
        source_key = _source_key(item)
        bucket = _bucket(source_key, item)
        score = getattr(item, "score", None)
        content = cast(object | None, getattr(item, "content", None))
        summary = cast(object | None, getattr(item, "summary", None))
        doc_date = cast(object | None, getattr(item, "doc_date", None))
        date_basis = cast(object | None, getattr(item, "date_basis", None))
        source_sha256 = cast(object | None, getattr(item, "sha256", None))
        normalized.append(EvidenceItem(
            source_key=source_key,
            bucket=bucket,
            summary=str(summary if summary is not None else content or ""),
            score=float(score) if isinstance(score, (int, float)) else None,
            week=None,
            doc_date=str(doc_date) if doc_date is not None else None,
            date_basis=str(date_basis) if date_basis is not None else None,
            source_sha256=(
                str(source_sha256) if source_sha256 is not None else None
            ),
            content=str(content) if content is not None else None,
        ))
    return tuple(normalized)


def gather_owner_evidence(
    goal: str, *, section: str | None = None, limit: int = 8,
    knowledge: object | None = None,
) -> EvidencePack:
    """Gather owner evidence only through the shared read-only knowledge facade."""
    text = "\n".join(part for part in (goal.strip(), (section or "").strip()) if part)
    if not text:
        raise ValueError("goal must not be empty")
    unavailable: list[str] = []
    notes: list[str] = []
    items: list[EvidenceItem] = []
    try:
        raw_facade = (
            _BuiltInFakeFacade()
            if os.environ.get("KNOWLEDGE_FAKE_PACK") == "1"
            else knowledge or module("automation.knowledge.facade")
        )
        facade = cast(_Facade, raw_facade)
    except (ImportError, OSError):
        facade = None

    queries = (
        (("rag", "obsidian"), "synthesize", frozenset({"rag"})),
        (("wiki-twin",), "judgment", frozenset({"wiki", "twin"})),
    )
    for affected, purpose, sources in queries:
        if facade is None:
            failed = True
        else:
            try:
                query = facade.KnowledgeQuery(
                    text, purpose=purpose, sources=sources,
                    limit=max(1, min(limit, 8)), caller="proposal",
                )
                result = facade.collect_evidence(query)
                failed = any(result.layers.get(source) == "unavailable" for source in sources)
                items.extend(_normalize(result.items))
            except Exception:  # Facade is the fail-closed system boundary; never search directly.
                failed = True
        if failed:
            for bucket in affected:
                unavailable.append(bucket)
                notes.append(f"근거 수집 불가: {bucket}")

    selected = tuple(items)
    if not selected:
        notes.append("근거 없음")
    return EvidencePack(
        goal.strip(), selected, tuple(dict.fromkeys(unavailable)), tuple(dict.fromkeys(notes)),
    )
