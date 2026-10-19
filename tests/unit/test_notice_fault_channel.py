"""Fault routing regressions live separately from FS3-pinned test files."""
from __future__ import annotations

import io
import json
import subprocess
from dataclasses import replace
from hashlib import sha256

import pytest

from automation import cron_timer_run, healthcheck_notify, notice_bot, notice_bot_release
from automation import owner_notice, release_approval
from automation.cron_timer import Job
from automation.doctor import alarm
from tests.unit import test_release_authorize as auth
from tests.unit.test_healthcheck_wrapper_release import _fake_plan, _plan_args, _spec

OWNER = "444444444444444444"
PAIR = "555555555555555555"
TOKEN = "synthetic fault notice credential"
DIGEST = sha256(TOKEN.encode()).hexdigest()


@pytest.fixture
def routing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = tmp_path / notice_bot.OVERRIDE
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"token": TOKEN, "channel_id": OWNER, "fault_channel_id": PAIR}))
    path.chmod(0o600)
    return tmp_path


def test_fault_override_preserves_owner_target_and_sender(routing):
    # Given/When: the release-installed optional destination is read.
    assert notice_bot.fault_channel(routing) == PAIR
    # Then: ordinary notices retain their destination and sender.
    assert notice_bot.notice_channel(routing) == OWNER
    assert notice_bot.resolve_token(home=routing) == TOKEN


@pytest.mark.parametrize("fault", [PAIR, None])
@pytest.mark.parametrize("rc", [0, 1])
def test_timer_failure_uses_pair_but_success_keeps_owner(routing, fault, rc):
    # Given: an installation with or without fault routing.
    path = routing / notice_bot.OVERRIDE
    payload = {"token": TOKEN, "channel_id": OWNER}
    if fault:
        payload["fault_channel_id"] = fault
    path.write_text(json.dumps(payload))
    sent = []
    # When: a job delivers its result.
    cron_timer_run.deliver(
        routing, Job("sample", "* * * * *", "sample.py", (), "discord"), rc, "sample",
        send=lambda *args: sent.append(args),
    )
    # Then: only failures take the fault destination.
    assert sent[0][:2] == (TOKEN, PAIR if rc and fault else OWNER)


@pytest.mark.parametrize("caller", ["healthcheck", "doctor", "attachment"])
@pytest.mark.parametrize("configured", [True, False])
def test_fault_producers_use_pair_with_legacy_fallback(routing, monkeypatch, caller, configured):
    # Given: the real notice facade, with only its network send intercepted.
    if not configured:
        (routing / notice_bot.OVERRIDE).write_text(json.dumps({"token": TOKEN, "channel_id": OWNER}))
    sent = []
    monkeypatch.setattr(owner_notice, "send_notice", lambda *args: sent.append(args))
    monkeypatch.setenv("HEALTHCHECK_NOTIFY_STATE", str(routing / "health/state.json"))
    # When: a fault producer emits one incident.
    if caller == "healthcheck":
        monkeypatch.setattr(healthcheck_notify, "SUSTAIN_SECONDS", 0.0)  # wait pinned in test_q281_alerts.py
        healthcheck_notify.main(["synthetic-fault|local|node|account|target"])
    elif caller == "attachment":
        from tests.unit.test_mail_attachment_retry import syncer

        assert syncer.retry.notify_exhausted("synthetic-fault")
    else:
        assert alarm._send("synthetic-fault", None)
    # Then: a configured pair receives the fault with the YooDori credential.
    assert sent[0][:2] == (TOKEN, PAIR if configured else OWNER)


def test_install_and_readback_include_fault_destination(routing, monkeypatch):
    # Given: an approved payload and explicit expected pair id.
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({
        "token": TOKEN, "channel_id": OWNER, "fault_channel_id": PAIR,
    })))
    # When: the node installs the extension of its existing override.
    assert notice_bot.main(["install", "--digest", DIGEST, "--channel", OWNER,
                            "--fault-channel", PAIR]) == 0
    # Then: a wrong destination cannot obtain a clean readback.
    assert notice_bot.installed(DIGEST, OWNER, PAIR)
    assert not notice_bot.installed(DIGEST, OWNER, OWNER)


def test_removing_fault_target_restores_owner_without_changing_sender(routing, monkeypatch):
    # Given: a node with fault routing that receives an approved restore payload.
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"token": TOKEN, "channel_id": OWNER})))
    # When: the same notice-bot override is redeployed without the optional field.
    notice_bot.install(DIGEST, OWNER)
    # Then: faults use the legacy channel again, with no credential change.
    assert notice_bot.fault_channel(routing) is None
    assert notice_bot.notice_channel(routing) == OWNER
    assert notice_bot.resolve_token(home=routing) == TOKEN


def test_release_plan_binds_fault_destination_and_one_card_line(monkeypatch, capsys):
    # Given: the unchanged base release plan.
    _fake_plan(monkeypatch)
    args = _plan_args()
    assert release_approval.cmd_plan(args) == 0
    before = json.loads(capsys.readouterr().out)
    args.notice_bot, args.hermes_gateways, args.yoodori_channel = DIGEST, "stopped", OWNER
    assert release_approval.cmd_plan(args) == 0
    baseline = json.loads(capsys.readouterr().out)
    args.fault_channel = PAIR
    # When: the optional host flag is included.
    assert release_approval.cmd_plan(args) == 0
    after = json.loads(capsys.readouterr().out)
    # Then: the pair id is hash-bound and contributes exactly one approval line.
    surface = ("fault-channel:" + PAIR, sha256(PAIR.encode()).hexdigest())
    assert list(surface) in after["surface_digests"]
    spec = replace(_spec(), surface_digests=tuple(map(tuple, after["surface_digests"])))
    old = replace(spec, surface_digests=tuple(map(tuple, baseline["surface_digests"])))
    assert spec.action_hash() != old.action_hash()
    assert spec.render().count("고장 알림은 #dori-pair 로(유돌 먼저)") == 1
    assert "fault-channel:" not in json.dumps(before)


def test_absent_fault_flag_preserves_previous_card_and_hash(tmp_path):
    # Given: literal hashes of the HEAD fixture, before fault routing existed.
    assert notice_bot_release.fault_switch(tmp_path) == "off"
    # When/Then: an unchanged release keeps both approval and displayed bytes.
    assert _spec().action_hash() == "fb17cdbb60ccb11840684527a1daeffc94c7b69b1e6035c10662c7f7143b6dfc"
    assert sha256(_spec().render().encode()).hexdigest() == (
        "a3d23a31938565414513f51e72115cf36b2161c618223bcdf56b3d279127478d"
    )


@pytest.mark.parametrize("value", ["", PAIR + "\n", "off", "x" * 18])
def test_fault_release_flag_refuses_nonliteral_ids(tmp_path, value):
    flag = tmp_path / ".hermes/release-flags/fault-channel"
    flag.parent.mkdir(parents=True)
    flag.write_text(value)
    with pytest.raises(ValueError):
        notice_bot_release.fault_switch(tmp_path)


def test_fault_release_transport_authorizes_before_writing(tmp_path, monkeypatch):
    # Given: valid sender and approval seam.
    monkeypatch.setattr(notice_bot_release, "source_token", lambda home: TOKEN)
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(notice_bot_release.subprocess, "run", run)
    # When: the release installs the optional fault target.
    assert notice_bot_release.deploy(tmp_path, "node", "/runtime", "a" * 40,
                                     DIGEST, OWNER, PAIR) == 0
    # Then: both authorizations precede all writes and the node receives the target.
    assert [call[0][1] for call in calls[:2]] == ["authorize-notice-bot", "authorize-fault-channel"]
    assert len(calls[2:]) == 3
    assert all(json.loads(kwargs["input"])["fault_channel_id"] == PAIR for _, kwargs in calls[2:])


@pytest.mark.parametrize("reaction,channel,head,expected", [
    ("approve", PAIR, "a" * 40, 0),
    ("pending", PAIR, "a" * 40, 1),
    ("both", PAIR, "a" * 40, 8),
    ("approve", OWNER, "a" * 40, 4),
    ("approve", PAIR, "b" * 40, 4),
])
def test_fault_surface_requires_owner_approval_for_exact_channel_and_head(
    tmp_path, monkeypatch, reaction, channel, head, expected,
):
    # Given: a stored release that binds exactly one pair destination.
    base = auth._spec()
    surface = ("fault-channel:" + PAIR, sha256(PAIR.encode()).hexdigest())
    spec = replace(base, surface_digests=(*base.surface_digests, surface))
    monkeypatch.setattr(auth, "_spec", lambda: spec)
    owner = [{"id": auth._OWNER, "bot": False}]
    auth._env(tmp_path, monkeypatch, reactions={
        auth._APPROVE: owner if reaction in {"approve", "both"} else [],
        auth._CANCEL: owner if reaction == "both" else [],
    }, render_version=6)
    # When/Then: only the owner's single approval authorizes this destination.
    assert release_approval.main([
        "authorize-fault-channel", "--channel-id", channel, "--head", head,
    ]) == expected
