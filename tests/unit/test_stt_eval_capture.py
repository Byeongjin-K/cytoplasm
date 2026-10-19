"""가짜 소유자 노트·Drive에서 실제 수집과 멱등성을 검증한다."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from automation.drive_client import DriveClient
from automation.entity_preflight.contracts import JsonValue
from automation.plaud_sync.audio_manifest import ManifestEntry, record_manifest
from automation.plaud_sync.model import PlaudSyncRecord, PlaudSyncState
from automation.plaud_sync.store import save_note_body, save_state
from automation.stt_eval import model, snapshot
from automation.stt_eval.cron import capture

REPO = Path(__file__).resolve().parents[2]
WRAPPER = REPO / "automation/stt_eval/cron/stt_eval_capture_watch.py"
BODY = "[00:00:00] 화자1 · 가상인물\n가상 문장입니다.\n\n[00:00:01] 화자2\n다른 문장입니다."
LEGEND = "- 화자: 화자1=가상인물 [소유자] · 화자2=미상"


def note(body: str = BODY) -> str:
    return "---\ntitle: fixture\n---\n# fixture\n\n## 한눈에\n- 한 줄:: fixture\n\n## 전문\n> [!quote]- 전문 펼치기\n" + "\n".join("> " + line for line in (LEGEND + "\n\n" + body).splitlines()) + "\n\n---\n출처 fixture\n"


def fixture(root: Path, *, registered: bool = True) -> tuple[dict[str, str], Path]:
    """개인 자료 없이 읽기 전용 입력과 공용 원장을 준비한다."""
    env = {"HOME": str(root), "STT_EVAL_ROOT": str(root / "eval"), "AUTOPHAGY_REPO_ROOT": str(REPO), "DRIVE_PUBLISH_ENABLED": "0"}
    config = root / ".hermes/rag-ingest/config.json"
    config.parent.mkdir(parents=True)
    mirror = root / "mirror"
    _ = config.write_text(json.dumps({"obsidian": {"enabled": True, "repo_url": "fixture", "mirror_dir": str(mirror), "ssh_key_path": str(root / "unused"), "sensitivity_rules_path": str(root / "unused")}}))
    relative = "000_PARA/Area/Lifelog/2026/fixture.md"
    edited = mirror / relative
    edited.parent.mkdir(parents=True)
    _ = edited.write_text(note())
    state_dir = root / ".hermes/plaud-sync"
    record = PlaudSyncRecord(1, "fixture", "2026-09-07", relative, "fixture", "a" * 64, "b" * 64, "written", "fixture", "fixture", "fixture", 1, None, "2026-09-07", None, "2026-09-07", None, None, None)
    save_state(state_dir / "state.json", PlaudSyncState(1, None, {"fixture": record}))
    save_note_body(state_dir, "fixture", note())
    if registered:
        _ = record_manifest(ManifestEntry("a" * 64, "fixture", 2000, "fixture", "2026-09-07T00:00:00Z"), env)
    return env, edited


def references(env: dict[str, str]) -> list[Path]:
    return list((Path(env["STT_EVAL_ROOT"]) / "reference").glob("*.json"))


def test_unchanged_silent_and_verbose(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env, _ = fixture(tmp_path)
    assert capture.run_once(env) == 0
    assert references(env) == []
    assert capsys.readouterr().out == ""
    assert capture.run_once(env, verbose=True) == 0
    assert capsys.readouterr().out == "STT-EVAL-CAPTURE new=0 updated=0 skipped=0\n"


def test_edit_latest_dedup_and_revert(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env, edited = fixture(tmp_path)
    manifest = Path(env["STT_EVAL_ROOT"]) / "manifest.jsonl"
    before = manifest.read_bytes()
    _ = edited.write_text(note(BODY.replace("가상 문장입니다.", "수정 문장입니다.")))
    assert capture.run_once(env) == 0
    assert capsys.readouterr().out == "STT-EVAL-CAPTURE new=1 updated=0 skipped=0\n"
    target, = references(env)
    record = model.load_record(target)
    assert record.text == "수정 문장입니다. 다른 문장입니다."
    assert record.words[0].tag == model.SpeakerTag("SPEAKER", ("화자1",))
    assert record.words[-1].tag == model.SpeakerTag("SPEAKER", ("화자2",))
    assert all(word.start_ms is None and word.end_ms is None and word.timing_source == "missing" for word in record.words)
    assert all(record.text[word.char_start:word.char_end] for word in record.words)
    assert record.turns == record.entities == () and record.status == "ok"
    assert record.provenance is not None and record.provenance.startswith("owner-edit:lifelog:")
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    saved = target.read_bytes()
    assert capture.run_once(env) == 0
    assert capsys.readouterr().out == ""
    assert target.read_bytes() == saved and references(env) == [target]
    _ = edited.write_text(note(BODY.replace("가상 문장입니다.", "최신 문장입니다.")))
    assert capture.run_once(env) == 0
    assert capsys.readouterr().out == "STT-EVAL-CAPTURE new=0 updated=1 skipped=0\n"
    assert model.load_record(target).text.startswith("최신")
    _ = edited.write_text(note())
    assert capture.run_once(env) == 0
    assert model.load_record(target).text.startswith("가상")
    assert manifest.read_bytes() == before
    assert not (manifest.parent / "hyp").exists()


COSMETIC: tuple[Callable[[str], str], ...] = (
    lambda text: text.replace("## 전문", "####   전문").replace("# fixture", "### fixture"),
    lambda text: text.replace("가상 문장", "가상   문장").replace("> ", "  >   ").replace("\n\n", "\n\n\n"),
    lambda text: unicodedata.normalize("NFD", text),
    lambda text: text.replace('title: fixture', 'title: "fixture"'),
    lambda text: text.replace("[00:00:01]", "[00:00:02]"),
)


@pytest.mark.parametrize("change", COSMETIC)
def test_cosmetic_changes_are_not_edits(tmp_path: Path, capsys: pytest.CaptureFixture[str], change: Callable[[str], str]) -> None:
    env, edited = fixture(tmp_path)
    _ = edited.write_text(change(note()))
    assert capture.run_once(env) == 0
    assert references(env) == []
    assert capsys.readouterr().out == ""


def test_missing_manifest_skips(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env, edited = fixture(tmp_path, registered=False)
    _ = edited.write_text(note(BODY.replace("가상", "수정")))
    assert capture.run_once(env, verbose=True) == 0
    assert references(env) == []
    assert capsys.readouterr().out == "STT-EVAL-CAPTURE new=0 updated=0 skipped=1\n"


@pytest.mark.parametrize("before,after", [("가상인물", "다른인물"), ("화자1=가상인물", "화자1=다른인물"), ("화자1 · 가상인물", "화자1 · 다른인물")])
def test_speaker_and_legend_changes_are_edits(tmp_path: Path, capsys: pytest.CaptureFixture[str], before: str, after: str) -> None:
    env, edited = fixture(tmp_path)
    _ = edited.write_text(note().replace(before, after))
    assert capture.run_once(env) == 0
    assert capsys.readouterr().out == "STT-EVAL-CAPTURE new=1 updated=0 skipped=0\n"
    assert capture.run_once(env) == 0
    assert capsys.readouterr().out == ""
    _ = edited.write_text(note().replace("가상인물", "다른인물").replace("[00:00:01] 화자2", "[00:00:01] 화자1"))
    assert capture.run_once(env) == 0
    target, = references(env)
    assert model.load_record(target).words[-1].tag.speakers == ("화자1",)
    assert capsys.readouterr().out == "STT-EVAL-CAPTURE new=0 updated=1 skipped=0\n"


class DriveFixture:
    """실제 DriveClient의 list/get만 허용하는 오프라인 gws 경계."""
    def __init__(self, body: str) -> None:
        self.body: str = body
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> dict[str, JsonValue]:
        self.calls.append(argv)
        assert argv[1:3] == ["drive", "files"]
        params = cast(dict[str, str], json.loads(argv[argv.index("--params") + 1]))
        if argv[3] == "get":
            _ = Path(argv[argv.index("-o") + 1]).write_text(self.body)
            return {}
        assert argv[3] == "list"
        query = params["q"]
        folder = "application/vnd.google-apps.folder"
        if "name =" in query:
            return {"files": [{"id": "transcripts"}]}
        if "'transcripts'" in query:
            return {"files": [{"id": "project", "name": "fixture", "mimeType": folder}]}
        if "'project'" in query:
            return {"files": [{"id": "year", "name": "2026", "mimeType": folder}]}
        assert "'year'" in query
        return {"files": [{"id": "file", "name": "meeting-fixture.md", "modifiedTime": "2026-09-07T00:00:00Z"}]}


def test_drive_existing_minutes_do_not_hide_owner_edit(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env, _ = fixture(tmp_path)
    local = tmp_path / ".hermes/speechtotext/transcripts/meeting-fixture.md"
    local.parent.mkdir(parents=True)
    markdown = "# fixture\n\n" + LEGEND + "\n\n---\n\n" + BODY
    _ = local.write_text(markdown)
    _ = record_manifest(ManifestEntry("c" * 64, "fixture-audio", 2000, local.stem, "2026-09-07T00:00:00Z", "meeting"), env)
    fake = DriveFixture(markdown)
    drive = DriveClient("gws", tmp_path / "unused-cache", runner=fake)
    assert capture.run_once(env, drive=drive) == 0
    assert references(env) == [] and capsys.readouterr().out == ""
    fake.body = markdown.replace("가상 문장", "편집 문장")
    assert capture.run_once(env, drive=drive) == 0
    assert capsys.readouterr().out == "STT-EVAL-CAPTURE new=1 updated=0 skipped=0\n"
    target, = references(env)
    provenance = model.load_record(target).provenance
    assert provenance is not None and provenance.startswith("owner-edit:meeting:")
    assert model.load_record(target).text.startswith("편집")
    assert capture.run_once(env, drive=drive) == 0
    assert capsys.readouterr().out == ""
    assert local.read_text() == markdown
    assert not (tmp_path / "unused-cache").exists()


def test_failed_reference_write_is_retryable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env, edited = fixture(tmp_path)
    _ = edited.write_text(note(BODY.replace("가상", "수정")))
    original = snapshot.write_reference

    def refuse(*_args: object) -> None:
        return None

    monkeypatch.setattr(snapshot, "write_reference", refuse)
    with pytest.raises(RuntimeError):
        _ = capture.run_once(env)
    assert not list((Path(env["STT_EVAL_ROOT"]) / "reference").rglob("*.json"))
    monkeypatch.setattr(snapshot, "write_reference", original)
    assert capture.run_once(env) == 0
    assert len(references(env)) == 1


def test_wrapper_lock_busy_is_silent(tmp_path: Path) -> None:
    from automation.interop.approval_lease import FileKeyLease

    env, edited = fixture(tmp_path)
    _ = edited.write_text(note(BODY.replace("가상", "수정")))
    with FileKeyLease(tmp_path / ".hermes/stt-eval-capture").hold("watch") as held:
        assert held
        result = subprocess.run([sys.executable, str(WRAPPER), "--once", "--verbose"], cwd=tmp_path, env={**os.environ, **env}, text=True, capture_output=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ""
    assert references(env) == []


def test_wrapper_missing_mirror(tmp_path: Path) -> None:
    env = {**os.environ, "HOME": str(tmp_path), "AUTOPHAGY_REPO_ROOT": str(REPO), "STT_EVAL_ROOT": str(tmp_path / "eval"), "DRIVE_PUBLISH_ENABLED": "0"}
    result = subprocess.run([sys.executable, str(WRAPPER), "--once"], cwd=tmp_path, env=env, text=True, capture_output=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "STT-EVAL-CAPTURE-SKIP reason=mirror-missing\n"
