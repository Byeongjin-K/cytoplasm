"""Q282 removes only the derived YAML write, not milestones in meeting minutes."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills/meeting"
sys.path.insert(0, str(SKILL / "scripts"))
import meeting_cli  # noqa: E402


@pytest.mark.parametrize("existing", [False, True])
def test_ingest_keeps_minutes_milestones_without_writing_reminder_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], existing: bool,
) -> None:
    # Given: a legacy state override and an ordinary recorded meeting.
    state = tmp_path / "state/milestones.yaml"
    if existing:
        state.parent.mkdir()
        state.write_bytes(b"retired user state")
    monkeypatch.setenv("HOME", str(tmp_path))
    for key, value in {
        "MEETING_STATE_FILE": state,
        "MEETING_NOTES_DIR": tmp_path / "notes",
        "MEETING_PLAN_DIR": tmp_path / "plan",
        "MEETING_LOG_DIR": tmp_path / "logs",
        "MEETING_CONFIG": tmp_path / "absent.json",
        "MEETING_PROMPT_FILE": SKILL / "prompts/meeting-extraction-v4.md",
    }.items():
        monkeypatch.setenv(key, str(value))
    monkeypatch.setenv("DRIVE_PUBLISH_ENABLED", "0")
    # When: the actual ingestion pipeline handles its normal milestone fixture.
    code = meeting_cli.main([
        "ingest", "--file", str(SKILL / "fixtures/meeting-clean.md"),
        "--recorded-response", str(SKILL / "fixtures/recorded-clean.json"), "--offline",
    ])
    # Then: all extracted milestone titles remain in the minutes; YAML stays untouched.
    assert code == 0
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    source = json.loads((SKILL / "fixtures/recorded-clean.json").read_text(encoding="utf-8"))
    note = next((tmp_path / "notes").glob("*.md")).read_text(encoding="utf-8")
    assert result["milestones"] == len(source["milestones"]) > 0
    assert all(item["title"] in note for item in source["milestones"])
    assert state.read_bytes() == b"retired user state" if existing else not state.exists()
