"""Finalized minutes in the analysis tick: read-only Drive scan, conclusions only, one card per meeting series."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from automation.project_board import minutes
from automation.project_board.board_store import task_key_of
from automation.project_board.llm import LlmAnswer
from automation.project_board.models import ProjectBoardError
from automation.project_board.service import Board, analyze_new_minutes
from automation.project_board.sources import meeting_key

TODAY = date(2026, 10, 14)
FOLDER = minutes.FOLDER_MIME
MINUTES = "# 굴착기 3기관 회의\n\n## 결정사항\n- 시험 계획 보고서를 10/30까지 제출\n\n## 부록 · 근거와 원문\n전사본비밀문장\n"


class FakeDrive:
    def __init__(self) -> None:
        self.tree: dict[str, list[dict[str, object]]] = {
            "root-meeting": [{"id": "p1", "name": "과제A", "mimeType": FOLDER}],
            "p1": [{"id": "y1", "name": "2026", "mimeType": FOLDER}],
            "y1": [],
        }
        self.bodies: dict[str, str] = {}
        self.downloads: list[str] = []

    def add(self, file_id: str, name: str, modified: str, body: str = MINUTES) -> None:
        self.tree["y1"].append({"id": file_id, "name": name, "mimeType": "text/markdown", "modifiedTime": modified})
        self.bodies[file_id] = body

    def find_folder_path(self, parts: tuple[str, ...]) -> str | None:
        return "root-meeting" if parts[-1] == "회의록" else None

    def list_children(self, folder_id: str) -> list[dict[str, object]]:
        return list(self.tree.get(folder_id, []))

    def download_file(self, file_id: str, dest: Path, *, export_as: str = "") -> str:
        self.downloads.append(file_id)
        dest.write_text(self.bodies[file_id], encoding="utf-8")
        return "sha"


class Model:
    name = "fake"

    def __init__(self, error: ProjectBoardError | None = None) -> None:
        self.prompts: list[str] = []
        self.error = error

    def complete(self, prompt: str) -> LlmAnswer:
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return LlmAnswer(json.dumps({
            "relevant": True, "project": "과제A", "summary": "회의 결정",
            "todos": [{"title": "시험 계획 보고서 제출", "owner": "", "due": "2026-10-30"}],
        }, ensure_ascii=False), "fake", "m")


def test_meeting_series_ignore_date_label_and_session_number() -> None:
    assert meeting_key("회의록 - 굴착기 3기관 회의 (2차)") == meeting_key("굴착기 3기관 회의") == "굴착기 3기관 회의"
    assert meeting_key("2026-07-22 산업부-자율굴착기-개발회의-2") == meeting_key("산업부-자율굴착기-개발회의")
    assert meeting_key("굴착기 회의 제3회") == "굴착기 회의"
    assert meeting_key("다른 회의") != meeting_key("굴착기 회의")
    assert task_key_of("meeting", "gd-aaaaaaaa1", "굴착기 회의 1차") == task_key_of("meeting", "gd-bbbbbbbb2", "굴착기 회의 (2차)")


def test_scan_lists_minutes_only_and_never_needs_a_folder_it_did_not_find(tmp_path: Path) -> None:
    drive = FakeDrive()
    drive.add("fileBBBB22", "회의록-b.md", "2026-10-02T00:00:00Z")
    drive.add("fileAAAA11", "회의록-a.md", "2026-10-01T00:00:00Z")
    drive.tree["y1"] += [{"id": "csvfile123", "name": "action-items.csv", "mimeType": "text/csv"},
                         {"id": "bad/id", "name": "x.md", "mimeType": "text/markdown"}]
    drive.tree["root-meeting"].append({"id": "y0", "name": "2026", "mimeType": FOLDER})
    drive.tree["p1"].append({"id": "tmplFILE01", "name": "회의록양식.md", "mimeType": "text/markdown"})
    drive.tree["y0"] = [{"id": "fileCCCC33", "name": "회의록-c.md", "mimeType": "text/markdown",
                         "modifiedTime": "2026-10-03T00:00:00Z"}]
    found = minutes.scan(drive, ("autophagy", "회의록"))
    assert [(f.file_id, f.project) for f in found] == [
        ("fileAAAA11", "과제A"), ("fileBBBB22", "과제A"), ("fileCCCC33", "")]
    assert minutes.scan(drive, ("autophagy", "없는폴더")) == ()


def test_only_the_finalized_view_is_cached_and_analysed(tmp_path: Path) -> None:
    drive = FakeDrive()
    drive.add("fileAAAA11", "회의록-2026-10-01-a.md", "2026-10-01T00:00:00Z")
    item = minutes.scan(drive, ("autophagy", "회의록"))[0]
    document = minutes.fetch(drive, item, tmp_path / "cache")
    cached = minutes.cache_path(tmp_path / "cache", item.source_id)
    assert cached is not None and cached.stat().st_mode & 0o777 == 0o600
    assert "전사본비밀문장" not in document.text and "전사본비밀문장" not in cached.read_text(encoding="utf-8")
    assert "시험 계획 보고서" in document.text
    assert (document.ref.kind, document.ref.source_id, document.ref.date) == ("meeting", "gd-fileAAAA11", "2026-10-01")
    assert minutes.cache_path(tmp_path, "gd-../../etc") is None and minutes.cache_path(tmp_path, "note.md") is None


def test_first_tick_only_records_and_later_minutes_become_one_card_per_series(tmp_path: Path) -> None:
    from automation.project_board.config import BoardConfig
    from automation.project_board.llm import LlmConfig

    board, drive, model, config = Board(tmp_path / "board"), FakeDrive(), Model(), BoardConfig(llm=LlmConfig())
    drive.add("oldOLD0001", "회의록-old.md", "2026-09-01T00:00:00Z")
    first = analyze_new_minutes(board, drive, model, config, today=TODAY, post=None)
    assert first.initialised and model.prompts == [] and drive.downloads == []

    drive.add("newNEW0001", "회의록-new.md", "2026-10-13T00:00:00Z")
    drive.add("newNEW0002", "회의록-new-2.md", "2026-10-14T00:00:00Z",
              MINUTES.replace("# 굴착기 3기관 회의", "# 굴착기 3기관 회의 (2차)"))
    second = analyze_new_minutes(board, drive, model, config, today=TODAY, post=None)
    assert len(second.proposed) == 1 and len(model.prompts) == 1 and second.skipped == 1
    assert drive.downloads == ["newNEW0001", "newNEW0002"]
    record = board.proposals.all()[0]
    assert (record.source_kind, record.source_id) == ("meeting", "gd-newNEW0001")
    assert analyze_new_minutes(board, drive, model, config, today=TODAY, post=None).proposed == ()
    assert len(model.prompts) == 1


def test_a_retryable_failure_leaves_the_minutes_for_the_next_tick(tmp_path: Path) -> None:
    from automation.project_board.config import BoardConfig
    from automation.project_board.llm import LlmConfig

    board, drive, config = Board(tmp_path / "board"), FakeDrive(), BoardConfig(llm=LlmConfig())
    analyze_new_minutes(board, drive, Model(), config, today=TODAY, post=None)
    drive.add("newNEW0001", "회의록-new.md", "2026-10-13T00:00:00Z")
    tick = analyze_new_minutes(board, drive, Model(ProjectBoardError("busy", retryable=True)), config,
                               today=TODAY, post=None)
    assert tick.deferred and board.proposals.all() == ()
    assert len(analyze_new_minutes(board, drive, Model(), config, today=TODAY, post=None).proposed) == 1


def test_a_corrupt_watermark_stops_instead_of_flooding(tmp_path: Path) -> None:
    from automation.project_board.config import BoardConfig
    from automation.project_board.llm import LlmConfig

    board = Board(tmp_path / "board")
    board.root.mkdir(parents=True)
    (board.root / "minutes-state.json").write_text("{", encoding="utf-8")
    with pytest.raises(ProjectBoardError):
        analyze_new_minutes(board, FakeDrive(), Model(), BoardConfig(llm=LlmConfig()), today=TODAY, post=None)
