"""Regression specs for the owner gate at the Obsidian push boundary."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import pytest

from automation.interop.effect_approval import (
    ApprovalBinding,
    ApprovalContext,
    SignedApprovalEvent,
    approval_challenge,
    record_signed_e2e_approval,
)
from automation.interop.injection_adapter import InboundEvent, sign_event
from automation.obsidian_write import NotePlan, ObsidianWriteConfig, ObsidianWriteError, plan_note, write_note

OWNER_ID = "owner"
_E2E_SECRET = b"obsidian-gate-test"


@dataclass(frozen=True, slots=True)
class GitCall:
    argv: tuple[str, ...]


class FakeGitRunner:
    def __init__(self, clone_dir: Path) -> None:
        self.calls: list[GitCall] = []
        self._clone_dir: Path = clone_dir

    def __call__(
        self,
        argv: list[str],
        /,
        *,
        cwd: Path | None,
        env: dict[str, str],
        capture_output: bool,
        text: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, env, capture_output, text, timeout
        self.calls.append(GitCall(tuple(argv)))
        if argv[1] == "show":
            _remote_ref, _separator, relpath = argv[-1].partition(":")
            content = (self._clone_dir / PurePosixPath(relpath)).read_text(encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0, stdout=content, stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")


@pytest.fixture
def write_config(tmp_path: Path) -> ObsidianWriteConfig:
    # Given: an isolated clone and a readable non-production deploy-key path.
    clone_dir = tmp_path / "obsidian-write"
    clone_dir.mkdir(mode=0o700)
    (clone_dir / ".git").mkdir()
    key_path = tmp_path / "obsidian-write-key"
    _ = key_path.write_text("test key material", encoding="utf-8")
    _ = key_path.chmod(0o600)
    return ObsidianWriteConfig(
        repo_url="git@example.invalid:owner/vault.git",
        clone_dir=clone_dir,
        ssh_key_path=key_path,
    )


def test_write_note_when_approval_is_missing_then_refuses_before_git_push(
    write_config: ObsidianWriteConfig,
) -> None:
    # Given
    plan = plan_note("gate boundary", "unapproved body", institutional=False, bucket_hint="area")
    runner = FakeGitRunner(write_config.clone_dir)

    # When / Then
    with pytest.raises(ObsidianWriteError, match="approval") as captured:
        _ = write_note(plan, write_config, runner)

    assert captured.value.retryable is False
    # `git config` calls are one-off clone setup; the gate's property is where work stops.
    assert [call.argv[:2] for call in runner.calls if call.argv[1] != "config"] == [
        ("git", "fetch"),
        ("git", "reset"),
        ("git", "add"),
        ("git", "commit"),
    ]


def test_write_note_when_approved_body_changes_then_refuses_before_git_push(
    tmp_path: Path,
    write_config: ObsidianWriteConfig,
) -> None:
    # Given
    from automation.obsidian_write import gate_binding

    approved_plan = plan_note("same note", "approved body", institutional=False, bucket_hint="area")
    changed_plan = plan_note("same note", "changed body", institutional=False, bucket_hint="area")
    context = ApprovalContext(tmp_path / "approvals.jsonl", OWNER_ID, True)
    _grant_approval(approved_plan, context)
    runner = FakeGitRunner(write_config.clone_dir)

    # When / Then
    with pytest.raises(ObsidianWriteError, match="approval"):
        _ = write_note(changed_plan, write_config, runner, approval_context=context)

    assert gate_binding.evaluate(approved_plan, context=context).action_hash != gate_binding.evaluate(
        changed_plan, context=context
    ).action_hash
    assert all(call.argv[:2] != ("git", "push") for call in runner.calls)


def test_write_note_when_matching_owner_approval_exists_then_pushes(
    tmp_path: Path,
    write_config: ObsidianWriteConfig,
) -> None:
    # Given
    plan = plan_note("approved note", "approved body", institutional=False, bucket_hint="area")
    context = ApprovalContext(tmp_path / "approvals.jsonl", OWNER_ID, True)
    _grant_approval(plan, context)
    runner = FakeGitRunner(write_config.clone_dir)

    # When
    _ = write_note(plan, write_config, runner, approval_context=context)

    # Then
    assert ("git", "push", "origin", "HEAD:main") in [call.argv for call in runner.calls]


def _grant_approval(plan: NotePlan, context: ApprovalContext) -> None:
    from automation.obsidian_write import gate_binding

    decision = gate_binding.evaluate(plan, context=context)
    event = InboundEvent(
        event_id="obsidian-gate-approval",
        user_id=OWNER_ID,
        channel_id="approvals",
        text=approval_challenge(decision.action_hash, decision.target_id),
    )
    assert record_signed_e2e_approval(
        context,
        ApprovalBinding(decision.action_hash, decision.target_id),
        SignedApprovalEvent(event, sign_event(event, _E2E_SECRET), _E2E_SECRET),
    )
