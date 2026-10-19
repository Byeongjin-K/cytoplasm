"""Account-specific overrides and the real release-host -> node deployment boundary."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import io
from dataclasses import replace
from pathlib import Path
from urllib.error import HTTPError

import pytest

from automation import deploy_all_config, node_config, release_approval, yoodori_channel as move
from automation.interop import approval_directory as directory, digest_thread
from automation.owner_notice import owner_notice_channel
from automation.release_spec import yoodori_channel_surface
from automation.deploy_declarations import all_declarations
from tests.unit import test_release_authorize as auth
from tests.unit.test_agent_chat_channel_move import NEW, OLD
from tests.unit.test_dori_deploy_switch import HEAD, REPO, _rig
from tests.unit.test_dori_switch_node_probe import _plan

NOTICE = "444444444444444444"


def _node_config(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = home.parent / f"{home.name}-node.toml"
    path.write_text(
        f"private_root = {json.dumps(str(home.parent / (home.name + '-private')))}\n"
        f"ops_home = {json.dumps(str(home))}\n",
    )
    monkeypatch.setenv("HEALTHCHECK_NODE_CONFIG_PATH", str(path))


def _override(home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _node_config(home, monkeypatch)
    path = directory.channel_override_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({move.CHAT: NEW, move.NOTICE: NEW}))
    path.chmod(0o600)
    return path


@pytest.mark.parametrize("case", ["valid", "absent", "json", "mode", "owner", "short", "symlink"])
def test_override_precedes_root_config_and_env_only_when_trusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], case: str,
) -> None:
    # Given
    monkeypatch.setenv("HOME", str(tmp_path))
    root_config = tmp_path / "root-config.json"
    root_config.write_text(json.dumps({move.CHAT: OLD}))
    monkeypatch.setenv("INTEROP_CONFIG", str(root_config))
    monkeypatch.setenv("OWNER_NOTICE_CHANNEL_ID", NOTICE)
    path = _override(tmp_path, monkeypatch)
    if case == "absent":
        path.unlink()
    elif case == "json":
        path.write_text("private malformed text")
    elif case == "mode":
        path.chmod(0o644)
    elif case == "owner":
        monkeypatch.setattr(directory.os, "geteuid", lambda: path.stat().st_uid + 1)
    elif case == "short":
        path.write_text(json.dumps({move.CHAT: "123", move.NOTICE: NEW}))
    elif case == "symlink":
        target = path.with_suffix(".target")
        path.rename(target)
        path.symlink_to(target)
    # When
    chat = directory._configured_agent_chat_channel()
    notice = owner_notice_channel()
    # Then
    assert (chat, notice) == ((NEW, NEW) if case == "valid" else (OLD, NOTICE))
    stderr = capsys.readouterr().err
    assert bool(stderr) == (case not in {"valid", "absent"})
    assert "private" not in stderr and OLD not in stderr and NOTICE not in stderr


@pytest.mark.parametrize("role", ["agent", "peer", "ops"])
def test_declared_channel_writer_moves_and_restores_both_accounts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, role: str,
) -> None:
    # Given
    home = tmp_path / role
    path = home / ".hermes/interop/config.json"
    path.parent.mkdir(parents=True)
    original = f'{{ "agent_chat_channel_id": "{OLD}", "owner_notice_channel_id": "{NOTICE}", "keep": [1,  2] }}\n'
    path.write_text(original)
    monkeypatch.setenv("INTEROP_CONFIG", str(path))
    monkeypatch.setenv("OWNER_NOTICE_CHANNEL_ID", NOTICE)
    _node_config(home, monkeypatch)
    args = ["--role", role, "--yoodori-channel", NEW]
    # When / Then
    assert move.main(["--apply", *args], home=home) == 0
    target = directory.channel_override_path() if role == "ops" else path
    data = json.loads(target.read_text())
    assert data[move.NOTICE] == NEW
    assert data[move.CHAT] == (OLD if role == "peer" else NEW)
    assert data[move.NOTICE + move.BEFORE] == NOTICE
    assert target.stat().st_mode & 0o777 == 0o600
    assert move.main(["--check", *args], home=home) == 0
    assert move.main(["--apply", *args], home=home) == 0
    assert len(list(target.parent.glob(target.name + ".bak-*"))) == (0 if role == "ops" else 1)
    assert move.main(["--apply", "--role", role, "--yoodori-channel", "off"], home=home) == 0
    assert path.read_text() == original
    assert role != "ops" or not target.exists()


@pytest.mark.parametrize("mode,expected", [(NEW, "stale"), ("off", "ok")])
def test_node_observation_uses_host_value_with_empty_node_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, expected: str,
) -> None:
    # Given
    monkeypatch.setenv("HOME", str(tmp_path))
    row = next(d for d in all_declarations(REPO) if d.account == "ops" and d.destination == move.OVERRIDE)
    def runner(account, args):
        assert account == "ops"
        assert args[-4:] == ("--yoodori-channel", mode, "--role", "ops")
        return 0, f"{move.MARKER} have=yoodori_channel=off"
    # When
    result = deploy_all_config.observe_config(row, REPO, runner, dori_mode="off", chat_channel=mode)
    # Then
    assert result[2] == expected


def test_deploy_all_hands_yoodori_channel_to_node_probe(tmp_path: Path) -> None:
    # Given
    home = tmp_path / "host"
    flag = home / ".hermes/release-flags/yoodori-channel"
    flag.parent.mkdir(parents=True)
    flag.write_text(NEW)
    # When
    calls = _plan(tmp_path, home)
    # Then
    assert f"--yoodori-channel {NEW}" in calls


@pytest.mark.parametrize("value,auth_rc,expected", [(NEW, 0, 0), (NEW, 4, 4), (NEW + "\n", 0, 4)])
def test_release_authorization_precedes_every_channel_write(
    tmp_path: Path, value: str, auth_rc: int, expected: int,
) -> None:
    # Given
    repo, home, env = _rig(tmp_path, None, 97)
    env["INTEROP_CONFIG"] = str(tmp_path / "interop-ops.json")  # the node's real one is root:ops 0640
    flag = home / ".hermes/release-flags/yoodori-channel"
    flag.parent.mkdir(parents=True)
    flag.write_text(value)
    authorize = repo / "automation/release_approval_remote.sh"
    authorize.write_text(
        '#!/usr/bin/env bash\nprintf "authorize %s\\n" "$*" >> "$CALLS"\n'
        f'[[ "$*" == "authorize-yoodori-channel --channel-id {NEW} --head {HEAD}" ]] || exit 97\n'
        f"exit {auth_rc}\n",
    )
    path = tmp_path / "node/agent/.hermes/interop/config.json"
    before = path.read_bytes()
    # When
    result = subprocess.run(
        ["bash", str(repo / "automation/memory_curator/deploy.sh")], env=env,
        capture_output=True, text=True, timeout=30, check=False,
    )
    # Then
    assert result.returncode == expected, result.stdout + result.stderr
    calls = (tmp_path / "calls").read_text() if (tmp_path / "calls").exists() else ""
    if expected:
        assert path.read_bytes() == before
        assert "push" not in calls
        if "\n" in value:
            assert not calls
    else:
        assert json.loads(path.read_text())[move.CHAT] == NEW
        assert json.loads((tmp_path / "node/peer/.hermes/interop/config.json").read_text())[move.NOTICE] == NEW
        private = node_config.load_node_config(Path(env["HEALTHCHECK_NODE_CONFIG_PATH"])).private_root
        assert (private / "channel-override.json").exists()
        flag.unlink()
        restored = subprocess.run(
            ["bash", str(repo / "automation/memory_curator/deploy.sh")], env=env,
            capture_output=True, text=True, timeout=30, check=False,
        )
        assert restored.returncode == 0, restored.stdout + restored.stderr
        assert path.read_bytes() == before
        assert not (private / "channel-override.json").exists()
        assert NEW not in (tmp_path / "node/agent/.hermes/config.yaml").read_text()


@pytest.mark.parametrize("reaction,covered,expected", [
    ("approve", True, 0), ("pending", True, 1), ("both", True, 8), ("approve", False, 4),
])
def test_yoodori_surface_uses_existing_owner_authorization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reaction: str, covered: bool, expected: int,
) -> None:
    # Given
    base = auth._spec()
    spec = replace(base, surface_digests=(*base.surface_digests, *([yoodori_channel_surface(NEW)] if covered else [])))
    monkeypatch.setattr(auth, "_spec", lambda: spec)
    owner = [{"id": auth._OWNER, "bot": False}]
    auth._env(tmp_path, monkeypatch, reactions={
        auth._APPROVE: owner if reaction in {"approve", "both"} else [],
        auth._CANCEL: owner if reaction == "both" else [],
    }, api_log=[], render_version=6)
    # When / Then
    assert release_approval.cmd_authorize_yoodori_channel(argparse.Namespace(channel_id=NEW, head="a" * 40)) == expected


def test_deleted_digest_thread_settle_is_quiet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given
    monkeypatch.setenv(digest_thread.STATE_ENV, str(tmp_path))
    digest_thread.open_thread("2026-10-17", lambda: digest_thread.DigestThread(
        "2026-10-17", "321", "agent-chat-thread", 8, None, "digest",
    ))
    digest_thread.open_thread("2026-10-18", lambda: digest_thread.DigestThread(
        "2026-10-18", "322", "agent-chat-thread", 8, None, "digest",
    ))
    def missing(*_args):
        raise HTTPError("https://example.invalid", 404, "Unknown Channel", {}, None)
    # When
    digest_thread.settle({"321": 0}, missing)
    # Then
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("actual,expected", [("111", 0), ("222", 3)])
def test_release_writer_validates_configured_owner_guild_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, actual: str, expected: int,
) -> None:
    # Given
    path = tmp_path / ".hermes/interop/config.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({move.CHAT: OLD, move.NOTICE: NOTICE, "owner_guild_id": "111"}))
    before = path.read_bytes()
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-credential")
    calls = []
    def get(request, **_kwargs):
        assert request.get_method() == "GET"
        calls.append(request.full_url)
        return io.BytesIO(json.dumps({"guild_id": actual, "type": 0}).encode())
    monkeypatch.setattr("urllib.request.urlopen", get)
    # When
    code = move.main(["--apply", "--yoodori-channel", NEW], home=tmp_path)
    # Then
    assert code == expected
    assert len(calls) == 1
    assert (path.read_bytes() == before) == bool(expected)


def test_override_status_is_pass_and_absence_is_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given / When / Then
    _node_config(tmp_path, monkeypatch)
    assert move.main(["--override-status"], home=tmp_path) == 0
    assert capsys.readouterr().out == ""
    _override(tmp_path, monkeypatch)
    assert move.main(["--override-status"], home=tmp_path) == 0
    result = capsys.readouterr()
    assert result.out.startswith("[PASS]")
    assert result.err == ""


def test_doctor_and_healthcheck_report_override_as_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    from automation.doctor.capabilities import evaluate
    from automation.install.checks import Status
    from tests.unit.test_doctor import _connected_home, _facts
    from tests.unit.test_healthcheck_owner_notice_probe import _FILLED

    home = _connected_home(tmp_path)
    _override(home, monkeypatch)
    credential = tmp_path / "notice.env"
    credential.write_text(_FILLED)
    monkeypatch.setenv("HOME", str(home))
    # When
    findings = evaluate(_facts(home))
    check = subprocess.run(
        ["bash", "-c", 'source "$1"; probe_owner_notice_credentials primary ops "$2"',
         "_", str(REPO / "automation/owner_notice_probe.sh"), str(credential)],
        env=dict(os.environ), capture_output=True, text=True, check=False, timeout=15,
    )
    # Then
    found = next(f for f in findings if f.key == "channel-override")
    assert found.status is Status.PASS and not found.steps
    assert check.returncode == 0
    assert check.stdout.count("[PASS]") == 1
    assert check.stderr == ""
