#!/usr/bin/env python3
"""No-agent cron: discover Plaud recordings and drive lifelog notes.

Watcher contract: only Discord REACTIONS are polled (규약 a) — the Plaud cloud
poll is a proposal source like the relocate classifier. By default each note needs
an owner card; a release-authorized Plaud auto-write policy replaces that card.
`~/.env.secrets` is self-loaded (b), the
repo import goes through the runtime-root resolver (c), the deployed file name
is skill-unique (e), and records advance only after the effect succeeded (f).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path


def _runtime_root() -> Path:
    override = os.environ.get("AUTOPHAGY_REPO_ROOT", "").strip()
    if override:
        return Path(override).expanduser()
    current = Path("/srv/autophagy-agent-current")
    return current if current.exists() else Path("/srv/autophagy-agents")


_REPO_ROOT = _runtime_root()
if (_REPO_ROOT / "automation").is_dir() and str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from automation.interop.approval_surface import POLICY_VERSION  # noqa: E402
from automation.plaud_sync.duration import DEFAULT_MIN_DURATION_MS  # noqa: E402
from automation.plaud_sync.lifelog_extract_live import build_extractor  # noqa: E402
from automation.plaud_sync import watch_runtime  # noqa: E402
from automation.plaud_sync.model import PlaudSyncState  # noqa: E402
from automation.plaud_sync.store import (  # noqa: E402
    PlaudSyncStore,
    load_state,
    save_note_body,
    save_state,
)
from automation.plaud_sync.sync import plan_new_records, poll_due  # noqa: E402
from automation.plaud_sync import terms, transcribe_live  # noqa: E402
from automation.plaud_sync.watch_step import ResolveResult, resolve_tick  # noqa: E402

ENV_SECRETS = watch_runtime.ENV_SECRETS
INTEROP_CONFIG = watch_runtime.INTEROP_CONFIG
STATE_DIR = watch_runtime.STATE_DIR
STATE_PATH = watch_runtime.STATE_PATH
LOCK_PATH = watch_runtime.LOCK_PATH
JsonLoader = watch_runtime.JsonLoader
WatchError = watch_runtime.WatchError
_load_env_secrets = watch_runtime.load_env_secrets
_owner_id = watch_runtime.owner_id
acquire_single_instance_lock = watch_runtime.acquire_single_instance_lock
_env_int = watch_runtime.env_int
_note_timezone = watch_runtime.note_timezone
_masked_error = watch_runtime.masked_error


def _discover(state: PlaudSyncState, now: datetime) -> PlaudSyncState:
    """Fetch new recordings when the poll is due; a bad tick must not block resolve."""
    if not poll_due(state, now, _env_int("PLAUD_SYNC_POLL_SECONDS", 1800)):
        return state
    try:
        from automation.plaud_sync.fetch import fetch_recordings
        from automation.plaud_sync.mcp_client import PlaudMcpClient

        lookback = _env_int("PLAUD_SYNC_LOOKBACK_DAYS", 14)
        date_from = (now - timedelta(days=lookback)).date().isoformat()
        with PlaudMcpClient() as client:
            recordings = fetch_recordings(client, date_from=date_from)
        initial_status = "transcribing" if transcribe_live.enabled(os.environ) else "planned"
        result = plan_new_records(
            state,
            recordings,
            now=now,
            policy_version=POLICY_VERSION,
            extractor=build_extractor(os.environ, repo_root=_REPO_ROOT),
            tz=_note_timezone(),
            initial_status=initial_status,
            glossary=terms.glossary(),
            min_duration_ms=_env_int("PLAUD_SYNC_MIN_DURATION_MS", DEFAULT_MIN_DURATION_MS),
        )
        for recording_id, body in result.bodies.items():
            save_note_body(STATE_DIR, recording_id, body)
        if initial_status == "planned":
            # 'transcribing' 으로 언 것은 초안이고 transcribe.finalize 가 로컬 전사로 다시 언다 —
            # 그 초안까지 적으면 같은 녹음이 로그에 두 번 남아 오탐을 되짚기 어려워진다.
            for label, corrections in result.corrections:
                _ = terms.record(corrections, label=label)
        for line in result.skipped_lines:
            print(line, file=sys.stderr)
        for recording_id in result.deferred:
            print(
                f"plaud-sync: field extraction failed; retry next poll: {recording_id}",
                file=sys.stderr,
            )
    except Exception as error:  # noqa: BLE001 - discovery is best-effort; resolve must still run
        print(f"plaud-sync discovery error: {_masked_error(error)}", file=sys.stderr)
        return state
    return result.state


def _merge_effect_bindings(
    before: PlaudSyncState,
    result: ResolveResult,
    persisted: PlaudSyncState,
) -> ResolveResult:
    """Adopt bindings the store committed mid-tick so the final save keeps them."""
    records = dict(result.state.records)
    for key, resolved in records.items():
        initial = before.records.get(key)
        current = persisted.records.get(key)
        if initial is None or current is None:
            continue
        if (
            initial.action_hash != current.action_hash
            or resolved.action_hash != current.action_hash
        ):
            continue
        merged = resolved
        if (current.message_id, current.channel_id) != (
            initial.message_id,
            initial.channel_id,
        ):
            merged = replace(
                merged, message_id=current.message_id, channel_id=current.channel_id
            )
        if current.approval_thread_id != initial.approval_thread_id:
            merged = replace(merged, approval_thread_id=current.approval_thread_id)
        if current.approval_guild_id != initial.approval_guild_id:
            merged = replace(merged, approval_guild_id=current.approval_guild_id)
        if current.last_block_reason != initial.last_block_reason:
            merged = replace(merged, last_block_reason=current.last_block_reason)
        if current.render_version != initial.render_version:
            merged = replace(merged, render_version=current.render_version)
        records[key] = merged
    return replace(
        result,
        state=PlaudSyncState(result.state.version, result.state.last_poll_at, records),
    )


def run_once(now: datetime) -> ResolveResult:
    from automation.notice_bot import resolve_token

    token = resolve_token()
    if not token:
        raise WatchError("Discord token is unavailable")
    from automation.plaud_sync.effects_live import build_effects

    state = _discover(load_state(STATE_PATH), now)
    save_state(STATE_PATH, state)
    result = resolve_tick(
        state,
        effects=build_effects(
            state_path=STATE_PATH, token=token, owner_id=_owner_id(), now=now
        ),
        max_posts=_env_int("PLAUD_SYNC_MAX_POSTS", 3),
    )
    result = _merge_effect_bindings(state, result, load_state(STATE_PATH))
    save_state(STATE_PATH, result.state)
    return result


def _transcribe() -> transcribe_live.StepSummary | None:
    try:
        return transcribe_live.run_transcribe_step(
            state_dir=STATE_DIR, lock_path=LOCK_PATH, env=os.environ
        )
    except Exception as error:  # noqa: BLE001 - best-effort like discovery; this tick's approvals already ran
        print(f"plaud-sync transcribe error: {_masked_error(error)}", file=sys.stderr)
        return None


def _summary(result: ResolveResult) -> str | None:
    if not (result.posted or result.written or result.abandoned):
        return None
    return (
        f"plaud-sync: posted={len(result.posted)} "
        f"written={len(result.written)} abandoned={len(result.abandoned)}"
    )



def _repost_posted() -> tuple[str, ...]:
    from automation.notice_bot import resolve_token

    token = resolve_token()
    if not token:
        raise WatchError("Discord token is unavailable")
    from automation.plaud_sync.reaction_transport import DiscordTransport
    from automation.plaud_sync.repost import repost_posted

    return repost_posted(PlaudSyncStore(STATE_PATH), DiscordTransport(token, _owner_id()))


def _reprocess(recording_id: str) -> bool:
    """Queue one finished recording for a fresh transcription (owner-run, never a sweep)."""
    from automation.plaud_sync.repost import reprocess

    return reprocess(PlaudSyncStore(STATE_PATH), recording_id)


def _reprocess_targets(argv: list[str]) -> tuple[str, ...]:
    """Pull ``--reprocess <id>`` pairs out of ``argv`` in place; a bare flag is an error."""
    targets: list[str] = []
    index = 0
    while index < len(argv):
        if argv[index] != "--reprocess":
            index += 1
            continue
        if index + 1 >= len(argv) or argv[index + 1].startswith("--"):
            raise WatchError("--reprocess needs a recording id")
        targets.append(argv[index + 1])
        del argv[index : index + 2]
    return tuple(targets)


def _run(argv: Sequence[str]) -> list[str]:
    """One tick; ``--repost-posted`` re-cards live requests and ``--reprocess <id>``
    sends a finished recording back through transcription before the tick runs."""
    rest = list(argv)
    targets = _reprocess_targets(rest)
    unknown = sorted(set(rest) - {"--repost-posted"})
    if unknown:
        raise WatchError(f"unknown argument: {unknown}")
    lines: list[str | None] = [
        f"plaud-sync: reprocess {recording_id} "
        f"{'queued' if _reprocess(recording_id) else 'refused'}"
        for recording_id in targets
    ]
    lines.append(_summary(run_once(datetime.now(UTC))))
    if "--repost-posted" in argv:
        lines.append(f"plaud-sync: reposted={len(_repost_posted())}")
        lines.append(_summary(run_once(datetime.now(UTC))))
    return [line for line in lines if line is not None]


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = tuple(sys.argv[1:] if argv is None else argv)
        migration = "--migrate-aliases" in args
        if migration and set(args) - {"--migrate-aliases", "--apply"}:
            raise WatchError("--migrate-aliases only accepts --apply")
        _load_env_secrets()
        lock = acquire_single_instance_lock(LOCK_PATH)
        if lock is None:
            return 0
        with lock:
            if migration:
                from automation.plaud_sync.aliases import migrate_file

                for line in migrate_file(STATE_PATH, apply="--apply" in args):
                    print(line)
                return 0
            lines = _run(args)
        # watch.lock is released here on purpose: local transcription runs for tens of
        # minutes and must not hold the next tick's ✅ hostage (transcribe_live docstring).
        step = _transcribe()
        if step is not None:
            lines.extend(filter(None, [step.line]))
            if step.promoted and (lock := acquire_single_instance_lock(LOCK_PATH)) is not None:
                with lock:
                    lines.extend(filter(None, [_summary(run_once(datetime.now(UTC)))]))
        for line in lines:
            print(line)
    except Exception as error:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK - final cron alert boundary
        print(f"plaud-sync-watch error: {_masked_error(error)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
