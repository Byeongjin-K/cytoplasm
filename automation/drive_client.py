"""Idempotent Drive folder/file upsert over the existing ``gws drive`` CLI.

Generalizes ``procure_review._drive_upload``: find-or-create each folder in the
project tree (ids cached so re-syncs never duplicate folders), then upsert each
file by (name, parent) — update media if present, else ``+upload``. A ``runner``
seam lets unit tests inject an in-memory fake gws; production shells out.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from automation.drive_client_cache import _folder_alive as folder_alive
from automation.drive_client_cache import ensure_folder_path as resolve_folder_path
from automation.interop.approval_types import JsonValue

_JsonResult = dict[str, JsonValue] | list[JsonValue]
CommandRunner = Callable[[list[str]], _JsonResult]
_FOLDER_MIME = "application/vnd.google-apps.folder"
_PUBLIC_TYPES = frozenset({"anyone", "domain"})
_WRITE_ROLES = frozenset({"writer", "organizer", "fileOrganizer"})
_PERMISSION_FIELDS = "permissions(id,type,role)"
_SHARE_FIELDS = "permissions(id,type,role,emailAddress)"
#: The only role a per-person share may grant: view and download, nothing more.
SHARE_ROLE = "reader"


class DriveClientError(RuntimeError):
    """A gws Drive call failed or returned an unusable response (fail closed)."""



def _q_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _result_id(result: _JsonResult) -> str:
    return str(result.get("id", "")) if isinstance(result, dict) else ""


def _resolve_executable(executable: str) -> str:
    resolved = shutil.which(executable)
    if resolved is not None:
        return str(Path(resolved).resolve())
    if os.sep in executable or (os.altsep is not None and os.altsep in executable):
        return str(Path(executable).resolve())
    return executable


def _permission_rows(result: _JsonResult) -> list[dict[str, JsonValue]]:
    raw = result.get("permissions", []) if isinstance(result, dict) else []
    if not isinstance(raw, list):
        return []
    return [row for row in raw if isinstance(row, dict)]


def _reject_reason(ptype: str, role: str) -> str | None:
    """Name why a single permission breaks owner-only, or ``None`` when it is the owner."""
    if ptype in _PUBLIC_TYPES:
        return f"공개/도메인 공유 감지 type={ptype} role={role}"
    if role in _WRITE_ROLES:
        return f"소유자 외 쓰기 권한 감지 role={role}"
    if role != "owner":
        return f"소유자 외 권한 감지 type={ptype} role={role}"
    return None


@dataclass(frozen=True, slots=True)
class DriveClient:
    gws_bin: str
    folder_cache: Path
    runner: CommandRunner | None = None

    def _run(self, argv: list[str], *, cwd: Path | None = None) -> _JsonResult:
        if self.runner is not None:
            return self.runner(argv)
        if cwd is not None:
            argv = [_resolve_executable(argv[0]), *argv[1:]]
        proc = subprocess.run(  # noqa: S603
            argv, capture_output=True, text=True, timeout=600, check=False, cwd=cwd
        )
        if proc.returncode != 0:
            raise DriveClientError(
                f"{' '.join(argv[:4])} 실패 rc={proc.returncode}: {proc.stderr.strip()[:200]}"
            )
        decoded, _ = json.JSONDecoder().raw_decode(proc.stdout.strip() or "{}")
        return decoded

    def _list_first_id(self, query: str) -> str | None:
        result = self._run(
            [self.gws_bin, "drive", "files", "list", "--params",
             json.dumps({"q": query, "fields": "files(id,name)", "pageSize": 10})]
        )
        files = result.get("files", []) if isinstance(result, dict) else []
        if not isinstance(files, list) or not files:
            return None
        first = files[0]
        file_id = str(first.get("id", "")) if isinstance(first, dict) else ""
        return file_id or None

    def _find_folder(self, name: str, parent: str) -> str | None:
        query = (
            f"name = '{_q_escape(name)}' and '{parent}' in parents "
            f"and mimeType = '{_FOLDER_MIME}' and trashed = false"
        )
        return self._list_first_id(query)

    def _create_folder(self, name: str, parent: str) -> str:
        result = self._run(
            [self.gws_bin, "drive", "files", "create", "--json",
             json.dumps({"name": name, "mimeType": _FOLDER_MIME, "parents": [parent]})]
        )
        folder_id = _result_id(result)
        if not folder_id:
            raise DriveClientError(f"폴더 생성 응답에 id 없음: {name}")
        return folder_id

    def _find_file(self, name: str, parent: str) -> str | None:
        return self._list_first_id(
            f"name = '{_q_escape(name)}' and '{parent}' in parents and trashed = false"
        )

    def _upload_new(self, local: Path, name: str, parent: str) -> str:
        upload_path = str(local) if self.runner is not None else local.name
        result = self._run(
            [self.gws_bin, "drive", "+upload", upload_path, "--parent", parent, "--name", name],
            cwd=None if self.runner is not None else local.parent,
        )
        file_id = _result_id(result)
        if not file_id:
            raise DriveClientError(f"업로드 응답에 id 없음: {name}")
        return file_id

    def _update_media(self, file_id: str, local: Path) -> str:
        upload_path = str(local) if self.runner is not None else local.name
        result = self._run(
            [self.gws_bin, "drive", "files", "update", "--params",
             json.dumps({"fileId": file_id}), "--upload", upload_path],
            cwd=None if self.runner is not None else local.parent,
        )
        return _result_id(result) or file_id

    def _files_update(self, file_id: str, *, params: dict[str, str] | None = None,
                      body: dict[str, JsonValue] | None = None) -> str:
        try:
            argv = [self.gws_bin, "drive", "files", "update", "--params",
                    json.dumps({"fileId": file_id, **(params or {})})]
            if body is not None:
                argv += ["--json", json.dumps(body)]
            return _result_id(self._run(argv)) or file_id
        except DriveClientError:
            raise
        except Exception as error:
            raise DriveClientError("Drive 파일 갱신 실패") from error

    def move_file(self, file_id: str, add_parent: str, remove_parent: str) -> str:
        return self._files_update(file_id, params={"addParents": add_parent, "removeParents": remove_parent})

    def rename_file(self, file_id: str, new_name: str) -> str:
        return self._files_update(file_id, body={"name": new_name})

    def list_children(self, folder_id: str) -> list[dict[str, JsonValue]]:
        files: list[dict[str, JsonValue]] = []
        page_token: str | None = None
        try:
            while True:
                params: dict[str, str | int] = {
                    "q": f"'{folder_id}' in parents and trashed = false",
                    "fields": "files(id,name,mimeType,modifiedTime,createdTime,size)",
                    "pageSize": 1000,
                }
                if page_token:
                    params["pageToken"] = page_token
                result = self._run(
                    [self.gws_bin, "drive", "files", "list", "--params", json.dumps(params)]
                )
                raw_files = result.get("files", []) if isinstance(result, dict) else None
                if not isinstance(raw_files, list):
                    raise DriveClientError("자식 파일 목록 응답이 올바르지 않음")
                files.extend(row for row in cast(list[object], raw_files) if isinstance(row, dict))
                raw_token = result.get("nextPageToken") if isinstance(result, dict) else None
                page_token = raw_token if isinstance(raw_token, str) else None
                if not isinstance(page_token, str) or not page_token:
                    return files
        except DriveClientError:
            raise
        except Exception as error:
            raise DriveClientError("자식 파일 목록 조회 실패") from error

    def trash_file(self, file_id: str) -> str:
        return self._files_update(file_id, body={"trashed": True})

    def web_view_link(self, file_id: str) -> str:
        return self._web_view_link(file_id)

    def find_file(self, name: str, parent_id: str) -> str | None:
        """The id of the untrashed file ``name`` directly under ``parent_id``, if any."""
        return self._find_file(name, parent_id)

    def _web_view_link(self, file_id: str) -> str:
        result = self._run(
            [self.gws_bin, "drive", "files", "get", "--params",
             json.dumps({"fileId": file_id, "fields": "webViewLink"})]
        )
        link = str(result.get("webViewLink", "")) if isinstance(result, dict) else ""
        return link or f"https://drive.google.com/file/d/{file_id}/view"

    def file_checksum(self, file_id: str) -> tuple[str, int]:
        """Return ``(sha256Checksum, size)`` Drive reports for ``file_id`` — a read, no media.

        The read-back an upload verification needs when the local copy is the only
        other party: comparing the two metadata fields costs one API call, while
        :meth:`download_and_verify` re-fetches every byte. A response missing either
        field is unusable (Google-native documents carry neither), so it fails closed
        rather than reporting an unverified upload as verified.
        """
        result = self._run(
            [self.gws_bin, "drive", "files", "get", "--params",
             json.dumps({"fileId": file_id, "fields": "sha256Checksum,size"})]
        )
        checksum = str(result.get("sha256Checksum", "")) if isinstance(result, dict) else ""
        raw_size = result.get("size") if isinstance(result, dict) else None
        if not checksum or raw_size is None:
            raise DriveClientError(
                f"체크섬/크기 응답 불완전(fail-closed): {file_id} "
                f"sha256={checksum or '없음'} size={raw_size if raw_size is not None else '없음'}"
            )
        try:
            size = int(str(raw_size))
        except ValueError as error:
            raise DriveClientError(f"크기 값이 정수가 아님: {file_id} size={raw_size!r}") from error
        return checksum, size

    def _folder_alive(self, file_id: str) -> bool:
        return folder_alive(file_id, gws_bin=self.gws_bin, run=self._run)

    def find_folder_path(self, parts: tuple[str, ...]) -> str | None:
        """Resolve an existing folder path, or ``None`` — never creates, never caches.

        The sibling of ``ensure_folder_path`` for folders we do not own: the owner's
        own reference shelf must not gain a folder because we looked for it, and the
        shared publish cache must not learn ids this read-only path resolved.
        """
        parent = "root"
        for name in parts:
            found = self._find_folder(name, parent)
            if found is None:
                return None
            parent = found
        return parent if parts else None

    def ensure_folder_path(self, parts: tuple[str, ...]) -> str:
        if not parts:
            raise DriveClientError("folder path is empty")
        return resolve_folder_path(
            parts,
            folder_cache=self.folder_cache,
            find_folder=self._find_folder,
            create_folder=self._create_folder,
            folder_alive=self._folder_alive,
        )

    def upsert_file(
        self, local: Path, name: str, parent_id: str, prior_id: str | None = None
    ) -> dict[str, str]:
        existing = prior_id or self._find_file(name, parent_id)
        if existing:
            file_id = self._update_media(existing, local)
            action = "updated"
        else:
            file_id = self._upload_new(local, name, parent_id)
            action = "created"
        return {"id": file_id, "webViewLink": self._web_view_link(file_id), "action": action}

    def verify_owner_only(self, file_id: str) -> None:
        """Fail closed unless the owner is the sole principal holding ``file_id``."""
        result = self._run(
            [self.gws_bin, "drive", "permissions", "list", "--params",
             json.dumps({"fileId": file_id, "fields": _PERMISSION_FIELDS})]
        )
        rows = _permission_rows(result)
        if not rows:
            raise DriveClientError(f"권한 목록이 비어 판정 불가(fail-closed): {file_id}")
        for row in rows:
            reason = _reject_reason(str(row.get("type", "")), str(row.get("role", "")))
            if reason is not None:
                raise DriveClientError(f"{reason}: {file_id}")
        if len(rows) != 1:
            raise DriveClientError(
                f"소유자 권한이 유일하지 않음({len(rows)}건, fail-closed): {file_id}"
            )

    def shared_readers(self, file_id: str) -> frozenset[str]:
        """The people ``file_id`` is shared with, failing closed on anything but owner + readers.

        A per-person share is the owner plus named ``user`` readers and nothing else:
        an ``anyone``/``domain`` grant would turn the link public, a ``group`` hides who
        can open it, and any write role lets someone replace the file the owner approved.
        """
        rows = _permission_rows(self._run(
            [self.gws_bin, "drive", "permissions", "list", "--params",
             json.dumps({"fileId": file_id, "fields": _SHARE_FIELDS})]
        ))
        owners = 0
        readers: set[str] = set()
        for row in rows:
            ptype = str(row.get("type", ""))
            role = str(row.get("role", ""))
            if ptype in _PUBLIC_TYPES:
                raise DriveClientError(f"공개/도메인 공유 감지 type={ptype} role={role}: {file_id}")
            if role == "owner":
                owners += 1
                continue
            email = str(row.get("emailAddress", "")).strip().lower()
            if ptype != "user" or role != SHARE_ROLE or not email:
                raise DriveClientError(f"개인 보기 권한이 아닌 공유 감지 type={ptype} role={role}: {file_id}")
            readers.add(email)
        if owners != 1:
            raise DriveClientError(f"소유자 권한이 유일하지 않음({owners}건, fail-closed): {file_id}")
        return frozenset(readers)

    def grant_reader(self, file_id: str, email: str) -> bool:
        """Give one named person view-only access; return whether Google mailed them an invite.

        The mail carries the link, so Drive is asked to stay silent. Drive refuses a silent
        invite to an address with no Google account; the owner chose (Q273, 2026-10-19 12:31
        KST) to share anyway and let Google send its own invite in that case.
        """
        def create(notify: bool) -> None:
            _ = self._run(
                [self.gws_bin, "drive", "permissions", "create", "--params",
                 json.dumps({"fileId": file_id, "sendNotificationEmail": notify}),
                 "--json", json.dumps({"type": "user", "role": SHARE_ROLE, "emailAddress": email})]
            )

        try:
            create(False)
        except DriveClientError:
            create(True)
            return True
        return False

    def _fetch_media(self, file_id: str, fetched: Path, tmp: Path, export_as: str = "") -> None:
        """Land ``file_id``'s bytes at ``fetched``, inside the private directory ``tmp``.

        ``export_as`` names the MIME type for a Google-native document, which has no
        media to download and answers ``alt=media`` with an error.
        """
        argv = [
            _resolve_executable(self.gws_bin),
            "drive",
            "files",
            "export" if export_as else "get",
            "--params",
            json.dumps(
                {"fileId": file_id, "mimeType": export_as}
                if export_as
                else {"fileId": file_id, "alt": "media"}
            ),
        ]
        if self.runner is not None:
            self._run([*argv, "-o", str(fetched)], cwd=tmp)
            return
        # gws currently emits `alt=media` bytes to stdout even when --output is
        # supplied. Capture the binary stream directly instead of trusting a
        # success exit code that produced no read-back artifact.
        proc = subprocess.run(  # noqa: S603
            argv,
            capture_output=True,
            timeout=600,
            check=False,
            cwd=tmp,
        )
        if proc.returncode != 0:
            detail = proc.stderr.decode("utf-8", "replace").strip()[:200]
            raise DriveClientError(
                f"{' '.join(argv[:4])} 실패 rc={proc.returncode}: {detail}"
            )
        try:
            metadata = json.loads(proc.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            metadata = None
        saved_name = metadata.get("saved_file") if isinstance(metadata, dict) else None
        if isinstance(saved_name, str) and saved_name:
            saved = (tmp / saved_name).resolve()
            try:
                saved.relative_to(tmp.resolve())
            except ValueError as error:
                raise DriveClientError("gws download escaped its private directory") from error
            if saved.is_symlink() or not saved.is_file():
                raise DriveClientError(f"재다운로드 산출물 없음(fail-closed): {file_id}")
            saved.replace(fetched)
        else:
            fetched.write_bytes(proc.stdout)

    def download_file(self, file_id: str, dest: Path, *, export_as: str = "") -> str:
        """Fetch ``file_id`` into ``dest`` and return its sha256 (no read-back to compare).

        The verify-after-upload path re-downloads to compare against a local file;
        this one is the plain read used when the remote file is the only copy —
        an owner's recording sitting in the watched Drive folder, for instance.
        """
        with tempfile.TemporaryDirectory(prefix="drive-fetch-") as tmp:
            fetched = Path(tmp) / "remote.bin"
            self._fetch_media(file_id, fetched, Path(tmp), export_as)
            if not fetched.is_file():
                raise DriveClientError(f"다운로드 산출물 없음(fail-closed): {file_id}")
            payload = fetched.read_bytes()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)
        return hashlib.sha256(payload).hexdigest()

    def _is_gws_json_rendering(
        self, file_id: str, local_bytes: bytes, fetched_bytes: bytes, expected: str
    ) -> bool:
        """True when a byte mismatch is only gws re-serializing a stored JSON file.

        gws parses a JSON media response and prints its own rendering (sorted keys,
        two-space indent), so a JSON file whose local bytes use another layout never
        matches its re-download even though Drive stores it byte for byte (2026-09-28:
        proposal ``figures.json``/``tables.json`` publish refused). Byte integrity then
        comes from Drive's own ``sha256Checksum`` over the stored object, and the
        re-download must still carry the same JSON value — any other mismatch stays fatal.
        """
        try:
            same_value = cast(object, json.loads(local_bytes)) == cast(
                object, json.loads(fetched_bytes)
            )
        except ValueError:
            return False
        if not same_value:
            return False
        checksum, size = self.file_checksum(file_id)
        return checksum == expected and size == len(local_bytes)

    def download_and_verify(self, file_id: str, local: Path) -> str:
        """Re-download ``file_id`` and fail closed unless its sha256 matches ``local``."""
        local_bytes = local.read_bytes()
        expected = hashlib.sha256(local_bytes).hexdigest()
        with tempfile.TemporaryDirectory(prefix="drive-verify-") as tmp:
            fetched = Path(tmp) / "remote.bin"
            self._fetch_media(file_id, fetched, Path(tmp))
            if not fetched.is_file():
                raise DriveClientError(f"재다운로드 산출물 없음(fail-closed): {file_id}")
            fetched_bytes = fetched.read_bytes()
            actual = hashlib.sha256(fetched_bytes).hexdigest()
        if actual != expected and self._is_gws_json_rendering(
            file_id, local_bytes, fetched_bytes, expected
        ):
            return expected
        if actual != expected:
            raise DriveClientError(
                f"재다운로드 sha256 불일치 {file_id}: "
                f"local={expected[:12]}… remote={actual[:12]}…"
            )
        return actual
