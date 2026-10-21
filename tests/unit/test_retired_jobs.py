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
    removed = {"budget-watch", "memory-curator-watch", "memory-relocate-watch", "reminder-poller",
               "daily-cost-report", "research-trends"}
    assert not any(row.kind == "cron" and row.destination in removed for row in rows)
    assert not any(row.source.startswith("automation/reminder_poller/") for row in rows)
    cleanup = [row for row in rows if row.owner == "automation/retired_jobs" and row.kind == "derived"]
    assert len(cleanup) == 11
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
    for name in ("memory-curator-watch", "memory-relocate-watch"):
        for unit in retired_jobs.unit_paths(tmp_path, name):
            unit.parent.mkdir(parents=True, exist_ok=True)
            unit.write_bytes(b"unit")
    # When
    for name in ("memory-curator-watch", "memory-relocate-watch"):
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
        "budget-watch", "memory-curator-watch", "memory-relocate-watch", "reminder-poller", "research-trends",
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
    monkeypatch.setattr(node_config, "load_node_config", lambda: _config())

    def run(argv: tuple[str, ...], **_kwargs) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        target = head if matches else "b" * 40
        output = f"/srv/releases/{target}\n" if len(calls) == 1 else ""
        # The peer account still exists, so its plugin cleanup runs as before.
        return subprocess.CompletedProcess(argv, 1 if "getent passwd" in argv[2] else 0, output, "")

    monkeypatch.setattr(retired_jobs.subprocess, "run", run)
    result = retired_jobs.deploy("test-node", "/srv/current", head)
    assert result == (0 if matches else 4)
    assert len(calls) == (len(retired_jobs.JOBS) + 4 if matches else 1)
    if matches:
        assert "sudo -n -u peer " in calls[-1][2]
    assert calls[0] == ("ssh", "test-node", "readlink /srv/current")
    if matches:
        assert all("automation.retired_jobs install --name " + name in command[2]
                   for name, command in zip(retired_jobs.JOBS, calls[1:1 + len(retired_jobs.JOBS)], strict=True))


def _config() -> SimpleNamespace:
    return SimpleNamespace(agent_account="agent", peer_account="peer", ops_account="ops",
                           private_root=Path("/srv/private"))


@pytest.mark.parametrize("removed", [True, False])
def test_deployer_skips_a_peer_account_the_owner_already_removed(
    monkeypatch: pytest.MonkeyPatch, removed: bool,
) -> None:
    # v2.6.7 stopped at `sudo: unknown user peer`: P5 retired peer and the owner's root
    # block deleted the account, so there is no peer plugin left to remove.
    from automation import node_config

    head = "a" * 40
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(node_config, "load_node_config", _config)

    def run(argv: tuple[str, ...], **_kwargs) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if "getent passwd" in argv[2]:
            assert "sudo -n -u ops -H bash -c 'cmp -s /srv/private/peer-retired <(printf retired)'" in argv[2]
            return subprocess.CompletedProcess(argv, 0 if removed else 1, "", "")
        if "sudo -n -u peer " in argv[2]:
            return subprocess.CompletedProcess(argv, 1, "", "sudo: unknown user peer")
        output = f"/srv/releases/{head}\n" if len(calls) == 1 else ""
        return subprocess.CompletedProcess(argv, 0, output, "")

    monkeypatch.setattr(retired_jobs.subprocess, "run", run)
    assert retired_jobs.deploy("test-node", "/srv/current", head) == (0 if removed else 5)
    assert any("sudo -n -u agent " in c[2] and "interop-plugin" in c[2] for c in calls)
    assert any("sudo -n -u peer " in c[2] for c in calls) is not removed


def test_research_release_cleanup_removes_node_leftovers_only(tmp_path: Path) -> None:
    # Given: the retired units plus the node runtime, prompt state and topic list, beside other data.
    units = retired_jobs.unit_paths(tmp_path, "research-trends")
    units[0].parent.mkdir(parents=True)
    for path in units:
        path.write_bytes(b"old unit")
    removed = (tmp_path / ".hermes/scripts/research_trends.py",
               tmp_path / ".hermes/state/research-topics.yaml",
               tmp_path / ".hermes/research-trends/research-trends-v1.md",
               tmp_path / ".hermes/research-trends/logs/run.log",
               tmp_path / ".hermes/research_trends_runtime/research_trends_core.py")
    kept = (tmp_path / ".hermes/state/other.yaml", tmp_path / "notes/meetings/m.md")
    for path in (*removed, *kept):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    calls: list[tuple[str, ...]] = []
    runner = _live_runner(calls, obeys=True)
    assert retired_jobs.check(tmp_path, "research-trends", runner) == "stale"

    # When
    retired_jobs.install(tmp_path, "research-trends", runner)

    # Then: every leftover and both trees are gone; unrelated files stay.
    assert all(not path.exists() for path in (*units, *removed))
    assert not (tmp_path / ".hermes/research-trends").exists()
    assert not (tmp_path / ".hermes/research_trends_runtime").exists()
    assert all(path.read_bytes() == b"x" for path in kept)
    assert retired_jobs.check(tmp_path, "research-trends", runner) == "ok"


def test_research_cleanup_refuses_a_symlink_inside_its_tree(tmp_path: Path) -> None:
    # Given: a link planted inside the retired runtime tree that points outside the home.
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_bytes(b"keep")
    tree = tmp_path / "home/.hermes/research_trends_runtime"
    tree.mkdir(parents=True)
    (tree / "link").symlink_to(outside)
    home = tmp_path / "home"

    # When / Then: observation is unknown and install refuses before deleting anything.
    assert retired_jobs.check(home, "research-trends", _runner(home, [])) == "unknown"
    with pytest.raises(ValueError, match="RETIRED-JOB-REFUSED|GATEWAY-RETIRE-REFUSED"):
        retired_jobs.install(home, "research-trends", _runner(home, []))
    assert (outside / "keep.txt").read_bytes() == b"keep"
    assert (tree / "link").is_symlink()


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


def test_budget_release_cleanup_removes_the_watcher_its_data_and_the_sheet_setting(tmp_path: Path) -> None:
    # Given: the budget-watch timer the owner retired (2026-10-22) with its wrapper, its node data
    # and the sheet id line in the secrets file, beside files that must stay.
    units = retired_jobs.unit_paths(tmp_path, "budget-watch")
    units[0].parent.mkdir(parents=True)
    for path in units:
        path.write_bytes(b"old unit")
    removed = (tmp_path / ".hermes/scripts/budget_watch.py", tmp_path / ".hermes/budget/config.json",
               tmp_path / ".hermes/budget-gate/drafts/ab12cd.json", tmp_path / ".hermes/budget-gate/send-log.jsonl",
               tmp_path / "state/budget.db")
    kept = (tmp_path / ".hermes/scripts/watch_failure_streak.py", tmp_path / "state/reminders-other.db",
            tmp_path / ".hermes/budget-other.txt")
    for path in (*removed, *kept):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    secrets = tmp_path / ".env.secrets"
    secrets.write_bytes(b"A=1\nBUDGET_SHEET_ID=sheet-1\nexport BUDGET_SHEET_ID=sheet-2\nB=2\n")
    secrets.chmod(0o600)
    calls: list[tuple[str, ...]] = []
    runner = _live_runner(calls, obeys=True)
    assert retired_jobs.check(tmp_path, "budget-watch", runner) == "stale"

    # When
    retired_jobs.install(tmp_path, "budget-watch", runner)

    # Then: the timer was stopped first; units, wrapper, data and the sheet line are gone.
    assert ("systemctl", "--user", "disable", "--now", units[0].name) in calls
    assert all(not path.exists() for path in (*units, *removed))
    assert not (tmp_path / ".hermes/budget").exists() and not (tmp_path / ".hermes/budget-gate").exists()
    assert all(path.read_bytes() == b"x" for path in kept)
    assert secrets.read_bytes() == b"A=1\nB=2\n"
    assert secrets.stat().st_mode & 0o777 == 0o600
    assert retired_jobs.check(tmp_path, "budget-watch", runner) == "ok"


def test_budget_cleanup_refuses_a_symlink_inside_its_data(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".hermes/budget-gate").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (home / ".hermes/budget-gate/drafts").symlink_to(outside)
    assert retired_jobs.check(home, "budget-watch", _runner(home, [])) == "unknown"
    with pytest.raises(ValueError):
        retired_jobs.install(home, "budget-watch", _runner(home, []))
    assert outside.is_dir()
