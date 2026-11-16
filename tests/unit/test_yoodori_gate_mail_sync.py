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


WATCH = ["/usr/bin/python3", "/srv/autophagy-skills/live/mail/scripts/mail_sync_watch.py", "--now"]


@pytest.mark.parametrize("scope,tail", [
    (["--all-folders"], ["--user-folders"]),
    (["--all-folders", "--since-days", "1"], ["--user-folders", "--since-days", "1"]),
    (["--all-folders", "--since-days", "31"], ["--user-folders", "--since-days", "31"]),
    (["--all-folders", "--since-days", "007"], ["--user-folders", "--since-days", "7"]),
])
def test_mail_sync_takes_the_user_folder_scope(scope: list[str], tail: list[str]) -> None:
    # Given / When: YooDori asks for the user folders too (issue #251, owner Q58).
    job = gate.build_job(["mail-sync", *scope, *ORIGIN])
    # Then: the same watcher, with the scope that keeps the exclusions and the day limit.
    assert job.argv == [*WATCH, *tail] and job.write is False


@pytest.mark.parametrize("scope", [
    ["--all-folders", "--since-days", "0"],
    ["--all-folders", "--since-days", "32"],
    ["--all-folders", "--since-days", "-1"],
    ["--all-folders", "--since-days", "01x"],
    ["--all-folders", "--since-days", "1000"],
    ["--all-folders", "--since-days"],
    ["--since-days", "3"],
    ["--all-folders", "--all-folders"],
    ["--all-folders", "--since-days", "3", "--since-days", "4"],
    ["--user-folders"],
    ["--all-folders", "--full-scan"],
    ["--all-folders", "--limit", "5"],
])
def test_mail_sync_refuses_every_other_argument(scope: list[str]) -> None:
    with pytest.raises(gate.Refused):
        gate.build_job(["mail-sync", *scope, *ORIGIN])


def test_mail_sync_day_cap_matches_the_mail_skill() -> None:
    import importlib.util
    import sys

    scripts = Path(__file__).resolve().parents[2] / "skills/mail/scripts"
    sys.path.insert(0, str(scripts))
    spec = importlib.util.spec_from_file_location("mail_user_folders_mode", scripts / "mail_user_folders_mode.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert gate.MAIL_SYNC_MAX_DAYS == module.MAX_SINCE_DAYS


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
    monkeypatch.setattr(jobs, "verify_owner_origin", lambda *a, **k: jobs.Origin("123456789", "987654321", link))
    # When: the job starts.
    gate.run_job(gate.build_job(["mail-sync", *ORIGIN]))
    # Then: its record carries the owner link and is not a write.
    [spec] = [json.loads(path.read_text()) for path in jobs.jobs_root(home).rglob("spec.json")]
    assert spec["origin"]["link"] == link and spec["write"] is False
