"""A folder tree from one staged manifest is one Drive card and one approval (2026-11-23).

On 2026-11-23 a 76-file survey upload went file by file and raised about 40 cards, saving
21 files. Owner: "드라이브 업로드 하는거, 건별로 파일들 묶어서 승인 받도록 설정해줘". These tests
drive the real ``TreeWriter`` against an in-memory Drive so folder order, the no-overwrite
rule and the read-back are the production code's.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest

from automation import drive_outputs
from automation.drive_client import DriveClientError
from automation.effect_request import cli, gate
from automation.effect_request.model import EffectRequest, EffectRequestError, EffectRequestStore
from automation.effect_request.render import RENDER_VERSION_TREE, render_effect_approval, render_effect_list
from automation.effect_request.tree import MAX_TREE_FILES
from automation.effect_request.watch import notice_text, run_tick
from tests.unit.test_effect_request import OWNER, _approve, _post

PARENT = "1PaReNtFoLdErIdX"
FOLDER_MIME = "application/vnd.google-apps.folder"


class FakeDrive:
    """The owner's Drive below one owner-only parent; records every mutation in order."""

    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, object]] = {
            PARENT: {"name": "측량자료", "mime": FOLDER_MIME, "parent": "ROOT"}}
        self.ops: list[tuple[str, str]] = []
        self.shared: set[str] = set()

    def path_of(self, node_id: str) -> str:
        parts: list[str] = []
        while node_id != PARENT:
            parts.append(str(self.nodes[node_id]["name"]))
            node_id = str(self.nodes[node_id]["parent"])
        return "/".join(reversed(parts))

    def add(self, parent: str, name: str, *, data: bytes | None = None) -> str:
        node_id = f"1NoDe{len(self.nodes):08d}"
        node: dict[str, object] = {"name": name, "parent": parent,
                                   "mime": FOLDER_MIME if data is None else "application/octet-stream"}
        if data is not None:
            node.update(sha=hashlib.sha256(data).hexdigest(), size=len(data))
        self.nodes[node_id] = node
        return node_id

    def folder_info(self, folder_id: str) -> tuple[str, tuple[str, ...]]:
        node = self.nodes[folder_id]
        if node["mime"] != FOLDER_MIME:
            raise DriveClientError(f"폴더가 아니거나 휴지통에 있음: {folder_id}")
        return str(node["name"]), ("내 드라이브", str(node["name"]))

    def verify_owner_only(self, file_id: str) -> None:
        if file_id in self.shared:
            raise DriveClientError(f"소유자 외 권한: {file_id}")

    def list_children(self, folder_id: str) -> list[dict[str, object]]:
        return [{"id": key, "name": node["name"], "mimeType": node["mime"]}
                for key, node in self.nodes.items() if node["parent"] == folder_id]

    def create_folder(self, name: str, parent_id: str) -> str:
        node_id = self.add(parent_id, name)
        self.ops.append(("mkdir", self.path_of(node_id)))
        return node_id

    def upload_new(self, local: Path, name: str, parent_id: str) -> str:
        node_id = self.add(parent_id, name, data=local.read_bytes())
        self.ops.append(("upload", self.path_of(node_id)))
        return node_id

    def file_checksum(self, file_id: str) -> tuple[str, int]:
        node = self.nodes[file_id]
        return str(node["sha"]), int(node["size"])  # type: ignore[call-overload]

    def download_and_verify(self, file_id: str, local: Path) -> str:
        assert self.file_checksum(file_id) == (hashlib.sha256(local.read_bytes()).hexdigest(), local.stat().st_size)
        return str(self.nodes[file_id]["sha"])

    def web_view_link(self, file_id: str) -> str:
        return f"https://drive.example/{file_id}"


def _tree(tmp_path: Path, layout: dict[str, bytes]) -> Path:
    """Stage every file under a plain name and write the manifest beside them."""
    staging = tmp_path / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, (path, data) in enumerate(layout.items()):
        staged = f"survey-{index:03d}{Path(path).suffix}"
        (staging / staged).write_bytes(data)
        rows.append({"path": path, "staged": staged, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
    manifest = staging / "manifest.json"
    manifest.write_text(json.dumps({"version": 1, "files": rows}, ensure_ascii=False), encoding="utf-8")
    return manifest


def _survey(count: int) -> dict[str, bytes]:
    days = ("1일차", "2일차/오전", "2일차/오후", "사진")
    return {f"현장측량/{days[n % len(days)]}/scan-{n:03d}.las": f"point cloud {n}\n".encode() for n in range(count)}


def _args(manifest: Path, **extra: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "effect": "drive-save", "summary": "측량 자료 올리기", "file": None, "title": None,
        "kind": "doctype", "project": "", "folder": PARENT, "on": None, "manifest": str(manifest),
        "origin_channel_id": "11", "origin_message_id": "22",
    }
    values.update(extra)
    return argparse.Namespace(**values)


@pytest.fixture
def drive(monkeypatch: pytest.MonkeyPatch) -> FakeDrive:
    fake = FakeDrive()
    monkeypatch.setattr(drive_outputs, "client_from_environment", lambda: fake)
    return fake


def _tick(store: EffectRequestStore, transport: object, drive: FakeDrive):  # noqa: ANN202
    runtime = gate.DiscordRuntime(transport, object(), OWNER)  # type: ignore[arg-type]
    return run_tick(store, runtime, token="", now=datetime(2026, 11, 23),
                    publisher=lambda *a, **k: pytest.fail("the dated-tree publisher ran"),
                    tree_writer=lambda: drive_outputs.TreeWriter(client=drive))  # type: ignore[arg-type]


def _cards(transport: object, record: EffectRequest) -> list[str]:
    return [content for _, content in transport.messages.values() if record.action_hash in content]  # type: ignore[attr-defined]


def test_a_tree_is_one_card_with_every_path_listed_once_below_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drive: FakeDrive,
) -> None:
    layout = _survey(76)
    store = EffectRequestStore(tmp_path / "state")
    record = cli._new_record(_args(_tree(tmp_path, layout)), store)
    card = render_effect_approval(record)
    assert record.payload["version"] == 3 and f"판본: {RENDER_VERSION_TREE}" in card
    assert "파일 76개" in card and f"id {PARENT}" in card and "- 현장측량/2일차/오전/ (파일 19개)" in card
    transport, posted = _post(monkeypatch, store, record)
    listing = [content for message_id, (_, content) in transport.messages.items() if message_id != posted.message_id]
    assert _cards(transport, posted) == [card] and len(listing) == len(render_effect_list(record)) >= 2
    assert f"전체 목록: 이 카드 아래 글 {len(listing)}개" in card
    listed, folder = [], ""
    for line in (line for chunk in listing for line in chunk.splitlines()[1:]):
        if line.startswith("📁 "):
            folder = line[2:].rstrip("/")
        else:
            listed.append(f"{folder}/{line[2:].split(' · ')[0]}")
    assert sorted(listed) == sorted(layout) and all(len(chunk) <= 1900 for chunk in listing)
    assert {message_id for message_id, _ in transport.reactions} == {posted.message_id}


def test_a_list_message_changed_or_gone_before_approval_runs_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drive: FakeDrive,
) -> None:
    store = EffectRequestStore(tmp_path / "state")
    transport, record = _post(monkeypatch, store, cli._new_record(_args(_tree(tmp_path, _survey(76))), store))
    assert len(record.list_message_ids) == len(render_effect_list(record)) >= 2
    first = record.list_message_ids[0]
    channel, body = transport.messages[first]
    transport.messages[first] = (channel, body.replace("scan-000", "scan-999"))
    _approve(transport, record)
    _tick(store, transport, drive)
    assert (store.get(record.request_id) or record).status == "posted" and drive.ops == []
    del transport.messages[first]
    result = _tick(store, transport, drive)
    assert result.cancelled == (record.request_id,) and drive.ops == []
    assert (store.get(record.request_id) or record).status == "abandoned"


def test_two_copies_of_one_request_racing_keep_one_record_and_one_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drive: FakeDrive,
) -> None:
    manifest = _tree(tmp_path, _survey(8))
    store = EffectRequestStore(tmp_path / "state")
    first = cli._new_record(_args(manifest), store)
    stored = store.all
    missed: list[bool] = []

    def lookup() -> tuple[EffectRequest, ...]:
        # The second copy looked before the first one was stored.
        if not missed:
            missed.append(True)
            return ()
        return stored()

    monkeypatch.setattr(store, "all", lookup)
    second = cli._new_record(_args(manifest, summary="같은 일 다른 말"), store)
    assert second == first and len(stored()) == 1 and len(list((store.root / "frozen").iterdir())) == 1
    transport, posted = _post(monkeypatch, store, first)
    runtime = gate.DiscordRuntime(transport, object(), OWNER)  # type: ignore[arg-type]
    assert gate.request_approval(second, store, runtime).outcome.name == "PENDING"
    assert len(_cards(transport, posted)) == 1


def test_a_file_whose_read_back_fails_is_never_counted_saved_on_a_later_try(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drive: FakeDrive,
) -> None:
    def unreadable(file_id: str, local: Path) -> str:
        raise DriveClientError(f"다시 내려받은 sha256 불일치: {file_id}")

    monkeypatch.setattr(drive, "download_and_verify", unreadable)
    store = EffectRequestStore(tmp_path / "state")
    transport, record = _post(monkeypatch, store, cli._new_record(
        _args(_tree(tmp_path, {"현장측량/a.las": b"a\n", "현장측량/b.las": b"b\n"})), store))
    _approve(transport, record)
    _tick(store, transport, drive)
    _tick(store, transport, drive)
    after = store.get(record.request_id) or record
    assert after.status == "approved" and after.attempts == 2
    assert [row["status"] for row in after.receipt["files"]] == ["failed", "failed"]
    assert [op for op, _ in drive.ops].count("upload") == 2


def test_the_deepest_and_longest_tree_allowed_still_fits_one_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drive: FakeDrive,
) -> None:
    deep = "/".join(f"{'가' * 119}{depth}" for depth in range(7))
    layout = {f"{deep}/{'나' * 119}{n % 4}/f-{n:03d}.las": f"{n}\n".encode() for n in range(76)}
    store = EffectRequestStore(tmp_path / "state")
    record = cli._new_record(_args(_tree(tmp_path, layout)), store)
    assert len(record.payload["folders"]) == 11 and len(render_effect_approval(record)) <= 1900
    transport, posted = _post(monkeypatch, store, record)
    assert len(_cards(transport, posted)) == 1 and all(len(body) <= 1900 for _, body in transport.messages.values())


def test_a_card_whose_list_cannot_be_posted_is_taken_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drive: FakeDrive,
) -> None:
    from automation.interop.approval_lifecycle import ApprovalSurfaceError
    from tests.unit.test_effect_request import FakeTransport

    class ListFails(FakeTransport):
        def post_message(self, channel_id: str, content: str) -> str:
            if self.messages:
                raise OSError("Discord 503")
            return super().post_message(channel_id, content)

    store = EffectRequestStore(tmp_path / "state")
    record = cli._new_record(_args(_tree(tmp_path, _survey(76))), store)
    transport = ListFails()
    _main(monkeypatch, transport)
    with pytest.raises(ApprovalSurfaceError):
        gate.request_approval(record, store, gate.DiscordRuntime(transport, object(), OWNER))  # type: ignore[arg-type]
    assert transport.messages == {} and transport.reactions == {}
    assert (store.get(record.request_id) or record).message_id is None


def test_after_one_approval_folders_are_made_parents_first_then_every_listed_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drive: FakeDrive,
) -> None:
    layout = _survey(12)
    store = EffectRequestStore(tmp_path / "state")
    transport, record = _post(monkeypatch, store, cli._new_record(_args(_tree(tmp_path, layout)), store))
    # A file that reaches the frozen folder after the card is outside the approved list.
    stray = Path(str(record.payload["files"][0]["frozen_path"])).parent.parent / "99" / "stray.las"
    stray.parent.mkdir(parents=True)
    stray.write_bytes(b"not approved")
    assert _tick(store, transport, drive).done == () and drive.ops == []
    _approve(transport, record)
    result = _tick(store, transport, drive)
    done = store.get(record.request_id) or record
    assert result.done == (record.request_id,) and done.status == "done" and done.receipt["saved"] == 12
    made = [path for op, path in drive.ops if op == "mkdir"]
    assert made == ["현장측량", "현장측량/1일차", "현장측량/2일차", "현장측량/사진", "현장측량/2일차/오전", "현장측량/2일차/오후"]
    uploads = [path for op, path in drive.ops if op == "upload"]
    assert sorted(uploads) == sorted(layout) and "stray.las" not in str(drive.ops)
    first_upload = {path.rpartition("/")[0]: drive.ops.index(("upload", path)) for path in reversed(uploads)}
    assert all(drive.ops.index(("mkdir", folder)) < at for folder, at in first_upload.items())
    assert "파일 12개" in notice_text(done, "done")


@pytest.mark.parametrize("row, reason", [
    ({"staged": "../outside.las"}, "staged"),
    ({"staged": "/etc/passwd"}, "staged"),
    ({"path": "현장측량/../../밖.las"}, "경로"),
    ({"path": "/절대.las"}, "경로"),
    ({"path": "현장측량/.숨김.las"}, "경로"),
    ({"extra": "x"}, "네 개"),
])
def test_a_manifest_entry_outside_the_staging_folder_or_the_tree_is_refused(
    tmp_path: Path, drive: FakeDrive, row: dict[str, str], reason: str,
) -> None:
    manifest = _tree(tmp_path, {"현장측량/a.las": b"a\n"})
    body = json.loads(manifest.read_text(encoding="utf-8"))
    body["files"][0].update(row)
    manifest.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    store = EffectRequestStore(tmp_path / "state")
    with pytest.raises(EffectRequestError, match=reason):
        cli._new_record(_args(manifest), store)
    assert store.all() == () and drive.ops == []


def test_a_duplicate_path_or_a_file_named_like_a_folder_is_refused(tmp_path: Path, drive: FakeDrive) -> None:
    store = EffectRequestStore(tmp_path / "state")
    clash = _tree(tmp_path, {"현장측량": b"file\n", "현장측량/a.las": b"a\n"})
    with pytest.raises(EffectRequestError, match="겹친다"):
        cli._new_record(_args(clash), store)
    twice = _tree(tmp_path / "twice", {"현장측량/a.las": b"a\n", "현장측량/b.las": b"b\n"})
    body = json.loads(twice.read_text(encoding="utf-8"))
    body["files"][1]["path"] = body["files"][0]["path"]
    twice.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(EffectRequestError, match="두 번"):
        cli._new_record(_args(twice), store)


def test_a_sha256_mismatch_refuses_the_request_before_anything_is_stored(
    tmp_path: Path, drive: FakeDrive,
) -> None:
    manifest = _tree(tmp_path, {"현장측량/a.las": b"a\n", "현장측량/b.las": b"b\n"})
    (manifest.parent / "survey-001.las").write_bytes(b"other bytes\n")
    store = EffectRequestStore(tmp_path / "state")
    with pytest.raises(EffectRequestError, match="sha256 이 매니페스트와 다르다: 현장측량/b.las"):
        cli._new_record(_args(manifest), store)
    assert store.all() == () and not (store.root / "frozen").exists()


def test_a_frozen_file_changed_after_approval_is_final_and_the_rest_is_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drive: FakeDrive,
) -> None:
    store = EffectRequestStore(tmp_path / "state")
    manifest = _tree(tmp_path, {"현장측량/a.las": b"a\n", "현장측량/b.las": b"b\n"})
    transport, record = _post(monkeypatch, store, cli._new_record(_args(manifest), store))
    Path(str(record.payload["files"][0]["frozen_path"])).write_bytes(b"tampered")
    _approve(transport, record)
    result = _tick(store, transport, drive)
    failed = store.get(record.request_id) or record
    assert result.failed == (record.request_id,) and [p for op, p in drive.ops if op == "upload"] == ["현장측량/b.las"]
    assert "바뀌었다" in failed.receipt["files"][0]["error"] and "2개 중 1개 저장" in notice_text(failed, "failed")


def test_a_same_name_file_with_other_bytes_is_left_out_for_its_own_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drive: FakeDrive, capsys: pytest.CaptureFixture[str],
) -> None:
    day = drive.add(drive.add(PARENT, "현장측량"), "1일차")
    drive.add(day, "a.las", data=b"older survey\n")
    drive.add(day, "b.las", data=b"b\n")
    layout = {"현장측량/1일차/a.las": b"new survey\n", "현장측량/1일차/b.las": b"b\n", "현장측량/1일차/c.las": b"c\n"}
    store = EffectRequestStore(tmp_path / "state")
    record = cli._new_record(_args(_tree(tmp_path, layout)), store)
    assert [item["path"] for item in record.payload["files"]] == ["현장측량/1일차/c.las"]
    assert record.payload["unchanged"] == ["현장측량/1일차/b.las"]
    (item,) = record.payload["overwrite"]
    assert item == {"path": "현장측량/1일차/a.las", "folder_id": day, "staged": "survey-000.las", "title": "a"}
    assert cli.overwrite_command(item) == f"drive-save --card --folder {day} --file survey-000.las --title a"
    card = render_effect_approval(record)
    assert "새로 만들 폴더 0개" in card and "건너뛰는 파일 1개" in card and "이 카드에서 뺀 파일 1개" in card
    # The overwrite is its own drive-save card for that folder under the tree's name, never part of the tree card.
    staged = tmp_path / "staging" / "survey-000.las"
    overwrite = cli._new_record(argparse.Namespace(
        effect="drive-save", summary="a.las 덮어쓰기", file=[str(staged)], title=["a"], kind="doctype", project="",
        folder=day, on=None, origin_channel_id="11", origin_message_id="22"), store)
    assert overwrite.key != record.key and overwrite.payload["version"] == 2
    assert [file["drive_name"] for file in overwrite.payload["files"]] == ["a.las"]
    assert "같은 이름 파일이 그 폴더에 이미 있으면 그 파일을 새 내용으로 갱신한다" in render_effect_approval(overwrite)
    # A same-name file that appears after the card is never replaced either.
    transport, record = _post(monkeypatch, store, record)
    drive.add(day, "c.las", data=b"someone else's c\n")
    _approve(transport, record)
    _tick(store, transport, drive)
    after = store.get(record.request_id) or record
    assert after.status == "failed" and "덮어쓰지 않았다" in after.receipt["files"][0]["error"]
    assert not any(op == "upload" for op, _ in drive.ops)


def test_when_every_file_would_overwrite_the_refusal_names_each_card_request(
    tmp_path: Path, drive: FakeDrive,
) -> None:
    day = drive.add(drive.add(PARENT, "현장측량"), "1일차")
    drive.add(day, "a.las", data=b"older survey\n")
    store = EffectRequestStore(tmp_path / "state")
    with pytest.raises(EffectRequestError) as refused:
        cli._new_record(_args(_tree(tmp_path, {"현장측량/1일차/a.las": b"new survey\n"})), store)
    assert f"drive-save --card --folder {day} --file survey-000.las --title a" in str(refused.value)
    assert store.all() == ()


def test_a_staged_name_with_another_extension_than_its_path_is_refused(tmp_path: Path, drive: FakeDrive) -> None:
    manifest = _tree(tmp_path, {"현장측량/a.las": b"a\n"})
    body = json.loads(manifest.read_text(encoding="utf-8"))
    (manifest.parent / "survey-000.bin").write_bytes(b"a\n")
    body["files"][0]["staged"] = "survey-000.bin"
    manifest.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(EffectRequestError, match="확장자"):
        cli._new_record(_args(manifest), EffectRequestStore(tmp_path / "state"))


def test_a_long_parent_path_and_the_longest_tree_still_fit_one_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drive: FakeDrive,
) -> None:
    monkeypatch.setattr(drive, "folder_info",
                        lambda folder_id: ("p" * 100, tuple(f"{'q' * 99}{depth}" for depth in range(8))))
    deep = "/".join(f"{'가' * 119}{depth}" for depth in range(7))
    layout = {f"{deep}/{'나' * 119}{n % 4}/f-{n:03d}.las": f"{n}\n".encode() for n in range(76)}
    store = EffectRequestStore(tmp_path / "state")
    record = cli._new_record(_args(_tree(tmp_path, layout), summary="요" * 120), store)
    transport, posted = _post(monkeypatch, store, record)
    assert len(_cards(transport, posted)) == 1 and all(len(body) <= 1900 for _, body in transport.messages.values())


def test_a_record_settled_while_its_copy_waited_gets_no_second_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drive: FakeDrive,
) -> None:
    from dataclasses import replace

    store = EffectRequestStore(tmp_path / "state")
    stale = cli._new_record(_args(_tree(tmp_path, _survey(4))), store)
    transport, posted = _post(monkeypatch, store, stale)
    store.update(replace(posted, status="done"))
    runtime = gate.DiscordRuntime(transport, object(), OWNER)  # type: ignore[arg-type]
    before = dict(transport.messages)
    assert gate.request_approval(stale, store, runtime).outcome.name == "DEFERRED"
    journal = gate.PostingJournal(store.root / "posting-journal")
    with pytest.raises(gate.ApprovalSurfaceError, match="already done"):
        gate.EffectApprovalGate(stale, store, transport, journal).post(  # type: ignore[arg-type]
            gate.ApprovalIntent(key=stale.key, action_hash=stale.action_hash, channel_id="4242"))
    assert transport.messages == before


def test_a_list_longer_than_the_message_cap_is_refused_before_any_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drive: FakeDrive,
) -> None:
    from automation.effect_request import render

    monkeypatch.setattr(render, "MAX_LIST_MESSAGES", 1)
    store = EffectRequestStore(tmp_path / "state")
    with pytest.raises(EffectRequestError, match="목록 글이 1개를 넘는다"):
        cli._new_record(_args(_tree(tmp_path, _survey(76))), store)
    assert store.all() == () and not any((store.root / "frozen").iterdir())


def test_a_record_finished_while_its_thread_was_resolved_keeps_its_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drive: FakeDrive,
) -> None:
    from dataclasses import replace

    from tests.unit.test_effect_request import FakeTransport

    store = EffectRequestStore(tmp_path / "state")
    transport, posted = _post(monkeypatch, store, cli._new_record(_args(_tree(tmp_path, _survey(4))), store))
    finished = replace(posted, status="done", receipt={"saved": 4, "total": 4})
    binding = gate.resolve_new_binding(None, None, None)  # the fixed test binding from _post

    def resolve_while_the_watcher_finishes(*args: object, **kwargs: object) -> object:
        store.update(finished)
        return binding

    monkeypatch.setattr(gate, "resolve_new_binding", resolve_while_the_watcher_finishes)
    retry = FakeTransport()
    verdict = gate.request_approval(posted, store, gate.DiscordRuntime(retry, object(), OWNER))  # type: ignore[arg-type]
    after = store.get(posted.request_id) or posted
    assert verdict.outcome.name == "DEFERRED" and retry.messages == {}
    assert (after.status, after.receipt, after.message_id) == ("done", {"saved": 4, "total": 4}, posted.message_id)


def test_dropping_a_vanished_card_never_demotes_a_finished_record(tmp_path: Path, drive: FakeDrive) -> None:
    from dataclasses import replace

    store = EffectRequestStore(tmp_path / "state")
    record = cli._new_record(_args(_tree(tmp_path, _survey(2))), store)
    store.update(replace(record, status="done", message_id="900", channel_id="4242"))
    store.clear_message(record.key, record.action_hash, "900")
    assert (store.get(record.request_id) or record).status == "done"


@pytest.mark.parametrize("name", ["-survey.las", "survey .las", " survey.las"])
def test_a_name_that_could_not_keep_itself_on_an_overwrite_card_is_refused(
    tmp_path: Path, drive: FakeDrive, name: str,
) -> None:
    with pytest.raises(EffectRequestError, match="경로|-로 시작"):
        cli._new_record(_args(_tree(tmp_path, {f"현장측량/{name}": b"a\n"})), EffectRequestStore(tmp_path / "state"))


@pytest.mark.parametrize("name", ["R&D", "a  b", "괄호(1)", "x$y", "semi;colon", "quote'mark", 'dq"x', "back`tick",
                                  "pipe|x", "한글 이름", "lt<gt>"])
def test_an_overwrite_request_passes_the_real_gate_and_keeps_the_tree_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drive: FakeDrive, capsys: pytest.CaptureFixture[str], name: str,
) -> None:
    from automation.yoodori_gate import yoodori_read_gate as ygate
    from tests.unit.test_effect_request import FakeTransport
    from tests.unit.test_yoodori_gate import ORIGIN

    day = drive.add(drive.add(PARENT, "현장측량"), "1일차")
    drive.add(day, f"{name}.las", data=b"older\n")
    manifest = _tree(tmp_path, {f"현장측량/1일차/{name}.las": b"newer\n", "현장측량/1일차/other.las": b"o\n"})
    store = EffectRequestStore(tmp_path / "state")
    (item,) = cli._new_record(_args(manifest), store).payload["overwrite"]
    ran: list[list[str]] = []
    monkeypatch.setattr(ygate, "HOME", tmp_path)
    monkeypatch.setattr(ygate, "STAGING", manifest.parent)
    monkeypatch.setattr(ygate, "_log", lambda *_: None)
    monkeypatch.setattr(ygate, "_switched", lambda kind: True)
    monkeypatch.setattr(ygate, "card_env", lambda *args: {})
    monkeypatch.setattr(ygate, "run_job", lambda job: pytest.fail("the overwrite went cardless"))
    monkeypatch.setattr(ygate.subprocess, "run", lambda argv, **kwargs: ran.append(argv) or __import__("subprocess")
                        .CompletedProcess(argv, 0))
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", f"{cli.overwrite_command(item)} --summary overwrite {' '.join(ORIGIN)}")
    assert ygate.main() == 0, capsys.readouterr().err
    (argv,) = ran
    assert argv[3:5] == ["request", "drive-save"]
    _main(monkeypatch, FakeTransport())
    monkeypatch.setenv("EFFECT_REQUEST_ROOT", str(store.root))
    assert cli.main(argv[3:]) == 0, capsys.readouterr().err
    (card,) = [r for r in EffectRequestStore(store.root).all() if r.payload.get("version") == 2]
    assert [file["drive_name"] for file in card.payload["files"]] == [f"{name}.las"]
    assert card.payload["folder_id"] == day


def test_every_overwrite_is_named_when_all_files_conflict(tmp_path: Path, drive: FakeDrive) -> None:
    day = drive.add(drive.add(PARENT, "현장측량"), "1일차")
    for n in range(11):
        drive.add(day, f"f{n:02d}.las", data=b"older\n")
    store = EffectRequestStore(tmp_path / "state")
    with pytest.raises(EffectRequestError) as refused:
        cli._new_record(_args(_tree(tmp_path, {f"현장측량/1일차/f{n:02d}.las": b"newer\n" for n in range(11)})), store)
    assert all(f"--title f{n:02d}" in str(refused.value) for n in range(11))


def test_a_shared_parent_folder_refuses_the_request(tmp_path: Path, drive: FakeDrive) -> None:
    drive.shared.add(PARENT)
    store = EffectRequestStore(tmp_path / "state")
    with pytest.raises(EffectRequestError, match="형만 볼 수 있는 폴더"):
        cli._new_record(_args(_tree(tmp_path, {"a.las": b"a\n"})), store)
    assert store.all() == ()


def _main(monkeypatch: pytest.MonkeyPatch, transport: object) -> None:
    from automation import notice_bot
    from automation.interop.approval_surface import ApprovalBinding, ApprovalKind, required_surface
    from automation.interop.approval_surface import POLICY_VERSION

    binding = ApprovalBinding(ApprovalKind.EXTERNAL_EFFECT, required_surface(ApprovalKind.EXTERNAL_EFFECT),
                              "4242", POLICY_VERSION, "77")
    monkeypatch.setattr(gate, "reuse_request_thread", lambda *args: None)
    monkeypatch.setattr(gate, "resolve_new_binding", lambda *args, **kwargs: binding)
    monkeypatch.setattr(notice_bot, "resolve_token", lambda: "token")
    monkeypatch.setattr(cli, "owner_id_from_config", lambda: OWNER)
    monkeypatch.setattr(cli, "discord_runtime",
                        lambda token, owner, store: gate.DiscordRuntime(transport, object(), OWNER))  # type: ignore[arg-type]


def test_a_repeated_request_or_one_over_the_cap_posts_no_second_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drive: FakeDrive, capsys: pytest.CaptureFixture[str],
) -> None:
    from tests.unit.test_effect_request import FakeTransport

    transport = FakeTransport()
    _main(monkeypatch, transport)
    monkeypatch.setenv("EFFECT_REQUEST_ROOT", str(tmp_path / "state"))
    manifest = _tree(tmp_path, _survey(76))
    argv = ["request", "drive-save", "--manifest", str(manifest), "--folder", PARENT, "--summary", "측량 자료",
            "--origin-channel-id", "11", "--origin-message-id", "22"]
    assert cli.main(argv) == 0
    posted = len(transport.messages)
    # The same job again, even worded differently, finds the same card: nothing new is posted.
    assert cli.main([*argv[:-5], "측량 자료 다시", *argv[-4:]]) == 0
    assert len(transport.messages) == posted
    (record,) = EffectRequestStore(tmp_path / "state").all()
    assert len(_cards(transport, record)) == 1
    out = capsys.readouterr().out
    assert out.count(f"REQUESTED request={record.request_id}") == 2
    _approve(transport, record)
    _tick(EffectRequestStore(tmp_path / "state"), transport, drive)
    posted = len(transport.messages)
    assert cli.main(argv) == 0 and len(transport.messages) == posted and len(_cards(transport, record)) == 1
    assert f"EFFECT-REQUEST-ALREADY request={record.request_id} status=done" in capsys.readouterr().out
    # Over the cap: refused before any card, and a retry is refused the same way.
    big = _tree(tmp_path / "big", {f"묶음/{n:04d}.las": b"x" for n in range(MAX_TREE_FILES + 1)})
    over = ["request", "drive-save", "--manifest", str(big), "--folder", PARENT, "--summary", "큰 묶음",
            "--origin-channel-id", "11", "--origin-message-id", "23"]
    assert cli.main(over) == 2 and cli.main(over) == 2
    assert len(transport.messages) == posted and "나눠서 요청" in capsys.readouterr().err


def test_a_cancelled_tree_is_not_asked_again_for_the_same_owner_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drive: FakeDrive, capsys: pytest.CaptureFixture[str],
) -> None:
    from tests.unit.test_effect_request import FakeTransport

    transport = FakeTransport()
    _main(monkeypatch, transport)
    monkeypatch.setenv("EFFECT_REQUEST_ROOT", str(tmp_path / "state"))
    argv = ["request", "drive-save", "--manifest", str(_tree(tmp_path, _survey(4))), "--folder", PARENT,
            "--summary", "측량 자료", "--origin-channel-id", "11", "--origin-message-id", "22"]
    assert cli.main(argv) == 0
    (record,) = EffectRequestStore(tmp_path / "state").all()
    transport.reactions.setdefault((record.message_id or "", "\u26d4"), []).append((OWNER, False))
    _tick(EffectRequestStore(tmp_path / "state"), transport, drive)
    posted = len(transport.messages)
    assert cli.main(argv) == cli.EXIT_REFUSED and len(transport.messages) == posted and drive.ops == []
    assert "status=cancelled" in capsys.readouterr().out


def test_save_now_never_saves_a_tree_without_a_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drive: FakeDrive, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("EFFECT_REQUEST_ROOT", str(tmp_path / "state"))
    argv = ["save-now", "--manifest", str(_tree(tmp_path, _survey(3))), "--folder", PARENT, "--summary", "s",
            "--origin-channel-id", "11", "--origin-message-id", "22"]
    assert cli.main(argv) == cli.EXIT_REFUSED
    assert "tree-needs-card" in capsys.readouterr().err and drive.ops == []
    assert EffectRequestStore(tmp_path / "state").all() == ()
