"""Separate owner-off gate coverage because FS3 pins existing test file outputs."""
import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from automation import cron_timer_owner_off as owner, release_approval
from automation.cron_timer import TimerError
from tests.unit import test_release_authorize as auth
from tests.unit.test_healthcheck_wrapper_release import _fake_plan, _plan_args, _spec

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("raw", [
    b"", b"budget-watch", b"budget-watch\r\n", b" budget-watch\n", b"budget-watch\n\n",
    b"budget-watch\nbudget-watch\n", b"mail-attachment-drive-watch\nbudget-watch\n",
    b"../job\n", b"budget_watch\n", b"budget-watch\xff\n",
])
def test_switch_refuses_malformed_flag_when_present(tmp_path, raw):
    # Given: an exact-bytes release-host file with malformed contents.
    flag = tmp_path / owner.FLAG
    flag.parent.mkdir(parents=True)
    flag.write_bytes(raw)
    # When / Then: parsing refuses instead of silently dropping unsafe names.
    with pytest.raises(TimerError):
        owner.release_names(tmp_path, REPO)


@pytest.mark.parametrize("name", ["missing-job", "memory-curator-watch",
                                  "memory-relocate-watch"])
def test_switch_refuses_unsupported_jobs_when_not_eligible(tmp_path, name):
    # Given: a name outside the independent owner-off contract.
    flag = tmp_path / owner.FLAG
    flag.parent.mkdir(parents=True)
    flag.write_bytes(f"{name}\n".encode())
    # When / Then: unknown jobs and the guarded memory switch cannot be bypassed.
    with pytest.raises(TimerError):
        owner.release_names(tmp_path, REPO)


def test_switch_preserves_absent_behavior_and_parses_valid_selection(tmp_path):
    # Given: no host flag.
    assert owner.release_names(tmp_path, REPO) == ()
    flag = tmp_path / owner.FLAG
    flag.parent.mkdir(parents=True)
    flag.write_bytes(b"budget-watch\nmail-attachment-drive-watch\n")
    # When: the release host reads its strict file.
    names = owner.release_names(tmp_path, REPO)
    # Then: both jobs survive canonical transport.
    assert names == ("budget-watch", "mail-attachment-drive-watch")
    assert owner.selected(",".join(names)) == names


def test_plan_binds_each_selected_timer_when_release_is_built(monkeypatch, capsys):
    # Given: a deterministic release plan.
    _fake_plan(monkeypatch)
    args = _plan_args()
    args.repo = str(REPO)
    args.cron_timers_off = "budget-watch,mail-attachment-drive-watch"
    # When: the release producer constructs its payload.
    assert release_approval.cmd_plan(args) == 0
    payload = json.loads(capsys.readouterr().out)
    # Then: every selected job changes the release's approval hash.
    surfaces = tuple(owner.surface(name) for name in owner.selected(args.cron_timers_off))
    assert all(list(surface) in payload["surface_digests"] for surface in surfaces)
    assert len({_spec().action_hash(), _spec(*surfaces).action_hash(),
                _spec(surfaces[0]).action_hash()}) == 3


@pytest.mark.parametrize("reaction,covered,head,name,expected", [
    ("approve", True, "a" * 40, "budget-watch", 0),
    ("pending", True, "a" * 40, "budget-watch", 1),
    ("both", True, "a" * 40, "budget-watch", 8),
    ("approve", False, "a" * 40, "budget-watch", 4),
    ("approve", True, "b" * 40, "budget-watch", 4),
    ("approve", True, "a" * 40, "mail-attachment-drive-watch", 4),
])
def test_gate_refuses_unapproved_stop_when_owner_head_or_job_differs(
    tmp_path, monkeypatch, reaction, covered, head, name, expected,
):
    # Given: the real release gate with local Discord protocol fixtures only.
    base = auth._spec()
    spec = replace(base, surface_digests=(
        *base.surface_digests, *([owner.surface("budget-watch")] if covered else []),
    ))
    monkeypatch.setattr(auth, "_spec", lambda: spec)
    users = [{"id": auth._OWNER, "bot": False}]
    auth._env(tmp_path, monkeypatch, reactions={
        auth._APPROVE: users if reaction in {"approve", "both"} else [],
        auth._CANCEL: users if reaction == "both" else [],
    }, render_version=6)
    # When: deployment requests authorization for this exact job and HEAD.
    result = release_approval.main(["authorize-cron-timer", "--name", name, "--head", head])
    # Then: only the covered owner-approved operation is authorized.
    assert result == expected


def test_deploy_refuses_before_node_write_when_any_selected_stop_is_unapproved(tmp_path, monkeypatch):
    # Given: the first job is approved but the second is not.
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        code = 4 if "--name" in argv and argv[argv.index("--name") + 1] == "mail-attachment-drive-watch" else 0
        return subprocess.CompletedProcess(argv, code, f"/srv/releases/{'a' * 40}\n", "")

    monkeypatch.setattr(owner.subprocess, "run", run)
    target = owner.ReleaseTarget(REPO, "example-node", "/srv/current", "a" * 40)
    # When: deployment authorizes the entire selected set.
    result = owner.deploy(target, ("budget-watch", "mail-attachment-drive-watch"))
    # Then: authorization fails before an install can stop even one timer.
    assert result == 4
    assert len(calls) == 3
    assert not any(" install " in " ".join(argv) for argv in calls)


def test_deploy_installs_signed_runtime_marker_when_all_stops_are_approved(monkeypatch):
    # Given: the deployed pointer and every per-job approval match this release.
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, f"/srv/releases/{'a' * 40}\n", "")

    monkeypatch.setattr(owner.subprocess, "run", run)
    target = owner.ReleaseTarget(REPO, "example-node", "/srv/current", "a" * 40)
    # When: the release host deploys its approved selection.
    result = owner.deploy(target, ("budget-watch", "mail-attachment-drive-watch"))
    # Then: all authorizations precede the one node install using the signed runtime.
    assert result == 0
    assert [call[1] for call in calls[1:-1]] == ["authorize-cron-timer", "authorize-cron-timer"]
    assert calls[-1][0:2] == ("ssh", "example-node")
    assert "PYTHONPATH=/srv/current" in calls[-1][2]
    assert "install --jobs budget-watch,mail-attachment-drive-watch" in calls[-1][2]


def test_card_refuses_hidden_switch_when_owner_envelope_is_unavailable(monkeypatch):
    # Given: a selected job and unavailable new-card rendering.
    from automation import release_card
    from automation.release_spec import EnvelopeUnavailable, ReleaseSpec

    spec = replace(_spec(owner.surface("budget-watch")), render_version=8)
    render = ReleaseSpec.render

    def unavailable(candidate):
        if candidate.render_version == 8:
            raise EnvelopeUnavailable("fixture")
        return render(candidate)

    monkeypatch.setattr(ReleaseSpec, "render", unavailable)
    # When: producer considers fallback cards.
    candidate, refusal = release_card.preflight_new_request(spec)
    # Then: no older card can approve an effect it does not show.
    assert candidate is None
    assert refusal
