"""doctor: 계정 안의 연결·승인 상태를 읽기만 해서 판정하고, 바뀔 때만 알린다.

관측은 가짜 홈 디렉터리와 주입된 실행기로만 한다 — 실제 systemctl·gws·Discord 를 부르지 않는다.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Final

import pytest

from automation.doctor import alarm, cli, report
from automation.doctor.capabilities import Finding, evaluate
from automation.doctor.facts import Facts, gather
from automation.install.checks import CheckResult, Status

_TOKEN: Final = "doctor-fake-" + "x" * 40
_G: Final = "https://www.googleapis.com/auth/"
_ALL_SCOPES: Final = [f"{_G}{name}" for name in (
    "calendar", "drive", "spreadsheets", "gmail.send", "gmail.readonly", "tasks", "meetings.space.created",
)]
_NOW: Final = datetime(2026, 9, 29, 9, 0, tzinfo=timezone(timedelta(hours=9)))


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(text, encoding="utf-8")
    return path


def _connected_home(home: Path, *, jobs: list[dict[str, object]] | None = None) -> Path:
    whisper = _write(home / "tools" / "whisper-cli", "")
    model = _write(home / "tools" / "model.bin", "")
    _ = _write(home / ".local" / "bin" / "hermes", "")
    _ = _write(home / ".local" / "bin" / "gws", "")
    for name in ("client_secret.json", "credentials.enc", ".encryption_key"):
        _ = _write(home / ".config" / "gws" / name, "{}")
    _ = _write(home / ".env.secrets", "\n".join((
        f"DISCORD_BOT_TOKEN={_TOKEN}", "DRIVE_PUBLISH_ENABLED=1",
        "MAILON_ID=someone", "MAILON_PW=pw", "MAILON_TOTP_SECRET=otp",
        f"SPEECHTOTEXT_WHISPER_BIN={whisper}", f"export SPEECHTOTEXT_WHISPER_MODEL=\"{model}\"",
    )) + "\n")
    _ = _write(home / ".hermes" / "auth.json", json.dumps({"credential_pool": {
        "openai-codex": [{"last_status": "ok", "access_token": _TOKEN}],
        "xai-oauth": [{"last_status": None}],
    }}))
    _ = _write(home / ".hermes" / "config.yaml",
               "model:\n  provider: openai-codex\nfallback_providers:\n  - provider: xai-oauth\n    model: g\n"
               "timezone: Asia/Seoul\ndiscord:\n  require_mention: true\n  free_response_channels: '11'\n")
    _ = _write(home / ".hermes" / "interop" / "config.json", json.dumps({
        "agent_chat_channel_id": "11", "owner_id": "22", "owner_notice_channel_id": "33",
    }))
    _ = _write(home / ".hermes" / "cron" / "jobs.json", json.dumps({"jobs": jobs if jobs is not None else [
        {"name": "reminder-poller", "enabled": True, "last_status": "ok", "failure_streak": 0},
    ]}))
    return home


def _run(gws: dict[str, object] | None = None, gateway: str = "active"):
    status = gws if gws is not None else {"token_valid": True, "scopes": _ALL_SCOPES}

    def run(argv: tuple[str, ...], _extra: object) -> tuple[int, str]:
        if argv[0] == "systemctl":
            return (0 if gateway == "active" else 3), f"{gateway}\n"
        if argv[1:] == ("auth", "status"):
            return 0, json.dumps(status)
        return 127, ""

    return run


def _probe_ok(token: str, _config: Path | None) -> tuple[CheckResult, ...]:
    assert token == _TOKEN
    return (CheckResult("token", Status.PASS, "ok"), CheckResult("surface[owner-dm]", Status.WARN, "x"))


def _facts(home: Path, **kwargs: object) -> Facts:
    options: dict[str, object] = {"run": _run(), "discord_probe": _probe_ok, "online": True}
    options.update(kwargs)
    return gather(account="agent", role="agent", home=home, gateway_unit="hermes-gateway.service",
                  uid=1000, **options)  # type: ignore[arg-type]


def _by_key(findings: tuple[Finding, ...]) -> dict[str, Finding]:
    return {finding.key: finding for finding in findings}


def test_a_fully_connected_agent_passes_every_check(tmp_path: Path) -> None:
    findings = evaluate(_facts(_connected_home(tmp_path)))

    assert {f.key: str(f.status) for f in findings if f.status is not Status.PASS} == {}
    assert len(findings) == 13
    assert all(f.steps == () for f in findings)


def test_agent_chat_missing_from_free_response_is_broken(tmp_path: Path) -> None:
    home = _connected_home(tmp_path)
    _ = _write(home / ".hermes" / "config.yaml", "discord:\n  require_mention: true\n  free_response_channels: '44'\n")

    found = _by_key(evaluate(_facts(home)))["agent-chat-free-response"]

    assert found.status is Status.FAIL and found.subjects == ("free_response_channels",)
    assert found.steps


@pytest.mark.parametrize("free", ["44", "11"])
def test_agent_chat_in_ignored_channels_is_intended_even_when_free(tmp_path: Path, free: str) -> None:
    home = _connected_home(tmp_path)
    _ = _write(home / ".hermes" / "config.yaml",
               f"discord:\n  ignored_channels: '11,44'\n  free_response_channels: '{free}'\n")

    found = _by_key(evaluate(_facts(home)))["agent-chat-free-response"]

    assert found.status is Status.PASS and found.subjects == ()
    assert found.steps == ()


def test_a_repeatedly_failing_job_is_broken_and_named(tmp_path: Path) -> None:
    jobs = [
        {"name": "daily-cost-report", "enabled": True, "last_status": "error", "failure_streak": 26,
         "last_error": "Script exited with code 1\ncost-report error: 'COST_REPORT_SOFT_CAP'"},
        {"name": "flaky", "enabled": True, "last_status": "error", "failure_streak": 1, "last_error": "x"},
    ]
    job = _by_key(evaluate(_facts(_connected_home(tmp_path, jobs=jobs))))["scheduled-jobs"]

    assert job.status is Status.FAIL
    assert job.subjects == ("daily-cost-report",)
    assert "26회 연속" in job.detail and "COST_REPORT_SOFT_CAP" in job.detail
    assert job.steps


def test_a_job_that_failed_once_only_warns(tmp_path: Path) -> None:
    jobs = [{"name": "flaky", "enabled": True, "last_status": "error", "failure_streak": 2}]

    job = _by_key(evaluate(_facts(_connected_home(tmp_path, jobs=jobs))))["scheduled-jobs"]

    assert job.status is Status.WARN and job.subjects == ("flaky",)


def test_a_job_skipped_after_a_model_change_is_broken_on_the_first_skip(tmp_path: Path) -> None:
    jobs = [{"name": "mail-triage-repair-daily", "enabled": True, "last_status": "error", "failure_streak": 1,
             "last_error": "RuntimeError: [drift_skip:silent] Skipped to prevent unintended spend"}]

    job = _by_key(evaluate(_facts(_connected_home(tmp_path, jobs=jobs))))["scheduled-jobs"]

    assert job.status is Status.FAIL and job.subjects == ("mail-triage-repair-daily",)
    assert "다시 만든다" in job.detail
    assert any("drift_skip" in step for step in job.steps)


def test_secret_values_never_reach_the_report(tmp_path: Path) -> None:
    jobs = [{"name": "leaky", "enabled": True, "last_status": "error", "failure_streak": 5,
             "last_error": f"auth failed with {_TOKEN}"}]
    findings = evaluate(_facts(_connected_home(tmp_path, jobs=jobs)))

    text = report.render_text("agent", "agent", findings)
    payload = json.dumps(report.to_json("agent", "agent", findings), ensure_ascii=False)

    assert _TOKEN not in text and _TOKEN not in payload
    assert "<masked>" in text


def test_an_empty_home_names_every_missing_connection_without_crashing(tmp_path: Path) -> None:
    def run(_argv: tuple[str, ...], _extra: object) -> tuple[int, str]:
        return 127, ""

    found = _by_key(evaluate(_facts(tmp_path, run=run)))

    for key in ("hermes-cli", "primary-model", "discord-bot", "approval-surface"):
        assert found[key].status is Status.FAIL, key
    for key in ("google-workspace", "drive-publish", "institutional-mail", "speech-to-text", "scheduled-jobs"):
        assert found[key].status is Status.WARN, key
    assert found["hermes-gateway"].status is Status.WARN
    assert all(f.steps[-1].startswith("자세한 절차") for f in found.values() if f.status is not Status.PASS)


def test_an_expired_google_token_is_broken(tmp_path: Path) -> None:
    home = _connected_home(tmp_path)

    found = _by_key(evaluate(_facts(home, run=_run({"token_valid": False, "scopes": _ALL_SCOPES}))))

    assert found["google-workspace"].status is Status.FAIL


def test_missing_google_scopes_only_warn_naming_the_features(tmp_path: Path) -> None:
    scopes = [scope for scope in _ALL_SCOPES if not scope.endswith(("tasks", "gmail.send"))]

    found = _by_key(evaluate(_facts(_connected_home(tmp_path), run=_run({"token_valid": True, "scopes": scopes}))))

    assert found["google-workspace"].status is Status.WARN
    assert found["google-workspace"].subjects == ("메일 발송", "할 일")


def test_partial_mail_credentials_are_broken_but_absent_ones_only_warn(tmp_path: Path) -> None:
    home = _connected_home(tmp_path)
    secrets = home / ".env.secrets"
    _ = secrets.write_text(secrets.read_text().replace("MAILON_PW=pw\n", ""))

    assert _by_key(evaluate(_facts(home)))["institutional-mail"].status is Status.FAIL

    _ = secrets.write_text("".join(line + "\n" for line in secrets.read_text().splitlines() if "MAILON" not in line))
    assert _by_key(evaluate(_facts(home)))["institutional-mail"].status is Status.WARN


def test_a_discord_rejection_is_broken_and_a_probe_crash_is_undecided(tmp_path: Path) -> None:
    home = _connected_home(tmp_path)

    def rejected(_token: str, _config: Path | None) -> tuple[CheckResult, ...]:
        return (CheckResult("intent", Status.FAIL, "Message Content 인텐트가 꺼져 있다"),)

    def crashing(_token: str, _config: Path | None) -> tuple[CheckResult, ...]:
        raise OSError("network")

    assert _by_key(evaluate(_facts(home, discord_probe=rejected)))["discord-bot"].status is Status.FAIL
    assert _by_key(evaluate(_facts(home, discord_probe=crashing)))["discord-bot"].status is Status.WARN


def test_a_custom_primary_model_needs_no_login_but_must_be_declared(tmp_path: Path) -> None:
    home = _connected_home(tmp_path)
    config = home / ".hermes" / "config.yaml"
    _ = config.write_text("model:\n  provider: custom:litellm\n  default: glm\n"
                          "custom_providers:\n  - name: litellm\n    base_url: http://127.0.0.1:4000/v1\n")
    _ = (home / ".hermes" / "auth.json").unlink()

    declared = _by_key(evaluate(_facts(home)))["primary-model"]
    _ = config.write_text("model:\n  provider: custom:litellm\n")
    undeclared = _by_key(evaluate(_facts(home)))["primary-model"]

    assert declared.status is Status.PASS
    assert undeclared.status is Status.FAIL and undeclared.subjects == ("custom:litellm",)


def test_an_approvals_channel_found_by_name_is_not_a_fault_but_other_missing_ids_are(tmp_path: Path) -> None:
    home = _connected_home(tmp_path)

    def approvals_by_name(_token: str, _config: Path | None) -> tuple[CheckResult, ...]:
        return (CheckResult("channel[approvals]", Status.FAIL, "MISSING-CHANNEL-ID: #approvals"),)

    def interop_missing(_token: str, _config: Path | None) -> tuple[CheckResult, ...]:
        return (CheckResult("channel[interop]", Status.FAIL, "MISSING-CHANNEL-ID: #autophagy-agents"),)

    assert _by_key(evaluate(_facts(home, discord_probe=approvals_by_name)))["discord-bot"].status is Status.PASS
    assert _by_key(evaluate(_facts(home, discord_probe=interop_missing)))["discord-bot"].status is Status.FAIL


def test_offline_skips_the_network_but_still_reads_local_state(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []

    def run(argv: tuple[str, ...], _extra: object) -> tuple[int, str]:
        calls.append(argv)
        return 0, "active\n"

    def forbidden(_token: str, _config: Path | None) -> tuple[CheckResult, ...]:
        raise AssertionError("offline must not call Discord")

    facts = _facts(_connected_home(tmp_path), run=run, discord_probe=forbidden, online=False)

    assert facts.gws is None and facts.discord is None
    assert calls == [("systemctl", "--user", "is-active", "hermes-gateway.service")]
    assert all(f.status is Status.PASS for f in evaluate(facts))


# --- alarm ---------------------------------------------------------------------------


class _Outbox:
    def __init__(self, *, ok: bool = True) -> None:
        self.ok = ok
        self.sent: list[str] = []

    def __call__(self, body: str, message: object) -> bool:
        assert message is not None
        self.sent.append(body)
        return self.ok


def _finding(key: str, status: Status, subjects: tuple[str, ...] = ()) -> Finding:
    return Finding(key, f"라벨-{key}", status, "detail", "affects", ("step",), subjects)


def test_the_alarm_speaks_only_when_the_problem_set_changes(tmp_path: Path) -> None:
    outbox = _Outbox()
    broken = (_finding("primary-model", Status.FAIL), _finding("drive-publish", Status.PASS))
    fixed = (_finding("primary-model", Status.PASS), _finding("drive-publish", Status.PASS))

    first = alarm.run_alarm("agent", broken, home=tmp_path, now=_NOW, send=outbox)
    again = alarm.run_alarm("agent", broken, home=tmp_path, now=_NOW, send=outbox)
    healed = alarm.run_alarm("agent", fixed, home=tmp_path, now=_NOW, send=outbox)

    assert first.startswith("DOCTOR-ALARM-SENT new=1") and again == "DOCTOR-ALARM-UNCHANGED"
    assert healed == "DOCTOR-ALARM-RECOVERED resolved=1"
    assert len(outbox.sent) == 1
    assert "라벨-primary-model" in outbox.sent[0]


def test_a_new_failing_job_under_an_already_broken_check_still_alarms(tmp_path: Path) -> None:
    outbox = _Outbox()
    _ = alarm.run_alarm("agent", (_finding("scheduled-jobs", Status.FAIL, ("a",)),), home=tmp_path, now=_NOW, send=outbox)
    _ = alarm.run_alarm("agent", (_finding("scheduled-jobs", Status.FAIL, ("a", "b")),), home=tmp_path, now=_NOW,
                        send=outbox)

    assert len(outbox.sent) == 2


def test_a_failed_send_keeps_the_state_so_the_next_check_retries(tmp_path: Path) -> None:
    broken = (_finding("discord-bot", Status.FAIL),)

    failed = alarm.run_alarm("agent", broken, home=tmp_path, now=_NOW, send=_Outbox(ok=False))
    retry = _Outbox()
    resent = alarm.run_alarm("agent", broken, home=tmp_path, now=_NOW, send=retry)

    assert failed.startswith("DOCTOR-ALARM-NOTIFY-FAILED")
    assert resent.startswith("DOCTOR-ALARM-SENT") and len(retry.sent) == 1


def test_a_clean_first_check_is_silent_and_the_state_is_private(tmp_path: Path) -> None:
    outbox = _Outbox()

    result = alarm.run_alarm("agent", (_finding("primary-model", Status.PASS),), home=tmp_path, now=_NOW, send=outbox)

    path = alarm.state_path(tmp_path)
    assert result == "DOCTOR-ALARM-UNCHANGED" and outbox.sent == []
    assert path.stat().st_mode & 0o777 == 0o600 and path.parent.stat().st_mode & 0o777 == 0o700


def test_the_alarm_envelope_renders_through_the_owner_message_contract() -> None:
    from automation.interop.owner_message import Ref, render

    change = alarm.diff((_finding("primary-model", Status.FAIL),), None)
    assert change is not None
    body = alarm.content("agent", change)
    message = alarm.envelope("agent", body, _NOW - timedelta(hours=1), _NOW)
    assert message is not None

    text = render(message, destination=Ref(scope="channel", space="guild", guild_id="1", channel_id="2"))

    assert "라벨-primary-model" in text and alarm.DOCTOR_COMMAND in text


# --- CLI -----------------------------------------------------------------------------


def test_operator_mode_collects_each_account_and_names_the_one_it_could_not_reach(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    monkeypatch.setattr(cli, "_config", lambda: None)
    monkeypatch.setattr(cli, "_account", lambda: "operator")
    agent_json = json.dumps(report.to_json("agent", "agent", (_finding("primary-model", Status.FAIL),)))
    seen: list[tuple[str, ...]] = []

    def run_child(argv: tuple[str, ...], _cwd: Path) -> tuple[int, str, str]:
        seen.append(argv)
        if "agent" in argv:
            return 1, agent_json, ""
        return 1, "", "sudo: a password is required"

    code = cli.main(["--offline"], run_child=run_child)

    out = capsys.readouterr().out
    assert code == 1
    doctor_calls = [argv for argv in seen if "automation.doctor" in argv]
    assert [argv[:5] for argv in doctor_calls] == [
        ("sudo", "-n", "-u", "agent", "-H"), ("sudo", "-n", "-u", "peer", "-H")
    ]
    assert all("--offline" in argv and "--json" in argv for argv in doctor_calls)
    assert "model-parity" in out
    assert "[FAIL] primary-model" in out
    assert "[WARN] doctor-run · peer" in out and "sudo: a password is required" in out


def test_notify_is_refused_outside_a_service_account(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "_config", lambda: None)
    monkeypatch.setattr(cli, "_account", lambda: "operator")

    assert cli.main(["--notify"]) == 2
    assert "DOCTOR-USAGE" in capsys.readouterr().err


def test_the_watcher_hands_its_self_loaded_secrets_to_the_child(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    from automation.doctor.cron import doctor_watch

    secrets = _write(tmp_path / ".env.secrets", f"DISCORD_BOT_TOKEN={_TOKEN}\n")
    monkeypatch.setattr(doctor_watch.os, "environ", {"PATH": "/usr/bin:/bin"})
    captured: dict[str, object] = {}

    def fake_run(argv: list[str], **kwargs: object) -> SimpleNamespace:
        captured.update(kwargs, argv=argv)
        return SimpleNamespace(returncode=1, stdout="--- DOCTOR agent: 통과 1 · 확인 0 · 고장 0\nDOCTOR-ALARM-UNCHANGED\n",
                               stderr="")

    monkeypatch.setattr(doctor_watch.subprocess, "run", fake_run)
    doctor_watch._load_env_secrets(secrets)  # pyright: ignore[reportPrivateUsage]

    assert doctor_watch.run_once(tmp_path) == 0
    env = captured["env"]
    assert isinstance(env, dict) and env["DISCORD_BOT_TOKEN"] == _TOKEN and env["PYTHONPATH"] == str(tmp_path)
    assert captured["argv"][-1] == "--notify"  # type: ignore[index]
    assert _TOKEN not in capsys.readouterr().out
