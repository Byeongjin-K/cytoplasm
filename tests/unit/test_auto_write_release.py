"""Per-kind switch binding, owner authorization and reversible node settings."""
from __future__ import annotations

import json
import subprocess
from dataclasses import replace

import pytest

from automation import auto_write, auto_write_release, release_approval
from tests.unit import test_release_authorize as auth
from tests.unit.test_healthcheck_wrapper_release import _fake_plan, _plan_args, _spec


def test_plan_and_card_bind_enabled_kind(monkeypatch, capsys):
    # Given: the ordinary release planner plus the Plaud opt-in.
    _fake_plan(monkeypatch)
    args = _plan_args()
    args.auto_write = ["plaud"]
    # When: a release plan and its card are generated.
    assert release_approval.cmd_plan(args) == 0
    plan = json.loads(capsys.readouterr().out)
    name, digest = auto_write.surface("plaud")
    # Then: the release hash covers this policy and the card exposes its consequence.
    assert [name, digest] in plan["surface_digests"]
    assert auto_write.CARD_LINES["plaud"] in _spec((name, digest)).render()
    assert auto_write.CARD_LINES["plaud"] not in _spec().render()
    assert _spec((name, digest)).action_hash() != _spec().action_hash()


@pytest.mark.parametrize("reaction,covered,head,expected", [
    ("approve", True, "a" * 40, 0), ("pending", True, "a" * 40, 1),
    ("both", True, "a" * 40, 8), ("approve", False, "a" * 40, 4),
    ("approve", True, "b" * 40, 4),
])
def test_auto_surface_uses_existing_owner_gate(tmp_path, monkeypatch, reaction, covered, head, expected):
    # Given: the existing owner gate, with or without this release's auto-write effect.
    base = auth._spec()
    spec = replace(base, surface_digests=(*base.surface_digests,
                                        *([auto_write.surface("plaud")] if covered else [])))
    monkeypatch.setattr(auth, "_spec", lambda: spec)
    owner = [{"id": auth._OWNER, "bot": False}]
    auth._env(tmp_path, monkeypatch, reactions={
        auth._APPROVE: owner if reaction in {"approve", "both"} else [],
        auth._CANCEL: owner if reaction == "both" else [],
    }, render_version=6)
    # When / Then: only the matching HEAD's owner-approved surface authorizes.
    assert release_approval.main(["authorize-auto-write", "--kind", "plaud", "--head", head]) == expected


@pytest.mark.parametrize("approved", [True, False])
def test_deploy_authorizes_before_writing(tmp_path, monkeypatch, approved):
    # Given: an instrumented transport, not a node.
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0 if approved else 4)
    monkeypatch.setattr(auto_write_release.subprocess, "run", run)
    # When: deployment is attempted.
    result = auto_write_release.deploy(tmp_path, "test-node", "/srv/runtime", "a" * 40, ("plaud",))
    # Then: denial performs no SSH write.
    assert result == (0 if approved else 4)
    assert "authorize-auto-write" in calls[0]
    assert len(calls) == (2 if approved else 1)


def test_install_and_remove_setting_are_private_and_reversible(tmp_path):
    # Given: no manual node setup.
    assert not auto_write.enabled("plaud", home=tmp_path)
    # When: the approved release opts in and its successor removes the flag.
    auto_write_release.install(("plaud",), tmp_path)
    assert auto_write.enabled("plaud", home=tmp_path)
    assert (auto_write.state_root(tmp_path) / "plaud.mode").stat().st_mode & 0o777 == 0o600
    auto_write_release.install((), tmp_path)
    # Then: the old card flow is restored by absence.
    assert not auto_write.enabled("plaud", home=tmp_path)


def test_deploy_restore_never_reads_a_source_or_requests_approval(tmp_path, monkeypatch):
    # Given: only the removal operation.
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0)
    monkeypatch.setattr(auto_write_release.subprocess, "run", run)
    # When / Then: restore does one setting removal, not a new opt-in.
    assert auto_write_release.deploy(tmp_path, "test-node", "/srv/runtime", "a" * 40, ()) == 0
    assert len(calls) == 1 and calls[0][0] == "ssh"


def test_summary_timer_uses_account_timezone():
    # Given: the declared shared job and the same P2 renderer as other daily timers.
    from automation.deploy_declarations import all_declarations
    from automation.cron_timer import Job, render
    from pathlib import Path
    declarations = all_declarations(Path(__file__).resolve().parents[2])
    row, = [r for r in declarations if r.destination == "auto-write-daily-summary"]
    # When: the KST account renders the timer.
    _, timer = render(Job(row.destination, row.attr("schedule"), row.attr("script")),
                      root="/srv/runtime", timezone="Asia/Seoul")
    # Then: 21:30 KST is not interpreted as the node's UTC clock.
    assert row.attr("runner") == "systemd"
    assert "OnCalendar=*-*-* 21:30:00 Asia/Seoul" in timer
