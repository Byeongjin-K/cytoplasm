"""Q250/Q251: monitor services and job results, not the retired Hermes stack."""
from __future__ import annotations

import json
import ast
import os
import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from automation.doctor import cli
from automation.doctor.capabilities import evaluate
from automation.install.checks import Status
from tests.unit.test_doctor import _connected_home, _facts, _finding

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("key", ["hermes-cli", "hermes-gateway", "agent-chat-free-response"])
def test_retired_doctor_checks_are_absent(tmp_path: Path, key: str) -> None:
    facts = replace(_facts(_connected_home(tmp_path)), hermes=False, gateway="failed")
    assert key not in {finding.key for finding in evaluate(facts)}


def test_operator_runs_only_agent_without_peer_or_parity(tmp_path, monkeypatch, capsys) -> None:
    from automation.doctor import report

    monkeypatch.setattr(cli, "_config", lambda: None)
    monkeypatch.setattr(cli, "_account", lambda: "ops")
    monkeypatch.setattr(cli, "_code_root", lambda _config: tmp_path)
    calls = []

    def child(argv, _root):
        calls.append(argv)
        return 0, json.dumps(report.to_json("agent", "agent", (_finding("primary-model", Status.PASS),))), ""

    assert cli.main(["--offline"], run_child=child) == 0
    assert len(calls) == 1 and calls[0][3] == "agent"
    assert "model-parity" not in capsys.readouterr().out


@pytest.mark.parametrize("probe", [
    "peer_ignored_channels", "agent_selfskill_root_topology",
])
def test_retired_healthcheck_probe_is_not_catalogued(probe) -> None:
    out = subprocess.run(
        ["bash", "-c", f"source '{REPO}/automation/healthcheck.sh'; printf '%s\\n' \"${{LIVE_CHECKS[@]}}\""],
        text=True, capture_output=True, check=True,
    ).stdout
    assert f"|{probe}|" not in out


def test_gateway_and_report_hub_checks_are_not_catalogued() -> None:
    out = subprocess.run(
        ["bash", "-c", f"source '{REPO}/automation/healthcheck.sh'; printf '%s\\n' \"${{LIVE_CHECKS[@]}}\""],
        text=True, capture_output=True, check=True,
    ).stdout
    assert "gateway" not in out
    assert "report-hub" not in out


def test_mirror_behind_is_information_even_without_release(tmp_path) -> None:
    out = subprocess.run(["bash", "-c", f"""
source '{REPO}/automation/checkout_mirror_probe.sh'
checkout_mirror_verdict() {{ echo mirror-behind; return 1; }}
checkout_mirror_grade '{tmp_path}'
"""], text=True, capture_output=True)
    assert out.returncode == 0
    assert "확인" in out.stderr


@pytest.mark.parametrize("verdict", ["mirror-dirty", "mirror-ahead"])
def test_real_mirror_failure_stays_failure(tmp_path, verdict) -> None:
    out = subprocess.run(["bash", "-c", f"""
source '{REPO}/automation/checkout_mirror_probe.sh'
checkout_mirror_verdict() {{ echo {verdict}; return 1; }}
checkout_mirror_grade '{tmp_path}'
"""], text=True, capture_output=True)
    assert out.returncode == 1


def test_semantic_checks_are_part_of_scheduled_jobs(tmp_path, monkeypatch) -> None:
    from automation.doctor import scheduled

    home = _connected_home(tmp_path)
    folder = home / ".hermes/mail-sync"
    folder.mkdir()
    (folder / "collection.json").write_text(json.dumps({
        "zero_weekday_streak": 36, "last_status": "ok",
        "last_run_at": "2026-10-19T08:00:00+09:00",
    }))
    (home / ".hermes/mailon-runtime").mkdir()
    facts = _facts(home)
    checks = scheduled.observe(
        home, REPO, lambda _argv, _env: (1, "DRIFT mailon-runtime-drift"),
        datetime(2026, 10, 19, tzinfo=UTC),
    )
    finding = next(f for f in evaluate(replace(facts, job_checks=checks)) if f.key == "scheduled-jobs")
    assert finding.status is Status.FAIL
    assert {"mail-sync-quiet", "mailon-runtime-drift"} <= set(finding.subjects)


def test_weekend_quiet_is_not_a_failure(tmp_path) -> None:
    from automation.doctor import scheduled

    folder = tmp_path / ".hermes/mail-sync"
    folder.mkdir(parents=True)
    (folder / "collection.json").write_text(json.dumps({
        "zero_weekday_streak": 36, "last_status": "ok",
        "last_run_at": "2026-10-18T08:00:00+09:00",
    }))
    checks = scheduled.observe(tmp_path, REPO, lambda _argv, _env: (0, ""), datetime(2026, 10, 18, tzinfo=UTC))
    assert not any(check.name == "mail-sync-quiet" for check in checks)


@pytest.mark.parametrize("relative", [
    "skills/mail/scripts/mail_sync_watch.py", "skills/mail/scripts/mail_triage_watch.py",
    "skills/mail/scripts/mail_digest_watch.py", "skills/budget/scripts/budget_watch.py",
    "automation/research_trends/research_trends.py", "automation/notes_organize/notes_organize.py",
])
def test_wrappers_do_not_write_retired_ledger(relative) -> None:
    tree = ast.parse((REPO / relative).read_text())
    assert not any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name) and node.func.value.id == "watch_failure_streak"
        and node.func.attr == "record"
        for node in ast.walk(tree)
    )


def test_collection_result_retains_weekday_zeroes_without_old_ledger(tmp_path) -> None:
    from skills.mail.scripts import mail_sync_watch as sync

    monday = datetime(2026, 10, 19, 1, tzinfo=UTC)
    for _ in range(6):
        sync._record_collection(tmp_path, True, 0, monday)
    state = json.loads((tmp_path / ".hermes/mail-sync/collection.json").read_text())
    assert state["zero_weekday_streak"] == 6
    sync._record_collection(tmp_path, True, 0, datetime(2026, 10, 24, 1, tzinfo=UTC))
    state = json.loads((tmp_path / ".hermes/mail-sync/collection.json").read_text())
    assert state["zero_weekday_streak"] == 0
    assert not (tmp_path / ".hermes/watch-failure").exists()


def test_wrapper_drift_does_not_open_incident_or_repair(tmp_path) -> None:
    out = subprocess.run(["bash", "-c", f"""
source '{REPO}/automation/healthcheck.sh'
setup_log() {{ :; }}
require_commands() {{ :; }}
report_roster_identity() {{ :; }}
healthcheck_run_check_with_evidence() {{
  case "$1" in
    *'|healthcheck_wrapper_current|'*|*'|watcher_wrappers_current|'*) return 1 ;;
    *) return 0 ;;
  esac
}}
report_repair() {{ echo UNEXPECTED_REPAIR; }}
main
"""], text=True, capture_output=True, env={
        "PATH": os.environ["PATH"], "HOME": str(tmp_path),
        "HEALTHCHECK_LOG_DIR": str(tmp_path / "logs"),
        "HEALTHCHECK_NOTIFY_STATE": str(tmp_path / "incidents.json"),
        "HEALTHCHECK_NOTIFY_ENV": "/dev/null",
        "AUTOPHAGY_RUNTIME_ROOT": str(REPO),
    })
    assert out.returncode == 0, out.stdout + out.stderr
    assert "FAIL " not in out.stdout
    assert "UNEXPECTED_REPAIR" not in out.stdout
    assert "확인" in out.stdout
    assert json.loads((tmp_path / "incidents.json").read_text())["open_incidents"] == []


def test_wrapper_refresh_is_pending_only_for_same_head_and_digest(tmp_path) -> None:
    from automation.healthcheck_pending import planned_refresh
    from automation.release_spec import ReleaseSpec
    from automation.skill_gate_surface import binding_of_post
    from automation.interop.approval_surface import ApprovalKind

    spec = ReleaseSpec("v2.3.0", "a" * 40, "b" * 32, (("healthcheck-wrapper:node", "c" * 64),),
                       "synthetic release", render_version=1)
    record = spec.new_record("12345", binding_of_post(ApprovalKind.RELEASE, "98765"))
    path = tmp_path / "release.json"
    path.write_text(json.dumps(record))
    assert planned_refresh(path, "node", "c" * 64, "a" * 40)
    assert not planned_refresh(path, "node", "d" * 64, "a" * 40)
    assert not planned_refresh(path, "node", "c" * 64, "e" * 40)


@pytest.mark.parametrize("attempts,status", [(1, Status.WARN), (2, Status.FAIL)])
def test_doctor_reads_supply_chain_reminder_failure_record(tmp_path, attempts, status) -> None:
    from automation.doctor.scheduled import observe

    path = tmp_path / ".hermes/supply-chain-watch/tick.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"reminder_failures": {"release:demo": {
        "attempts": attempts, "fingerprint": "ApprovalSurfaceError", "next_attempt_at": 0,
    }}}))
    checks = observe(tmp_path, REPO, lambda *_: (0, ""), datetime(2026, 10, 18, tzinfo=UTC))
    assert [(check.name, check.status) for check in checks] == [("supply-chain-reminder", status)]
