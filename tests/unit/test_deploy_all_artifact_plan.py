"""todo 5: v2 선언 관측(트리·빌드·cron·roster·위임 프로브)이 계획·행동으로 이어진다.

축은 셋이다. 어긋난 artifact 는 그것을 선언한 디렉터리의 배포기를 부른다(소유자를 짐작하지
않는다). 읽지 못한 artifact 는 clean 이 아니되 배포기도 부르지 않는다. 릴리스가 올릴 수 없는
표면(위임 프로브)은 `ACT|owner|` 로 말할 뿐 clean 을 막지 않는다.
"""
from __future__ import annotations

from typing import Final

import pytest

from automation import deploy_all, deploy_all_kinds
from automation.deploy_all import ObservationError, parse_observations, render_actions

_A: Final = "a" * 64
_B: Final = "b" * 64
_HOME_OK: Final = "OBS|home|agent|.hermes/scripts/w.py|skills/mail/scripts/w.py|required|aaa|aaa"


def _artifact(
    kind: str,
    owner: str,
    status: str,
    *,
    destination: str = ".hermes/x",
    account: str = "agent",
    policy: str = "required",
    activation: str = "none",
    want: str = _A,
    have: str | None = None,
) -> str:
    if have is None:
        have = {"ok": want, "stale": _B, "extra": _B, "absent": "-", "unknown": "?"}.get(status, _B)
    return (
        f"OBS|artifact|{kind}|{account}|{destination}|{owner}|{policy}|{activation}"
        f"|{status}|{want}|{have}"
    )


def _obs(*family: str, count: int | None = None) -> list[str]:
    n = sum(1 for line in family if line.startswith("OBS|artifact|")) if count is None else count
    return [
        "OBS|release|abc123",
        "OBS|mounts|judged",
        _HOME_OK,
        *family,
        f"OBS|artifacts|{n}",
        "OBS|end",
    ]


def _acts(plan: deploy_all.Plan) -> list[str]:
    return [line for line in render_actions(plan).split("\n") if line]


def test_vendor_only_change_runs_mail_deployer() -> None:
    plan = parse_observations(
        _obs(_artifact("derived", "skills/mail", "stale", destination=".hermes/mailon-runtime"))
    )
    assert all(state.status == "ok" for state in plan.home)
    assert not plan.clean
    assert _acts(plan) == ["ACT|run-deployer|skills/mail/deploy.sh"]
    assert not plan.gateway_restart_needed


def test_package_only_change_runs_its_deployer() -> None:
    plan = parse_observations(
        _obs(
            _artifact("tree", "automation/rag_ingest", "stale"),
            "OBS|artifact-detail|tree|agent|.hermes/x|different-file|trends.py",
        )
    )
    assert not plan.clean
    assert plan.packages_to_deploy == ("automation/rag_ingest",)
    assert plan.artifacts[0].details == (("different-file", "trends.py"),)


@pytest.mark.parametrize("status", ["stale", "absent", "extra"])
def test_missing_gateway_module_deploys_and_restarts(status: str) -> None:
    plan = parse_observations(
        _obs(_artifact("tree", "automation/interop", status, activation="gateway"))
    )
    assert _acts(plan) == [
        "ACT|run-deployer|automation/interop/deploy.sh",
        "ACT|restart-gateway|agent+peer",
    ]


def test_config_or_prompt_change_runs_the_declaring_deployer() -> None:
    plan = parse_observations(
        _obs(
            _artifact(
                "file",
                "automation/notes_organize/sub",
                "stale",
                destination=".hermes/notes-organize/prompt.md",
            )
        )
    )
    assert plan.packages_to_deploy == ("automation/notes_organize/sub",)
    assert "ACT|run-deployer|automation/notes_organize/sub/deploy.sh" in _acts(plan)


def test_held_and_retired_rows_do_not_block_clean() -> None:
    plan = parse_observations(
        _obs(
            _artifact("tree", "automation/interop", "held", activation="gateway"),
            "OBS|artifact-detail|tree|agent|.hermes/x|roster-required|-",
            _artifact("cron", "skills/budget", "retired-present", destination="old-job",
                      policy="retired", want="-", have=_B),
            _artifact("file", "skills/budget", "retired-absent", destination=".hermes/gone",
                      policy="retired", want="-", have="-"),
            _artifact("file", "skills/mail", "ok", destination=".hermes/ok"),
        )
    )
    assert plan.clean
    assert plan.packages_to_deploy == ()
    assert not plan.gateway_restart_needed
    assert _acts(plan) == []


def test_failed_delegated_probes_are_owner_actions_and_do_not_block_clean() -> None:
    plan = parse_observations(
        _obs(
            "OBS|pending|release_helper_drift|primary|FAIL|privileged release helpers match release",
            "OBS|pending-detail|release_helper_drift|primary|HELPER-DRIFT: converge",
            "OBS|pending-detail|release_helper_drift|primary|re-run the provisioner on the node",
            "OBS|pending|healthcheck_wrapper_current|primary|PASS|allowlist primary",
            "OBS|pending-detail|healthcheck_wrapper_current|primary|WRAPPER ok",
            "OBS|pending|healthcheck_wrapper_current|rag|UNKNOWN|allowlist rag",
        )
    )
    assert plan.clean
    assert [(p.probe, p.node, p.status) for p in plan.pending] == [
        ("release_helper_drift", "primary", "FAIL"),
        ("healthcheck_wrapper_current", "rag", "UNKNOWN"),
    ]
    assert plan.pending[0].details == (
        "HELPER-DRIFT: converge",
        "re-run the provisioner on the node",
    )
    assert plan.delegated_checked == 1
    assert not plan.pending_unknown
    assert _acts(plan) == [
        "ACT|owner|release_helper_drift@primary: privileged release helpers match release",
        "ACT|owner|healthcheck_wrapper_current@rag: allowlist rag",
    ]


def test_delegated_probes_that_could_not_run_are_recorded_as_unknown() -> None:
    plan = parse_observations(_obs("OBS|pending-unknown|delegated-probes-unavailable"))
    assert plan.pending_unknown
    assert plan.clean


def test_delegated_surfaces_no_longer_include_runtime_packages() -> None:
    assert deploy_all.DELEGATED_SURFACES == ("release-helpers", "rag-stack")


def test_unknown_artifact_is_not_clean_and_runs_no_deployer() -> None:
    plan = parse_observations(
        _obs(_artifact("cron", "skills/budget", "unknown", activation="gateway",
                       destination="budget-digest"))
    )
    assert not plan.clean
    assert plan.packages_to_deploy == ()
    assert not plan.gateway_restart_needed
    assert not any(line.startswith(("ACT|run-deployer|", "ACT|restart-gateway|")) for line in _acts(plan))
    assert "ACT|manual|unreadable:cron:agent:budget-digest" in _acts(plan)


@pytest.mark.parametrize(
    "observed",
    [
        _obs(_artifact("tree", "skills/mail", "ok"), count=2),
        _obs(_artifact("tree", "skills/mail", "ok"), count=0),
        # 개수 줄이 없는데 artifact 줄이 있다 — 잘린 관측이다
        [line for line in _obs(_artifact("tree", "skills/mail", "ok")) if not line.startswith("OBS|artifacts|")],
        # 개수 줄 뒤에 artifact 줄이 더 온다 — 마지막이어야 한다
        ["OBS|release|abc", "OBS|mounts|judged", _HOME_OK, "OBS|artifacts|0",
         _artifact("tree", "skills/mail", "ok"), "OBS|end"],
        ["OBS|release|abc", "OBS|mounts|judged", _HOME_OK, "OBS|artifacts|0",
         "OBS|artifacts|0", "OBS|end"],
    ],
)
def test_artifact_count_mismatch_is_rejected(observed: list[str]) -> None:
    with pytest.raises(ObservationError):
        _ = parse_observations(observed)


def test_optional_absence_is_clean_but_unreadable_is_not() -> None:
    absent = parse_observations(
        _obs(_artifact("tree", "automation/x", "ok", policy="optional", have="-"))
    )
    unreadable = parse_observations(
        _obs(_artifact("tree", "automation/x", "unknown", policy="optional"))
    )
    assert absent.clean
    assert not unreadable.clean
    assert unreadable.packages_to_deploy == ()


@pytest.mark.parametrize(
    "line",
    [
        "OBS|artifact|tree|agent|.hermes/x|skills/mail|required|none|stale|" + _A,
        _artifact("tree", "skills/mail", "stale") + "|extra",
        _artifact("blob", "skills/mail", "stale"),
        _artifact("tree", "skills/mail", "fine"),
        _artifact("tree", "skills/mail", "stale", policy="maybe"),
        _artifact("tree", "skills/mail", "stale", activation="reboot"),
        _artifact("tree", "../escape", "stale"),
        _artifact("tree", "/etc", "stale"),
        _artifact("tree", "skills/mail/../../tmp", "stale"),
        _artifact("tree", "skills/$(touch x)", "stale"),
        _artifact("tree", "", "stale"),
        _artifact("tree", "skills/mail", "stale", account="a b"),
        _artifact("tree", "skills/mail", "stale", destination=""),
        # 성공을 주장하지만 신원이 다르거나 모른다
        _artifact("tree", "skills/mail", "ok", have=_B),
        _artifact("tree", "skills/mail", "ok", have="?"),
        _artifact("tree", "skills/mail", "ok", want="?", have="?"),
        _artifact("tree", "skills/mail", "ok", have="-"),  # required 의 부재는 ok 가 아니다
        # retired 정책과 상태는 짝이 맞아야 한다
        _artifact("tree", "skills/mail", "retired-present"),
        _artifact("tree", "skills/mail", "stale", policy="retired"),
        # 앞선 artifact 없는 detail
        "OBS|artifact-detail|tree|agent|.hermes/x|different-file|a.py",
        "OBS|pending-detail|release_helper_drift|primary|orphan",
        "OBS|pending|release_helper_drift|primary|BROKEN|name",
        "OBS|pending|release_helper_drift|primary|FAIL|",
        "OBS|pending|bad probe|primary|FAIL|name",
        "OBS|pending-unknown|",
        "OBS|artifact-detail|tree|agent|.hermes/x|different-file",
    ],
)
def test_malformed_artifact_lines_are_rejected(line: str) -> None:
    with pytest.raises(ObservationError):
        _ = parse_observations(_obs(line, count=0 if not line.startswith("OBS|artifact|") else 1))


def test_detail_for_another_artifact_is_rejected() -> None:
    with pytest.raises(ObservationError):
        _ = parse_observations(
            _obs(
                _artifact("tree", "skills/mail", "stale", destination=".hermes/a"),
                "OBS|artifact-detail|tree|agent|.hermes/b|different-file|a.py",
            )
        )


def test_duplicate_artifact_is_rejected() -> None:
    line = _artifact("tree", "skills/mail", "ok")
    with pytest.raises(ObservationError):
        _ = parse_observations(_obs(line, line))


def test_errors_are_the_planner_error_type() -> None:
    assert deploy_all_kinds.ObservationError is deploy_all.ObservationError


@pytest.mark.parametrize("status", ["stale", "absent", "extra"])
def test_held_policy_with_a_deployable_status_is_rejected(status: str) -> None:
    line = _artifact("tree", "skills/mail", status, policy="held")
    with pytest.raises(ObservationError):
        _ = parse_observations(_obs(line))


@pytest.mark.parametrize(("policy", "status"), [("held", "held"), ("held", "ok"), ("required", "ok")])
def test_held_or_ok_rows_are_clean_and_run_no_deployer(policy: str, status: str) -> None:
    plan = parse_observations(_obs(_artifact("tree", "skills/mail", status, policy=policy)))
    assert plan.clean
    assert plan.packages_to_deploy == ()
    assert _acts(plan) == []


@pytest.mark.parametrize("status", ["stale", "absent", "extra"])
def test_deployable_status_blocks_clean_and_runs_the_owner_deployer(status: str) -> None:
    plan = parse_observations(_obs(_artifact("tree", "skills/mail", status)))
    assert not plan.clean
    assert plan.packages_to_deploy == ("skills/mail",)
    assert "ACT|run-deployer|skills/mail/deploy.sh" in _acts(plan)


def test_unknown_status_blocks_clean_but_runs_no_deployer() -> None:
    plan = parse_observations(_obs(_artifact("tree", "skills/mail", "unknown")))
    assert not plan.clean
    assert plan.packages_to_deploy == ()
    assert not any("run-deployer" in line for line in _acts(plan))


def test_rendered_plan_names_owner_actions_and_defects() -> None:
    plan = parse_observations(
        _obs(
            _artifact("derived", "skills/mail", "stale", destination=".hermes/mailon-runtime"),
            "OBS|pending|release_helper_drift|primary|FAIL|privileged release helpers match release",
            "OBS|pending-detail|release_helper_drift|primary|re-run the provisioner",
        )
    )
    lines = deploy_all_kinds.render_artifact_lines(plan)
    assert any(".hermes/mailon-runtime" in line and "skills/mail/deploy.sh" in line for line in lines)
    assert any("re-run the provisioner" in line for line in lines)
    assert all(line in deploy_all.render_plan(plan) for line in lines)
