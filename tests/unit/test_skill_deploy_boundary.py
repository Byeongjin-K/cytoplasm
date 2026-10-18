from __future__ import annotations

import subprocess
import shutil
import sys
from pathlib import Path

from automation.node_asset_renderer import render_asset
from automation.node_config import default_node_config

import pytest


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "automation" / "deploy-skill.sh"
MEETING_SCENARIO = ROOT / "skills" / "meeting" / "scripts" / "scenario.sh"
REPORT_SCENARIO = ROOT / "skills" / "report" / "scripts" / "scenario.sh"
PROPOSAL_CLI = ROOT / "skills" / "proposal" / "scripts" / "proposal_cli.py"


def test_deploy_when_mounting_or_removing_then_uses_only_privileged_store_helper() -> None:
    # Given
    script = DEPLOY.read_text(encoding="utf-8")

    # When / Then
    assert "/usr/local/libexec/autophagy-install-skill install" in script
    assert "/usr/local/libexec/autophagy-install-skill remove" in script
    assert "mount_reviewed_skill" not in script
    assert 'rm -rf "\\$HOME/.hermes/skills/$SKILL"' not in script


def test_deploy_when_parsing_flags_then_accepts_approve_only_and_keeps_existing_arms() -> None:
    # Given
    script = DEPLOY.read_text(encoding="utf-8")

    # When / Then
    assert "REQUEST_ONLY=0 APPROVE_ONLY=0 FRESH=0 SANDBOX_ONLY=0 REMOVE=0" in script
    assert "--approve-only) APPROVE_ONLY=1 ;;" in script
    assert "--request-only) REQUEST_ONLY=1 ;;" in script
    assert "--sandbox-only) SANDBOX_ONLY=1 ;;" in script
    assert "--remove) REMOVE=1 ;;" in script
    assert "--approve-only" in script


def test_deploy_when_managed_activation_is_requested_then_parses_its_quarantine_dir() -> None:
    # Given
    script = DEPLOY.read_text(encoding="utf-8")

    # When / Then
    assert "ACTIVATE_MANAGED=0" in script
    assert "--activate-managed)" in script
    assert 'QUARANTINE_DIR="$2"' in script
    assert "shift 2" in script


def test_deploy_when_managed_name_would_mount_plainly_then_blocks_before_stage_one() -> None:
    # Given
    script = DEPLOY.read_text(encoding="utf-8")

    # When / Then
    guard = (
        'if [[ "$ACTIVATE_MANAGED" == 0 && "$APPROVE_ONLY" == 0 '
        '&& "$SANDBOX_ONLY" == 0 && "$REQUEST_ONLY" == 0 '
        '&& "$SKILL" == managed-* ]]; then'
    )
    assert guard in script
    assert 'die "MANAGED-BLOCK: mounting a managed skill requires --activate-managed"' in script


def test_deploy_when_managed_activation_quarantine_is_missing_then_fails_before_remote_stages(
    tmp_path: Path,
) -> None:
    # Given
    missing_quarantine = tmp_path / "missing"

    # When
    completed = subprocess.run(
        ["bash", str(DEPLOY), "managed-demo", "--activate-managed", str(missing_quarantine)],
        capture_output=True,
        text=True,
        check=False,
    )

    # Then
    assert completed.returncode != 0
    assert "MANAGED-BLOCK: quarantine directory missing" in completed.stderr


def test_deploy_when_managed_activation_requests_approval_then_forwards_provenance_file() -> None:
    # Given
    script = DEPLOY.read_text(encoding="utf-8")

    # When / Then
    assert 'PROVENANCE_REQUEST_ARGS=(--provenance-file "$PROVENANCE_REMOTE")' in script
    assert script.count('"${PROVENANCE_REQUEST_ARGS[@]}"') == 4
    assert script.count('check_with_attestation_refresh "$') == 2
    for key in ("publisher", "tag", "release_sequence", "manifest_sha256"):
        assert f'"{key}"' in script


def test_deploy_when_managed_activation_mounts_then_uses_managed_store_verb() -> None:
    # Given
    script = DEPLOY.read_text(encoding="utf-8")

    # When / Then
    assert "install_managed_skill() {" in script
    assert "autophagy-install-skill install-managed --publisher" in script
    assert 'install_managed_skill "$SRC_DIR" "$MANAGED_PUBLISHER" "$SKILL" "$DIGEST"' in script


def test_deploy_when_skill_names_overlap_then_checks_both_live_namespace_collisions() -> None:
    # Given
    script = DEPLOY.read_text(encoding="utf-8")

    # When / Then
    assert 'readlink "$STORE_ROOT/live/$MANAGED_BASE"' in script
    assert 'readlink "$STORE_ROOT/live/managed-$SKILL"' in script
    assert "COLLISION-BLOCK" in script


def test_deploy_when_activating_quarantine_then_recomputes_manifest_bound_digest() -> None:
    # Given
    script = DEPLOY.read_text(encoding="utf-8")

    # When / Then
    assert "from automation.managed_skills.manifest import manifest_digest, parse_manifest" in script
    assert 'QUARANTINE_DIGEST="$(skill_digest "$MANAGED_SKILL_DIR")"' in script
    assert '[[ "$QUARANTINE_DIGEST" == "$MANAGED_DIGEST" ]]' in script


def test_deploy_when_approval_grants_then_exits_before_stage_four_with_evidence() -> None:
    # Given
    script = DEPLOY.read_text(encoding="utf-8")

    # When
    approval_granted = script.index('log "approval GRANTED (logged to approvals.jsonl)"')
    approve_only_exit = script.index("if [[ \"$APPROVE_ONLY\" == 1 ]]; then")
    evidence_output = script.index("printf '%s:%s\\n' \"$MESSAGE_ID\" \"$DEPLOY_NONCE\"")
    stage_four = script.index("# ---------- stage 4: recheck reviewed current hash, then mount its artifact ----------")

    # Then
    assert approval_granted < approve_only_exit < evidence_output < stage_four


@pytest.mark.parametrize(
    "required_text",
    (
        "^[a-z0-9][a-z0-9-]{1,40}$",
        'SRC_DIR="${SKILL_SRC_DIR:-$REPO_ROOT/skills/$SKILL}"',
        "autophagy-install-skill install --skill",
        "autophagy-install-skill remove --skill",
    ),
    ids=("skill-name-regex", "source-override", "install-helper", "remove-helper"),
)
def test_deploy_when_current_boundary_contract_is_read_then_required_text_is_present(
    required_text: str,
) -> None:
    # Given: the current deploy script text.
    script = DEPLOY.read_text(encoding="utf-8")

    # When / Then: each managed-skill prerequisite is checked statically.
    assert required_text in script


def test_sudoers_when_installed_then_grants_only_root_owned_helper() -> None:
    # Given
    sudoers = render_asset(
        ROOT / "automation" / "sudoers.d" / "autophagy-skill-store",
        default_node_config(),
    )

    # When / Then
    assert sudoers.splitlines() == [
        "operator ALL=(root) NOPASSWD: /usr/local/libexec/autophagy-install-skill install --skill * --hash *",
        "operator ALL=(root) NOPASSWD: /usr/local/libexec/autophagy-install-skill install-managed --publisher * --skill * --hash *",
        "operator ALL=(root) NOPASSWD: /usr/local/libexec/autophagy-install-skill remove --skill *",
    ]


def test_meeting_compile_when_skill_is_read_only_then_uses_temporary_pycache() -> None:
    # Given
    script = MEETING_SCENARIO.read_text(encoding="utf-8")

    # When / Then
    assert 'export PYTHONPYCACHEPREFIX="$work/pycache"' in script


def test_report_isolated_runner_when_mounted_then_adds_skill_package_parent() -> None:
    # Given
    script = REPORT_SCENARIO.read_text(encoding="utf-8")

    # When / Then
    assert "Path(script_dir).parents[1]" in script


def test_proposal_cli_when_invoked_through_release_symlink_then_resolves_its_package(
    tmp_path: Path,
) -> None:
    # Given
    release = tmp_path / "releases" / "digest"
    _ = shutil.copytree(PROPOSAL_CLI.parents[1], release)
    mounted = tmp_path / "skills" / "proposal"
    mounted.parent.mkdir()
    mounted.symlink_to(release, target_is_directory=True)

    # When
    completed = subprocess.run(
        [sys.executable, "-I", str(mounted / "scripts" / "proposal_cli.py"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    # Then
    assert completed.returncode == 0, completed.stderr


def test_deploy_when_running_the_sandbox_scenario_then_uses_a_disposable_home() -> None:
    # Given
    script = DEPLOY.read_text(encoding="utf-8")

    # When
    start = script.index("SCENARIO_OUT=")
    end = script.index('|| sandbox_block "scenario failed under dummy secrets"', start)
    statement = script[start:end]

    # Then
    assert r'env -i HOME=\"\$HOME\"' not in statement
    assert r'env -i HOME=\"\$SH\"' in statement


def test_deploy_when_running_the_post_mount_smoke_then_uses_a_disposable_home() -> None:
    # Given
    script = DEPLOY.read_text(encoding="utf-8")

    # When
    start = script.index("INVOKE_OUT=")
    end = script.index('|| die "post-mount invoke smoke failed on agent"', start)
    statement = script[start:end]

    # Then
    assert r'env -i HOME=\"\$HOME\"' not in statement
    assert r'env -i HOME=\"\$SH\"' in statement


def test_deploy_when_running_the_sandbox_scenario_then_declares_the_staged_root_as_live_root() -> None:
    """The staged copy under the peer home must pass the governed-copy guard.

    Every mutating CLI refuses to run from a copy whose directory is not the live mount
    (``skill_mount.governed_copy_refusal``). The sandbox IS a copy, so it declares its own
    root through ``AUTOPHAGY_SKILL_LIVE_ROOT`` — otherwise the guard blocks stage 1 for every
    guarded skill (2026-09-03: 13 skills stayed SKILL-STALE behind v1.1.0).
    """
    # Given
    script = DEPLOY.read_text(encoding="utf-8")

    # When
    start = script.index("SCENARIO_OUT=")
    end = script.index('|| sandbox_block "scenario failed under dummy secrets"', start)
    statement = script[start:end]

    # Then
    assert r'AUTOPHAGY_SKILL_LIVE_ROOT=\"\$REAL_HOME/.hermes/skills\"' in statement


def test_deploy_when_isolating_the_scenario_home_then_still_forwards_interop_runtime() -> None:
    # Given
    script = DEPLOY.read_text(encoding="utf-8")

    # When
    sandbox_start = script.index("SCENARIO_OUT=")
    sandbox_end = script.index('|| sandbox_block "scenario failed under dummy secrets"', sandbox_start)
    invoke_start = script.index("INVOKE_OUT=")
    invoke_end = script.index('|| die "post-mount invoke smoke failed on agent"', invoke_start)
    statements = (script[sandbox_start:sandbox_end], script[invoke_start:invoke_end])

    # Then
    assert all("INTEROP_RUNTIME=" in statement for statement in statements)


def test_deploy_when_creating_a_scenario_home_then_it_is_private_and_removed() -> None:
    # Given
    script = DEPLOY.read_text(encoding="utf-8")

    # When
    sandbox_start = script.index("SCENARIO_OUT=")
    sandbox_end = script.index('|| sandbox_block "scenario failed under dummy secrets"', sandbox_start)
    invoke_start = script.index("INVOKE_OUT=")
    invoke_end = script.index('|| die "post-mount invoke smoke failed on agent"', invoke_start)
    statements = (script[sandbox_start:sandbox_end], script[invoke_start:invoke_end])

    # Then
    for statement in statements:
        assert "mktemp -d" in statement
        assert r'chmod 700 \"\$SH\"' in statement
        assert r'rm -rf \"\$SH\"' in statement


def test_deploy_when_the_interop_runtime_is_missing_then_the_sandbox_blocks_loudly() -> None:
    # Given
    script = DEPLOY.read_text(encoding="utf-8")

    # When
    start = script.index("SCENARIO_OUT=")
    end = script.index('|| sandbox_block "scenario failed under dummy secrets"', start)
    statement = script[start:end]

    # Then
    assert "SANDBOX-HOME-BLOCK interop runtime missing" in statement
    assert "exit 90" in statement


def test_deploy_header_when_describing_isolation_then_does_not_claim_env_i_alone() -> None:
    # Given
    script = DEPLOY.read_text(encoding="utf-8")

    # When
    header = script[: script.index("set -euo pipefail")]

    # Then
    assert "DUMMY secrets only (env -i isolation)" not in header
    assert "disposable HOME" in header


def test_deploy_when_python_runs_then_never_caches_bytecode_into_the_sealed_release() -> None:
    """봉인된 릴리스 안에서 이 파이프라인이 돌 때 .pyc 를 남기면 다음 배포가 막힌다.

    자율 경로는 이 스크립트를 root 로 띄운다(⑦ 워처의 특권 헬퍼). root 에게는 릴리스의
    `dr-xr-xr-x` 봉인이 통하지 않으므로, CPython 이 import 한 모듈 옆에 `__pycache__`
    를 만든다. release-provenance 는 트리가 커밋과 정확히 같기를 요구하니 첫 실행이
    두 번째 실행을 영구히 막는다(2026-08-03 실측).

    헬퍼도 같은 변수를 세우지만 그것은 설치된 복사본이라 갱신에 root 가 필요하다.
    파이프라인은 릴리스에서 매번 새로 읽히므로, 여기에도 두어 누가 띄우든 보호된다.
    """
    script = DEPLOY.read_text(encoding="utf-8")
    assert "export PYTHONDONTWRITEBYTECODE=1" in script


# --- SS-1: coexistence with agent/peer self-authored Hermes skills -----------
#
# The agent's primary root (/home/agent/.hermes/skills) stops being a read-only bind
# mount of the governed store and becomes the agent's OWN writable space; the governed
# store is discovered through skills.external_dirs instead. Hermes already refuses
# `skill_manage(create)` for a name that exists in any root, which covers self→governed.
# These tests pin the other direction — governed→self — plus the two things the
# inversion breaks: the post-mount smoke's path, and peer staging that never got
# cleaned because the shipped tree is sealed read-only.


def test_deploy_when_agent_self_root_has_the_name_then_blocks_before_stage_one() -> None:
    # Given
    script = DEPLOY.read_text(encoding="utf-8")

    # When: the guard is invoked after the reserved/managed name guards.
    call = script.index("assert_agent_root_has_no_self_skill\n")
    body_start = script.index("assert_agent_root_has_no_self_skill() {")
    body = script[body_start : script.index("\n}", body_start)]

    # Then: it runs before anything is staged, probes the agent account itself, and
    # every non-`absent` answer — including an unreadable one — refuses with exit 4.
    assert script.index('die "MANAGED-BLOCK: mounting a managed skill requires --activate-managed"') < call
    assert call < script.index('log "stage 1/4 SANDBOX (peer, dummy secrets)"')
    assert call < script.index('push_skill "$SANDBOX_ACCOUNT" "$SRC_DIR" "$SKILL"')
    assert 'run_as "$NODE_AGENT_ACCOUNT"' in body
    assert "$HOME/.hermes/skills" in body
    assert 'die "SELF-SKILL-COLLISION-BLOCK: cannot read the agent skill root' in body
    assert '*) die "SELF-SKILL-COLLISION-BLOCK: agent skill root probe returned an invalid state" 4 ;;' in body
    assert 'present) self_skill_collision_block "$NODE_AGENT_ACCOUNT"' in body

    refusal = script[script.index("self_skill_collision_block() {") :]
    refusal = refusal[: refusal.index("\n}")]
    assert "SELF-SKILL-COLLISION-BLOCK" in refusal
    assert "hermes curator archive $SKILL" in refusal
    assert "owner remove" in refusal
    assert '" 4' in refusal


def test_deploy_when_peer_root_entry_is_not_pipeline_authored_then_blocks_fail_closed() -> None:
    # Given: peer's ~/.hermes/skills is BOTH this pipeline's staging area and the home
    # of peer's own self-authored skills, and push_skill overwrites whatever stands there.
    script = DEPLOY.read_text(encoding="utf-8")

    # When: the root is classified before the staging push.
    region = script[
        script.index("peer_skill_root_state() {") : script.index('push_skill "$SANDBOX_ACCOUNT" "$SRC_DIR" "$SKILL"')
    ]

    # Then: only a proven leftover of our own is overwritten; everything else blocks,
    # and an unreadable/unparseable record blocks too (never falls through to `absent`).
    assert ".usage.json" in region
    assert "created_by" in region
    assert "agent_created" in region
    assert "author: autophagy-agents" in region
    assert 'agent-created) self_skill_collision_block "$NODE_PEER_ACCOUNT"' in region
    assert 'foreign) self_skill_collision_block "$NODE_PEER_ACCOUNT"' in region
    assert 'die "SELF-SKILL-COLLISION-BLOCK: cannot classify the peer skill root' in region
    assert '*) die "SELF-SKILL-COLLISION-BLOCK: peer skill root probe returned an invalid state" 4 ;;' in region
    assert "STAGING-RESIDUE-CLEANED" in region
    assert region.index("staging-residue)") < region.index("STAGING-RESIDUE-CLEANED")


def test_deploy_when_post_mount_smoke_runs_then_invokes_from_the_live_store() -> None:
    # Given: after the inversion the mounted skill is no longer visible under the
    # agent's home, so a smoke test reading from there would fail on every deploy.
    script = DEPLOY.read_text(encoding="utf-8")

    # When
    start = script.index("INVOKE_OUT=")
    statement = script[start : script.index('|| die "post-mount invoke smoke failed on agent"', start)]

    # Then: it invokes the governed artifact in the live store, by way of STORE_ROOT.
    assert 'STORE_ROOT="$NODE_SKILL_STORE"' in script
    assert "$STORE_ROOT/live/$SKILL/scripts/scenario.sh" in statement
    assert ".hermes/skills/" not in statement


def test_deploy_when_sandbox_only_exits_then_peer_staging_is_removed() -> None:
    # Given: staged trees ship sealed (0555/0444), so a bare `rm -rf` cannot remove
    # them — every cleanup path had been failing silently and leaking staging copies.
    script = DEPLOY.read_text(encoding="utf-8")

    # When
    sandbox_only = next(
        line for line in script.splitlines() if line.startswith('[[ "$SANDBOX_ONLY" == 1 ]]')
    )
    trap_body = script[script.index("cleanup_deploy_temps() {") :]
    trap_body = trap_body[: trap_body.index("\n}")]
    guarded = script[script.index("cleanup_peer_staging() {") :]
    guarded = guarded[: guarded.index("\n}")]
    remover = script[script.index("remove_peer_skill_copy() {") :]
    remover = remover[: remover.index("\n}")]

    # Then: the sandbox-only success exit AND the EXIT trap both clean, the trap only
    # fires when staging was actually pushed, and removal unseals before removing.
    assert "cleanup_peer_staging" in sandbox_only
    assert "cleanup_peer_staging" in trap_body
    assert "trap cleanup_deploy_temps EXIT" in script
    assert "PEER_STAGING_PUSHED" in guarded
    assert remover.index("chmod -R u+w") < remover.index("rm -rf")
    assert script.index("PEER_STAGING_PUSHED=1") < script.index('push_skill "$SANDBOX_ACCOUNT" "$SRC_DIR" "$SKILL"')


def test_deploy_when_remove_arm_runs_then_collision_guard_is_skipped() -> None:
    # Given: --remove exists to resolve a collision. Guarding it would make the only
    # in-pipeline way out of a collision itself refuse to run.
    script = DEPLOY.read_text(encoding="utf-8")

    # When
    arm = script.index('if [[ "$REMOVE" == 1 ]]; then')
    arm_exit = script.index("  exit 0\nfi", arm)
    guard = script.index("assert_agent_root_has_no_self_skill\n")

    # Then: the arm returns before the guard, and carries none of it.
    assert arm < arm_exit < guard
    assert "SELF-SKILL-COLLISION-BLOCK" not in script[arm:arm_exit]
    assert "cleanup_peer_staging" not in script[arm:arm_exit]
