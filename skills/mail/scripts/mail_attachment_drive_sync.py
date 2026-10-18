#!/usr/bin/env python3
"""Execute the MailOn attachment archive plan against the owner's Google Drive.

Planning and archive.db compatibility live in ``mail_attachment_archive`` so this
CLI contains the sole Drive effect boundary and the governed-copy refusal.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import fcntl
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import mail_attachment_archive as archive
import mail_attachment_retry as retry
import mail_runtime

if TYPE_CHECKING:  # pragma: no cover - automation is resolved only for Drive calls
    from automation.drive_client import DriveClient

RELEASE_CURRENT = Path("/srv/autophagy-agent-current")
MIRROR_CHECKOUT = Path("/srv/autophagy-agents")
GWS_BIN_ENV = "MAIL_ATTACHMENT_GWS_BIN"
Attachment = archive.Attachment
SyncError = archive.SyncError


def _runtime_root() -> Path:
    """Resolve automation lazily because mounted skills do not ship that package."""
    override = os.environ.get("AUTOPHAGY_REPO_ROOT", "").strip()
    if override:
        return Path(override).expanduser()
    return RELEASE_CURRENT if RELEASE_CURRENT.exists() else MIRROR_CHECKOUT


def drive_client() -> DriveClient:
    """Build the shared argv client only when the planned work needs Drive."""
    root = _runtime_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from automation.drive_client import DriveClient as client_type  # noqa: PLC0415
    except ImportError as error:
        raise SyncError(
            "automation_unavailable", f"automation package not importable from {root}"
        ) from error
    return client_type(gws_bin=os.environ.get(GWS_BIN_ENV, "gws"), folder_cache=archive.folder_cache())


def ensure_parent(client: DriveClient, parts: tuple[str, ...]) -> str:
    """Prove each destination is owner-only before an upload can use it."""
    folder_id = client.ensure_folder_path(parts)
    client.verify_owner_only(folder_id)
    return folder_id


def verify_remote(client: DriveClient, file_id: str, expected_sha: str, expected_size: int) -> None:
    """Fail closed on remote metadata because re-downloading doubles archive traffic."""
    checksum, size = client.file_checksum(file_id)
    if size != expected_size:
        raise SyncError("size_mismatch", f"{file_id}: remote={size} local={expected_size}")
    if checksum.lower() != expected_sha.lower():
        raise SyncError(
            "checksum_mismatch", f"{file_id}: remote={checksum[:12]}… local={expected_sha[:12]}…"
        )
    client.verify_owner_only(file_id)


def archive_one(
    client: DriveClient, item: Attachment, parent_id: str, prior_id: str | None = None
) -> tuple[str, str]:
    """Upload one item and return it only after Drive has verified its bytes."""
    digest = archive.sha256(item.local)
    result = client.upsert_file(item.local, archive.remote_name(item), parent_id, prior_id)
    file_id = str(result.get("id", ""))
    if not file_id:
        raise SyncError("upload_no_id", f"upload of {item.key[:12]} returned no id")
    verify_remote(client, file_id, digest, item.size)
    return digest, file_id


def sync(
    client: DriveClient | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    workers: int = 4,
) -> dict[str, Any]:
    """Execute a stable plan; failed items remain unrecorded for the next tick."""
    state = archive.state_connection()
    try:
        if not dry_run:
            retry.recover(state)
        items, bogus, missing = archive.discover()
        pending, skipped, prior_ids = archive.plan(state, items, limit)
        if dry_run:
            return archive.report(
                state, eligible=len(items), uploaded=0, skipped=skipped, failed=0,
                bogus_excluded=bogus, missing_local=missing, planned=len(pending), failure_code="",
            )
        drive = drive_client() if client is None else client
        parents: dict[tuple[str, ...], str] = {}
        for item in pending:
            parts = archive.folder_parts(item)
            if parts not in parents:
                parents[parts] = ensure_parent(drive, parts)

        def upload(item: Attachment) -> tuple[Attachment, str, str, str]:
            parent_id = parents[archive.folder_parts(item)]
            digest, file_id = archive_one(drive, item, parent_id, prior_ids.get(item.key))
            return item, digest, parent_id, file_id

        uploaded = 0
        failed = 0
        failure_code = ""
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = [pool.submit(upload, item) for item in pending]
            for future in concurrent.futures.as_completed(futures):
                try:
                    item, digest, parent_id, file_id = future.result()
                except Exception as error:  # noqa: BLE001 - one item must not stop the tick
                    failed += 1
                    failure_code = failure_code or archive.failure_code(error)
                else:
                    archive.record(state, item, digest, parent_id, file_id)
                    uploaded += 1
        return archive.report(
            state, eligible=len(items), uploaded=uploaded, skipped=skipped, failed=failed,
            bogus_excluded=bogus, missing_local=missing, planned=0, failure_code=failure_code,
        )
    finally:
        state.close()


def main() -> int:
    refusal = mail_runtime.governed_copy_refusal(Path(__file__))
    if refusal:
        print(refusal, file=sys.stderr)
        return 3
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 0:
        parser.error("--limit must be >= 0")
    if args.workers < 1 or args.workers > 8:
        parser.error("--workers must be between 1 and 8")
    archive.STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (archive.STATE_DIR / "sync.lock").open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        try:
            result = sync(limit=args.limit, dry_run=args.dry_run, workers=args.workers)
        except Exception as error:  # noqa: BLE001 - cron needs one structured failure
            print(
                json.dumps({"status": "error", "code": archive.failure_code(error)}),
                file=sys.stderr,
            )
            return 1
    if args.dry_run:
        print(json.dumps({"status": "ok", **result}, ensure_ascii=False, sort_keys=True))
        return 0
    if int(result["failed"]):
        print(
            json.dumps({
                "status": "partial", "code": str(result["failure_code"]) or "upload_failed",
                "failed": int(result["failed"]), "uploaded": int(result["uploaded"]),
            }),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
