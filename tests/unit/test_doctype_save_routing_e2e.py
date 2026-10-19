from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from typing import final

import pytest

from automation import drive_outputs
from automation.drive_client import DriveClient
from automation.interop.effect_approval import (
    ApprovalBinding,
    ApprovalContext,
    JsonValue,
    SignedApprovalEvent,
    approval_challenge,
    record_signed_e2e_approval,
)
from automation.interop.injection_adapter import InboundEvent, sign_event
from automation.obsidian_write import ObsidianWriteConfig, plan_note
from automation.obsidian_write import gate_binding
from skills.doctype.scripts.doctype_routing import SaveRoute, classify_save_request
from skills.doctype.scripts.doctype_save import DocumentSaveError, SaveAdapters, save_artifact

_OWNER_ID = "owner"
_E2E_SECRET = b"doctype-save-routing-e2e"


@final
class FakeGitRunner:
    clone_dir: Path
    failure: str | None
    mismatched_readback: bool

    def __init__(self, clone_dir: Path, *, failure: str | None = None, mismatched_readback: bool = False) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.clone_dir = clone_dir
        self.failure = failure
        self.mismatched_readback = mismatched_readback

    def __call__(
        self,
        argv: list[str],
        /,
        *,
        cwd: Path | None,
        env: dict[str, str],
        capture_output: bool,
        text: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, env, capture_output, text, timeout
        self.calls.append(tuple(argv))
        if argv[1] == self.failure:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="simulated failure")
        if argv[1] == "show":
            if self.mismatched_readback:
                return subprocess.CompletedProcess(argv, 0, stdout="tampered", stderr="")
            _remote, separator, relpath = argv[-1].partition(":")
            assert separator == ":"
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout=(self.clone_dir / PurePosixPath(relpath)).read_text(encoding="utf-8"),
                stderr="",
            )
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")


@final
class FakeDriveRunner:
    remote_bytes: bytes
    corrupt_download: bool

    def __init__(self, *, corrupt_download: bool = False, owner_only: bool = True) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.folders: dict[tuple[str, str], str] = {}
        self.files: dict[tuple[str, str], str] = {}
        self.remote_bytes = b""
        self.corrupt_download = corrupt_download
        self.permissions: list[dict[str, JsonValue]] = [
            {"id": "owner", "type": "user", "role": "owner"}
        ]
        if not owner_only:
            self.permissions.append({"id": "reader", "type": "user", "role": "reader"})

    def __call__(self, argv: list[str]) -> dict[str, JsonValue]:
        self.calls.append(tuple(argv))
        match tuple(argv[2:4]):
            case ("files", "list"):
                return self._list(argv)
            case ("files", "create"):
                return self._create(argv)
            case ("files", "update"):
                return self._update(argv)
            case ("files", "get"):
                return self._get(argv)
            case ("permissions", "list"):
                permissions: list[JsonValue] = []
                permissions.extend(self.permissions)
                return {"permissions": permissions}
            case ("+upload", _):
                return self._upload(argv)
            case unexpected:
                raise AssertionError(f"unexpected Drive command {unexpected!r}")

    def _list(self, argv: list[str]) -> dict[str, JsonValue]:
        query = _json_string(argv[argv.index("--params") + 1], "q")
        if "name =" not in query:
            parent = query.split("'")[1]
            children: list[JsonValue] = [
                {"id": identifier, "name": name, "mimeType": "application/vnd.google-apps.folder"}
                for (name, folder_parent), identifier in self.folders.items()
                if folder_parent == parent
            ]
            children.extend(
                {"id": identifier, "name": name, "mimeType": "text/markdown"}
                for (name, file_parent), identifier in self.files.items()
                if file_parent == parent
            )
            return {"files": children}
        quoted = query.split("'")
        key = (quoted[1], quoted[3])
        registry = self.folders if "mimeType" in query else self.files
        identifier = registry.get(key)
        files: list[JsonValue] = []
        if identifier is not None:
            files.append({"id": identifier, "name": key[0]})
        return {"files": files}

    def _create(self, argv: list[str]) -> dict[str, JsonValue]:
        metadata = argv[argv.index("--json") + 1]
        identifier = f"folder-{len(self.folders) + 1}"
        self.folders[(_json_string(metadata, "name"), _json_parent(metadata))] = identifier
        return {"id": identifier}

    def _upload(self, argv: list[str]) -> dict[str, JsonValue]:
        identifier = f"file-{len(self.files) + 1}"
        parent = argv[argv.index("--parent") + 1]
        name = argv[argv.index("--name") + 1]
        self.files[(name, parent)] = identifier
        self.remote_bytes = Path(argv[3]).read_bytes()
        return {"id": identifier}

    def _update(self, argv: list[str]) -> dict[str, JsonValue]:
        file_id = _json_string(argv[argv.index("--params") + 1], "fileId")
        self.remote_bytes = Path(argv[argv.index("--upload") + 1]).read_bytes()
        return {"id": file_id}

    def _get(self, argv: list[str]) -> dict[str, JsonValue]:
        params = argv[argv.index("--params") + 1]
        if '"alt": "media"' in params:
            payload = b"corrupt" if self.corrupt_download else self.remote_bytes
            _ = Path(argv[argv.index("-o") + 1]).write_bytes(payload)
            return {}
        return {"webViewLink": f"https://drive.invalid/{_json_string(params, 'fileId')}"}


@dataclass(frozen=True, slots=True)
class Harness:
    adapters: SaveAdapters
    git: FakeGitRunner
    drive: FakeDriveRunner
    approval_context: ApprovalContext
    clone_dir: Path


def _json_string(encoded: str, key: str) -> str:
    match = re.search(rf'"{key}": "([^"]+)"', encoded)
    assert match is not None
    return match.group(1)


def _json_parent(encoded: str) -> str:
    match = re.search(r'"parents": \["([^"]+)"\]', encoded)
    assert match is not None
    return match.group(1)


def _harness(
    tmp_path: Path,
    *,
    git_failure: str | None = None,
    mismatched_readback: bool = False,
    corrupt_download: bool = False,
    owner_only: bool = True,
) -> Harness:
    clone_dir = tmp_path / "obsidian-write"
    clone_dir.mkdir(mode=0o700, parents=True)
    (clone_dir / ".git").mkdir()
    key_path = tmp_path / "obsidian-write-key"
    _ = key_path.write_text("test key", encoding="utf-8")
    _ = key_path.chmod(0o600)
    git = FakeGitRunner(clone_dir, failure=git_failure, mismatched_readback=mismatched_readback)
    drive = FakeDriveRunner(corrupt_download=corrupt_download, owner_only=owner_only)
    approval = ApprovalContext(tmp_path / "approvals.jsonl", _OWNER_ID, True)
    adapters = SaveAdapters(
        obsidian_config=ObsidianWriteConfig("git@example.invalid:owner/vault.git", clone_dir, key_path),
        drive_client=DriveClient("gws", tmp_path / "drive-folders.json", runner=drive),
        git_runner=git,
        approval_context=approval,
    )
    return Harness(adapters, git, drive, approval, clone_dir)


def _artifact(tmp_path: Path) -> Path:
    artifact = tmp_path / "weekly-report.md"
    _ = artifact.write_text("# Weekly report\n\nVerified content.\n", encoding="utf-8")
    return artifact


def _approve_obsidian(artifact: Path, context: ApprovalContext) -> None:
    plan = plan_note(artifact.stem, artifact.read_text(encoding="utf-8"), institutional=False, bucket_hint="resource")
    decision = gate_binding.evaluate(plan, context=context)
    event = InboundEvent(
        event_id="doctype-save-routing",
        user_id=_OWNER_ID,
        channel_id="approvals",
        text=approval_challenge(decision.action_hash, decision.target_id),
    )
    assert record_signed_e2e_approval(
        context,
        ApprovalBinding(decision.action_hash, decision.target_id),
        SignedApprovalEvent(event, sign_event(event, _E2E_SECRET), _E2E_SECRET),
    )


def _run_save(artifact: Path, request: str, harness: Harness) -> SaveRoute:
    route = classify_save_request(request, has_file_artifact=True)
    if "obsidian" in route.destinations:
        _approve_obsidian(artifact, harness.approval_context)
    save_artifact(artifact, route, harness.adapters)
    return route


def _operations(calls: list[tuple[str, ...]]) -> list[tuple[str, str]]:
    return [(call[2], call[3] if len(call) > 3 else "") for call in calls]


def test_personal_note_when_routed_then_commits_pushes_and_verifies_obsidian_only(tmp_path: Path) -> None:
    # Given
    artifact = _artifact(tmp_path)
    harness = _harness(tmp_path)

    # When
    route = _run_save(artifact, "개인노트 저장해줘", harness)

    # Then
    assert route == SaveRoute(("obsidian",), "personal-note", False)
    # `config` is one-off partial-clone setup, not part of the save path itself.
    saving = [call[1] for call in harness.git.calls if call[1] != "config"]
    assert saving == ["fetch", "reset", "add", "commit", "push", "fetch", "show"]
    assert tuple(harness.clone_dir.rglob("*.md")) == (
        harness.clone_dir / "000_PARA/Resource/weekly-report--94cc4e792f0a.md",
    )
    assert harness.drive.calls == []


def test_destinationless_report_when_routed_then_facade_publishes_canonical_drive_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    artifact = _artifact(tmp_path)
    harness = _harness(tmp_path)

    class FixedDate:
        @staticmethod
        def today() -> date:
            return date(2026, 8, 23)

    monkeypatch.setattr(drive_outputs, "date", FixedDate)

    # When
    route = _run_save(artifact, "주간 보고서를 만들어 저장해줘", harness)

    # Then
    assert route == SaveRoute(("drive",), "default-drive", False)
    assert harness.git.calls == []
    creates = [
        json.loads(call[call.index("--json") + 1])["name"]
        for call in harness.drive.calls
        if call[2:4] == ("files", "create")
    ]
    assert creates == ["autophagy", "문서", "2026"]
    upload = next(call for call in harness.drive.calls if call[2] == "+upload")
    assert upload == (
        "gws", "drive", "+upload", str(artifact), "--parent", "folder-3", "--name",
        "2026-08-23_weekly-report.md",
    )


def test_explicit_both_when_routed_then_executes_verified_obsidian_and_drive_saves(tmp_path: Path) -> None:
    # Given
    artifact = _artifact(tmp_path)
    harness = _harness(tmp_path)

    # When
    route = _run_save(artifact, "옵시디언과 드라이브 둘 다 저장해줘", harness)

    # Then
    assert route == SaveRoute(("obsidian", "drive"), "explicit-destination", False)
    assert [call[1] for call in harness.git.calls][-3:] == ["push", "fetch", "show"]
    assert _operations(harness.drive.calls)[-2:] == [("permissions", "list"), ("files", "get")]


def test_same_request_twice_when_routed_then_updates_deterministic_targets_without_duplicates(tmp_path: Path) -> None:
    # Given
    artifact = _artifact(tmp_path)
    harness = _harness(tmp_path)

    # When
    _ = _run_save(artifact, "둘 다 저장해줘", harness)
    _ = _run_save(artifact, "둘 다 저장해줘", harness)

    # Then
    assert len(tuple(harness.clone_dir.rglob("*.md"))) == 1
    assert len(harness.drive.files) == 1
    assert sum(call[2] == "+upload" for call in harness.drive.calls) == 1
    assert sum(call[2:4] == ("files", "update") for call in harness.drive.calls) == 1


def test_adapter_failures_when_routed_then_surface_one_overall_failure_without_success(tmp_path: Path) -> None:
    # Given / When / Then: a failed push or remote read-back never begins the Drive leg.
    push_failure = _harness(tmp_path / "push", git_failure="push")
    push_artifact = _artifact(tmp_path / "push")
    with pytest.raises(DocumentSaveError, match="obsidian"):
        _ = _run_save(push_artifact, "개인노트 저장해줘", push_failure)
    assert push_failure.drive.calls == []

    readback_failure = _harness(tmp_path / "readback", mismatched_readback=True)
    readback_artifact = _artifact(tmp_path / "readback")
    with pytest.raises(DocumentSaveError, match="obsidian"):
        _ = _run_save(readback_artifact, "개인노트 저장해줘", readback_failure)
    assert readback_failure.drive.calls == []

    # Given / When / Then: each Drive verification rejection prevents a success receipt.
    hash_failure = _harness(tmp_path / "hash", corrupt_download=True)
    hash_artifact = _artifact(tmp_path / "hash")
    with pytest.raises(DocumentSaveError, match="drive") as hash_error:
        _ = _run_save(hash_artifact, "주간 보고서를 만들어 저장해줘", hash_failure)
    assert hash_error.value.destination == "drive"

    permissions_failure = _harness(tmp_path / "permissions", owner_only=False)
    permissions_artifact = _artifact(tmp_path / "permissions")
    with pytest.raises(DocumentSaveError, match="drive") as permissions_error:
        _ = _run_save(permissions_artifact, "주간 보고서를 만들어 저장해줘", permissions_failure)
    assert permissions_error.value.destination == "drive"


def test_missing_drive_artifact_when_routed_then_fails_closed_without_drive_argv(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    missing = tmp_path / "missing.md"

    with pytest.raises(DocumentSaveError, match="drive") as error:
        save_artifact(missing, SaveRoute(("drive",), "default-drive", False), harness.adapters)

    assert error.value.destination == "drive"
    assert harness.drive.calls == []
