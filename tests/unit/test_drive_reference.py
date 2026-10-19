"""참고자료(내 드라이브/KIMM) 조회 — 읽기 전용·fail-closed 계약 (소유자 지시 2026-08-26).

소유자는 근거자료를 Drive 의 한 폴더에 모아 두고, 에이전트가 회의록 같은 작업에서 그
폴더를 뒤져 내용·용어를 교정하기를 원한다. 그 폴더는 **소유자의 자료 보관함**이지 우리
산출물 트리가 아니므로, 이 경로는 절대 폴더를 만들지 않고 못 찾으면 그대로 멈춘다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from automation import drive_reference
from automation.drive_client import DriveClient
from automation.entity_preflight.contracts import JsonValue

_FOLDER_MIME = "application/vnd.google-apps.folder"


def _group(pattern: str, text: str) -> str:
    found = re.search(pattern, text)
    if found is None:
        raise AssertionError(f"참고자료 질의를 해석하지 못했다: {text}")
    return found.group(1)


def _named_query(query: str) -> tuple[str, str, bool]:
    name = _group(r"name = '((?:[^'\\]|\\.)*)'", query)
    return (
        name.replace("\\'", "'").replace("\\\\", "\\"),
        _group(r"'([^']+)' in parents", query),
        _FOLDER_MIME in query,
    )


class FakeDrive:
    """In-memory `gws drive` wire protocol over a read-only tree."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.folders: dict[str, tuple[str, str]] = {}
        self.files: dict[str, dict[str, str]] = {}
        self.media: dict[str, bytes] = {}
        self.exports: dict[str, bytes] = {}
        self._n = 0

    def _id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n}"

    def folder(self, name: str, parent: str = "root") -> str:
        folder_id = self._id("fold")
        self.folders[folder_id] = (name, parent)
        return folder_id

    def google_file(self, name: str, parent: str, *, exported: bytes, kind: str = "document") -> str:
        file_id = self.file(name, parent, mime=f"application/vnd.google-apps.{kind}")
        self.exports[file_id] = exported
        return file_id

    def file(
        self,
        name: str,
        parent: str,
        *,
        body: bytes = b"",
        mime: str = "application/octet-stream",
        modified: str = "2026-08-01T09:00:00.000Z",
        size: int | None = None,
    ) -> str:
        file_id = self._id("file")
        self.files[file_id] = {
            "id": file_id,
            "name": name,
            "mimeType": mime,
            "modifiedTime": modified,
            "createdTime": modified,
            "size": str(len(body) if size is None else size),
            "_parent": parent,
        }
        self.media[file_id] = body
        return file_id

    def creates(self) -> list[list[str]]:
        return [call for call in self.calls if call[2:4] == ["files", "create"]]

    def downloads(self) -> list[list[str]]:
        return [call for call in self.calls if "--params" in call and '"alt"' in call[call.index("--params") + 1]]

    def _list(self, query: str) -> dict[str, JsonValue]:
        if query.startswith("name = "):
            name, parent, want_folder = _named_query(query)
            if want_folder:
                rows: list[JsonValue] = [
                    {"id": folder_id, "name": folder_name}
                    for folder_id, (folder_name, folder_parent) in self.folders.items()
                    if folder_name == name and folder_parent == parent
                ]
            else:
                rows = [
                    {"id": row["id"], "name": row["name"]}
                    for row in self.files.values()
                    if row["name"] == name and row["_parent"] == parent
                ]
            return {"files": rows}
        parent = _group(r"'([^']+)' in parents", query)
        children: list[JsonValue] = []
        for folder_id, (folder_name, folder_parent) in self.folders.items():
            if folder_parent == parent:
                folder_row: dict[str, JsonValue] = {
                    "id": folder_id,
                    "name": folder_name,
                    "mimeType": _FOLDER_MIME,
                }
                children.append(folder_row)
        for row in self.files.values():
            if row["_parent"] == parent:
                file_row: dict[str, JsonValue] = {
                    key: value for key, value in row.items() if key != "_parent"
                }
                children.append(file_row)
        return {"files": children}

    def __call__(self, argv: list[str]) -> dict[str, JsonValue]:
        self.calls.append(list(argv))
        operation, method = argv[2], argv[3]
        params = json.loads(argv[argv.index("--params") + 1]) if "--params" in argv else {}
        if operation == "files" and method == "list":
            return self._list(str(params["q"]))
        if operation == "files" and method == "export":
            exported = self.exports.get(str(params["fileId"]))
            if exported is None:
                raise AssertionError(f"내보낼 수 없는 파일이다: {params['fileId']}")
            Path(argv[argv.index("-o") + 1]).write_bytes(exported)
            return {}
        if operation == "files" and method == "get":
            if params.get("alt") == "media":
                file_id = str(params["fileId"])
                if self.files[file_id]["mimeType"].startswith("application/vnd.google-apps."):
                    raise AssertionError(f"Google 문서는 alt=media 로 받을 수 없다: {file_id}")
                Path(argv[argv.index("-o") + 1]).write_bytes(self.media[file_id])
                return {}
            return {"webViewLink": f"https://drive.google.com/file/d/{params['fileId']}/view"}
        raise AssertionError(f"참고자료 경로가 쓰기/미지원 호출을 했다: {argv}")


def _client(tmp_path: Path, fake: FakeDrive) -> DriveClient:
    return DriveClient(gws_bin="gws", folder_cache=tmp_path / "folders.json", runner=fake)


def test_search_without_optin_never_builds_a_client(monkeypatch: pytest.MonkeyPatch) -> None:
    def _explode(_env: object) -> object:
        raise AssertionError("옵트인 없이 Drive 클라이언트를 만들었다")

    monkeypatch.setattr(drive_reference, "_default_client", _explode)
    result = drive_reference.search("굴착 오차", env={}, client=None)

    assert result.status == drive_reference.DISABLED
    assert result.hits == ()


def test_missing_root_reports_and_creates_nothing(tmp_path: Path) -> None:
    fake = FakeDrive()
    result = drive_reference.search(
        "굴착 오차", env={"DRIVE_PUBLISH_ENABLED": "1"}, client=_client(tmp_path, fake)
    )

    assert result.status == drive_reference.ROOT_MISSING
    assert result.hits == ()
    assert fake.creates() == []


def test_configured_nested_root_is_resolved_find_only(tmp_path: Path) -> None:
    fake = FakeDrive()
    kimm = fake.folder("KIMM")
    inner = fake.folder("회의자료", kimm)
    fake.file("굴착 오차 관리.md", inner, body="굴착 오차는 10 mm 이하로 관리한다.".encode())

    result = drive_reference.search(
        "굴착 오차",
        env={"DRIVE_PUBLISH_ENABLED": "1", "DRIVE_REFERENCE_ROOT": "KIMM/회의자료"},
        client=_client(tmp_path, fake),
    )

    assert result.status == drive_reference.OK
    assert result.root == "KIMM/회의자료"
    assert fake.creates() == []


def _kimm_tree(fake: FakeDrive) -> str:
    kimm = fake.folder("KIMM")
    year = fake.folder("2026", kimm)
    fake.file(
        "굴착 오차 관리기준.md",
        year,
        body="굴착 오차는 10 mm 이하로 관리한다. 측정은 KIMM 표준 절차를 따른다.".encode(),
    )
    fake.file("총회 안내.md", year, body="장소와 일정만 적힌 안내문.".encode())
    return kimm


def test_matching_document_comes_back_with_a_quotable_snippet(tmp_path: Path) -> None:
    fake = FakeDrive()
    _kimm_tree(fake)

    result = drive_reference.search(
        "굴착 오차 기준", env={"DRIVE_PUBLISH_ENABLED": "1"}, client=_client(tmp_path, fake)
    )

    assert result.status == drive_reference.OK
    assert result.scanned == 2
    assert [hit.name for hit in result.hits] == ["굴착 오차 관리기준.md"]
    assert "10 mm 이하로 관리한다" in result.hits[0].snippet
    assert result.hits[0].path == "KIMM/2026/굴착 오차 관리기준.md"
    assert result.hits[0].link.endswith("/view")


def test_documents_nested_deeper_are_still_found(tmp_path: Path) -> None:
    fake = FakeDrive()
    kimm = fake.folder("KIMM")
    project = fake.folder("해양고신뢰성", kimm)
    year = fake.folder("2026", project)
    fake.file("센서 캘리브레이션.md", year, body="캘리브레이션 주기는 6개월이다.".encode())

    result = drive_reference.search(
        "캘리브레이션 주기", env={"DRIVE_PUBLISH_ENABLED": "1"}, client=_client(tmp_path, fake)
    )

    assert [hit.path for hit in result.hits] == ["KIMM/해양고신뢰성/2026/센서 캘리브레이션.md"]
    assert "6개월" in result.hits[0].snippet


def test_query_with_no_evidence_returns_no_hits(tmp_path: Path) -> None:
    fake = FakeDrive()
    _kimm_tree(fake)

    result = drive_reference.search(
        "예산 집행 실적", env={"DRIVE_PUBLISH_ENABLED": "1"}, client=_client(tmp_path, fake)
    )

    assert result.status == drive_reference.OK
    assert result.hits == ()


def test_denser_evidence_ranks_first(tmp_path: Path) -> None:
    fake = FakeDrive()
    kimm = fake.folder("KIMM")
    fake.file("배경.md", kimm, body="굴착 이야기를 한 번 언급한다.".encode())
    fake.file("본문.md", kimm, body="굴착 굴착 굴착 — 굴착 공정을 세 번 더 다룬다.".encode())

    result = drive_reference.search(
        "굴착", env={"DRIVE_PUBLISH_ENABLED": "1"}, client=_client(tmp_path, fake)
    )

    assert [hit.name for hit in result.hits] == ["본문.md", "배경.md"]


def test_scan_stops_at_the_depth_and_file_caps(tmp_path: Path) -> None:
    fake = FakeDrive()
    parent = fake.folder("KIMM")
    root_id = parent
    for level in range(1, 6):
        parent = fake.folder(f"단계{level}", parent)
        fake.file(f"자료{level}.md", parent, body=f"단계 {level} 본문".encode())
    client = _client(tmp_path, fake)

    files, notes = drive_reference.walk(client, root_id, "KIMM", max_depth=3, max_files=2)

    assert len(files) <= 2
    assert any("깊은 폴더는 보지 않았습니다" in note for note in notes)


def test_unreadable_reference_is_reported_and_the_others_survive(tmp_path: Path) -> None:
    fake = FakeDrive()
    kimm = fake.folder("KIMM")
    fake.file("굴착 오차 스캔본.pdf", kimm, body=b"%PDF-1.4 broken")
    fake.file("굴착 오차 요약.md", kimm, body="굴착 오차는 10 mm 이하.".encode())

    result = drive_reference.search(
        "굴착 오차", env={"DRIVE_PUBLISH_ENABLED": "1"}, client=_client(tmp_path, fake)
    )

    assert result.status == drive_reference.OK
    by_name = {hit.name: hit for hit in result.hits}
    assert by_name["굴착 오차 요약.md"].status == drive_reference.OK
    assert by_name["굴착 오차 스캔본.pdf"].status.startswith("읽지 못함")
    assert by_name["굴착 오차 스캔본.pdf"].snippet == ""


def test_old_hwp_is_refused_with_an_actionable_reason_and_never_fetched(tmp_path: Path) -> None:
    fake = FakeDrive()
    kimm = fake.folder("KIMM")
    fake.file("굴착 설계도.hwp", kimm, body=b"\x00\x01")

    result = drive_reference.search(
        "굴착 설계도", env={"DRIVE_PUBLISH_ENABLED": "1"}, client=_client(tmp_path, fake)
    )

    assert result.status == drive_reference.OK
    assert result.hits[0].status == "읽지 못함: 구형 hwp 는 hwpx 나 pdf 로 저장해 주세요"
    assert fake.downloads() == []


def test_unknown_format_names_what_is_supported_without_fetching(tmp_path: Path) -> None:
    fake = FakeDrive()
    kimm = fake.folder("KIMM")
    fake.file("굴착 설치본.exe", kimm, body=b"MZ")

    result = drive_reference.search(
        "굴착 설치본", env={"DRIVE_PUBLISH_ENABLED": "1"}, client=_client(tmp_path, fake)
    )

    assert result.hits[0].status.endswith("지원 형식은 pdf·pptx·docx·hwpx·xlsx·md·txt·csv 입니다")
    assert fake.downloads() == []


def test_downloads_are_bounded_by_the_fetch_limit(tmp_path: Path) -> None:
    fake = FakeDrive()
    kimm = fake.folder("KIMM")
    for index in range(9):
        fake.file(f"굴착 자료 {index}.md", kimm, body=f"굴착 {index}".encode())

    result = drive_reference.search(
        "굴착", env={"DRIVE_PUBLISH_ENABLED": "1"}, client=_client(tmp_path, fake), limit=2
    )

    assert result.scanned == 9
    assert len(fake.downloads()) == 2
    assert len(result.hits) <= 2


def test_google_document_is_exported_as_readable_text(tmp_path: Path) -> None:
    fake = FakeDrive()
    kimm = fake.folder("KIMM")
    fake.google_file(
        "굴착 오차 관리기준",
        kimm,
        exported="굴착 오차는 10 mm 이하로 관리한다.".encode(),
    )

    result = drive_reference.search(
        "굴착 오차", env={"DRIVE_PUBLISH_ENABLED": "1"}, client=_client(tmp_path, fake)
    )

    assert result.status == drive_reference.OK
    assert [hit.name for hit in result.hits] == ["굴착 오차 관리기준"]
    assert result.hits[0].status == drive_reference.OK
    assert "10 mm 이하로 관리한다" in result.hits[0].snippet


def test_google_spreadsheet_is_exported_as_csv(tmp_path: Path) -> None:
    fake = FakeDrive()
    kimm = fake.folder("KIMM")
    fake.google_file(
        "굴착 실적", kimm, exported="항목,값\n굴착 오차,10 mm\n".encode(), kind="spreadsheet"
    )

    result = drive_reference.search(
        "굴착 오차", env={"DRIVE_PUBLISH_ENABLED": "1"}, client=_client(tmp_path, fake)
    )

    assert result.hits[0].status == drive_reference.OK
    assert "10 mm" in result.hits[0].snippet


def test_a_google_form_is_refused_by_name_without_a_fetch(tmp_path: Path) -> None:
    fake = FakeDrive()
    kimm = fake.folder("KIMM")
    fake.file("굴착 설문", kimm, mime="application/vnd.google-apps.form")

    result = drive_reference.search(
        "굴착 설문", env={"DRIVE_PUBLISH_ENABLED": "1"}, client=_client(tmp_path, fake)
    )

    assert result.hits[0].status == "읽지 못함: 내보낼 수 없는 Google 형식입니다"
    assert fake.downloads() == []


def test_one_unfetchable_file_does_not_sink_the_whole_scan(tmp_path: Path) -> None:
    fake = FakeDrive()
    kimm = fake.folder("KIMM")
    fake.file("굴착 설문", kimm, mime="application/vnd.google-apps.form")
    fake.file("굴착 오차 요약.md", kimm, body="굴착 오차는 10 mm 이하.".encode())

    result = drive_reference.search(
        "굴착 오차", env={"DRIVE_PUBLISH_ENABLED": "1"}, client=_client(tmp_path, fake), limit=5
    )

    assert result.status == drive_reference.OK
    by_name = {hit.name: hit for hit in result.hits}
    assert by_name["굴착 오차 요약.md"].status == drive_reference.OK
    assert by_name["굴착 설문"].status.startswith("읽지 못함")


def test_oversized_file_is_refused_from_metadata_and_yields_its_slot(tmp_path: Path) -> None:
    fake = FakeDrive()
    kimm = fake.folder("KIMM")
    fake.file(
        "굴착 오차 대용량.pdf",
        kimm,
        body=b"%PDF-1.4",
        size=drive_reference.MAX_REFERENCE_BYTES + 1,
    )
    fake.file("굴착 오차 요약.md", kimm, body="굴착 오차는 10 mm 이하.".encode())

    result = drive_reference.search(
        "굴착 오차",
        env={"DRIVE_PUBLISH_ENABLED": "1"},
        client=_client(tmp_path, fake),
        limit=1,
    )

    assert [hit.name for hit in result.hits] == ["굴착 오차 요약.md"]
    assert len(fake.downloads()) == 1


def test_a_form_never_takes_the_slot_from_a_readable_document(tmp_path: Path) -> None:
    fake = FakeDrive()
    kimm = fake.folder("KIMM")
    fake.file("굴착 오차 기준 설문", kimm, mime="application/vnd.google-apps.form")
    fake.file("굴착 메모.md", kimm, body="굴착 오차는 10 mm 이하.".encode())

    result = drive_reference.search(
        "굴착 오차 기준",
        env={"DRIVE_PUBLISH_ENABLED": "1"},
        client=_client(tmp_path, fake),
        limit=1,
    )

    assert [hit.name for hit in result.hits] == ["굴착 메모.md"]


def test_broad_matches_outrank_a_single_repeated_term(tmp_path: Path) -> None:
    fake = FakeDrive()
    kimm = fake.folder("KIMM")
    fake.file("가.md", kimm, body=("굴착 " * 12).encode())
    fake.file("나.md", kimm, body="굴착 오차 기준을 함께 다룬다.".encode())

    result = drive_reference.search(
        "굴착 오차 기준",
        env={"DRIVE_PUBLISH_ENABLED": "1"},
        client=_client(tmp_path, fake),
        limit=3,
    )

    assert [hit.name for hit in result.hits] == ["나.md", "가.md"]


def test_empty_query_reads_nothing(tmp_path: Path) -> None:
    fake = FakeDrive()
    _kimm_tree(fake)

    result = drive_reference.search(
        "  ", env={"DRIVE_PUBLISH_ENABLED": "1"}, client=_client(tmp_path, fake)
    )

    assert result.hits == ()
    assert fake.downloads() == []


def test_root_resolution_leaves_the_shared_folder_cache_untouched(tmp_path: Path) -> None:
    fake = FakeDrive()
    fake.folder("KIMM")
    cache = tmp_path / "folders.json"

    drive_reference.search(
        "굴착",
        env={"DRIVE_PUBLISH_ENABLED": "1"},
        client=DriveClient(gws_bin="gws", folder_cache=cache, runner=fake),
    )

    assert not cache.exists()
