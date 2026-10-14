"""No-agent, reaction-only driver for owner-gated native-memory relocation."""

from __future__ import annotations

import fcntl
import json
import os
import re
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Protocol, TypeAlias

# Runtime root order (DG-4): AUTOPHAGY_REPO_ROOT override, else the release
# `current` symlink, else the resident mirror. Inlined by value because this
# wrapper sets sys.path BEFORE it can import automation.runtime_root.
def _runtime_root() -> Path:
    override = os.environ.get("AUTOPHAGY_REPO_ROOT", "").strip()
    if override:
        return Path(override).expanduser()
    current = Path("/srv/autophagy-agent-current")
    return current if current.exists() else Path("/srv/autophagy-agents")


_REPO_ROOT = _runtime_root()
if (_REPO_ROOT / "automation").is_dir() and str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from automation.memory_curator import _bootstrap as _bootstrap  # noqa: E402 - establishes repo imports first.
from automation.memory_relocate.effects_live import build_effects  # noqa: E402
from automation.memory_relocate.model import RelocationState, record_key  # noqa: E402
from automation.memory_relocate.store import load_state, save_state  # noqa: E402
from automation.memory_relocate.watch_step import ResolveResult, resolve_tick  # noqa: E402

ENV_SECRETS = Path.home() / ".env.secrets"
INTEROP_CONFIG = Path.home() / ".hermes" / "interop" / "config.json"
MEMORY_DIR = Path.home() / ".hermes" / "memories"
STATE_PATH = Path.home() / ".hermes" / "memory-curator" / "relocations.json"
RAG_STATE_PATH = Path.home() / ".hermes" / "rag-ingest" / "state.json"
LOCK_PATH = Path.home() / ".hermes" / "memory-relocate" / "watch.lock"
JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
_IN_FLIGHT: frozenset[str] = frozenset({"proposed", "posted", "approved", "written", "ingested"})
_BINDING_POLICY_VERSION = 6

_LONG_DIGITS = re.compile(r"\d{5,}")
_SECRET_VALUE = re.compile(r"(?i)(token|secret|password|key)=[^\s]+")


class JsonLoader(Protocol):
    def __call__(self, s: str) -> JsonValue: ...


_JSON_LOADS: JsonLoader = json.loads


class WatchError(RuntimeError):
    """Node configuration is insufficient for a fail-closed relocation tick."""


def _load_env_secrets(path: Path = ENV_SECRETS) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw_line in lines:
        key, separator, value = raw_line.strip().partition("=")
        if separator and key and not key.startswith("#") and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def _owner_id(path: Path = INTEROP_CONFIG) -> str:
    try:
        payload = _JSON_LOADS(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WatchError("interop owner configuration is unavailable") from error
    if not isinstance(payload, dict):
        raise WatchError("interop owner configuration is malformed")
    owner_id = payload.get("owner_id")
    if not isinstance(owner_id, str) or not owner_id:
        raise WatchError("interop owner configuration has no owner id")
    return owner_id


def acquire_single_instance_lock(lock_path: Path = LOCK_PATH) -> IO[str] | None:
    """Return a non-blocking watcher flock or ``None`` for an overlapping tick."""
    lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    handle = lock_path.open("a", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def _discover_and_propose(state: RelocationState, now: datetime) -> RelocationState:
    """Let the node find its OWN next candidate when nothing is in flight.

    Without this, reclamation only advances when a human runs the CLI.  Classifying and
    proposing is not an external effect — the proposal still has to win cha's ✅ before a
    single byte is written or deleted — so the node may do it unattended.  The classifier
    authenticates through Codex OAuth like every other non-interactive path; an unavailable
    tier leaves the state untouched rather than proposing from a downgraded model.  One at a time:
    a new candidate is only sought while no relocation is pending, which also caps the owner
    to one approval DM per cycle.  Any classifier failure leaves the state untouched.
    """
    if any(record.status in _IN_FLIGHT for record in state.relocations.values()):
        return state
    try:
        from automation.memory_relocate.verdict_cache import classify_with_cache
        from automation.memory_curator.watch_steps import read_native
        from automation.memory_relocate.discover import select_batch, select_candidate
        from automation.memory_relocate.propose import build_batch, build_proposed_record, card_fits
        from automation.twin_distill.llm import CodexLlmClient

        files = {kind: read_native(MEMORY_DIR, kind)[1] for kind in ("memory", "user")}
        verdicts = classify_with_cache(
            {kind: files[kind].entries for kind in ("memory", "user")},
            client=CodexLlmClient.from_environment(os.environ),
            path=STATE_PATH.parent / "classify-cache.json",
        )
        known = frozenset(record.entry_sha256 for record in state.relocations.values())
        batch = select_batch(verdicts, files, known)
        if batch:
            members = [
                (
                    build_proposed_record(
                        found.candidate.entry_text,
                        source_kind=found.candidate.source_kind,
                        entry_sha256=found.candidate.entry_sha256,
                        reclaimable_chars=found.candidate.reclaimable_chars,
                        binding_kind="obsidian-write",
                        binding_surface="owner-dm",
                        binding_channel_id="",
                        binding_policy_version=_BINDING_POLICY_VERSION,
                        now=now,
                        destination=found.destination,
                    ),
                    found.candidate.entry_text,
                )
                for found in batch
            ]
            records = build_batch(members, fits=card_fits)
            if records:
                added = {record_key(record.source_kind, record.entry_sha256): record for record in records}
                return replace(state, relocations={**dict(state.relocations), **added})
        candidate = select_candidate(verdicts, files, known)
        if candidate is None:
            return state
        record = build_proposed_record(
            candidate.entry_text,
            source_kind=candidate.source_kind,
            entry_sha256=candidate.entry_sha256,
            reclaimable_chars=candidate.reclaimable_chars,
            binding_kind="obsidian-write",
            binding_surface="owner-dm",
            binding_channel_id="",
            binding_policy_version=_BINDING_POLICY_VERSION,
            now=now,
        )
    except Exception:  # noqa: BLE001 - discovery is best-effort; a bad tick must not block resolve
        return state
    key = record_key(record.source_kind, record.entry_sha256)
    return replace(state, relocations={**dict(state.relocations), key: record})


def _merge_effect_bindings(
    before: RelocationState,
    result: ResolveResult,
    persisted: RelocationState,
) -> ResolveResult:
    relocations = dict(result.state.relocations)
    for key, resolved in relocations.items():
        initial = before.relocations.get(key)
        current = persisted.relocations.get(key)
        if initial is None or current is None:
            continue
        if initial.action_hash != current.action_hash or resolved.action_hash != current.action_hash:
            continue
        initial_binding = initial.message_id, initial.channel_id
        current_binding = current.message_id, current.channel_id
        if current_binding != initial_binding or current.approval_guild_id != initial.approval_guild_id:
            relocations[key] = replace(
                resolved,
                message_id=current.message_id,
                channel_id=current.channel_id,
                approval_guild_id=current.approval_guild_id,
            )
        if current.render_version != initial.render_version:
            relocations[key] = replace(relocations[key], render_version=current.render_version)
    return replace(
        result,
        state=RelocationState(result.state.version, relocations),
    )


def run_once(now: datetime) -> ResolveResult:
    """Run one state-machine tick and persist its resulting immutable snapshot."""
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not token:
        raise WatchError("Discord token is unavailable")
    state = _discover_and_propose(load_state(STATE_PATH), now)
    # Checkpoint the proposal BEFORE anything posts: the approval gate binds the message id
    # through the on-disk store, so posting a record that exists only in memory posts a real
    # DM the record can never hold — an orphan, and a posting journal that outruns the state.
    save_state(STATE_PATH, state)
    result = resolve_tick(state, effects=build_effects(memory_dir=MEMORY_DIR, state_path=STATE_PATH, rag_state_path=RAG_STATE_PATH, token=token, owner_id=_owner_id(), now=now), max_posts=1)
    result = _merge_effect_bindings(state, result, load_state(STATE_PATH))
    save_state(STATE_PATH, result.state)
    return result


def _summary(result: ResolveResult) -> str | None:
    if not (result.posted or result.written or result.reconciled or result.abandoned):
        return None
    return f"memory-relocate: posted={len(result.posted)} written={len(result.written)} reconciled={len(result.reconciled)} abandoned={len(result.abandoned)}"


def _masked_error(error: Exception) -> str:
    secret_safe = _SECRET_VALUE.sub(r"\1=[MASKED]", str(error))
    return _LONG_DIGITS.sub("[MASKED-NUM]", secret_safe)[:300]


def main() -> int:
    """Run quietly unless the tick changes state or requires operator attention."""
    try:
        _load_env_secrets()
        lock = acquire_single_instance_lock()
        if lock is None:
            return 0
        with lock:
            summary = _summary(run_once(datetime.now(UTC)))
        if summary is not None:
            print(summary)
    except Exception as error:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK - final cron alert boundary
        print(f"memory-relocate-watch error: {_masked_error(error)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
