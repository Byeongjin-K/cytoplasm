"""Removal uses the release declaration, stopped-unit read-back and exact data paths."""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from automation import retired_jobs
from automation.deploy_declarations import all_declarations
from automation.deploy_all_observe import _derived

ROOT = Path(__file__).resolve().parents[2]


def _seed(home: Path) -> tuple[Path, ...]:
    units = retired_jobs.unit_paths(home, "reminder-poller")
    units[0].parent.mkdir(parents=True)
    for path in units:
        path.write_text("old unit", encoding="utf-8")
    data = tuple(home / name for name in retired_jobs.DATA)
    data[0].parent.mkdir(parents=True)
    for path in data:
        path.write_bytes(b"derived")
    return (*units, *data)


def _runner(home: Path, calls: list[tuple[str, ...]], *, active: bool = False):
    has_data = all((home / name).is_file() for name in retired_jobs.DATA)

    def run(argv: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        verb = argv[2]
        if verb == "daemon-reload":
            # The files must survive until the deployment confirmation, not just stop.
            if has_data:
                assert all((home / name).read_bytes() == b"derived" for name in retired_jobs.DATA)
            return subprocess.CompletedProcess(argv, 0, "", "")
        output = ("active" if active else "inactive") if verb == "is-active" else (
            "enabled" if active else "disabled"
        )
        return subprocess.CompletedProcess(argv, 0 if active else 3, output, "")
    return run


def test_data_deleted_only_after_stopped_unit_removal_confirmation(tmp_path: Path) -> None:
    # Given: disabled old units and derived data beside unrelated user data.
    paths = _seed(tmp_path)
    other = tmp_path / "state/unrelated.md"
    other.write_bytes(b"keep")
    calls: list[tuple[str, ...]] = []
    # When: the declared cleanup runs.
    retired_jobs.install(tmp_path, "reminder-poller", _runner(tmp_path, calls))
    # Then: exact files are gone after reload; everything else is untouched.
    assert all(not path.exists() for path in paths)
    assert other.read_bytes() == b"keep"
    reload = calls.index(("systemctl", "--user", "daemon-reload"))
    assert all(("systemctl", "--user", "is-active", path.name) in calls[reload + 1:]
               for path in paths[:2])


def _live_runner(calls: list[tuple[str, ...]], *, obeys: bool):
    """A still-running timer; `obeys` says whether disable/stop actually takes effect."""
    halted: set[str] = set()

    def run(argv: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        verb, unit = argv[2], argv[-1]
        if verb in ("disable", "stop"):
            if obeys:
                halted.add(unit)
            return subprocess.CompletedProcess(argv, 0, "", "")
        if verb == "daemon-reload":
            return subprocess.CompletedProcess(argv, 0, "", "")
        if unit in halted:
            return subprocess.CompletedProcess(argv, 3, "inactive" if verb == "is-active" else "disabled", "")
        return subprocess.CompletedProcess(argv, 0, "active" if verb == "is-active" else "enabled", "")
    return run


def test_a_running_retired_timer_is_stopped_then_removed_with_its_data(tmp_path: Path) -> None:
    # Given: the node's reminder poller is still enabled and active (owner Q282 retires it).
    paths = _seed(tmp_path)
    calls: list[tuple[str, ...]] = []
    # When: the declared cleanup runs.
    retired_jobs.install(tmp_path, "reminder-poller", _live_runner(calls, obeys=True))
    # Then: the timer is disabled with --now and the service stopped before anything is deleted.
    timer, service = paths[0].name, paths[1].name
    assert ("systemctl", "--user", "disable", "--now", timer) in calls
    assert ("systemctl", "--user", "stop", service) in calls
    assert calls.index(("systemctl", "--user", "disable", "--now", timer)) < calls.index(
        ("systemctl", "--user", "daemon-reload"))
    assert all(not path.exists() for path in paths)


def test_a_running_timer_is_stale_work_for_the_deployer_not_unknown(tmp_path: Path) -> None:
    # Given: the retired timer still runs (the release must call the cleanup, not hold it).
    _seed(tmp_path)
    # When / Then
    assert retired_jobs.check(tmp_path, "reminder-poller", _live_runner([], obeys=True)) == "stale"


def test_an_unreadable_unit_state_is_unknown(tmp_path: Path) -> None:
    _seed(tmp_path)

    def garbled(argv: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, "???", "")

    assert retired_jobs.check(tmp_path, "reminder-poller", garbled) == "unknown"


def test_a_timer_that_stays_active_prevents_every_file_deletion(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    before = {path: path.read_bytes() for path in paths}
    calls: list[tuple[str, ...]] = []
    with pytest.raises(ValueError):
        retired_jobs.install(tmp_path, "reminder-poller", _live_runner(calls, obeys=False))
    assert {path: path.read_bytes() for path in paths} == before
    assert not any(call[2] == "daemon-reload" for call in calls)


@pytest.mark.parametrize("relative", retired_jobs.DATA + ("state",))
def test_symlink_is_refused_without_following_or_unlinking_it(tmp_path: Path, relative: str) -> None:
    # Given: either one data file or the state directory points outside the allowed tree.
    _seed(tmp_path)
    link = tmp_path / relative
    outside = tmp_path / "outside"
    if relative == "state":
        for path in link.iterdir():
            path.unlink()
        link.rmdir()
        outside.mkdir()
        (outside / "reminders.db").write_bytes(b"outside")
    else:
        link.unlink()
        outside.write_bytes(b"outside")
    link.symlink_to(outside, target_is_directory=relative == "state")
    calls: list[tuple[str, ...]] = []
    # When / Then: no file operation or systemctl mutation follows the unsafe path.
    with pytest.raises(ValueError):
        retired_jobs.install(tmp_path, "reminder-poller", _runner(tmp_path, calls))
    assert link.is_symlink()
    assert (outside / "reminders.db").read_bytes() == b"outside" if relative == "state" else (
        outside.read_bytes() == b"outside"
    )
    assert calls == []


def test_second_run_is_a_noop(tmp_path: Path) -> None:
    _seed(tmp_path)
    calls: list[tuple[str, ...]] = []
    run = _runner(tmp_path, calls)
    retired_jobs.install(tmp_path, "reminder-poller", run)
    calls.clear()
    retired_jobs.install(tmp_path, "reminder-poller", run)
    assert all(call[2] in ("is-active", "is-enabled") for call in calls)
    assert retired_jobs.check(tmp_path, "reminder-poller", run) == "ok"


def test_removed_jobs_have_no_schedule_and_cleanup_is_required() -> None:
    rows = all_declarations(ROOT)
    removed = {"memory-curator-watch", "memory-relocate-watch", "reminder-poller",
               "daily-cost-report", "research-trends"}
    assert not any(row.kind == "cron" and row.destination in removed for row in rows)
    assert not any(row.source.startswith("automation/reminder_poller/") for row in rows)
    cleanup = [row for row in rows if row.owner == "automation/retired_jobs" and row.kind == "derived"]
    assert len(cleanup) == 10
    assert all(row.kind == "derived" and row.policy == "required" for row in cleanup)
    assert {row.destination for row in cleanup if row.destination.startswith("state/")} == set(retired_jobs.DATA)


def test_memory_cleanup_preserves_native_files_and_archived_state(tmp_path: Path) -> None:
    # Given: only disabled generated units may be removed.
    memory = tmp_path / ".hermes/memories/USER.md"
    memory.parent.mkdir(parents=True)
    memory.write_bytes(b"owner memory")
    archive = tmp_path / ".hermes/memory-curator/state.json"
    archive.parent.mkdir()
    archive.write_bytes(b"archived")
    for name in retired_jobs.JOBS[:2]:
        for unit in retired_jobs.unit_paths(tmp_path, name):
            unit.parent.mkdir(parents=True, exist_ok=True)
            unit.write_bytes(b"unit")
    # When
    for name in retired_jobs.JOBS[:2]:
        retired_jobs.install(tmp_path, name, _runner(tmp_path, []))
    # Then
    assert memory.read_bytes() == b"owner memory"
    assert archive.read_bytes() == b"archived"


def test_timer_reactivation_during_reload_preserves_both_data_files(tmp_path: Path) -> None:
    _seed(tmp_path)
    reloaded = False

    def run(argv: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        nonlocal reloaded
        if argv[2] == "daemon-reload":
            reloaded = True
            output, code = "", 0
        elif argv[2] == "is-active":
            output, code = ("active", 0) if reloaded else ("inactive", 3)
        else:
            output, code = "disabled", 1
        return subprocess.CompletedProcess(argv, code, output, "")

    with pytest.raises(ValueError):
        retired_jobs.install(tmp_path, "reminder-poller", run)
    assert all((tmp_path / name).read_bytes() == b"derived" for name in retired_jobs.DATA)


@pytest.mark.parametrize("status", ["stale", "ok", "unexpected"])
def test_release_observer_dispatches_every_cleanup_declaration(status: str) -> None:
    rows = [row for row in all_declarations(ROOT)
            if row.owner == "automation/retired_jobs" and row.kind == "derived"]
    calls: list[tuple[str, ...]] = []

    def call(account: str, argv: tuple[str, ...]) -> tuple[int, str]:
        assert account in ("agent", "peer")
        calls.append(argv)
        return 0, status + "\n"

    for row in rows:
        result = _derived(row, ROOT, call, {})
        assert result.state == ("unknown" if status == "unexpected" else status)
    assert [call[2] for call in calls] == [
        "memory-curator-watch", "memory-relocate-watch", "reminder-poller", "research-trends",
        "reminder-poller", "reminder-poller", "wiki-confirm-watch",
        "wiki-confirm-watch", "interop-plugin", "interop-plugin",
    ]
    assert all(call[:2] == ("retired-job", str(ROOT)) for call in calls)


@pytest.mark.parametrize("matches", [False, True])
def test_deployer_requires_the_active_release_before_cleanup(
    monkeypatch: pytest.MonkeyPatch, matches: bool,
) -> None:
    from automation import node_config

    head = "a" * 40
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(node_config, "load_node_config", lambda: SimpleNamespace(agent_account="agent", peer_account="peer"))

    def run(argv: tuple[str, ...], **_kwargs) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        target = head if matches else "b" * 40
        output = f"/srv/releases/{target}\n" if len(calls) == 1 else ""
        return subprocess.CompletedProcess(argv, 0, output, "")

    monkeypatch.setattr(retired_jobs.subprocess, "run", run)
    result = retired_jobs.deploy("test-node", "/srv/current", head)
    assert result == (0 if matches else 4)
    assert len(calls) == (len(retired_jobs.JOBS) + 3 if matches else 1)
    assert calls[0] == ("ssh", "test-node", "readlink /srv/current")
    if matches:
        assert all("automation.retired_jobs install --name " + name in command[2]
                   for name, command in zip(retired_jobs.JOBS, calls[1:1 + len(retired_jobs.JOBS)], strict=True))


def test_research_timer_release_cleanup_preserves_reports_and_topic_state(tmp_path: Path) -> None:
    units = retired_jobs.unit_paths(tmp_path, "research-trends")
    units[0].parent.mkdir(parents=True)
    for path in units:
        path.write_bytes(b"old unit")
    kept = (tmp_path / ".hermes/state/research-topics.yaml",
            tmp_path / ".hermes/research-trends/research-trends-v1.md",
            tmp_path / "notes/research-trends/report.md")
    for path in kept:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"keep")
    calls: list[tuple[str, ...]] = []
    runner = _live_runner(calls, obeys=True)
    assert retired_jobs.check(tmp_path, "research-trends", runner) == "stale"
    retired_jobs.install(tmp_path, "research-trends", runner)
    assert all(not path.exists() for path in units)
    assert all(path.read_bytes() == b"keep" for path in kept)
    assert retired_jobs.check(tmp_path, "research-trends", runner) == "ok"


def test_retired_wiki_watcher_removes_only_its_script_and_units(tmp_path: Path) -> None:
    units = retired_jobs.unit_paths(tmp_path, "wiki-confirm-watch")
    script = tmp_path / retired_jobs.WIKI_SCRIPT
    for path in (*units, script):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"retired")
    other = script.with_name("mail_triage_watch.py")
    other.write_bytes(b"keep")
    retired_jobs.install(tmp_path, "wiki-confirm-watch", _runner(tmp_path, []))
    assert all(not path.exists() for path in (*units, script))
    assert other.read_bytes() == b"keep"
