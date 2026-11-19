"""mail-sync-watch: workday hourly MailOn collection that says so when it stops.

2026-10-13: the only scheduled collection was the 08:00 KST digest. Its sync died on a
DNS outage, the two ad-hoc syncs that afternoon were killed by the 900 s timeout, and
nothing stored mail until the next morning — while nobody was told. The watcher runs
the existing ``mail_wrapper.py list --sync`` path every workday hour, and speaks once
when collection has failed ``threshold`` ticks in a row (and once when it recovers),
plus once when collection succeeds but stores nothing for a long stretch.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

_REPO = Path(__file__).resolve().parents[2]
_PATH = _REPO / "skills" / "mail" / "scripts" / "mail_sync_watch.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("mail_sync_watch", _PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


watch = _load()


def _ok(new: int, newest: str | None = None) -> tuple[int, str]:
    payload = {"status": "ok", "command": "list",
               "sync": {"exit_code": 0, "meaning": "ok", "new_mails": new}}
    if newest is not None:  # the rows the wrapper lists back, newest first
        payload["mails"] = [{"uid": "u2", "date": newest}, {"uid": "u1", "date": "2026-10-01T00:00:00"}]
    return 0, json.dumps(payload)


_DNS = (2, json.dumps({"status": "error", "error_code": "external_service_error",
                       "mailon_exit_code": 2,
                       "failure_signature": "net::ERR_NAME_NOT_RESOLVED"}))
# exit 3 = the wrapper read the local state.db instead; nothing was collected.
_FALLBACK = (0, json.dumps({"status": "ok", "sync": {"exit_code": 3,
                                                       "fallback": "local-state-db"}}))


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(_REPO))
    monkeypatch.setenv("WATCH_FAILURE_ROOT", str(tmp_path / "watch-failure"))
    cli = tmp_path / "mail_wrapper.py"
    _ = cli.write_text("", encoding="utf-8")
    monkeypatch.setenv(watch.CLI_ENV, str(cli))
    monkeypatch.delenv(watch.ALERT_AFTER_ENV, raising=False)
    monkeypatch.delenv(watch.QUIET_AFTER_ENV, raising=False)
    monkeypatch.setenv(watch.LOCK_ENV, str(tmp_path / "mail-sync.lock"))
    monkeypatch.setattr(watch, "_now", lambda: watch.datetime(2026, 10, 19, 10, 0, tzinfo=watch.KST))


def _script(monkeypatch: pytest.MonkeyPatch, outcomes: list[tuple[int, str]]) -> list[list[str]]:
    calls: list[list[str]] = []
    queue = list(outcomes)

    def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append([str(arg) for arg in argv])
        rc, out = queue.pop(0)
        return subprocess.CompletedProcess(argv, rc, out + "\n", "")

    monkeypatch.setattr(watch.subprocess, "run", fake_run)
    return calls


def _ticks(monkeypatch, capsys, outcomes) -> list[str]:
    _script(monkeypatch, outcomes)
    printed = []
    for rc, payload in outcomes:
        success = rc == 0 and json.loads(payload).get("sync", {}).get("exit_code") == 0
        assert watch.main() == (0 if success else 1)
        printed.append(capsys.readouterr().out.strip())
    return printed


def test_tick_runs_the_existing_sync_path(monkeypatch, capsys) -> None:
    calls = _script(monkeypatch, [_ok(2)])
    assert watch.main() == 0
    assert calls[0][1:] == [watch.os.environ[watch.CLI_ENV], "list", "--sync",
                            "--limit", str(watch.SYNC_LIMIT)]
    assert capsys.readouterr().out == ""


def _switch(home: Path, value: bytes) -> None:
    marker = home / ".hermes" / "mail-user-folders.mode"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_bytes(value)


@pytest.mark.parametrize("marker,scope", [
    (None, []), (b"off", []), (b"on", ["--user-folders"]), (b"on\n", []),
])
def test_tick_adds_the_user_folders_only_under_the_released_switch(
    tmp_path: Path, monkeypatch, marker, scope
) -> None:
    # Given: the node marker of the mail-user-folders release switch (issue #251).
    if marker is not None:
        _switch(tmp_path, marker)
    calls = _script(monkeypatch, [_ok(1)])
    # When
    assert watch.main() == 0
    # Then: off, absent or broken keeps today's argv byte for byte.
    assert calls[0][2:] == ["list", "--sync", "--limit", str(watch.SYNC_LIMIT), *scope]


@pytest.mark.parametrize("words,scope", [
    ([], []),
    (["--user-folders"], ["--user-folders"]),
    (["--user-folders", "--since-days", "31"], ["--user-folders", "--since-days", "31"]),
    (["--user-folders", "--since-days", "01"], ["--user-folders", "--since-days", "1"]),
])
def test_now_takes_the_gate_scope(monkeypatch, capsys, words, scope) -> None:
    calls = _script(monkeypatch, [_ok(2)])
    assert watch.now(words) == 0
    assert calls[0][2:] == ["list", "--sync", "--limit", str(watch.SYNC_LIMIT), *scope]
    assert capsys.readouterr().out.strip() == "MAIL-SYNC-NOW ok new=2"


@pytest.mark.parametrize("words", [
    ["--user-folders", "--since-days", "0"], ["--user-folders", "--since-days", "32"],
    ["--user-folders", "--since-days", "-1"], ["--since-days", "3"], ["--all-folders"],
    ["--user-folders", "--user-folders"], ["--user-folders", "--since-days"],
])
def test_now_refuses_any_other_scope_before_collecting(monkeypatch, capsys, words) -> None:
    calls = _script(monkeypatch, [])
    assert watch.now(words) == 2
    assert calls == [] and capsys.readouterr().out.startswith("MAIL-SYNC-NOW refused")


def test_failed_collection_is_visible_to_timer_every_tick(monkeypatch, capsys) -> None:
    printed = _ticks(monkeypatch, capsys, [_DNS] * 4)
    assert len(printed) == 4 and all("ERR_NAME_NOT_RESOLVED" in line for line in printed)


def test_recovery_is_announced_once(monkeypatch, capsys) -> None:
    printed = _ticks(monkeypatch, capsys, [_DNS, _DNS, _DNS, _ok(5), _ok(1)])
    assert printed[3] == ""
    assert printed[4] == ""


def test_local_fallback_is_not_a_successful_collection(monkeypatch, capsys) -> None:
    printed = _ticks(monkeypatch, capsys, [_FALLBACK, _FALLBACK, _FALLBACK])
    assert "fallback=local-state-db" in printed[2]


def test_successful_but_empty_collection_is_told_after_the_quiet_threshold(
    monkeypatch, capsys
) -> None:
    monkeypatch.setenv(watch.QUIET_AFTER_ENV, "2")
    printed = _ticks(monkeypatch, capsys, [_ok(0), _ok(0), _ok(0), _ok(3)])
    assert printed == ["", "", "", ""]
    state = json.loads((Path.home() / ".local/state/autophagy/mail-sync/collection.json").read_text())
    assert state["new_mails"] == 3 and state["zero_weekday_streak"] == 0


def _streak() -> int:
    state = json.loads((Path.home() / ".local/state/autophagy/mail-sync/collection.json").read_text())
    return state["zero_weekday_streak"]


def test_mail_another_sync_stored_first_starts_the_quiet_count_over(monkeypatch, capsys) -> None:
    # Given: two ticks that find nothing new while the store's newest mail stays put.
    _ticks(monkeypatch, capsys, [_ok(0, "2026-10-19T00:10:00"), _ok(0, "2026-10-19T00:10:00")])
    assert _streak() == 2
    # When: another sync (a node's project-board sync, --now) stored a newer mail before this tick
    _ticks(monkeypatch, capsys, [_ok(0, "2026-10-19T00:31:00")])
    # Then: mail is being collected, so the quiet count starts over (issue #256).
    assert _streak() == 0


def test_a_store_that_stays_put_still_fails_doctor(tmp_path: Path, monkeypatch, capsys) -> None:
    from automation.doctor import scheduled

    # Given / When: six workday hours of successful ticks and the store's newest mail never moves
    _ticks(monkeypatch, capsys, [_ok(0, "2026-10-19T00:10:00")] * 36)
    # Then: collection has really stopped storing mail, and doctor still says so.
    checks = scheduled.observe(tmp_path, _REPO, lambda *_a: (0, ""), watch._now())
    assert _streak() == 36 and [c.name for c in checks] == ["mail-sync-quiet"]


def test_the_tick_hands_its_held_lock_to_the_wrapper(tmp_path: Path, monkeypatch) -> None:
    # Given: the wrapper takes the collection lock itself, so the tick must not make it wait on the tick.
    child = ("import importlib.util, sys; spec = importlib.util.spec_from_file_location('w', sys.argv[1]);"
             " w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w);"
             " print('HELD' if w.collection_lock(0) is not None else 'BUSY')")
    seen: list[str] = []

    def fake_run(argv, **kwargs):
        with subprocess.Popen([sys.executable, "-c", child, str(_PATH)], stdout=subprocess.PIPE, text=True,
                              env=kwargs["env"], pass_fds=kwargs["pass_fds"]) as proc:
            seen.append(proc.communicate()[0].strip())
        stranger = dict(kwargs["env"], **{watch.LOCK_FD_ENV: "0"})  # a descriptor that is not the lock
        with subprocess.Popen([sys.executable, "-c", child, str(_PATH)], stdout=subprocess.PIPE, text=True,
                              stdin=subprocess.DEVNULL, env=stranger) as proc:
            seen.append(proc.communicate()[0].strip())
        return subprocess.CompletedProcess(argv, *_ok(1), "")

    monkeypatch.setattr(watch.subprocess, "run", fake_run)
    # When
    assert watch.main() == 0
    # Then: the wrapper keeps the tick's lock; anything else still finds the collection busy.
    assert seen == ["HELD", "BUSY"]


def test_alert_threshold_is_configurable(monkeypatch, capsys) -> None:
    monkeypatch.setenv(watch.ALERT_AFTER_ENV, "1")
    printed = _ticks(monkeypatch, capsys, [_DNS])
    assert printed[0].startswith("mail-sync-watch error:")


def test_unmounted_skill_counts_as_a_failed_collection(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv(watch.CLI_ENV, str(tmp_path / "absent.py"))
    monkeypatch.setenv(watch.ALERT_AFTER_ENV, "1")
    assert watch.main() == 1
    assert "not mounted" in capsys.readouterr().out


def test_child_timeout_is_a_failed_tick_not_a_crash(monkeypatch, capsys) -> None:
    monkeypatch.setenv(watch.ALERT_AFTER_ENV, "1")

    def slow(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout", 0))

    monkeypatch.setattr(watch.subprocess, "run", slow)
    assert watch.main() == 1
    assert "TimeoutExpired" in capsys.readouterr().out


def test_unrecordable_failure_keeps_exit_one(monkeypatch, capsys) -> None:
    _script(monkeypatch, [_DNS])
    assert watch.main() == 1
    assert "mail-sync-watch error" in capsys.readouterr().out
