"""MailOn attachment → Drive archive: every Google call goes through DriveClient.

The archive runs in production (3,342 rows, 107 cached folders), so two facts are
pinned harder than the rest: an already-archived row is never uploaded again, and
the existing ``folders.json`` key shape still resolves from cache. Verification
compares the remote checksum metadata — a re-download of the 2.9 GB corpus on
every tick is not verification, it is a second copy of the traffic.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "skills" / "mail" / "scripts"
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_SCRIPTS))

from automation.drive_client import DriveClient  # noqa: E402
import mail_attachment_archive as archive  # noqa: E402

_PATH = _SCRIPTS / "mail_attachment_drive_sync.py"
_SPEC = importlib.util.spec_from_file_location("mail_attachment_drive_sync", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
syncer = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = syncer
_SPEC.loader.exec_module(syncer)


def _source_db(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE messages (
          uid TEXT PRIMARY KEY, folder TEXT, recv_date TEXT
        );
        CREATE TABLE attachments (
          uid TEXT NOT NULL, filename TEXT NOT NULL, local_path TEXT,
          first_seen TEXT, status TEXT NOT NULL, attempts INTEGER DEFAULT 0
        );
        """
    )
    return db


class FakeGws:
    """In-memory gws for the argv DriveClient builds — the injected runner seam."""

    def __init__(self) -> None:
        self.folders: dict[tuple[str, str], str] = {}
        self.files: dict[tuple[str, str], str] = {}
        self.contents: dict[str, bytes] = {}
        self.uploads: list[str] = []
        self.permissions: list[dict[str, object]] = [
            {"id": "p-own", "type": "user", "role": "owner"}
        ]
        #: When set, the remote object reports a different sha256 at the same size.
        self.tamper = False
        self.calls: list[list[str]] = []
        self._n = 0

    def _new(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n}"

    def _store(self, file_id: str, local: Path) -> None:
        self.contents[file_id] = local.read_bytes()
        self.uploads.append(file_id)

    def __call__(self, argv: list[str]) -> dict[str, object]:
        self.calls.append(argv)
        if argv[2] == "+upload":
            parent = argv[argv.index("--parent") + 1]
            name = argv[argv.index("--name") + 1]
            file_id = self._new("file")
            self.files[(name, parent)] = file_id
            self._store(file_id, Path(argv[3]))
            return {"id": file_id}
        if argv[2] == "permissions":
            return {"permissions": list(self.permissions)}
        method = argv[3]
        params = json.loads(argv[argv.index("--params") + 1]) if "--params" in argv else {}
        if method == "list":
            query = str(params["q"])
            name = re.search(r"name = '((?:[^'\\]|\\.)*)'", query).group(1)
            name = name.replace("\\'", "'").replace("\\\\", "\\")
            parent = re.search(r"'([^']+)' in parents", query).group(1)
            registry = (
                self.folders
                if "application/vnd.google-apps.folder" in query
                else self.files
            )
            found = registry.get((name, parent))
            return {"files": [{"id": found, "name": name}] if found else []}
        if method == "create":
            meta = json.loads(argv[argv.index("--json") + 1])
            folder_id = self._new("fold")
            self.folders[(str(meta["name"]), str(meta["parents"][0]))] = folder_id
            return {"id": folder_id}
        if method == "update":
            file_id = str(params["fileId"])
            if "--upload" in argv:
                self._store(file_id, Path(argv[argv.index("--upload") + 1]))
            return {"id": file_id}
        if method == "get":
            file_id = str(params["fileId"])
            if params.get("fields") == "sha256Checksum,size":
                payload = self.contents[file_id]
                stored = b"?" * len(payload) if self.tamper else payload
                return {
                    "sha256Checksum": hashlib.sha256(stored).hexdigest(),
                    "size": str(len(stored)),
                }
            return {"webViewLink": f"https://drive.google.com/file/d/{file_id}/view"}
        raise AssertionError(f"unexpected argv {argv}")


def _calls_of(fake: FakeGws, *prefix: str) -> list[list[str]]:
    return [call for call in fake.calls if call[2 : 2 + len(prefix)] == list(prefix)]


def _attachment(local: Path, uid: str = "u1", **overrides: object) -> object:
    stat = local.stat()
    fields: dict[str, object] = {
        "key": archive.attachment_key(uid, local.name), "uid": uid,
        "filename": local.name, "local": local, "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns, "year": "2026", "month": "08", "mailbox": "inbox",
    }
    fields.update(overrides)
    return archive.Attachment(**fields)


def _archive_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state_dir = tmp_path / "archive"
    monkeypatch.setattr(archive, "STATE_DIR", state_dir)
    monkeypatch.setattr(archive, "STATE_DB", state_dir / "archive.db")
    return state_dir


def _client(fake: FakeGws) -> DriveClient:
    return DriveClient(gws_bin="gws", folder_cache=archive.folder_cache(), runner=fake)


def _archived_keys(state_dir: Path) -> list[str]:
    db = sqlite3.connect(state_dir / "archive.db")
    try:
        return [str(row[0]) for row in db.execute("SELECT attachment_key FROM archived")]
    finally:
        db.close()


def test_discover_excludes_ui_controls_and_uses_runtime_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source_db = tmp_path / "state.db"
    runtime = tmp_path / "runtime"
    runtime_file = runtime / "u1" / "report.pdf"
    runtime_file.parent.mkdir(parents=True)
    runtime_file.write_bytes(b"pdf")
    db = _source_db(source_db)
    db.execute("INSERT INTO messages VALUES ('u1','inbox','2026-08-01T01:02:03')")
    db.executemany(
        "INSERT INTO attachments (uid,filename,local_path,first_seen,status) VALUES (?,?,?,?,?)",
        [
            ("u1", "report.pdf", "/missing/report.pdf", "", "ok"),
            ("u1", "Save", "/missing/Save", "", "ok"),
            ("u1", "Download all", "/missing/all", "", "ok"),
        ],
    )
    db.commit()
    db.close()
    monkeypatch.setattr(archive, "SOURCE_DB", source_db)
    monkeypatch.setattr(archive, "RUNTIME_ATTACHMENTS", runtime)

    items, bogus, missing = archive.discover()

    assert len(items) == 1
    assert items[0].local == runtime_file.resolve()
    assert (items[0].year, items[0].month, items[0].mailbox) == ("2026", "08", "inbox")
    assert bogus == 2
    assert missing == 0


def test_sync_is_resumable_and_skips_unchanged_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    first = tmp_path / "first.bin"
    second = tmp_path / "second.bin"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    items = [_attachment(first, "u1"), _attachment(second, "u2")]
    _archive_state(tmp_path, monkeypatch)
    monkeypatch.setattr(archive, "discover", lambda: (items, 0, 0))
    fake = FakeGws()

    first_result = syncer.sync(client=_client(fake), workers=2)
    second_result = syncer.sync(client=_client(fake), workers=2)

    assert first_result["uploaded"] == 2
    assert first_result["failed"] == 0
    assert second_result["uploaded"] == 0
    assert second_result["skipped"] == 2
    assert len(fake.uploads) == 2  # the second tick uploaded nothing


def test_already_archived_row_is_not_uploaded_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: the production shape — a row recorded by an earlier run for this exact file.
    local = tmp_path / "old.pdf"
    local.write_bytes(b"already in the archive")
    item = _attachment(local, "u9")
    state_dir = _archive_state(tmp_path, monkeypatch)
    monkeypatch.setattr(archive, "discover", lambda: ([item], 0, 0))
    state = archive.state_connection()
    archive.record(state, item, "sha", "fold-prior", "file-prior")
    state.close()
    fake = FakeGws()

    result = syncer.sync(client=_client(fake), workers=1)

    assert (result["uploaded"], result["skipped"], result["failed"]) == (0, 1, 0)
    assert fake.calls == []  # not one Google call for an item already archived
    assert _archived_keys(state_dir) == [item.key]


def test_existing_folder_cache_keys_resolve_without_creating_folders(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: the 107 folder ids production already cached, in their shipped key shape.
    state_dir = _archive_state(tmp_path, monkeypatch)
    state_dir.mkdir(mode=0o700, parents=True)
    cached = {
        "autophagy": "fold-root",
        "autophagy/메일 첨부파일": "fold-mail",
        "autophagy/메일 첨부파일/2023": "fold-2023",
        "autophagy/메일 첨부파일/2023/07": "fold-07",
        "autophagy/메일 첨부파일/2023/07/sent": "fold-sent",
    }
    (state_dir / "folders.json").write_text(
        json.dumps(cached, ensure_ascii=False), encoding="utf-8"
    )
    fake = FakeGws()

    parent_id = syncer.ensure_parent(_client(fake), (*archive.ROOT_PARTS, "2023", "07", "sent"))

    assert parent_id == "fold-sent"
    assert _calls_of(fake, "files", "create") == []
    assert json.loads((state_dir / "folders.json").read_text(encoding="utf-8")) == cached


def test_remote_name_is_bounded_utf8_and_keeps_extension(tmp_path: Path) -> None:
    local = tmp_path / ("긴이름" * 30 + ".pdf")
    item = archive.Attachment(
        key="a" * 64, uid="123", filename=local.name, local=local,
        size=1, mtime_ns=1, year="2026", month="08", mailbox="sent",
    )

    name = archive.remote_name(item)

    assert len(name.encode("utf-8")) <= archive.MAX_REMOTE_NAME_BYTES
    assert name.startswith("123__") and name.endswith(".pdf")
    assert item.key[:12] in name


def test_upload_is_verified_against_remote_checksum_without_downloading(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    local = tmp_path / "paper.pdf"
    local.write_bytes(b"attachment bytes")
    item = _attachment(local, "u7")
    _archive_state(tmp_path, monkeypatch)
    monkeypatch.setattr(archive, "discover", lambda: ([item], 0, 0))
    fake = FakeGws()

    result = syncer.sync(client=_client(fake), workers=1)

    assert result["uploaded"] == 1
    checksum_reads = [
        call for call in _calls_of(fake, "files", "get")
        if json.loads(call[call.index("--params") + 1]).get("fields") == "sha256Checksum,size"
    ]
    assert len(checksum_reads) == 1
    assert all(
        json.loads(call[call.index("--params") + 1]).get("alt") != "media"
        for call in _calls_of(fake, "files", "get")
    )
    assert _calls_of(fake, "permissions", "list")  # owner-only proof still runs


def test_checksum_mismatch_fails_the_item_and_records_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    local = tmp_path / "paper.pdf"
    local.write_bytes(b"attachment bytes")
    item = _attachment(local, "u7")
    state_dir = _archive_state(tmp_path, monkeypatch)
    monkeypatch.setattr(archive, "discover", lambda: ([item], 0, 0))
    fake = FakeGws()
    fake.tamper = True

    result = syncer.sync(client=_client(fake), workers=1)

    assert (result["uploaded"], result["failed"]) == (0, 1)
    assert result["failure_code"] == "checksum_mismatch"
    assert _archived_keys(state_dir) == []  # an unverified object is not archived


def test_drive_client_uses_the_gws_override_and_the_shared_folder_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state_dir = _archive_state(tmp_path, monkeypatch)
    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(_REPO))
    monkeypatch.setenv(syncer.GWS_BIN_ENV, str(tmp_path / "fake-gws"))

    client = syncer.drive_client()

    assert client.gws_bin == str(tmp_path / "fake-gws")
    assert client.folder_cache == state_dir / "folders.json"


def _run_main(monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["mail_attachment_drive_sync.py", *argv])
    return syncer.main()


def test_main_is_silent_on_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _archive_state(tmp_path, monkeypatch)
    monkeypatch.setenv("AUTOPHAGY_SKILL_LIVE_ROOT", str(_REPO / "skills"))
    monkeypatch.setattr(
        syncer, "sync", lambda **_kwargs: {"failed": 0, "uploaded": 3, "failure_code": ""}
    )

    assert _run_main(monkeypatch) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_main_reports_a_failure_as_one_json_object_with_a_code_on_stderr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _archive_state(tmp_path, monkeypatch)
    monkeypatch.setenv("AUTOPHAGY_SKILL_LIVE_ROOT", str(_REPO / "skills"))

    def boom(**_kwargs: object) -> dict[str, object]:
        raise syncer.SyncError("source_db_missing", "MailOn state DB not found")

    monkeypatch.setattr(syncer, "sync", boom)

    assert _run_main(monkeypatch) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err.strip()) == {
        "status": "error", "code": "source_db_missing"
    }


def test_main_reports_a_partial_run_with_the_first_failure_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _archive_state(tmp_path, monkeypatch)
    monkeypatch.setenv("AUTOPHAGY_SKILL_LIVE_ROOT", str(_REPO / "skills"))
    monkeypatch.setattr(
        syncer, "sync",
        lambda **_kwargs: {"failed": 2, "uploaded": 1, "failure_code": "checksum_mismatch"},
    )

    assert _run_main(monkeypatch) == 1
    payload = json.loads(capsys.readouterr().err.strip())
    assert payload["code"] == "checksum_mismatch"
    assert payload["status"] == "partial"


def test_main_refuses_a_copy_outside_the_governed_mount(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    live = tmp_path / "live"
    (live / "mail" / "scripts").mkdir(parents=True)
    monkeypatch.setenv("AUTOPHAGY_SKILL_LIVE_ROOT", str(live))
    monkeypatch.setattr(
        syncer, "sync", lambda **_kwargs: pytest.fail("a stale copy must not touch Drive")
    )

    assert _run_main(monkeypatch) == 3
    assert "STALE-SKILL-COPY-BLOCK" in capsys.readouterr().err
