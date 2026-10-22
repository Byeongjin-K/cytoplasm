"""Adding the owner's correction to the term glossary (owner 2026-10-22 Q359, YooDori's glossary-add).

New file rather than more cases in test_term_glossary.py, which covers the read side; these
cover the one write path. Drive is a fake client: no test touches the owner's account.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from automation import term_glossary


class FakeDrive:
    """Just enough of DriveClient for one layer read and one verified upsert."""

    def __init__(self, existing: str | None) -> None:
        self.files: dict[str, str] = {} if existing is None else {"용어집.csv": existing}
        self.uploads: list[tuple[tuple[str, ...], str]] = []

    def find_folder_path(self, parts):
        return "folder" if self.files else None

    def list_children(self, folder):
        return [{"name": name, "id": name} for name in self.files]

    def download_file(self, file_id, dest: Path):
        dest.write_text(self.files[file_id], encoding="utf-8")

    def ensure_folder_path(self, parts):
        self.parts = tuple(parts)
        return "folder"

    def upsert_file(self, local: Path, name: str, parent_id: str):
        self.files[name] = local.read_text(encoding="utf-8")
        self.uploads.append((self.parts, self.files[name]))
        return {"id": name, "webViewLink": "https://example.invalid/glossary"}

    def verify_owner_only(self, file_id):
        return None

    def download_and_verify(self, file_id, local: Path):
        assert self.files[file_id] == local.read_text(encoding="utf-8")


def _env(tmp_path: Path) -> dict[str, str]:
    return {"TERM_GLOSSARY_CACHE": str(tmp_path / "cache")}


def test_node_copy_gets_added_updated_and_unchanged_rows(tmp_path):
    # Given a node with Drive publishing off.
    env = _env(tmp_path)
    # When the owner's corrections arrive, one new, one repeated, one changed.
    actions = [
        term_glossary.add_pair("lifelog", "주간기관", "주관기관", env=env),
        term_glossary.add_pair("lifelog", "주간기관", "주관기관", env=env),
        term_glossary.add_pair("lifelog", "한건전", "한건영", env=env),
        term_glossary.add_pair("lifelog", "한건전", "한건연", env=env),
    ]
    # Then each is recorded once and the newest right spelling wins.
    assert actions == ["added", "unchanged", "added", "updated"]
    assert term_glossary.glossary_for("lifelog", env=env) == (("주간기관", "주관기관"), ("한건전", "한건연"))


def test_drive_layer_is_the_kind_folder_and_keeps_existing_rows(tmp_path):
    # Given the lifelog layer already holding a right spelling.
    drive = FakeDrive("오토파지\n")
    # When a correction is added with Drive on.
    action = term_glossary.add_pair("lifelog", "주간기관", "주관기관", client=drive, env=_env(tmp_path))
    # Then that layer is rewritten with the old row kept and the node copy updated.
    assert action == "added"
    assert drive.uploads == [(("autophagy", "라이프로그"), "오토파지\n주간기관,주관기관\n")]
    assert ("주간기관", "주관기관") in term_glossary._read_file(term_glossary.cache_path("lifelog", env=_env(tmp_path)))


@pytest.mark.parametrize("kind,wrong,right", [
    ("report", "가나다", "가나닥"),
    ("lifelog", "가나다", "가나다"),
    ("lifelog", "가,나", "가나닥"),
    ("lifelog", "가나다", "가=나"),
    ("lifelog", "가나다\n", "가\n나"),
    ("lifelog", "", "가나닥"),
    ("lifelog", "가" * 41, "가나닥"),
])
def test_malformed_corrections_are_refused(tmp_path, kind, wrong, right):
    # Given a kind YooDori may not write, a no-op pair or a row that would break the CSV.
    # When it is added, then nothing is written.
    with pytest.raises(term_glossary.TermGlossaryError):
        term_glossary.add_pair(kind, wrong, right, env=_env(tmp_path))
    assert not (tmp_path / "cache").exists()
