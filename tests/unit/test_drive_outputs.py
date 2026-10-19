"""Taxonomy-aware Drive output publishing specs via an in-memory fake gws."""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date
from pathlib import Path

import pytest

from automation.drive_client import DriveClient, DriveClientError
from automation.drive_outputs import PublishResult, client_from_environment, publish, publish_best_effort
from automation.drive_taxonomy import TaxonomyError
from automation.entity_preflight.contracts import JsonValue

_FOLDER_MIME = "application/vnd.google-apps.folder"


def _query_name(query: str) -> str:
    match = re.search(r"name = '((?:[^'\\]|\\.)*)'", query)
    assert match is not None
    return match.group(1).replace("\\'", "'").replace("\\\\", "\\")


def _query_parent(query: str) -> str:
    match = re.search(r"'([^']+)' in parents", query)
    assert match is not None
    return match.group(1)


class FakeGws:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.folders: dict[tuple[str, str], str] = {}
        self.files: dict[tuple[str, str], str] = {}
        self.children: dict[str, list[dict[str, JsonValue]]] = {}
        self.file_bytes: dict[str, bytes] = {}
        self.permissions: list[dict[str, JsonValue]] = [
            {"id": "owner", "type": "user", "role": "owner"}
        ]
        self._counter = 0

    def _new_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}-{self._counter}"

    def seed_folder(self, name: str, parent: str, folder_id: str) -> None:
        self.folders[(name, parent)] = folder_id
        self.children.setdefault(parent, []).append(
            {"id": folder_id, "name": name, "mimeType": _FOLDER_MIME}
        )

    def seed_file(self, name: str, parent: str, file_id: str, payload: bytes = b"") -> None:
        self.files[(name, parent)] = file_id
        self.file_bytes[file_id] = payload
        self.children.setdefault(parent, []).append(
            {"id": file_id, "name": name, "mimeType": "application/octet-stream"}
        )

    def __call__(self, argv: list[str]) -> dict[str, JsonValue]:
        self.calls.append(argv)
        resource = argv[2]
        if resource == "+upload":
            local = Path(argv[3])
            parent = argv[argv.index("--parent") + 1]
            name = argv[argv.index("--name") + 1]
            file_id = self._new_id("file")
            self.seed_file(name, parent, file_id, local.read_bytes())
            return {"id": file_id}

        method = argv[3]
        if resource == "permissions":
            assert method == "list"
            return {"permissions": list(self.permissions)}

        if method == "list":
            params = json.loads(argv[argv.index("--params") + 1])
            query = params["q"]
            parent = _query_parent(query)
            if "name =" not in query:
                return {"files": list(self.children.get(parent, []))}
            name = _query_name(query)
            registry = self.folders if _FOLDER_MIME in query else self.files
            found = registry.get((name, parent))
            return {"files": [{"id": found, "name": name}] if found else []}

        if method == "create":
            metadata = json.loads(argv[argv.index("--json") + 1])
            folder_id = self._new_id("folder")
            self.seed_folder(metadata["name"], metadata["parents"][0], folder_id)
            return {"id": folder_id}

        if method == "update":
            params = json.loads(argv[argv.index("--params") + 1])
            file_id = params["fileId"]
            if "--upload" in argv:
                self.file_bytes[file_id] = Path(argv[argv.index("--upload") + 1]).read_bytes()
            return {"id": file_id}

        if method == "get":
            params = json.loads(argv[argv.index("--params") + 1])
            file_id = params["fileId"]
            if params.get("alt") == "media":
                Path(argv[argv.index("-o") + 1]).write_bytes(self.file_bytes[file_id])
                return {}
            if params.get("fields") == "id,trashed,parents":
                return {"id": file_id, "trashed": False, "parents": ["root"]}
            return {"webViewLink": f"https://drive.google.test/{file_id}"}

        raise AssertionError(f"unexpected argv: {argv}")


def _client(tmp_path: Path, fake: FakeGws) -> DriveClient:
    return DriveClient("fake-gws", tmp_path / "folders.json", runner=fake)


def _artifact(tmp_path: Path, name: str = "proposal.md", payload: str = "content") -> Path:
    path = tmp_path / name
    path.write_text(payload, encoding="utf-8")
    return path


def _upload_calls(fake: FakeGws) -> list[list[str]]:
    return [call for call in fake.calls if call[2] == "+upload"]


def _media_updates(fake: FakeGws) -> list[list[str]]:
    return [call for call in fake.calls if call[2:4] == ["files", "update"] and "--upload" in call]


def _folder_creates(fake: FakeGws) -> list[dict[str, object]]:
    return [
        json.loads(call[call.index("--json") + 1])
        for call in fake.calls
        if call[2:4] == ["files", "create"]
    ]


def test_same_publish_twice_creates_once_then_updates_without_duplicates(tmp_path: Path) -> None:
    fake = FakeGws()
    artifact = _artifact(tmp_path)
    client = _client(tmp_path, fake)

    first = publish(
        "proposal", "제안서X", [(artifact, "제안서X")], on=date(2026, 8, 23), client=client
    )
    second = publish(
        "proposal", "제안서X", [(artifact, "제안서X")], on=date(2026, 8, 23), client=client
    )

    assert isinstance(first, PublishResult)
    assert (first.action, second.action) == ("created", "updated")
    assert first.links == second.links
    assert len(_upload_calls(fake)) == 1
    assert len(_media_updates(fake)) == 1
    assert len(fake.files) == 1


def test_oneshot_sticky_scans_years_newest_first_and_reuses_original_date(tmp_path: Path) -> None:
    fake = FakeGws()
    fake.seed_folder("autophagy", "root", "outputs")
    fake.seed_folder("제안서", "outputs", "proposal-category")
    fake.seed_folder("2025", "proposal-category", "year-2025")
    fake.seed_folder("2026", "proposal-category", "year-2026")
    fake.seed_folder("2026-08-01_제안서X", "year-2026", "old-bundle")
    artifact = _artifact(tmp_path)

    result = publish(
        "proposal",
        "제안서X",
        [(artifact, "제안서X")],
        companions=[_artifact(tmp_path, "prompt.txt")],
        on=date(2026, 8, 23),
        client=_client(tmp_path, fake),
    )

    assert result.folder_id == "old-bundle"
    uploaded_names = [call[call.index("--name") + 1] for call in _upload_calls(fake)]
    assert "2026-08-01_제안서X.md" in uploaded_names
    year_queries = [
        json.loads(call[call.index("--params") + 1])["q"]
        for call in fake.calls
        if call[2:4] == ["files", "list"] and "name =" not in json.loads(call[call.index("--params") + 1])["q"]
    ]
    assert year_queries[:2] == [
        "'proposal-category' in parents and trashed = false",
        "'year-2026' in parents and trashed = false",
    ]


def test_single_artifact_is_file_leaf_but_companion_makes_bundle(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    single_fake = FakeGws()
    single = publish(
        "proposal", "제안서X", [(artifact, "제안서X")],
        on=date(2026, 8, 23), client=_client(tmp_path / "single", single_fake)
    )
    assert single.folder_id
    assert not any(item["name"] == "2026-08-23_제안서X" for item in _folder_creates(single_fake))

    bundled_fake = FakeGws()
    decomposed_name = unicodedata.normalize("NFD", "이미지프롬프트.txt")
    companion = _artifact(tmp_path, decomposed_name)
    bundled = publish(
        "proposal", "제안서X", [(artifact, "제안서X")], companions=[companion],
        on=date(2026, 8, 23), client=_client(tmp_path / "bundle", bundled_fake)
    )
    bundle_folders = [item for item in _folder_creates(bundled_fake) if item["name"] == "2026-08-23_제안서X"]
    assert len(bundle_folders) == 1
    assert bundled.folder_id
    names = [call[call.index("--name") + 1] for call in _upload_calls(bundled_fake)]
    assert unicodedata.normalize("NFC", decomposed_name) in names


def test_report_always_bundles_even_one_artifact(tmp_path: Path) -> None:
    fake = FakeGws()
    artifact = _artifact(tmp_path, "report.md")

    result = publish(
        "report", "주간연구동향", [(artifact, "주간연구동향")],
        on=date(2026, 8, 23), client=_client(tmp_path, fake)
    )

    bundles = [item for item in _folder_creates(fake) if item["name"] == "2026-W34_주간연구동향"]
    assert len(bundles) == 1
    assert result.folder_id == fake.folders[("2026-W34_주간연구동향", fake.folders[("2026", fake.folders[("주간동향", fake.folders[("autophagy", "root")])])])]


def test_best_effort_disabled_makes_zero_drive_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DRIVE_PUBLISH_ENABLED", raising=False)
    fake = FakeGws()

    assert publish_best_effort(
        "proposal", "제안서X", [(_artifact(tmp_path), "제안서X")], client=_client(tmp_path, fake)
    ) is None
    assert fake.calls == []


def test_owner_verify_failure_is_strict_and_best_effort_marker_is_path_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DRIVE_PUBLISH_ENABLED", "1")
    secret_dir = tmp_path / "private-secret"
    secret_dir.mkdir()
    artifact = _artifact(secret_dir)
    fake = FakeGws()
    fake.permissions.append({"id": "reader", "type": "user", "role": "reader"})
    client = _client(tmp_path, fake)

    with pytest.raises(DriveClientError):
        publish("proposal", "제안서X", [(artifact, "제안서X")], client=client)
    assert publish_best_effort(
        "proposal", "제안서X", [(artifact, "제안서X")], client=client
    ) is None

    marker = capsys.readouterr().err
    assert marker == "DRIVE-PUBLISH-FAIL kind=proposal reason=DriveClientError\n"
    assert str(tmp_path) not in marker
    assert "private-secret" not in marker
    assert artifact.read_text(encoding="utf-8") not in marker


def test_gate_only_and_missing_inputs_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    artifact = _artifact(tmp_path)
    with pytest.raises(TaxonomyError):
        publish("patent", "특허", [(artifact, "특허")], client=_client(tmp_path, FakeGws()))

    missing = tmp_path / "private-missing.md"
    with pytest.raises(FileNotFoundError):
        publish("proposal", "제안서X", [(missing, "제안서X")], client=_client(tmp_path, FakeGws()))

    monkeypatch.setenv("DRIVE_PUBLISH_ENABLED", "1")
    assert publish_best_effort(
        "proposal", "제안서X", [(missing, "제안서X")], client=_client(tmp_path, FakeGws())
    ) is None
    marker = capsys.readouterr().err
    assert marker == "DRIVE-PUBLISH-FAIL kind=proposal reason=FileNotFoundError\n"
    assert str(missing) not in marker


def test_environment_client_honors_gws_precedence_cache_and_cached_folder_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "custom-folders.json"
    cache.write_text('{"Root":"cached-root"}', encoding="utf-8")
    monkeypatch.setenv("DRIVE_PUBLISH_CACHE", str(cache))
    monkeypatch.setenv("DRIVE_PUBLISH_GWS_BIN", "legacy-gws")
    monkeypatch.setenv("DRIVE_GWS_BIN", "preferred-gws")

    configured = client_from_environment()
    assert configured.gws_bin == "preferred-gws"
    assert configured.folder_cache == cache

    fake = FakeGws()
    cached = DriveClient(configured.gws_bin, configured.folder_cache, runner=fake)
    assert cached.ensure_folder_path(("Root",)) == "cached-root"
    assert len(fake.calls) == 1
    assert json.loads(fake.calls[0][fake.calls[0].index("--params") + 1]) == {
        "fileId": "cached-root",
        "fields": "id,trashed,parents",
    }

    monkeypatch.delenv("DRIVE_GWS_BIN")
    assert client_from_environment().gws_bin == "legacy-gws"


def test_publish_places_an_artifact_under_its_project(tmp_path: Path) -> None:
    fake = FakeGws()
    artifact = _artifact(tmp_path, "transcript.md")

    result = publish(
        "transcript", "킥오프", [(artifact, "킥오프")],
        on=date(2026, 8, 26), project="해양고신뢰성", client=_client(tmp_path, fake),
    )

    assert [item["name"] for item in _folder_creates(fake)] == [
        "autophagy", "전사본", "해양고신뢰성", "2026",
    ]
    outputs = fake.folders[("autophagy", "root")]
    category = fake.folders[("전사본", outputs)]
    project = fake.folders[("해양고신뢰성", category)]
    assert result.folder_id == fake.folders[("2026", project)]


def test_sticky_date_is_scanned_inside_the_project_folder(tmp_path: Path) -> None:
    """Without the project in the scan the original date would be missed and a second
    copy would appear under today's date."""
    fake = FakeGws()
    fake.seed_folder("autophagy", "root", "outputs")
    fake.seed_folder("전사본", "outputs", "category")
    fake.seed_folder("해양고신뢰성", "category", "project")
    fake.seed_folder("2026", "project", "year-2026")
    fake.seed_folder("2026-08-01_킥오프", "year-2026", "old-entry")

    publish(
        "transcript", "킥오프", [(_artifact(tmp_path, "t.md"), "킥오프")],
        on=date(2026, 8, 26), project="해양고신뢰성", client=_client(tmp_path, fake),
    )

    uploaded = [call[call.index("--name") + 1] for call in _upload_calls(fake)]
    assert uploaded == ["2026-08-01_킥오프.md"]
