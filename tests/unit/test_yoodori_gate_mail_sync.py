"""YooDori's on-request institutional mail collection (owner 2026-10-19): owner message only."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from automation.yoodori_gate import yoodori_read_gate as gate

jobs = gate.yoodori_jobs

ORIGIN = ["--origin-channel-id", "123456789", "--origin-message-id", "987654321"]


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(gate, "HOME", tmp_path / "home")
    notice_bot = SimpleNamespace(resolve_token=lambda environ=None: "token")
    monkeypatch.setattr(gate, "_release_modules", lambda: (None, notice_bot))
    monkeypatch.setattr(gate, "_owner_id", lambda: "111111111111111111")
    monkeypatch.setattr(jobs, "launch", lambda path, env: None)
    monkeypatch.setattr(jobs, "wait", lambda path, seconds: False)
    return gate.HOME


def test_mail_sync_is_a_read_job_running_the_watch_once() -> None:
    # Given / When: YooDori asks for a collection from the owner's message.
    job = gate.build_job(["mail-sync", *ORIGIN])
    # Then: one --now run of the deployed watcher, not a write.
    assert job.argv == ["/usr/bin/python3", "/srv/autophagy-skills/live/mail/scripts/mail_sync_watch.py", "--now"]
    assert job.write is False
    with pytest.raises(gate.Refused):
        gate.build_job(["mail-sync", "--limit", "5", *ORIGIN])
    with pytest.raises(gate.Refused):
        gate.build_job(["mail-sync"])


def test_mail_sync_starts_only_for_a_verified_owner_message(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: the origin is not the owner's recent message.
    def refuse(*_args, **_kwargs):
        raise jobs.JobRefused("origin is not the owner's message")

    monkeypatch.setattr(jobs, "verify_owner_origin", refuse)
    # When / Then: no job and no MailOn login.
    with pytest.raises(jobs.JobRefused):
        gate.run_job(gate.build_job(["mail-sync", *ORIGIN]))
    assert not jobs.jobs_root(home).exists()


def test_mail_sync_records_the_owner_link_when_verified(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: the owner's own message.
    link = "https://discord.com/channels/9/123456789/987654321"
    monkeypatch.setattr(jobs, "verify_owner_origin", lambda *a, **k: SimpleNamespace(link=link))
    # When: the job starts.
    gate.run_job(gate.build_job(["mail-sync", *ORIGIN]))
    # Then: its record carries the owner link and is not a write.
    [spec] = [json.loads(path.read_text()) for path in jobs.jobs_root(home).rglob("spec.json")]
    assert spec["origin"]["link"] == link and spec["write"] is False
