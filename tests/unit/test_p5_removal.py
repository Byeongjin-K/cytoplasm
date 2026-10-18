"""P5 effects run against private fixture homes and a stateful command seam."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from automation import p5_remove, peer_retired, release_approval
from tests.unit import test_release_authorize as auth
from tests.unit.test_healthcheck_wrapper_release import _spec

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("value", [b"", b"retired\n", b"active", b" retired"])
def test_switch_refuses_nonliteral_content(tmp_path: Path, value: bytes) -> None:
    flag = tmp_path / ".hermes/release-flags/peer-retired"
    flag.parent.mkdir(parents=True)
    flag.write_bytes(value)
    with pytest.raises(ValueError, match="PEER-RETIRED-REFUSED"):
        peer_retired.release_switch(tmp_path)


@pytest.mark.parametrize("reaction,covered,head,code", [
    ("approve", True, "a" * 40, 0), ("pending", True, "a" * 40, 1),
    ("both", True, "a" * 40, 8), ("approve", False, "a" * 40, 4),
    ("approve", True, "f" * 40, 4),
])
def test_retirement_authorization_binds_head_surface_and_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    reaction: str, covered: bool, head: str, code: int,
) -> None:
    base = auth._spec()
    spec = replace(base, surface_digests=(*base.surface_digests,
                                        *([peer_retired.surface()] if covered else [])))
    monkeypatch.setattr(auth, "_spec", lambda: spec)
    owner = [{"id": auth._OWNER, "bot": False}]
    reactions = {auth._APPROVE: owner if reaction in ("approve", "both") else [],
                 auth._CANCEL: owner if reaction == "both" else []}
    auth._env(tmp_path, monkeypatch, reactions=reactions, api_log=[], render_version=6)
    assert release_approval.main(["authorize-peer-retired", "--head", head]) == code


def test_card_lines_are_the_shipped_retirement_copy() -> None:
    base = _spec()
    spec = replace(base, surface_digests=(*base.surface_digests, peer_retired.surface()))
    content = spec.render()
    assert spec.action_hash() != base.action_hash()
    assert all(content.count(line) == 1 for line in peer_retired.CARD_LINES)
    assert peer_retired.ROOT_NOTE in content
    assert len(content) <= 1900


def _home(tmp_path: Path, role: str) -> tuple[Path, dict[str, str]]:
    home = tmp_path / role
    for relative in (".hermes/config.yaml", ".hermes/auth.json", ".hermes/kanban.db",
                     ".local/bin/hermes", ".hermes/SOUL.md", ".hermes/config.yaml.bak-old",
                     ".config/systemd/user/hermes-gateway.service",
                     ".hermes/skills/bundle/SKILL.md", ".hermes/plugins/interop/plugin.yaml"):
        path = home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"original {relative}")
    current = {f"{i:012x}": f"job-{i}" for i in range(30 if role == "agent" else 3)}
    path = home / ".hermes/cron/jobs.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"jobs": [{"id": key, "name": name, "enabled": False}
                                        for key, name in current.items()]}))
    return home, current


def _commands(monkeypatch: pytest.MonkeyPatch, current: dict[str, str],
              calls: list[tuple[str, ...]], *, fail: str = "") -> None:
    def command(argv: tuple[str, ...]) -> str:
        calls.append(argv)
        if argv[1:3] == ("cron", "list"):
            if not current:
                return "No scheduled jobs.\n"
            return "Scheduled Jobs\n\n" + "".join(
                f"  {key} [paused]\n    Name: {name}\n    Schedule: every 2m\n    Deliver: local\n\n"
                for key, name in current.items()
            )
        if argv[1:3] == ("cron", "remove"):
            if fail:
                raise ValueError(fail)
            del current[argv[3]]
        if "--property=LoadState" in argv:
            return "loaded\n"
        if "--property=ActiveState" in argv:
            return "inactive\n"
        return ""
    monkeypatch.setattr(p5_remove, "call", command)


@pytest.mark.parametrize("role", ["agent", "peer"])
def test_cleanup_exports_jobs_verifies_backup_and_preserves_model_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, role: str,
) -> None:
    home, current = _home(tmp_path, role)
    calls: list[tuple[str, ...]] = []
    _commands(monkeypatch, current, calls)
    kept = {p: (home / p).read_bytes() for p in (
        ".local/bin/hermes", ".hermes/config.yaml", ".hermes/auth.json", ".hermes/kanban.db"
    )}
    folder = p5_remove.remove(home, role, "a" * 40, "hermes", "systemctl")
    assert all((home / path).read_bytes() == content for path, content in kept.items())
    assert not (home / ".config/systemd/user/hermes-gateway.service").exists()
    assert not current
    assert len([c for c in calls if c[1:3] == ("cron", "remove")]) == (30 if role == "agent" else 3)
    assert (folder / "home.tar").stat().st_mode & 0o777 == 0o600
    assert json.loads((folder / "jobs.json").read_text())["jobs"]
    assert peer_retired.retired(home)
    assert p5_remove.remove(home, role, "b" * 40, "hermes", "systemctl") == folder
    if role == "agent":
        assert not (home / ".hermes/skills").exists()
        assert not (home / ".hermes/SOUL.md").exists()


def test_failed_cron_remove_keeps_archive_and_files_without_completion_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, current = _home(tmp_path, "agent")
    _commands(monkeypatch, current, [], fail="cron-remove")
    with pytest.raises(ValueError, match="cron-remove"):
        p5_remove.remove(home, "agent", "a" * 40, "hermes", "systemctl")
    assert (home / ".hermes/retired/p5" / ("a" * 40) / "home.tar").is_file()
    assert (home / ".hermes/plugins/interop/plugin.yaml").is_file()
    assert not peer_retired.retired(home)


def test_unexpected_job_count_refuses_before_unit_or_file_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, current = _home(tmp_path, "agent")
    current.pop(next(iter(current)))
    calls: list[tuple[str, ...]] = []
    _commands(monkeypatch, current, calls)
    with pytest.raises(ValueError, match="expected 30"):
        p5_remove.remove(home, "agent", "a" * 40, "hermes", "systemctl")
    assert not any("--user" in argv for argv in calls)
    assert (home / ".hermes/SOUL.md").is_file()


def test_peer_retirement_command_delegates_to_existing_release_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[argparse.Namespace] = []
    monkeypatch.setattr(release_approval.skill_gate, "cmd_release_authorize",
                        lambda args: captured.append(args) or 0)
    assert release_approval.cmd_authorize_peer_retired(argparse.Namespace(head="a" * 40)) == 0
    assert captured[0].surface == peer_retired.SURFACE
    assert captured[0].head == "a" * 40


def test_deploy_refuses_unapproved_retirement_before_cleanup(tmp_path: Path) -> None:
    from tests.unit.test_hermes_gateways_stop import _host

    repo, env = _host(tmp_path, "stopped", auth_rc=4)
    flag = Path(env["HOME"]) / ".hermes/release-flags/peer-retired"
    flag.write_bytes(b"retired")
    # The shared stop check also refuses; neither authorization failure may mutate.
    result = subprocess.run(("bash", str(repo / "automation/deploy_all.sh"), "--apply"),
                            env=env, text=True, capture_output=True, check=False)
    assert result.returncode == 4
    assert "p5_remove" not in (tmp_path / "calls").read_text()


def test_retired_probe_excludes_peer_and_gateway_artifacts_without_sudoing_peer(
    tmp_path: Path,
) -> None:
    from automation import deploy_all, deploy_all_observe
    from tests.unit.test_deploy_all_gateway_generation import _runtime, _wrap

    runtime = _runtime(tmp_path)
    calls: list[tuple[str, tuple[str, ...]]] = []

    def runner(account: str, argv: tuple[str, ...]) -> tuple[int, str]:
        calls.append((account, argv))
        assert account != "peer"
        assert argv[0] not in ("gateway", "gateway-service", "roster")
        return 0, "DELEGATED-END|0\n"

    lines = deploy_all_observe.collect(runtime, runner, "ops", peer_retired=True,
                                      hermes_gateways="stopped")
    plan = deploy_all.parse_observations(_wrap(lines))
    assert plan.clean
    assert plan.hermes_gateways == "retired"
    assert not plan.gateway_restart_needed


def test_retirement_healthchecks_do_not_query_removed_account(tmp_path: Path) -> None:
    marker = tmp_path / "peer-retired"
    marker.write_bytes(b"retired")
    script = f"""
set -eu
PRIMARY_NODE=fake; RAG_NODE=fake; NODE_AGENT_ACCOUNT=agent; NODE_PEER_ACCOUNT=peer
NODE_AGENT_GATEWAY_UNIT=hermes-gateway.service; NODE_PEER_GATEWAY_UNIT=hermes-gateway.service
NODE_OPS_ACCOUNT=ops; PEER_GATEWAY_CONFIG=unused; NODE_DEPLOY_CHECKOUT=unused
NODE_LIBEXEC_DIR=unused; NODE_SKILL_STORE=unused; NODE_RELEASE_STORE=unused
NODE_PRIVATE_ROOT={tmp_path}
HEALTHCHECK_SERVICES=core
log() {{ printf '%s\\n' "$*"; }}
probe_user_unit_active() {{ exit 97; }}
probe_peer_ignored_channels() {{ exit 97; }}
source {REPO}/automation/healthcheck_registry.sh
run_check 'gateway|user_unit_active|fake|peer|hermes-gateway.service'
run_check 'peer|peer_ignored_channels|fake|ops|unused'
"""
    result = subprocess.run(("bash", "-c", script), text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("퇴역(기대)") == 2


def test_p6_and_p5_plan_selects_retirement_note_on_the_same_card(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from automation import release_notes, release_plan
    from tests.unit.test_release_notes import _plan

    plan = _plan((), signals=("automation/install/profiles.py:DEFAULT_PROFILE",))
    monkeypatch.setattr(release_plan, "build_plan", lambda *args, **kwargs: plan)
    args = argparse.Namespace(repo=".", base="e" * 40, head="a" * 40,
                              version="v2.0.0", bump="major", healthcheck_wrapper=None,
                              peer_retired="retired", hermes_gateways="stopped")
    assert release_approval.cmd_plan(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert list(peer_retired.surface()) in payload["surface_digests"]
    assert payload["major_note"] == release_notes.major_note(plan, p5_retired=True)
    assert payload["major_note"] != release_notes.major_note(plan)
    assert "\n" not in payload["major_note"]


@pytest.mark.parametrize("version", [1, 2])
def test_only_complete_v2_retirement_receipt_preserves_no_gateway_policy(
    tmp_path: Path, version: int,
) -> None:
    from automation.hermes_gateways import stopped_in_receipt

    path = tmp_path / "receipt.json"
    path.write_text(json.dumps({
        "version": version, "release_sha": "a" * 40, "hermes_gateways": "retired",
        "peer_retired": "retired", "surfaces": {"gateway_services": {}},
    }))
    if version == 1:
        with pytest.raises(ValueError, match="incomplete retirement receipt"):
            stopped_in_receipt(path, ("agent", "peer"))
    else:
        assert stopped_in_receipt(path, ("agent", "peer"))


def test_cli_accepts_real_unmounted_skill_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".hermes/skills").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setattr(sys, "argv", ["p5-remove", "--role", "agent", "--head", "a" * 40])
    invoked: list[Path] = []
    monkeypatch.setattr(p5_remove, "remove", lambda home, *args: invoked.append(home) or tmp_path)
    # util-linux mountpoint returns 32 for an ordinary directory, not 1.
    assert p5_remove.main() == 0
    assert invoked == [tmp_path]


def test_retired_home_backs_up_and_removes_paused_jobs_a_deployer_recreated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a completed retirement, then a deployer re-registers two paused Hermes jobs.
    home, current = _home(tmp_path, "agent")
    calls: list[tuple[str, ...]] = []
    _commands(monkeypatch, current, calls)
    folder = p5_remove.remove(home, "agent", "a" * 40, "hermes", "systemctl")
    current.update({"aaaaaaaaaaaa": "mail-triage-watch", "bbbbbbbbbbbb": "mail-daily-digest"})
    # When: the next release runs the retirement step again.
    assert p5_remove.remove(home, "agent", "b" * 40, "hermes", "systemctl") == folder
    # Then: the late jobs were backed up before removal and none remain.
    late = home / ".hermes/retired/p5" / f"{'b' * 40}-late"
    assert not current
    assert set(json.loads((late / "job-ids.json").read_text())) == {"aaaaaaaaaaaa", "bbbbbbbbbbbb"}
    assert "mail-daily-digest" in (late / "cron-list.txt").read_text()
    assert all((late / name).stat().st_mode & 0o777 == 0o600 for name in ("cron-list.txt", "job-ids.json", "jobs.json"))
