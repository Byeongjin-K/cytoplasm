"""실제 회의 원장 생산자와 수집자를 연결한다. 원장 키를 테스트가 대신 맞추지 않는다."""
from __future__ import annotations

import tempfile
import unicodedata
from pathlib import Path
from typing import override

import pytest

from automation.drive_client import DriveClient
from automation.drive_taxonomy import artifact_name
from automation.entity_preflight.contracts import JsonValue
from automation.stt_eval import model
from automation.stt_eval.cron import capture
from tests.unit.test_speechtotext_drive_watch_manifest import FakeDrive, NOW, environment
from tests.unit.test_stt_eval_capture import BODY, LEGEND, DriveFixture, fixture, references

from skills.speechtotext.scripts import speechtotext_drive_watch as watcher, stt_transcript


class AudioDrive(FakeDrive):
    def __init__(self, label: str) -> None:
        super().__init__()
        self.label: str = label

    @override
    def list_children(self, folder_id: str) -> list[dict[str, str]]:
        children = super().list_children(folder_id)
        children[0]["name"] = self.label + ".m4a"
        return children


class TranscriptDrive(DriveFixture):
    def __init__(self, body: str, name: str) -> None:
        super().__init__(body)
        self.name: str = name

    @override
    def __call__(self, argv: list[str]) -> dict[str, JsonValue]:
        result = super().__call__(argv)
        files = result.get("files")
        if isinstance(files, list):
            for item in files:
                if isinstance(item, dict) and item.get("id") == "file":
                    item["name"] = self.name
        return result


def exercise(root: Path, label: str) -> None:
    env, lifelog = fixture(root)
    env.update(environment(root))
    local = root / ".hermes/speechtotext/transcripts" / stt_transcript.transcript_name(label, NOW)
    markdown = "# fixture\n\n" + LEGEND + "\n\n---\n\n" + BODY

    def ingest(argv: list[str], child: dict[str, str]) -> int:
        assert argv[argv.index("--label") + 1] == label
        assert child["HOME"] == str(root)
        local.parent.mkdir(parents=True)
        _ = local.write_text(markdown)
        return 0

    summary = watcher.run_once(client=AudioDrive(label), env=env, runner=ingest, now=NOW)
    assert summary["ingested"] == 1
    manifest = Path(env["STT_EVAL_ROOT"]) / "manifest.jsonl"
    original = manifest.read_bytes(), local.read_bytes(), lifelog.read_bytes()
    fake = TranscriptDrive(markdown, artifact_name(NOW.date().isoformat(), label, ".md"))
    drive = DriveClient("gws", root / "unused-cache", runner=fake)
    assert capture.run_once(env, drive=drive, verbose=True) == 0
    assert references(env) == []
    fake.body = markdown.replace("가상 문장입니다.", "수정 문장입니다.")
    assert capture.run_once(env, drive=drive, verbose=True) == 0
    found = references(env)
    print(f"meeting_edit references={len(found)} network_calls=0")
    assert len(found) == 1
    record = model.load_record(found[0])
    assert record.text == "수정 문장입니다. 다른 문장입니다."
    assert record.provenance is not None and record.provenance.startswith("owner-edit:meeting:")
    saved = found[0].read_bytes()
    assert capture.run_once(env, drive=drive, verbose=True) == 0
    assert found[0].read_bytes() == saved and references(env) == found
    assert (manifest.read_bytes(), local.read_bytes(), lifelog.read_bytes()) == original
    print("meeting_edit replay=unchanged manifest_frozen_lifelog=unchanged")


@pytest.mark.parametrize("label", ["sample", "가상 회의.v2", unicodedata.normalize("NFD", "가상 회의"), "2026-01-01_회의"])
def test_produced_meeting_manifest_captures_owner_edit(tmp_path: Path, label: str) -> None:
    exercise(tmp_path, label)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="task-27-meeting-") as temporary:
        exercise(Path(temporary), "가상 회의.v2")
