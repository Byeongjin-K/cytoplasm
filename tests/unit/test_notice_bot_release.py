"""Exercise authorization, secret transport and the release card with synthetic credentials."""
from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from hashlib import sha256
from types import SimpleNamespace

import pytest

from automation import notice_bot, notice_bot_release as release, release_approval
from tests.unit import test_release_authorize as auth
from tests.unit.test_healthcheck_wrapper_release import _fake_plan, _plan_args, _spec
from tests.unit.test_notice_bot import CHANNEL, VALUE
from tests.unit.test_yoodori_override_sandbox import _visible

DIGEST = sha256(VALUE.encode()).hexdigest()


@pytest.mark.parametrize("value", [b"", b"yoodori\n", b"YOODORI", b" yoodori"])
def test_switch_refuses_nonliteral_values(tmp_path, value):
    flag = tmp_path / ".hermes/release-flags/notice-bot"
    flag.parent.mkdir(parents=True)
    flag.write_bytes(value)
    with pytest.raises(ValueError):
        release.release_switch(tmp_path)


def test_absent_switch_does_not_read_secret(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("absent switch must not read a credential")
    monkeypatch.setattr(release, "source_token", forbidden)
    assert release.switch_digest(tmp_path) == "off"


def test_source_stays_in_capture_not_command_or_log(tmp_path, monkeypatch, capsys):
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, f"DORI_DISCORD_TOKEN='{VALUE}'\n", "")
    monkeypatch.setattr(release.subprocess, "run", run)
    assert release.source_token(tmp_path) == VALUE
    assert VALUE not in repr(calls)
    assert capsys.readouterr().out == ""


def test_plan_and_card_bind_only_digest(monkeypatch, capsys):
    _fake_plan(monkeypatch)
    args = _plan_args()
    args.notice_bot, args.hermes_gateways, args.yoodori_channel = DIGEST, "stopped", CHANNEL
    assert release_approval.cmd_plan(args) == 0
    output = capsys.readouterr().out
    assert [notice_bot.SURFACE, DIGEST] in json.loads(output)["surface_digests"]
    spec = _spec((notice_bot.SURFACE, DIGEST))
    assert notice_bot.CARD_LINE in spec.render()
    assert VALUE not in output + spec.render()
    assert spec.action_hash() != _spec().action_hash()


@pytest.mark.parametrize("reaction,covered,head,expected", [
    ("approve", True, "a" * 40, 0), ("pending", True, "a" * 40, 1),
    ("both", True, "a" * 40, 8), ("approve", False, "a" * 40, 4),
    ("approve", True, "b" * 40, 4),
])
def test_notice_surface_uses_existing_owner_gate(tmp_path, monkeypatch, reaction, covered, head, expected):
    base = auth._spec()
    spec = replace(base, surface_digests=(*base.surface_digests,
                                        *([(notice_bot.SURFACE, DIGEST)] if covered else [])))
    monkeypatch.setattr(auth, "_spec", lambda: spec)
    owner = [{"id": auth._OWNER, "bot": False}]
    auth._env(tmp_path, monkeypatch, reactions={
        auth._APPROVE: owner if reaction in {"approve", "both"} else [],
        auth._CANCEL: owner if reaction == "both" else [],
    }, render_version=6)
    assert release_approval.main(["authorize-notice-bot", "--hash", DIGEST, "--head", head]) == expected


@pytest.mark.parametrize("authorized", [True, False])
def test_deploy_authorizes_before_stdin_only_writes(tmp_path, monkeypatch, capsys, authorized):
    monkeypatch.setattr(release, "source_token", lambda home: VALUE)
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        assert VALUE not in repr(argv)
        code = 0 if argv[0] == "ssh" or authorized else 4
        return subprocess.CompletedProcess(argv, code, "", "")
    monkeypatch.setattr(release.subprocess, "run", run)
    assert release.deploy(tmp_path, "test-node", "/srv/runtime", "a" * 40, DIGEST, CHANNEL) == (0 if authorized else 4)
    assert "authorize-notice-bot" in calls[0][0]
    writes = calls[1:]
    assert len(writes) == (3 if authorized else 0)
    for argv, kwargs in writes:
        assert argv[0] == "ssh"
        assert json.loads(kwargs["input"]) == {"token": VALUE, "channel_id": CHANNEL}
    captured = capsys.readouterr()
    assert VALUE not in captured.out + captured.err


def test_changed_secret_never_reaches_node(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "source_token", lambda home: "changed synthetic credential")
    monkeypatch.setattr(release.subprocess, "run", lambda *a, **k: pytest.fail("must refuse before SSH"))
    with pytest.raises(ValueError):
        release.deploy(tmp_path, "test-node", "/srv/runtime", "a" * 40, DIGEST, CHANNEL)


def test_ops_override_is_visible_in_real_repair_unit_sandboxes(tmp_path, monkeypatch):
    import io

    home, private = tmp_path / "home/ops", tmp_path / "private"
    home.mkdir(parents=True)
    config = tmp_path / "node.toml"
    config.write_text(f"ops_home = {json.dumps(str(home))}\nprivate_root = {json.dumps(str(private))}\n")
    monkeypatch.setenv("HEALTHCHECK_NODE_CONFIG_PATH", str(config))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"token": VALUE, "channel_id": CHANNEL})))
    assert notice_bot.main(["install", "--digest", DIGEST, "--channel", CHANNEL]) == 0
    target = private / "notice-bot.json"
    _visible(target, home)
    assert target.stat().st_mode & 0o777 == 0o600
    assert notice_bot.resolve_token("old ops credential") == VALUE
    assert not (home / notice_bot.OVERRIDE).exists()


def test_restore_uses_no_source_or_authorize_call(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "source_token", lambda home: pytest.fail("restore must not read source"))
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(release.subprocess, "run", run)
    assert release.deploy(tmp_path, "test-node", "/srv/runtime", "a" * 40, "off", "off") == 0
    assert len(calls) == 3
    assert all(argv[0] == "ssh" and kwargs["input"] == "" for argv, kwargs in calls)
