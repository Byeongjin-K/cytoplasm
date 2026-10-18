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


def _ok(new: int) -> tuple[int, str]:
    payload = {"status": "ok", "command": "list",
               "sync": {"exit_code": 0, "meaning": "ok", "new_mails": new}}
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
    monkeypatch.setenv("WATCH_FAILURE_ROOT", str(tmp_path / "watch-failure"))
    cli = tmp_path / "mail_wrapper.py"
    _ = cli.write_text("", encoding="utf-8")
    monkeypatch.setenv(watch.CLI_ENV, str(cli))
    monkeypatch.delenv(watch.ALERT_AFTER_ENV, raising=False)
    monkeypatch.delenv(watch.QUIET_AFTER_ENV, raising=False)


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
    state = json.loads((Path.home() / ".hermes/mail-sync/collection.json").read_text())
    assert state["new_mails"] == 3 and state["zero_weekday_streak"] == 0


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
    monkeypatch.setattr(watch, "watch_failure_streak", None)
    assert watch.main() == 1
    assert "mail-sync-watch error" in capsys.readouterr().out
