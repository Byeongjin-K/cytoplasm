"""Owner-run, one-shot migration into the taxonomy-managed Drive outputs tree.

Dry-run is the default. Discovery uses only ``DriveClient.list_children`` and
prints a deterministic plan; folder creation and every other mutation are
strictly confined to ``--apply``. Files and empty legacy folders are only ever
trashed, never permanently deleted.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TextIO

from automation.drive_client import DriveClient, DriveClientError
from automation.drive_taxonomy import category, folder_parts, outputs_root

_FOLDER_MIME = "application/vnd.google-apps.folder"
_LEGACY_KINDS = ("report", "proposal", "procurement", "doctype")
_MONTH_RE = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")
_DASHED_DATE_RE = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)")
_COMPACT_DATE_RE = re.compile(r"(?<!\d)(\d{8})(?!\d)")
_CACHE_GUIDANCE = (
    "~/.hermes/doctype/drive-folders.json",
    "~/.hermes/drive-publish/folders.json",
)


def _nfc(value: object) -> str:
    return unicodedata.normalize("NFC", str(value or ""))


@dataclass(frozen=True, slots=True)
class Entry:
    file_id: str
    name: str
    original_name: str
    mime_type: str
    parent_id: str
    source_path: str
    created_time: str
    modified_time: str

    @property
    def is_folder(self) -> bool:
        return self.mime_type == _FOLDER_MIME


@dataclass(frozen=True, slots=True)
class Move:
    entry: Entry
    destination: tuple[str, ...]
    new_name: str | None = None
    patent: bool = False


@dataclass(frozen=True, slots=True)
class Trash:
    entry: Entry
    reason: str
    depth: int = 0


@dataclass(frozen=True, slots=True)
class Skip:
    target: str
    reason: str


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    moves: tuple[Move, ...]
    trashes: tuple[Trash, ...]
    skips: tuple[Skip, ...]

    def lines(self) -> tuple[str, ...]:
        lines: list[str] = []
        for move in self.moves:
            lines.append(
                "MIGRATE-PLAN move "
                f"id={move.entry.file_id} from={move.entry.source_path} "
                f"to={'/'.join(move.destination)}"
            )
            if move.new_name is not None:
                lines.append(
                    "MIGRATE-PLAN rename "
                    f"id={move.entry.file_id} from={move.entry.original_name} to={move.new_name}"
                )
        for trash in self.trashes:
            kind = "empty-folder" if trash.entry.is_folder else "duplicate"
            lines.append(
                "MIGRATE-PLAN trash "
                f"id={trash.entry.file_id} type={kind} name={trash.entry.name}"
            )
        for skip in self.skips:
            lines.append(f"MIGRATE-PLAN skip reason={skip.reason} target={skip.target}")
        return tuple(sorted(lines))


class MigrationApplyError(RuntimeError):
    """An apply mutation or its owner-only verification failed."""

    def __init__(self, entry: Entry, cause: Exception) -> None:
        super().__init__(str(cause))
        self.entry = entry


def _entry(row: Mapping[str, object], parent_id: str, source_path: str) -> Entry | None:
    file_id = str(row.get("id", "")).strip()
    original_name = str(row.get("name", ""))
    name = _nfc(original_name)
    if not file_id or not name:
        return None
    return Entry(
        file_id=file_id,
        name=name,
        original_name=original_name,
        mime_type=str(row.get("mimeType", "")),
        parent_id=parent_id,
        source_path=source_path,
        created_time=str(row.get("createdTime", "")),
        modified_time=str(row.get("modifiedTime", "")),
    )


def _children(client: DriveClient, parent_id: str, source_path: str) -> list[Entry]:
    found = [
        parsed
        for row in client.list_children(parent_id)
        if (parsed := _entry(row, parent_id, source_path)) is not None
    ]
    return sorted(found, key=lambda item: (item.name, item.file_id))


def _date_from_entry(entry: Entry) -> date | None:
    matches = [
        match
        for match in (
            _DASHED_DATE_RE.search(entry.name),
            _COMPACT_DATE_RE.search(entry.name),
        )
        if match is not None
    ]
    embedded = min(matches, key=lambda match: match.start()) if matches else None
    raw = embedded.group(1) if embedded is not None else entry.created_time[:10]
    try:
        return (
            date.fromisoformat(raw)
            if "-" in raw
            else date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
        )
    except (TypeError, ValueError):
        return None


def _migrated_name(entry: Entry, on: date) -> str:
    normalized = _nfc(entry.name)
    prefix = f"{on.isoformat()}_"
    return normalized if normalized.startswith(prefix) else f"{prefix}{normalized}"


def _discover_locations(
    client: DriveClient,
) -> tuple[list[Entry], Entry | None, dict[str, Entry], dict[str, Entry]]:
    root_entries = _children(client, "root", "root")
    output = next(
        (item for item in root_entries if item.is_folder and item.name == outputs_root()),
        None,
    )
    output_children: dict[str, Entry] = {}
    category_children: dict[str, Entry] = {}
    if output is not None:
        direct = _children(client, output.file_id, outputs_root())
        output_children = {item.file_id: item for item in direct}
        category_names = {category(kind).folder for kind in _LEGACY_KINDS}
        for category_entry in direct:
            if category_entry.is_folder and category_entry.name in category_names:
                source = f"{outputs_root()}/{category_entry.name}"
                for child in _children(client, category_entry.file_id, source):
                    category_children[child.file_id] = child
    return root_entries, output, output_children, category_children


def _legacy_entries(
    client: DriveClient,
    root_entries: Sequence[Entry],
) -> tuple[list[Entry], list[Trash], list[Skip]]:
    files: list[Entry] = []
    folders: list[tuple[Entry, int, bool]] = []
    skips: list[Skip] = []

    for legacy in root_entries:
        if not legacy.is_folder or legacy.name not in _LEGACY_KINDS:
            continue
        blocked = False
        month_states: list[tuple[Entry, bool]] = []
        for child in _children(client, legacy.file_id, legacy.name):
            if child.is_folder:
                if not _MONTH_RE.fullmatch(child.name):
                    skips.append(Skip(child.name, "unknown-legacy-folder"))
                    blocked = True
                    continue
                month_blocked = False
                for nested in _children(
                    client,
                    child.file_id,
                    f"{legacy.name}/{child.name}",
                ):
                    if nested.is_folder:
                        skips.append(Skip(nested.name, "depth-limit-or-unknown-folder"))
                        month_blocked = True
                    else:
                        files.append(nested)
                month_states.append((child, month_blocked))
                blocked = blocked or month_blocked
            else:
                files.append(child)
        for month, month_blocked in month_states:
            folders.append((month, 2, month_blocked))
        folders.append((legacy, 1, blocked))

    # Folder removal is safe only when every non-folder child has a planned
    # move/trash and every nested folder is itself removable. Invalid files are
    # accounted for below by removing their ancestors from this candidate set.
    trashes = [Trash(entry, "empty-folder", depth) for entry, depth, blocked in folders if not blocked]
    return files, trashes, skips


def build_plan(
    client: DriveClient, environ: Mapping[str, str]
) -> MigrationPlan:
    root_entries, output, output_children, category_children = _discover_locations(client)
    legacy_files, folder_trashes, skips = _legacy_entries(client, root_entries)

    duplicate_trashes: list[Trash] = []
    winners: list[Entry] = []
    by_name: dict[str, list[Entry]] = {}
    for entry in legacy_files:
        by_name.setdefault(_nfc(entry.name), []).append(entry)
    for entries in by_name.values():
        winner = max(entries, key=lambda item: (item.modified_time, item.file_id))
        winners.append(winner)
        duplicate_trashes.extend(
            Trash(item, "duplicate") for item in entries if item.file_id != winner.file_id
        )

    moves: list[Move] = []
    invalid_parent_ids: set[str] = set()
    for entry in sorted(winners, key=lambda item: (item.name, item.file_id)):
        on = _date_from_entry(entry)
        if on is None:
            skips.append(Skip(entry.name, "invalid-date"))
            invalid_parent_ids.add(entry.parent_id)
            continue
        kind = entry.source_path.split("/", 1)[0]
        destination = folder_parts(kind, on.year)
        new_name = _migrated_name(entry, on)
        moves.append(
            Move(
                entry,
                destination,
                new_name if new_name != entry.original_name else None,
            )
        )

    if invalid_parent_ids:
        invalid_paths = {
            entry.source_path
            for entry in winners
            if entry.parent_id in invalid_parent_ids
        }

        def contains_invalid_file(trash: Trash) -> bool:
            folder_path = (
                trash.entry.name
                if trash.entry.source_path == "root"
                else f"{trash.entry.source_path}/{trash.entry.name}"
            )
            return any(
                invalid_path == folder_path or invalid_path.startswith(f"{folder_path}/")
                for invalid_path in invalid_paths
            )

        folder_trashes = [trash for trash in folder_trashes if not contains_invalid_file(trash)]

    all_known = {item.file_id: item for item in (*root_entries, *legacy_files)}
    all_known.update(output_children)
    all_known.update(category_children)

    patent_id = environ.get("PATENT_ARCHIVE_FOLDER_ID", "").strip()
    if not patent_id:
        skips.append(Skip("patent", "missing-env:PATENT_ARCHIVE_FOLDER_ID"))
    else:
        patent = all_known.get(patent_id)
        patent_name = category("patent").folder
        if patent is None:
            skips.append(Skip("patent", "id-not-found"))
        elif output is not None and patent.parent_id == output.file_id and patent.name == patent_name:
            skips.append(Skip("patent", "already-migrated"))
        else:
            moves.append(
                Move(
                    patent,
                    (outputs_root(),),
                    patent_name if patent.original_name != patent_name else None,
                    patent=True,
                )
            )

    return MigrationPlan(
        moves=tuple(sorted(moves, key=lambda move: (move.entry.file_id, move.destination))),
        trashes=tuple(
            sorted(
                (*duplicate_trashes, *folder_trashes),
                key=lambda trash: (trash.entry.file_id, trash.reason),
            )
        ),
        skips=tuple(sorted(skips, key=lambda skip: (skip.target, skip.reason))),
    )


def apply_plan(client: DriveClient, plan: MigrationPlan, stdout: TextIO) -> None:
    for trash in sorted(
        (item for item in plan.trashes if not item.entry.is_folder),
        key=lambda item: item.entry.file_id,
    ):
        try:
            client.trash_file(trash.entry.file_id)
        except Exception as error:
            raise MigrationApplyError(trash.entry, error) from error

    for move in plan.moves:
        try:
            destination_id = client.ensure_folder_path(move.destination)
            client.move_file(move.entry.file_id, destination_id, move.entry.parent_id)
            if move.new_name is not None:
                client.rename_file(move.entry.file_id, move.new_name)
            client.verify_owner_only(move.entry.file_id)
            print(
                f"MIGRATE-VERIFY id={move.entry.file_id} owner-only=ok",
                file=stdout,
            )
            if move.patent:
                client.verify_owner_only(move.entry.file_id)
                print(
                    f"MIGRATE-VERIFY patent-folder id={move.entry.file_id} owner-only=ok",
                    file=stdout,
                )
        except Exception as error:
            raise MigrationApplyError(move.entry, error) from error

    for trash in sorted(
        (item for item in plan.trashes if item.entry.is_folder),
        key=lambda item: (-item.depth, item.entry.file_id),
    ):
        try:
            client.trash_file(trash.entry.file_id)
        except Exception as error:
            raise MigrationApplyError(trash.entry, error) from error

    for cache in _CACHE_GUIDANCE:
        print(f"MIGRATE-GUIDANCE delete-stale-cache path={cache}", file=stdout)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan or apply the one-shot Drive outputs migration (dry-run by default)."
    )
    parser.add_argument("--apply", action="store_true", help="execute the printed migration plan")
    return parser


def _default_client(environ: Mapping[str, str]) -> DriveClient:
    gws_bin = (
        environ.get("DRIVE_GWS_BIN")
        or environ.get("DRIVE_PUBLISH_GWS_BIN")
        or "gws"
    )
    cache = Path(
        environ.get("DRIVE_PUBLISH_CACHE", "~/.hermes/drive-publish/folders.json")
    ).expanduser()
    return DriveClient(gws_bin, cache)


def run(
    argv: Sequence[str] | None = None,
    *,
    client: DriveClient | None = None,
    environ: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    args = _parser().parse_args(argv)
    env = os.environ if environ is None else environ
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr
    active_client = _default_client(env) if client is None else client
    try:
        plan = build_plan(active_client, env)
        for line in plan.lines():
            print(line, file=out)
        if args.apply:
            apply_plan(active_client, plan, out)
    except MigrationApplyError as error:
        print(
            "MIGRATE-ERROR "
            f"id={error.entry.file_id} file={error.entry.name} reason={error}",
            file=err,
        )
        return 1
    except (DriveClientError, OSError, ValueError) as error:
        print(f"MIGRATE-ERROR reason={error}", file=err)
        return 1
    return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
