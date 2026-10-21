"""Collect private proposal input deltas without copying raw text into metadata."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Final, Iterable, Literal, cast

from . import proposal_knowledge, proposal_version

SourceType = Literal["meeting", "note", "obsidian", "wiki"]
_SOURCE_TYPES: Final = frozenset({"meeting", "note", "obsidian", "wiki"})
_VERSION_PREFIX: Final = "v"


class DeltaError(RuntimeError):
    """Delta discovery or snapshotting violated its private boundary."""


@dataclass(frozen=True, slots=True)
class DeltaSource:
    """One immutable source payload returned by the knowledge seam.

    ``source_sha256`` identifies the facade's source document version for ledger
    deduplication. The digest exposed by ``DeltaItem`` and INDEX.json always
    identifies the bytes actually stored in ``delta/raw``.
    """

    source_type: SourceType
    source_key: str
    content: bytes
    updated_after: str | None = None
    source_sha256: str | None = None
    doc_date: str | None = None
    date_basis: str | None = None
    payload: Literal["full", "excerpt"] = "full"


@dataclass(frozen=True, slots=True)
class DeltaSkip:
    source_key: str
    sha256: str
    reason: str


@dataclass(frozen=True, slots=True)
class DeltaItem:
    source_type: SourceType
    source_key: str
    sha256: str
    path: str


@dataclass(frozen=True, slots=True)
class DeltaReport:
    slug: str
    since_version: str
    destination: Path
    collected: tuple[DeltaItem, ...]
    skipped: tuple[DeltaSkip, ...]

    @property
    def collected_count(self) -> int:
        return len(self.collected)

    @property
    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.collected:
            counts[item.source_type] = counts.get(item.source_type, 0) + 1
        return counts

    def payload(self) -> dict[str, object]:
        return {
            "collected": self.collected_count,
            "counts": self.counts,
            "destination": str(self.destination),
            "sha256": [item.sha256 for item in self.collected],
            "skipped": [asdict(item) for item in self.skipped],
            "slug": self.slug,
        }


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()


def _atomic_write(path: Path, content: bytes) -> None:
    if path.is_symlink():
        raise DeltaError(f"refusing to replace symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _read_index(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    if path.is_symlink() or not path.is_file():
        raise DeltaError(f"delta index is not a regular file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DeltaError(f"delta index is malformed: {path}") from error
    if not isinstance(value, list) or not all(
        isinstance(entry, dict) for entry in value
    ):
        raise DeltaError(f"delta index is malformed: {path}")
    return cast(list[dict[str, object]], value)


def _read_ledger(path: Path) -> tuple[set[str], str | None]:
    if not path.exists():
        return set(), None
    if path.is_symlink() or not path.is_file():
        raise DeltaError("delta ledger is not a regular file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DeltaError("delta ledger is malformed") from error
    hashes = value.get("sha256") if isinstance(value, dict) else None
    if not isinstance(hashes, list) or not all(
        isinstance(item, str) for item in hashes
    ):
        raise DeltaError("delta ledger is malformed")
    collected_at = value.get("collected_at")
    if collected_at is not None and not isinstance(collected_at, str):
        raise DeltaError("delta ledger is malformed")
    return set(cast(list[str], hashes)), cast(str | None, collected_at)


def _source_type(item: proposal_knowledge.EvidenceItem) -> SourceType:
    if item.bucket == "obsidian":
        return "obsidian"
    if item.bucket == "wiki-twin":
        return "wiki"
    # Meeting-produced facade records use an explicit source type, which
    # proposal_knowledge preserves as a ``meeting:`` source-key prefix.
    if item.source_key.startswith("meeting:"):
        return "meeting"
    return "note"


def _discover(knowledge: object | None) -> tuple[DeltaSource, ...]:
    pack = proposal_knowledge.gather_owner_evidence(
        "proposal improvement delta collection",
        limit=8,
        knowledge=knowledge,
    )
    discovered: list[DeltaSource] = []
    for item in pack.items:
        content = item.content if item.content is not None else item.summary
        discovered.append(
            DeltaSource(
                _source_type(item),
                item.source_key,
                content.encode("utf-8"),
                source_sha256=item.source_sha256,
                doc_date=item.doc_date,
                date_basis=item.date_basis,
                payload="full" if item.content is not None else "excerpt",
            )
        )
    return tuple(discovered)


def _version_number(version: str) -> int:
    if (
        len(version) != 7
        or not version.startswith(_VERSION_PREFIX)
        or not version[1:].isdigit()
    ):
        raise DeltaError(f"invalid since version: {version}")
    return int(version[1:])


def _valid_source(source: DeltaSource) -> bool:
    source_digest = source.source_sha256
    return (
        source.source_type in _SOURCE_TYPES
        and bool(source.content)
        and bool(source.source_key)
        and len(source.source_key) <= 512
        and (
            source_digest is None
            or len(source_digest) == 64
            and all(character in "0123456789abcdef" for character in source_digest)
        )
    )


def _timestamp_date(value: object) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None


def _collection_cutoff(
    entries: Iterable[dict[str, object]], ledger_collected_at: str | None,
) -> date | None:
    dates = [
        parsed
        for parsed in (
            *(_timestamp_date(entry.get("collected_at")) for entry in entries),
            _timestamp_date(ledger_collected_at),
        )
        if parsed is not None
    ]
    return max(dates, default=None)


def _is_stale(source: DeltaSource, since_number: int, cutoff: date | None) -> bool:
    if source.updated_after is not None:
        return _version_number(source.updated_after) < since_number
    if source.doc_date is None or source.date_basis in {None, "", "none"}:
        return False
    source_date = _timestamp_date(source.doc_date)
    # Freshness fails open when the facade has no usable date; ledger identity
    # still prevents repeated collection of the same source version.
    return source_date is not None and cutoff is not None and source_date < cutoff


def _raw_name(source: DeltaSource, digest: str) -> str:
    suffix = Path(source.source_key).suffix.lower()
    extension = suffix if suffix in {".md", ".txt", ".json"} else ".bin"
    return f"{source.source_type}-{digest}{extension}"


def collect_deltas(
    slug: str,
    *,
    since_version: str,
    dest_dir: Path,
    knowledge: object | None = None,
    sources: Iterable[DeltaSource] | None = None,
) -> DeltaReport:
    """Snapshot new facade items into ``dest_dir/delta`` and update the slug ledger.

    Dated facade items older than the since-version/ledger collection date are
    stale. Missing or malformed dates fail open; source-identity dedup remains.
    Raw bytes are written before their INDEX entry is made durable.
    """
    since_number = _version_number(since_version)
    store = proposal_version.VersionStore.from_environment()
    slug_root = store.resolve_slug_dir(slug)
    destination = dest_dir.expanduser().resolve()
    try:
        destination.relative_to(slug_root.resolve())
    except ValueError as error:
        raise DeltaError("delta destination escapes the proposal slug root") from error
    if destination.is_symlink():
        raise DeltaError("delta destination must not be a symlink")

    offered = tuple(sources) if sources is not None else _discover(knowledge)
    index_path = destination / "delta" / "INDEX.json"
    entries = _read_index(index_path)
    prior_path = slug_root / "versions" / since_version / "delta" / "INDEX.json"
    prior_entries = _read_index(prior_path)
    ledger_path = slug_root / "delta-ledger.json"
    ledger_hashes, ledger_collected_at = _read_ledger(ledger_path)
    recorded_hashes = {
        str(entry.get("sha256"))
        for entry in (*prior_entries, *entries)
        if entry.get("sha256")
    }
    blocked_hashes = set(ledger_hashes)
    cutoff = _collection_cutoff(prior_entries, ledger_collected_at)
    collected_at = (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )
    collected: list[DeltaItem] = []
    skipped: list[DeltaSkip] = []
    new_entries: list[dict[str, object]] = []
    collected_identities: set[str] = set()

    for source in offered:
        stored_digest = _sha256(source.content)
        identity_digest = source.source_sha256 or stored_digest
        if not _valid_source(source):
            skipped.append(
                DeltaSkip(source.source_key, identity_digest, "INVALID-DELTA")
            )
            continue
        if _is_stale(source, since_number, cutoff):
            skipped.append(DeltaSkip(source.source_key, identity_digest, "STALE-DELTA"))
            continue
        if identity_digest in blocked_hashes or stored_digest in recorded_hashes:
            skipped.append(
                DeltaSkip(source.source_key, identity_digest, "DUPLICATE-DELTA")
            )
            continue
        relative = f"delta/raw/{_raw_name(source, stored_digest)}"
        _atomic_write(destination / relative, source.content)
        new_entries.append(
            {
                "source_key": source.source_key,
                "sha256": stored_digest,
                "collected_at": collected_at,
                # The fixed four-field INDEX contract has no payload field.
                # This sentinel marks the only lossy fallback without exposing
                # raw text or breaking proposal_publish/route validation.
                "sections": ["payload:excerpt"] if source.payload == "excerpt" else [],
            }
        )
        collected.append(
            DeltaItem(source.source_type, source.source_key, stored_digest, relative)
        )
        blocked_hashes.add(identity_digest)
        recorded_hashes.add(stored_digest)
        collected_identities.add(identity_digest)

    _atomic_write(index_path, _json_bytes([*entries, *new_entries]))
    if collected:
        _atomic_write(
            ledger_path,
            _json_bytes(
                {
                    "collected_at": collected_at,
                    "sha256": sorted(ledger_hashes | collected_identities),
                }
            ),
        )
    return DeltaReport(
        slug, since_version, destination, tuple(collected), tuple(skipped)
    )
