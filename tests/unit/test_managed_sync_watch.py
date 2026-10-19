"""W-F3-B — the managed-skill subscriber sync tick.

One tick implementation is shared by both deployments (Hermes no-agent cron and the
systemd timer), so its contract is pinned once, here: the shared lease, explicit child
credentials, a best-effort notice, and the D3 boundary that keeps mounting manual.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from automation.interop.owner_message import OwnerMessage
from automation.managed_sync.cron import managed_sync_watch as watch

_REPO = Path(__file__).resolve().parents[2]
_WRAPPER = _REPO / "automation" / "managed_sync" / "cron" / "managed_sync_watch.py"


# --- credential propagation (규약 (b-2)) --------------------------------------------


def test_child_env_carries_a_secret_the_parent_process_never_had(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The regression this pins: no-agent cron does not inject ~/.env.secrets into
    # os.environ, so a child spawned with env=None inherits an environment with no
    # token and fails at rc=1 far from the real cause.
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    secrets = tmp_path / ".env.secrets"
    _ = secrets.write_text('DISCORD_BOT_TOKEN="from-secrets-file"\n', encoding="utf-8")
    monkeypatch.setattr(watch, "REPO_ROOT", tmp_path / "release")

    environment = watch.child_environment(watch.load_secrets(secrets))

    assert environment["DISCORD_BOT_TOKEN"] == "from-secrets-file"
    assert environment["AUTOPHAGY_REPO_ROOT"] == str(tmp_path / "release")
    assert environment["AUTOPHAGY_RUNTIME_ROOT"] == str(tmp_path / "release")
    assert environment["PYTHONPATH"].split(":")[0] == str(tmp_path / "release")


def test_system_environment_wins_over_the_secrets_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secrets = tmp_path / ".env.secrets"
    _ = secrets.write_text("DISCORD_BOT_TOKEN=stale\n", encoding="utf-8")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "live")
    monkeypatch.setattr(watch, "REPO_ROOT", tmp_path / "release")

    assert watch.child_environment(watch.load_secrets(secrets))["DISCORD_BOT_TOKEN"] == "live"


def test_an_unreadable_secrets_file_degrades_to_the_process_environment(
    tmp_path: Path
) -> None:
    assert watch.load_secrets(tmp_path / "absent") == {}


# --- overlapping ticks -------------------------------------------------------------


def test_an_overlapping_tick_exits_zero_without_spawning_a_child(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from automation.interop.approval_lease import FileKeyLease

    lease_root = tmp_path / "leases"
    monkeypatch.setattr(watch, "LEASE_ROOT", lease_root)
    spawned: list[int] = []

    def never_called() -> tuple[int, str]:
        spawned.append(1)
        return 0, ""

    monkeypatch.setattr(watch, "run_sync_once", never_called)

    with FileKeyLease(lease_root).hold(watch.LEASE_KEY) as owned:
        assert owned
        code = watch.run_tick()

    assert code == 0  # silent: a skipped tick is not an incident
    assert spawned == []


def test_the_lease_is_released_so_the_next_tick_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(watch, "LEASE_ROOT", tmp_path / "leases")
    monkeypatch.setattr(watch, "SECRETS_PATH", tmp_path / "absent")
    runs: list[int] = []

    def one_pass() -> tuple[int, str]:
        runs.append(1)
        return 0, ""

    monkeypatch.setattr(watch, "run_sync_once", one_pass)
    monkeypatch.setattr(watch, "run_roster_once", lambda: None)

    assert watch.run_tick() == 0
    assert watch.run_tick() == 0
    assert runs == [1, 1]


def test_the_tick_reuses_the_repository_standard_lease_primitive() -> None:
    # Not a new lock: the same FileKeyLease that serialises every approval producer.
    source = _WRAPPER.read_text(encoding="utf-8")

    assert "from automation.interop.approval_lease import FileKeyLease" in source
    assert "fcntl" not in source


# --- owner notice ------------------------------------------------------------------


def test_a_tick_that_stages_nothing_sends_no_notice() -> None:
    assert (
        watch.staged_notice("SYNC-SUMMARY staged=0 skipped=1 failed=0 removal_requests=0\n")
        is None
    )


def test_a_rejected_release_is_exposed_in_the_log_but_not_dm_flooded() -> None:
    stdout = (
        "SYNC-FAILED skill=managed-lab tag=managed-lab/3 reason=WRONG-PRINCIPAL\n"
        "SYNC-SUMMARY staged=0 skipped=0 failed=1 removal_requests=0\n"
    )

    # An unverifiable release keeps failing every 30 minutes; a per-tick DM would be a
    # flood. The reason stays visible on the SYNC-FAILED line the wrapper re-emits.
    assert watch.staged_notice(stdout) is None


def test_a_staged_release_notice_names_the_skill_and_keeps_activation_manual() -> None:
    digest = "a" * 64
    notice = watch.staged_notice(
        f"SYNC-STAGED skill=managed-lab sequence=4 digest={digest}\n"
        + "SYNC-SUMMARY staged=1 skipped=0 failed=0 removal_requests=0\n"
    )

    assert notice is not None
    assert "managed-lab" in notice
    assert "seq=4" in notice
    assert digest[:12] in notice
    assert digest not in notice  # the full digest is noise in a DM
    assert "✅" in notice


def test_the_notice_uses_the_shared_owner_notice_surface_not_a_new_one() -> None:
    # No new approval surface and no new watcher: the notice rides the one shared
    # owner-notice sender, which is a notice channel and never a gate.
    source = _WRAPPER.read_text(encoding="utf-8")

    assert "from automation.owner_notice import notify_owner" in source
    assert "DiscordTransport" not in source


def test_a_failed_notice_does_not_undo_a_staged_release(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # 규약 (i): the mutation already happened on disk; a best-effort DM failure must not
    # turn a successful tick into a failing one, or the next tick re-reports forever.
    monkeypatch.setattr(watch, "LEASE_ROOT", tmp_path / "leases")
    monkeypatch.setattr(watch, "SECRETS_PATH", tmp_path / "absent")
    digest = "b" * 64

    def staged() -> tuple[int, str]:
        return 0, f"SYNC-STAGED skill=managed-lab sequence=1 digest={digest}\n"

    monkeypatch.setattr(watch, "run_sync_once", staged)
    monkeypatch.setattr(watch, "run_roster_once", lambda: None)

    def fail_notice(_notice: str, *, message: OwnerMessage | None = None, fault: bool = False) -> bool:
        return False

    monkeypatch.setattr(watch, "notify_owner", fail_notice)

    assert watch.run_tick() == 0


# --- D3: delivery is automatic, mounting is not ------------------------------------


def test_the_tick_runs_exactly_one_sync_subcommand_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recorded: list[list[str]] = []

    def fake_run(
        command: list[str],
        **kwargs: str | bool | dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        del kwargs
        recorded.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(watch, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(watch, "SECRETS_PATH", tmp_path / "absent")
    monkeypatch.setattr(subprocess, "run", fake_run)

    _ = watch.run_sync_once()

    # No --allow-rollback, no mark-activated, no activation instruction: exactly one
    # fetch/verify/quarantine pass, which is the whole authority this tick has.
    assert recorded == [[sys.executable, "-m", "automation.managed_sync", "sync"]]


def test_the_tick_reemits_child_output_so_the_journal_keeps_refusal_reasons(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    reason = "SYNC-FAILED skill=managed-lab tag=managed-lab/9 reason=BAD-SIGNATURE\n"

    def fake_run(
        command: list[str],
        **kwargs: str | bool | dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(command, 1, reason, "SYNC-FATAL reason=unreadable\n")

    monkeypatch.setattr(watch, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(watch, "SECRETS_PATH", tmp_path / "absent")
    monkeypatch.setattr(subprocess, "run", fake_run)

    code, stdout = watch.run_sync_once()
    captured = capsys.readouterr()

    assert code == 1
    assert reason in stdout
    assert reason in captured.out
    assert "SYNC-FATAL reason=unreadable" in captured.err
