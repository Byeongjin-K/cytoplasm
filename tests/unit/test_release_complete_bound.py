"""Bound-head completion through the real timer shell and approval CLI.

Git signing, CI and deployment are local fakes; graph queries and the notice
journal are real. Separate from the legacy timer suite to keep this scenario bounded.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from tests.unit.test_release_complete import _git, _lines, _origin_and_source, _run, _stub

_DRIVER = '''from __future__ import annotations
import os
import sys
from pathlib import Path
from automation import owner_notice, release_approval, skill_gate
from automation.interop.approval_types import Probe
from tests.unit.test_release_approval import _StubGate
skill_gate.GATE_DIR = Path(os.environ["GATE_DIR"])
release_approval._gate = lambda spec: _StubGate(Probe[os.environ.get("PROBE", "APPROVED")])
def notify(body, *, message=None):
    with Path(os.environ["NOTICES"]).open("a") as stream:
        stream.write("NOTIFY\\n")
    return True
owner_notice.notify_owner = notify
with Path(os.environ["CALLS"]).open("a") as stream:
    stream.write(" ".join(sys.argv[1:]) + "\\n")
raise SystemExit(release_approval.main(sys.argv[1:]))
'''
_GIT = '''#!/usr/bin/env bash
set -uo pipefail
if [[ "$*" == *" tag -s "* ]]; then
  printf '%s|%s\\n' "${@: -1}" "${@: -4:1}" >> "$TAG_CALLS"
  [[ "${TAG_RC:-0}" == 0 ]] || exit "$TAG_RC"
  exec /usr/bin/git -C "$2" -c user.name=test -c user.email=test@example.invalid tag -a "${@: -4:1}" -m test "${@: -1}"
fi
exec /usr/bin/git "$@"
'''
#: Release 노트는 이미 게시된 것으로 답한다 — 완결기가 태그 뒤 노트 단계를 부르는지만 본다.
_GH = '''#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$GH_CALLS"
[[ "$1 $2" == "release view" ]] || exit 97
printf '{"body":"note","assets":[{"name":"update-trust.pub"}]}\\n'
'''
_CI = '''#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$CI_CALLS"
exit "${CI_RC:-0}"
'''


@dataclass(frozen=True, slots=True)
class Scenario:
    root: Path
    source: Path
    state: Path
    bound: str
    env: dict[str, str]

    def tick(self, **env: str) -> subprocess.CompletedProcess[str]:
        return _run(self.root, self.source, self.state, decision_rc=0,
                    extra_env={**self.env, **env})


@pytest.fixture
def scenario(tmp_path: Path) -> Scenario:
    from tests.unit.test_release_approval import _binding, _spec

    _, source = _origin_and_source(tmp_path)
    bound = _git(source, "rev-parse", "HEAD")
    gate = tmp_path / "gate"
    (gate / "pending").mkdir(parents=True)
    spec = replace(_spec(), head_sha=bound)
    (gate / "pending/release.json").write_text(
        json.dumps(spec.new_record("request-1", _binding())), encoding="utf-8",
    )
    driver = tmp_path / "approval.py"
    driver.write_text(_DRIVER, encoding="utf-8")
    binary = tmp_path / "bin"
    binary.mkdir()
    _stub(binary / "git", _GIT)
    gh = _stub(tmp_path / "gh", _GH)
    ci = _stub(tmp_path / "ci", _CI)
    key = tmp_path / "key.pub"
    key.write_text("test signing boundary", encoding="utf-8")
    state = tmp_path / "state"
    state.mkdir()
    _git(source, "worktree", "add", "--detach", str(state / "worktree"), "origin/main")
    return Scenario(tmp_path, source, state, bound, {
        "PATH": f"{binary}:{os.environ['PATH']}",
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        "RELEASE_APPROVAL_CMD": f"{sys.executable} {driver}",
        "RELEASE_LOCAL_CI": str(ci),
        "UPDATE_TRUST_SIGNING_KEY": str(key),
        "GATE_DIR": str(gate), "NOTICES": str(tmp_path / "notices"),
        "TAG_CALLS": str(tmp_path / "tags"), "CI_CALLS": str(tmp_path / "ci-calls"),
        "RELEASE_GH": str(gh), "RELEASE_NOTE_REPO": "example/repo",
        "GH_CALLS": str(tmp_path / "gh-calls"),
    })


def advance(scenario: Scenario) -> str:
    _git(scenario.source, "commit", "--allow-empty", "-m", "advance")
    _git(scenario.source, "push", "origin", "main")
    return _git(scenario.source, "rev-parse", "HEAD")


def test_tags_and_deploys_bound_sha_when_approved_ancestor_is_behind_tip(scenario: Scenario) -> None:
    # Given: an approved request, no tag, and a later merged commit.
    tip = advance(scenario)
    # When: the real completer ticks after the requesting session has gone.
    result = scenario.tick()
    # Then: only the bound SHA is tagged, deployed and marked complete.
    assert result.returncode == 0, result.stdout + result.stderr
    assert _lines(scenario.root / "tags") == [f"{scenario.bound}|v1.2.3"]
    assert any(line.startswith("release view v1.2.3 ") for line in _lines(scenario.root / "gh-calls"))
    assert _lines(scenario.root / "ci-calls") == [f"verify {scenario.bound}"]
    assert _lines(scenario.root / "deploy-calls.log") == [
        f"{scenario.state / 'worktree'}|{scenario.bound}|--apply --wait-converge",
    ]
    assert (scenario.state / "completed" / scenario.bound).exists()
    assert not (scenario.state / "completed" / tip).exists()
    assert _lines(scenario.root / "notices") == []
    assert _lines(scenario.root / "release-calls.log") == []
    assert all(line.startswith("decision --head ") for line in _lines(scenario.root / "calls.log"))
    assert _git(scenario.state / "worktree", "rev-parse", "HEAD") == tip


def test_notifies_once_when_bound_sha_is_not_an_ancestor(scenario: Scenario) -> None:
    # Given: the approved commit is on an abandoned side branch, not origin/main.
    _git(scenario.source, "checkout", "-b", "abandoned")
    _git(scenario.source, "commit", "--allow-empty", "-m", "abandoned")
    abandoned = _git(scenario.source, "rev-parse", "HEAD")
    path = Path(scenario.env["GATE_DIR"]) / "pending/release.json"
    from tests.unit.test_release_approval import _binding, _spec
    spec = replace(_spec(), head_sha=abandoned)
    path.write_text(json.dumps(spec.new_record("request-1", _binding())), encoding="utf-8")
    _git(scenario.source, "checkout", "main")
    scenario.tick()
    before = path.read_bytes()
    # When: another tick encounters the same request.
    result = scenario.tick()
    # Then: one journaled owner notice total; neither tick releases anything.
    assert result.returncode == 0, result.stdout + result.stderr
    assert _lines(scenario.root / "notices") == ["NOTIFY"]
    assert len(list((Path(scenario.env["GATE_DIR"]) / "release-stale-notified").iterdir())) == 1
    assert path.read_bytes() == before
    assert "RELEASE-STALE-NOTIFIED" not in result.stderr
    assert "branch=non-ancestor" in result.stdout
    assert _lines(scenario.root / "tags") == []
    assert _lines(scenario.root / "deploy-calls.log") == []


def test_preserves_legacy_execution_bytes_when_bound_sha_is_tip(scenario: Scenario) -> None:
    # Given: the approved request still matches origin/main.
    # When: the completer ticks.
    result = scenario.tick()
    # Then: the original execution path and its machine-consumed decision stay unchanged.
    short = scenario.bound[:12]
    assert result.returncode == 0
    assert result.stdout == (
        f"[release-complete] approved release live for {short} — completing (attempt 1/3)\n"
        f"[release-complete] completed {short}\n"
    )
    assert result.stderr.endswith("RELEASE-DECISION: approved version=v1.2.3\n")
    worktree = scenario.state / "worktree"
    assert _lines(scenario.root / "release-calls.log") == [f"{worktree}|{worktree}"]
    assert _lines(scenario.root / "tags") == []


@pytest.mark.parametrize("failure", [{"CI_RC": "4"}, {"TAG_RC": "1"}, {"DEPLOY_RC": "3"}])
def test_caps_failures_per_bound_sha_when_completion_keeps_failing(
    scenario: Scenario, failure: dict[str, str],
) -> None:
    # Given: persistent failure on an approved ancestor.
    advance(scenario)
    for _ in range(3):
        scenario.tick(**failure)
    calls = _lines(scenario.root / "ci-calls")
    deployments = _lines(scenario.root / "deploy-calls.log")
    # When: the fourth tick runs (including the post-tag reconciliation path).
    result = scenario.tick(**failure)
    # Then: no fourth attempt, no false completion.
    assert result.returncode == 0
    assert _lines(scenario.root / "ci-calls") == calls
    assert _lines(scenario.root / "deploy-calls.log") == deployments
    assert not (scenario.state / "completed" / scenario.bound).exists()
    assert "GIVEUP" in result.stdout


def test_defers_without_spending_attempts_when_nodes_have_not_converged(scenario: Scenario) -> None:
    # Given: a newly tagged ancestor whose nodes still return RELEASE-MISMATCH.
    advance(scenario)
    scenario.tick(DEPLOY_RC="4")
    # When: further reconciliation ticks observe the same transition.
    results = [scenario.tick(DEPLOY_RC="4") for _ in range(3)]
    # Then: the transition never exhausts the retry cap.
    assert all("RECONCILE-DEFER" in result.stdout for result in results)
    assert len(_lines(scenario.root / "deploy-calls.log")) == 4
    assert list((scenario.state / "attempts").glob("*")) == []
    assert not (scenario.state / "completed" / scenario.bound).exists()
