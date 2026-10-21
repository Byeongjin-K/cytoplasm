from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import pytest

from automation.drive_client import DriveClient

migrate = importlib.import_module("automation.drive_migrate_outputs")

_FOLDER = "application/vnd.google-apps.folder"


class FakeGws:
    def __init__(self, rows: list[dict[str, Any]], *, verify_failure: str | None = None) -> None:
        self.rows = {str(row["id"]): dict(row, trashed=False) for row in rows}
        self.calls: list[list[str]] = []
        self.verify_failure = verify_failure
        self.next_id = 1

    def __call__(self, argv: list[str]) -> dict[str, Any]:
        self.calls.append(argv[:])
        if argv[1:4] == ["drive", "files", "list"]:
            params = json.loads(argv[argv.index("--params") + 1])
            query = params["q"]
            parent = query.split("'", 2)[1]
            files = [
                {key: value for key, value in row.items() if key not in {"parent", "trashed"}}
                for row in self.rows.values()
                if row.get("parent") == parent and not row.get("trashed")
            ]
            return {"files": list(reversed(files))}
        if argv[1:4] == ["drive", "files", "create"]:
            body = json.loads(argv[argv.index("--json") + 1])
            file_id = f"new-{self.next_id}"
            self.next_id += 1
            self.rows[file_id] = {
                "id": file_id,
                "name": body["name"],
                "mimeType": body["mimeType"],
                "parent": body["parents"][0],
                "createdTime": "2026-01-01T00:00:00Z",
                "modifiedTime": "2026-01-01T00:00:00Z",
                "trashed": False,
            }
            return {"id": file_id}
        if argv[1:4] == ["drive", "files", "update"]:
            params = json.loads(argv[argv.index("--params") + 1])
            file_id = params["fileId"]
            row = self.rows[file_id]
            if "addParents" in params:
                assert row["parent"] == params["removeParents"]
                row["parent"] = params["addParents"]
            if "--json" in argv:
                body = json.loads(argv[argv.index("--json") + 1])
                row.update(body)
            return {"id": file_id}
        if argv[1:4] == ["drive", "permissions", "list"]:
            params = json.loads(argv[argv.index("--params") + 1])
            if params["fileId"] == self.verify_failure:
                return {"permissions": [{"id": "other", "type": "user", "role": "writer"}]}
            return {"permissions": [{"id": "owner", "type": "user", "role": "owner"}]}
        raise AssertionError(f"unexpected argv: {argv}")

    def mutation_calls(self) -> list[list[str]]:
        return [
            call
            for call in self.calls
            if call[1:4] in (["drive", "files", "create"], ["drive", "files", "update"])
            or call[1:3] == ["drive", "+upload"]
        ]


def folder(file_id: str, name: str, parent: str = "root") -> dict[str, Any]:
    return {
        "id": file_id,
        "name": name,
        "mimeType": _FOLDER,
        "parent": parent,
        "createdTime": "2025-01-01T00:00:00Z",
        "modifiedTime": "2025-01-01T00:00:00Z",
    }


def file(
    file_id: str,
    name: str,
    parent: str,
    *,
    created: str = "2026-02-03T04:05:06Z",
    modified: str = "2026-02-03T04:05:06Z",
) -> dict[str, Any]:
    return {
        "id": file_id,
        "name": name,
        "mimeType": "text/plain",
        "parent": parent,
        "createdTime": created,
        "modifiedTime": modified,
    }


def client(tmp_path: Path, fake: FakeGws) -> DriveClient:
    return DriveClient("gws", tmp_path / "folders.json", runner=fake)


def invoke(
    tmp_path: Path,
    fake: FakeGws,
    capsys: pytest.CaptureFixture[str],
    *,
    apply: bool = False,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    code = migrate.run(
        ["--apply"] if apply else [],
        client=client(tmp_path, fake),
        environ={} if env is None else env,
    )
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_dry_run_snapshot_is_sorted_and_has_zero_mutation_argv(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeGws(
        [
            folder("legacy", "report"),
            folder("month", "2026-02", "legacy"),
            file("b", "beta.txt", "month"),
            file("a", "20260101_alpha.txt", "month"),
        ]
    )

    code, stdout, _ = invoke(tmp_path, fake, capsys)

    assert code == 0
    assert stdout == (
        "MIGRATE-PLAN move id=a from=report/2026-02 to=autophagy/주간동향/2026\n"
        "MIGRATE-PLAN move id=b from=report/2026-02 to=autophagy/주간동향/2026\n"
        "MIGRATE-PLAN rename id=a from=20260101_alpha.txt to=2026-01-01_20260101_alpha.txt\n"
        "MIGRATE-PLAN rename id=b from=beta.txt to=2026-02-03_beta.txt\n"
        "MIGRATE-PLAN skip reason=missing-env:PATENT_ARCHIVE_FOLDER_ID target=patent\n"
        "MIGRATE-PLAN trash id=legacy type=empty-folder name=report\n"
        "MIGRATE-PLAN trash id=month type=empty-folder name=2026-02\n"
    )
    assert fake.mutation_calls() == []


def test_apply_duplicate_trio_keeps_newest_and_trashes_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeGws(
        [
            folder("legacy", "doctype"),
            file("old", "same.txt", "legacy", modified="2026-01-01T00:00:00Z"),
            file("new", "same.txt", "legacy", modified="2026-03-01T00:00:00Z"),
            file("mid", "same.txt", "legacy", modified="2026-02-01T00:00:00Z"),
        ]
    )

    code, _, _ = invoke(tmp_path, fake, capsys, apply=True)

    assert code == 0
    trashed = {row_id for row_id, row in fake.rows.items() if row.get("trashed")}
    assert {"old", "mid"} <= trashed
    assert fake.rows["new"]["name"] == "2026-02-03_same.txt"


@pytest.mark.parametrize(
    ("name", "created", "expected"),
    [
        ("memo-20260823.txt", "2024-01-02T00:00:00Z", "2026-08-23_memo-20260823.txt"),
        ("memo-2026-08-22.txt", "2024-01-02T00:00:00Z", "2026-08-22_memo-2026-08-22.txt"),
        ("memo.txt", "2025-07-06T01:02:03Z", "2025-07-06_memo.txt"),
        ("2026-08-21_memo.txt", "2024-01-02T00:00:00Z", "2026-08-21_memo.txt"),
        ("메모.txt", "2025-07-06T01:02:03Z", "2025-07-06_메모.txt"),
    ],
)
def test_date_extraction_and_nfc(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    name: str,
    created: str,
    expected: str,
) -> None:
    fake = FakeGws([folder("legacy", "proposal"), file("doc", name, "legacy", created=created)])

    code, stdout, _ = invoke(tmp_path, fake, capsys)

    assert code == 0
    if name == expected:
        assert "id=doc" not in "\n".join(line for line in stdout.splitlines() if " rename " in line)
    else:
        assert f"to={expected}" in stdout


def test_a_budget_sheet_is_left_where_it_is_after_the_budget_skill_retired(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The budget skill and its sheet move were retired (owner 2026-10-22); the owner's
    # sheet must not be touched even when the old setting is still present.
    fake = FakeGws([file("sheet", "예산 원장", "root")])

    code, stdout, _ = invoke(tmp_path, fake, capsys, apply=True, env={"BUDGET_SHEET_ID": "sheet"})

    assert code == 0
    assert fake.rows["sheet"]["parent"] == "root"
    assert not any('"fileId": "sheet"' in " ".join(call) for call in fake.calls)
    assert "budget" not in stdout

def test_patent_folder_moves_and_renames_with_id_preserved(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeGws([folder("patent-id", "old patent archive")])

    code, stdout, _ = invoke(
        tmp_path,
        fake,
        capsys,
        apply=True,
        env={"PATENT_ARCHIVE_FOLDER_ID": "patent-id"},
    )

    assert code == 0
    assert fake.rows["patent-id"]["name"] == "특허"
    output_root = next(row for row in fake.rows.values() if row["name"] == "autophagy")
    assert fake.rows["patent-id"]["parent"] == output_root["id"]
    patent_updates = [
        call
        for call in fake.calls
        if call[1:4] == ["drive", "files", "update"]
        and '"fileId": "patent-id"' in " ".join(call)
    ]
    assert json.loads(patent_updates[0][patent_updates[0].index("--params") + 1]) == {
        "fileId": "patent-id",
        "addParents": output_root["id"],
        "removeParents": "root",
    }
    assert json.loads(patent_updates[1][patent_updates[1].index("--json") + 1]) == {
        "name": "특허"
    }
    assert "MIGRATE-VERIFY patent-folder id=patent-id owner-only=ok" in stdout
    assert "~/.hermes/doctype/drive-folders.json" in stdout
    assert "~/.hermes/drive-publish/folders.json" in stdout


def test_missing_env_skips_and_continues_legacy_apply(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeGws([folder("legacy", "procurement"), file("doc", "buy.txt", "legacy")])

    code, stdout, _ = invoke(tmp_path, fake, capsys, apply=True)

    assert code == 0
    assert "missing-env:PATENT_ARCHIVE_FOLDER_ID" in stdout
    assert fake.rows["doc"]["parent"] != "legacy"


def test_verify_failure_returns_nonzero_and_names_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeGws(
        [folder("legacy", "doctype"), file("bad-id", "unsafe.txt", "legacy")],
        verify_failure="bad-id",
    )

    code, _, stderr = invoke(tmp_path, fake, capsys, apply=True)

    assert code != 0
    assert "unsafe.txt" in stderr
    assert "bad-id" in stderr


def test_apply_is_idempotent_on_second_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeGws(
        [
            folder("legacy", "doctype"),
            file("doc", "memo.txt", "legacy"),
            folder("patent-id", "archive"),
        ]
    )
    env = {"PATENT_ARCHIVE_FOLDER_ID": "patent-id"}

    assert invoke(tmp_path, fake, capsys, apply=True, env=env)[0] == 0
    before = len(fake.mutation_calls())
    code, stdout, _ = invoke(tmp_path, fake, capsys, apply=True, env=env)

    assert code == 0
    assert len(fake.mutation_calls()) == before
    assert "reason=already-migrated" in stdout


def test_migrator_never_contacts_patent_external_effect_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # configs/external-effect-tools.yaml:23-27 gates only argv containing
    # "patent-drafts"; moving the bound archive folder must never match it.
    fake = FakeGws([folder("patent-id", "archive")])

    invoke(
        tmp_path,
        fake,
        capsys,
        apply=True,
        env={"PATENT_ARCHIVE_FOLDER_ID": "patent-id"},
    )

    assert all("patent-drafts" not in part for call in fake.calls for part in call)
    assert all("delete" not in part for call in fake.calls for part in call)


def test_unknown_and_malformed_legacy_entries_are_skipped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeGws(
        [
            folder("legacy", "report"),
            folder("odd", "not-a-month", "legacy"),
            file("bad", "undated.txt", "legacy", created="not-a-date"),
        ]
    )

    code, stdout, _ = invoke(tmp_path, fake, capsys)

    assert code == 0
    assert "reason=unknown-legacy-folder" in stdout
    assert "reason=invalid-date" in stdout
