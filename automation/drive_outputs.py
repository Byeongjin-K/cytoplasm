"""Single taxonomy-aware facade for publishing final artifacts to Google Drive.

All Drive mutations and verification go through :class:`DriveClient`. Callers
that require durable Drive storage use :func:`publish`; optional skill hooks use
:func:`publish_best_effort`, which is disabled unless explicitly opted in.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final, TypeAlias

from automation.drive_client import DriveClient, DriveClientError
from automation.drive_taxonomy import (
    artifact_name,
    bundle_name,
    category,
    TaxonomyError,
    dated,
    ensure_depth,
    name_prefix,
    folder_parts,
    para_enabled,
    period_key,
    project_folder_name,
    projects_parts,
    share_root,
)

_DEFAULT_CACHE: Final = Path.home() / ".hermes" / "drive-publish" / "folders.json"
_FOLDER_MIME: Final = "application/vnd.google-apps.folder"
_YEAR_NAME: Final = re.compile(r"^\d{4}$")

Artifact: TypeAlias = tuple[Path, str]


@dataclass(frozen=True, slots=True)
class PublishResult:
    links: tuple[str, ...]
    action: str
    folder_id: str


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def client_from_environment() -> DriveClient:
    """Build the production client using the shared publisher configuration."""
    gws_bin = (
        os.environ.get("DRIVE_GWS_BIN")
        or os.environ.get("DRIVE_PUBLISH_GWS_BIN")
        or "gws"
    )
    configured_cache = os.environ.get("DRIVE_PUBLISH_CACHE")
    cache = Path(configured_cache).expanduser() if configured_cache else _DEFAULT_CACHE
    return DriveClient(gws_bin=gws_bin, folder_cache=cache)


def _validate_inputs(
    title: str,
    artifacts: Sequence[Artifact],
    companions: Sequence[Path],
) -> tuple[str, tuple[Artifact, ...], tuple[Path, ...]]:
    normalized_title = _nfc(title)
    if not normalized_title:
        raise ValueError("publish title is empty")
    if not artifacts:
        raise ValueError("publish requires at least one artifact")

    checked_artifacts: list[Artifact] = []
    for local, artifact_title in artifacts:
        path = Path(local)
        if not path.is_file():
            raise FileNotFoundError(path)
        normalized_artifact_title = _nfc(artifact_title)
        if not normalized_artifact_title:
            raise ValueError("artifact title is empty")
        checked_artifacts.append((path, normalized_artifact_title))

    checked_companions: list[Path] = []
    for local in companions:
        path = Path(local)
        if not path.is_file():
            raise FileNotFoundError(path)
        checked_companions.append(path)

    return normalized_title, tuple(checked_artifacts), tuple(checked_companions)


def resolve_project(project: str | None, *, client: DriveClient, on: date) -> str | None:
    """The PARA project folder name for ``project``: an existing ``YYYY_<project>`` wins.

    Reads ``1_Project`` with ``find_folder_path`` (never creates it). Without PARA, or
    without a project, the value comes back unchanged.
    """
    if not project or not para_enabled():
        return project
    folder = client.find_folder_path(projects_parts())
    existing = (
        [_nfc(str(child.get("name", ""))) for child in client.list_children(folder)
         if str(child.get("mimeType", "")) == _FOLDER_MIME]
        if folder else []
    )
    return project_folder_name(project, year=on.year, existing=existing)


def _sticky_period(
    client: DriveClient,
    category_folder_id: str,
    title: str,
    *,
    yearly: bool = True,
    prefix: str = "",
) -> str | None:
    """Find a one-shot's original day by scanning numeric years newest-first.

    ``yearly=False`` scans the folder itself: a PARA project subfolder has no years.
    """
    years: list[tuple[int, str]] = []
    if not yearly:
        years.append((0, category_folder_id))
    for child in client.list_children(category_folder_id) if yearly else ():
        name = _nfc(str(child.get("name", "")))
        folder_id = str(child.get("id", ""))
        mime_type = str(child.get("mimeType", ""))
        if _YEAR_NAME.fullmatch(name) and mime_type == _FOLDER_MIME and folder_id:
            years.append((int(name), folder_id))

    target = re.compile(
        rf"^{re.escape(prefix)}(\d{{4}}-\d{{2}}-\d{{2}})_{re.escape(title)}(?:\.[^/]*)?$"
    )
    for _, year_folder_id in sorted(years, reverse=True):
        for child in client.list_children(year_folder_id):
            name = _nfc(str(child.get("name", "")))
            match = target.fullmatch(name)
            if match is not None:
                return match.group(1)
    return None


def publish(
    kind: str,
    title: str,
    artifacts: Sequence[Artifact],
    *,
    companions: Sequence[Path] = (),
    on: date | None = None,
    project: str | None = None,
    client: DriveClient | None = None,
) -> PublishResult:
    """Upsert and verify one logical output in the canonical Drive tree.

    One-shot documents retain the date prefix from their first publication.
    Every uploaded file is owner-only checked and read back for sha256 equality
    before this function returns.
    """
    selected = category(kind)
    # folder_parts is the registry's gate-only enforcement boundary. Resolve it
    # before validating local paths so a forbidden kind can never reach Drive.
    requested_on = on or date.today()
    initial_parts = folder_parts(kind, requested_on.year, project=project)
    normalized_title, checked_artifacts, checked_companions = _validate_inputs(
        title, artifacts, companions
    )
    drive = client or client_from_environment()
    project = resolve_project(project, client=drive, on=requested_on)
    initial_parts = folder_parts(kind, requested_on.year, project=project)
    prefix = name_prefix(kind, project)
    yearly = dated(kind, project)

    period = period_key(selected.periodicity, requested_on)
    if selected.periodicity == "oneshot":
        # Everything but the year: the folder whose year children hold prior copies.
        # With a project that is the project folder, so a re-publish keeps its original
        # date instead of starting a second copy under today's. A PARA project
        # subfolder has no year level, so it is scanned itself.
        scan_parts = ensure_depth(initial_parts[:-1] if yearly else initial_parts)
        scan_folder_id = drive.ensure_folder_path(scan_parts)
        sticky = _sticky_period(drive, scan_folder_id, normalized_title, yearly=yearly, prefix=prefix)
        if sticky is not None:
            period = sticky

    # Period keys always begin with their governing year. For ISO weeks this
    # can intentionally differ from the input date's calendar year.
    target_year = int(period[:4])
    base_parts = folder_parts(kind, target_year, project=project)
    bundled = selected.always_bundle or len(checked_artifacts) + len(checked_companions) > 1
    if bundled:
        parent_parts = ensure_depth((*base_parts, bundle_name(period, normalized_title, prefix=prefix)))
    else:
        parent_parts = ensure_depth(base_parts)
    parent_id = drive.ensure_folder_path(parent_parts)

    uploads: list[tuple[Path, str]] = []
    for local, artifact_title in checked_artifacts:
        name = artifact_name(period, artifact_title, local.suffix, prefix=prefix)
        _ = ensure_depth((*parent_parts, name))
        uploads.append((local, name))
    for local in checked_companions:
        name = _nfc(local.name)
        _ = ensure_depth((*parent_parts, name))
        uploads.append((local, name))

    links: list[str] = []
    actions: list[str] = []
    for local, name in uploads:
        result = drive.upsert_file(local, name, parent_id)
        file_id = result["id"]
        drive.verify_owner_only(file_id)
        _ = drive.download_and_verify(file_id, local)
        links.append(result["webViewLink"])
        actions.append(result["action"])

    action = "updated" if actions and all(item == "updated" for item in actions) else "created"
    return PublishResult(tuple(links), action, parent_id)


@dataclass(frozen=True, slots=True)
class ShareResult:
    file_id: str
    link: str
    action: str
    invited_by_google: tuple[str, ...] = ()


class ShareRefused(RuntimeError):
    """A share that must not proceed; ``code`` is a fixed lowercase cause for audit records."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


def share_target(name: str, *, on: date, project: str | None = None) -> tuple[tuple[str, ...], str]:
    """The folder parts and Drive file name a share of ``name`` will use, fixed at request time.

    The caller binds both into the owner's approval, so the upload later lands exactly
    where the card said — a date that rolls over between the card and the ✅ moves nothing.
    """
    path = Path(_nfc(name))
    if not path.stem or path.name != _nfc(name):
        raise TaxonomyError(f"invalid share file name: {name!r}")
    parts = folder_parts("share", on.year, project=project)
    drive_name = artifact_name(period_key("oneshot", on), path.stem, path.suffix)
    _ = ensure_depth((*parts, drive_name))
    return parts, drive_name


def require_share_folder(parts: Sequence[str]) -> None:
    """Refuse a folder outside this environment's share tree."""
    root = share_root()
    if tuple(parts[: len(root)]) != root:
        raise ShareRefused(f"share folder outside {'/'.join(root)}", "drive_folder_refused")


def _same_bytes(drive: DriveClient, file_id: str, sha256: str, size: int) -> bool:
    try:
        return drive.file_checksum(file_id) == (sha256, size)
    except DriveClientError:  # unreadable: upload again below rather than trust it
        return False


def _local_digest(local: Path) -> tuple[str, int]:
    with local.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    return digest, local.stat().st_size


def share(
    local: Path,
    parts: Sequence[str],
    name: str,
    *,
    sha256: str,
    size: int,
    grantees: Sequence[str],
    prior_id: str | None = None,
    client: DriveClient | None = None,
) -> ShareResult:
    """Upload ``local`` and share it view-only with exactly ``grantees``; return its link.

    Every step is checked before the next one: the local bytes must still be the ones the
    owner approved, Drive's own sha256 and size must match after the upload, the file may
    hold no permission beyond the owner and these readers before or after the grant, and
    nothing here can create an anyone-with-link or domain permission. ``prior_id`` is the
    file an earlier attempt already uploaded; when Drive still holds those exact bytes it
    is reused instead of uploading the same file again.
    """
    checked = ensure_depth((*parts, _nfc(name)))
    require_share_folder(checked)
    people = tuple(sorted({person.strip().lower() for person in grantees}))
    if not people or any("@" not in person for person in people):
        raise ShareRefused("share needs at least one named person", "drive_grantee_invalid")
    if _local_digest(local) != (sha256, size):
        raise ShareRefused("the approved file changed before upload", "local_copy_changed")
    drive = client or client_from_environment()

    file_id = ""
    action = "reused"
    if prior_id:
        try:
            if drive.file_checksum(prior_id) == (sha256, size):
                file_id = prior_id
        except Exception:  # noqa: BLE001 — an unreadable prior upload is uploaded again below
            file_id = ""
    if not file_id:
        parent_id = drive.ensure_folder_path(tuple(checked[:-1]))
        existing = drive.find_file(checked[-1], parent_id)
        if existing and _same_bytes(drive, existing, sha256, size):
            file_id = existing  # an earlier attempt already uploaded these exact bytes
    if not file_id:
        uploaded = drive.upsert_file(local, checked[-1], parent_id)
        file_id = uploaded["id"]
        action = uploaded["action"]
        if drive.file_checksum(file_id) != (sha256, size):
            raise ShareRefused("Drive copy does not match the approved bytes", "drive_checksum_mismatch")

    try:
        present = drive.shared_readers(file_id)
    except DriveClientError as error:
        raise ShareRefused(str(error), "drive_permission_refused") from error
    unexpected = sorted(present - set(people))
    if unexpected:
        raise ShareRefused(
            f"file is already shared beyond the approved people ({len(unexpected)})",
            "drive_permission_refused",
        )
    invited: list[str] = []
    for person in people:
        if person not in present:
            try:
                if drive.grant_reader(file_id, person):
                    invited.append(person)
            except DriveClientError as error:
                raise ShareRefused(str(error), "drive_grant_refused") from error
    try:
        final = drive.shared_readers(file_id)
    except DriveClientError as error:
        raise ShareRefused(str(error), "drive_permission_refused") from error
    if final != frozenset(people):
        raise ShareRefused("the share does not name exactly the approved people", "drive_permission_refused")
    return ShareResult(file_id=file_id, link=drive.web_view_link(file_id), action=action,
                       invited_by_google=tuple(invited))


_FOLDER_ID: Final = re.compile(r"[A-Za-z0-9_-]{10,128}")


@dataclass(frozen=True, slots=True)
class FolderTarget:
    folder_id: str
    name: str
    path: tuple[str, ...]


def folder_target(folder_id: str, *, client: DriveClient | None = None) -> FolderTarget:
    """Any folder of the owner's Drive by id (owner Q548), read and checked owner-only.

    A file put into a folder other people can open is shared with them, and sharing goes
    only through mail ``compose --drive-share``; so a folder holding any permission beyond
    the owner is refused here, at request time and again right before the upload.
    """
    if not _FOLDER_ID.fullmatch(folder_id):
        raise ValueError("a Drive folder id is letters, digits, - and _ only")
    drive = client or client_from_environment()
    name, path = drive.folder_info(folder_id)
    drive.verify_owner_only(folder_id)
    return FolderTarget(folder_id, _nfc(name), tuple(_nfc(part) for part in path))


def save_to_folder(
    local: Path,
    name: str,
    folder_id: str,
    *,
    sha256: str,
    size: int,
    client: DriveClient | None = None,
) -> PublishResult:
    """Upsert ``local`` as ``name`` directly into the owner-only folder ``folder_id``.

    The folder is re-checked first, the local bytes must still be the approved ones, and
    the upload is verified owner-only and read back for sha256 equality like :func:`publish`.
    """
    drive = client or client_from_environment()
    _ = folder_target(folder_id, client=drive)
    if _local_digest(local) != (sha256, size):
        raise ValueError("the approved file changed before upload")
    result = drive.upsert_file(local, _nfc(name), folder_id)
    file_id = result["id"]
    drive.verify_owner_only(file_id)
    _ = drive.download_and_verify(file_id, local)
    return PublishResult((result["webViewLink"],), result["action"], folder_id)


class TreeConflict(ValueError):
    """Another file already holds that name: a folder-tree save never replaces it."""


@dataclass(frozen=True, slots=True)
class TreeScan:
    existing: tuple[str, ...]
    unchanged: tuple[str, ...]
    overwrite: tuple[tuple[str, str], ...]


class TreeWriter:
    """Folder-tree saves below one owner-only parent (``drive-save --manifest``, owner 2026-11-23).

    Folders are found or created under their parent and checked owner-only; a file is only
    ever created. A same-name file with the approved bytes counts as saved, one with other
    bytes is a :class:`TreeConflict` (overwriting needs its own card). Listings are cached for
    the life of one writer, which is one request or one watcher tick.
    """

    def __init__(self, client: DriveClient | None = None) -> None:
        self.drive = client or client_from_environment()
        self._children: dict[str, dict[str, dict[str, str]]] = {}

    def parent(self, folder_id: str) -> FolderTarget:
        return folder_target(folder_id, client=self.drive)

    def _listing(self, folder_id: str) -> dict[str, dict[str, str]]:
        if folder_id not in self._children:
            self._children[folder_id] = {
                _nfc(str(row.get("name", ""))): {"id": str(row.get("id", "")),
                                                 "mimeType": str(row.get("mimeType", ""))}
                for row in self.drive.list_children(folder_id)
            }
        return self._children[folder_id]

    def child_folder(self, parent_id: str, name: str, *, create: bool) -> str | None:
        row = self._listing(parent_id).get(_nfc(name))
        if row is not None:
            if row["mimeType"] != _FOLDER_MIME:
                raise TreeConflict(f"폴더 자리에 같은 이름의 파일이 있다: {name}")
            folder_id = row["id"]
        elif not create:
            return None
        else:
            folder_id = self.drive.create_folder(_nfc(name), parent_id)
            self._children[parent_id][_nfc(name)] = {"id": folder_id, "mimeType": _FOLDER_MIME}
            self._children[folder_id] = {}
        self.drive.verify_owner_only(folder_id)
        return folder_id

    def _same_file(self, folder_id: str, name: str, sha256: str, size: int) -> str | None:
        """The id of an existing same-name file holding exactly these bytes; TreeConflict for any other."""
        row = self._listing(folder_id).get(_nfc(name))
        if row is None:
            return None
        if row["mimeType"] != _FOLDER_MIME and _same_bytes(self.drive, row["id"], sha256, size):
            return row["id"]
        raise TreeConflict(f"같은 이름의 다른 파일이 이미 있다: {name}")

    def scan(self, parent_id: str, folders: Sequence[str],
             files: Sequence[tuple[str, str, int]]) -> TreeScan:
        """Read only: which folders exist, which files are already there unchanged, which would be replaced."""
        ids: dict[str, str | None] = {"": parent_id}
        for folder in folders:
            up, _, name = folder.rpartition("/")
            above = ids.get(up)
            try:
                ids[folder] = self.child_folder(above, name, create=False) if above else None
            except TreeConflict:
                ids[folder] = None
        unchanged: list[str] = []
        overwrite: list[tuple[str, str]] = []
        for path, sha256, size in files:
            up, _, name = path.rpartition("/")
            folder_id = ids.get(up)
            if not folder_id:
                continue
            try:
                if self._same_file(folder_id, name, sha256, size):
                    unchanged.append(path)
            except TreeConflict:
                overwrite.append((path, folder_id))
        existing = tuple(folder for folder in folders if ids.get(folder))
        return TreeScan(existing, tuple(unchanged), tuple(overwrite))

    def save_new(self, local: Path, name: str, folder_id: str, *, sha256: str, size: int) -> PublishResult:
        if _local_digest(local) != (sha256, size):
            raise ValueError("the approved file changed before upload")
        file_id = self._same_file(folder_id, name, sha256, size)
        action = "unchanged"
        if file_id is None:
            file_id = self.drive.upload_new(local, _nfc(name), folder_id)
            self._children[folder_id][_nfc(name)] = {"id": file_id, "mimeType": ""}
            action = "created"
        # A file found in place (an earlier try's upload) is checked exactly like a new one.
        self.drive.verify_owner_only(file_id)
        _ = self.drive.download_and_verify(file_id, local)
        return PublishResult((self.drive.web_view_link(file_id),), action, folder_id)


def publish_state_file(
    parts: Sequence[str],
    name: str,
    local: Path,
    *,
    client: DriveClient | None = None,
) -> str:
    """Upsert and verify one non-dated state file in its exact folder path."""
    normalized_name = _nfc(name)
    if not normalized_name or "/" in normalized_name or "\\" in normalized_name:
        raise TaxonomyError(f"invalid state file name: {name!r}")
    checked = ensure_depth((*parts, normalized_name))
    drive = client or client_from_environment()
    parent_id = drive.ensure_folder_path(checked[:-1])
    result = drive.upsert_file(local, normalized_name, parent_id)
    file_id = result["id"]
    drive.verify_owner_only(file_id)
    _ = drive.download_and_verify(file_id, local)
    return result["webViewLink"]


def fetch_state_file(
    parts: Sequence[str],
    name: str,
    dest: Path,
    *,
    client: DriveClient | None = None,
    fallback: Sequence[Sequence[str]] = (),
) -> bool:
    """Download one named non-dated state file when it exists in ``parts``.

    ``fallback`` folders (the legacy layout while PARA settles in) are only read:
    looked up with ``find_folder_path`` and never created.
    """
    drive = client or client_from_environment()
    normalized_name = _nfc(name)
    folders: list[str | None] = [drive.ensure_folder_path(tuple(parts))]
    folders += [drive.find_folder_path(tuple(other)) for other in fallback]
    for parent_id in folders:
        if parent_id is None:
            continue
        for child in drive.list_children(parent_id):
            if _nfc(str(child.get("name", ""))) != normalized_name:
                continue
            file_id = str(child.get("id", ""))
            if file_id:
                _ = drive.download_file(file_id, dest)
                return True
    return False


def publish_best_effort(
    kind: str,
    title: str,
    artifacts: Sequence[Artifact],
    *,
    companions: Sequence[Path] = (),
    on: date | None = None,
    project: str | None = None,
    client: DriveClient | None = None,
) -> PublishResult | None:
    """Publish only when opted in and reduce any failure to one safe marker."""
    if os.environ.get("DRIVE_PUBLISH_ENABLED") != "1":
        return None
    try:
        return publish(
            kind,
            title,
            artifacts,
            companions=companions,
            on=on,
            project=project,
            client=client,
        )
    except Exception as error:
        safe_kind = str(kind).replace("\r", "_").replace("\n", "_")
        print(
            f"DRIVE-PUBLISH-FAIL kind={safe_kind} reason={type(error).__name__}",
            file=sys.stderr,
        )
        return None
