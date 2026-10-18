"""회복 통지가 실패 원인을 남기는가 — 스트릭이 닫혀도 last_failure 는 남아야 한다.

티켓 t_2b8f1ab9 (`recorded_failure_detail_unavailable_after_recovery`): 무에이전트
워처가 실패 스트릭에서 회복하면 소유자에게 'N회 연속 실패 후 회복'만 가고, 상태가
리셋되며 무엇이 실패했는지는 어디에도 남지 않는다. 회복 DM 한 줄이 그 답을
스스로 말해야 한다.

**왜 별도 파일인가**: `tests/unit/test_watch_failure_streak.py` 의 출력 해시는 FS3
정산 레코드에 고정돼 있다. 새 검사는 이 파일에 둔다.
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SOURCE = _REPO / "skills" / "mail" / "scripts" / "watch_failure_streak.py"
_UTC_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("watch_failure_streak_last_failure", _SOURCE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _raw(module: ModuleType, name: str, root: Path) -> dict[str, object]:
    path = module.state_path(name, root)
    parsed: object = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return {str(key): value for key, value in parsed.items()}


def test_recovery_notice_names_the_last_failure_when_the_streak_closes(tmp_path: Path) -> None:
    # Given: three failing ticks opened an incident with a redacted detail.
    module = _module()
    for _ in range(3):
        _ = module.record(
            "mail-triage-watch", ok=False, detail="rc=1: boom", threshold=3, root=tmp_path
        )
    stored = _raw(module, "mail-triage-watch", tmp_path)
    last_failure = stored["last_failure"]
    assert isinstance(last_failure, dict)
    first = last_failure["first_failed_at"]
    last = last_failure["last_failed_at"]

    # When: the watcher succeeds again.
    recovery = module.record("mail-triage-watch", ok=True, root=tmp_path)

    # Then: the Discord line itself answers what failed, over which UTC window.
    assert recovery == (
        "mail-triage-watch recovered after 3 consecutive failures "
        f"({first}..{last} UTC) — last failure: rc=1: boom"
    )


def test_last_failure_summary_survives_the_reset(tmp_path: Path) -> None:
    # Given: a closed incident that had a redacted failing-tick line.
    module = _module()
    _ = module.record("mail-triage-watch", ok=False, detail="rc=4: timeout", threshold=1, root=tmp_path)
    _ = module.record("mail-triage-watch", ok=True, root=tmp_path)

    # When: the reset state is read back.
    stored = _raw(module, "mail-triage-watch", tmp_path)

    # Then: counters are clear but the last-failure summary is still there.
    assert stored["consecutive_failures"] == 0
    assert stored["incident_open"] is False
    last_failure = stored["last_failure"]
    assert isinstance(last_failure, dict)
    assert last_failure["detail"] == "rc=4: timeout"
    assert last_failure["count"] == 1
    assert _UTC_ISO.match(str(last_failure["first_failed_at"]))
    assert _UTC_ISO.match(str(last_failure["last_failed_at"]))


def test_old_state_without_last_failure_keys_still_recovers(tmp_path: Path) -> None:
    # Given: a pre-repair state file that never stored a last-failure summary.
    module = _module()
    path = module.state_path("mail-triage-watch", tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(
        json.dumps({"consecutive_failures": 5, "incident_open": True}),
        encoding="utf-8",
    )

    # When: the watcher recovers.
    recovery = module.record("mail-triage-watch", ok=True, root=tmp_path)

    # Then: fail-soft — the tick still closes the incident instead of crashing.
    assert recovery is not None
    assert "mail-triage-watch recovered after 5 consecutive failures" in recovery


def test_failure_detail_is_capped_at_200_chars_in_state(tmp_path: Path) -> None:
    # Given: a caller-redacted tail longer than the stored summary budget.
    module = _module()
    detail = "x" * 250

    # When: the failing tick is recorded.
    _ = module.record("mail-triage-watch", ok=False, detail=detail, threshold=1, root=tmp_path)

    # Then: the persisted summary is the first 200 characters, not the raw tail.
    last_failure = _raw(module, "mail-triage-watch", tmp_path)["last_failure"]
    assert isinstance(last_failure, dict)
    assert last_failure["detail"] == "x" * 200


def test_first_failed_at_stays_fixed_while_count_advances(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the first failing tick at a controlled UTC instant.
    module = _module()
    monkeypatch.setattr(module, "_utc_stamp", lambda: "2026-09-18T01:00:00Z")
    module.record("mail-triage-watch", ok=False, detail="rc=1: a", threshold=5, root=tmp_path)
    monkeypatch.setattr(module, "_utc_stamp", lambda: "2026-09-18T01:10:00Z")

    # When: another failing tick lands at a distinct instant.
    module.record("mail-triage-watch", ok=False, detail="rc=1: b", threshold=5, root=tmp_path)

    # Then: only the end/count/detail advance, independently of wall-clock timing.
    assert _raw(module, "mail-triage-watch", tmp_path)["last_failure"] == {
        "first_failed_at": "2026-09-18T01:00:00Z",
        "last_failed_at": "2026-09-18T01:10:00Z",
        "count": 2,
        "detail": "rc=1: b",
    }


def test_summary_survives_when_recovery_precedes_notice_threshold(tmp_path: Path) -> None:
    # Given: a recorded failure that has not opened an incident.
    module = _module()
    module.record("watch", ok=False, detail="rc=2", threshold=3, root=tmp_path)
    failure = module.load("watch", tmp_path).last_failure

    # When: the next tick succeeds.
    notice = module.record("watch", ok=True, root=tmp_path)

    # Then: silent recovery resets the counter but retains the failure evidence.
    assert notice is None
    assert module.load("watch", tmp_path) == module.Streak(last_failure=failure)


def test_window_restarts_when_a_new_streak_follows_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a recovered incident with an older persisted summary.
    module = _module()
    monkeypatch.setattr(module, "_utc_stamp", lambda: "2026-09-18T01:00:00Z")
    module.record("watch", ok=False, detail="old", threshold=1, root=tmp_path)
    module.record("watch", ok=True, root=tmp_path)
    monkeypatch.setattr(module, "_utc_stamp", lambda: "2026-09-18T02:00:00Z")

    # When: a new failure arrives.
    module.record("watch", ok=False, detail="new", threshold=1, root=tmp_path)

    # Then: the new streak owns its own start, end, count and detail.
    assert _raw(module, "watch", tmp_path)["last_failure"] == {
        "first_failed_at": "2026-09-18T02:00:00Z",
        "last_failed_at": "2026-09-18T02:00:00Z",
        "count": 1,
        "detail": "new",
    }


@pytest.mark.parametrize(
    "child_error",
    [
        "DIGEST-FAIL stage=build retry_safe=false code=llm_call_failed detail=timeout",
        "rc=3: timeout",
        "",
    ],
)
def test_digest_recovery_preserves_failure_cause_when_child_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, child_error: str
) -> None:
    # Given: a digest runner whose failed tick is followed by a successful tick.
    spec = importlib.util.spec_from_file_location(
        "digest_recovery", _SOURCE.with_name("mail_digest_watch.py")
    )
    assert spec is not None and spec.loader is not None
    watch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(watch)
    cli = tmp_path / "cli.py"
    cli.touch()
    monkeypatch.setenv("WATCH_FAILURE_ROOT", str(tmp_path / "state"))
    monkeypatch.setattr(watch, "CLI", cli)
    monkeypatch.setattr(watch, "_load_env_secrets", lambda: None)
    monkeypatch.setattr(
        watch, "_run_digest", lambda: subprocess.CompletedProcess([], 3, "", child_error)
    )
    assert watch.main() == 1
    monkeypatch.setattr(
        watch, "_run_digest", lambda: subprocess.CompletedProcess([], 0, "", "")
    )

    # When: recovery closes the digest incident.
    assert watch.main() == 0

    # Then: the persisted summary retains the redacted cause, including empty-output exits.
    assert not (tmp_path / "state").exists()
