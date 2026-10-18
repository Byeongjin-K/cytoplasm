"""Owner-command writes still use the real clone lock and remote content verification."""
from __future__ import annotations

import subprocess

import pytest

from automation.obsidian_write import ObsidianWriteConfig, ObsidianWriteError, plan_note, write_note
from tests.unit.test_obsidian_write_writer import FakeGitRunner


@pytest.fixture
def configured_clone(tmp_path):
    key = tmp_path / "key"
    key.write_text("synthetic test key")
    return ObsidianWriteConfig("git@example.invalid:owner/vault.git",
                               tmp_path / "clone", ssh_key_path=key)


class ReceiptGit(FakeGitRunner):
    def __call__(self, argv, **kwargs):
        result = super().__call__(argv, **kwargs)
        if argv[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(argv, 0, "c" * 40 + "\n", "")
        return result


def test_owner_command_returns_the_verified_commit_without_card(configured_clone):
    # Given: the real writer and a Git boundary with verified remote bytes.
    plan = plan_note("test note", "frozen body", institutional=False, bucket_hint="area")
    runner = ReceiptGit(configured_clone.clone_dir)
    checked = []
    def authorize(candidate):
        checked.append(candidate)
        return candidate == plan
    # When: a bound owner command permits this exact note.
    receipt = write_note(plan, configured_clone, runner, authorize_push=authorize)
    # Then: the same push and readback path returns its actual commit identity.
    assert checked == [plan]
    assert receipt.commit_sha == "c" * 40
    assert ("git", "push", "origin", "HEAD:main") in [call.argv for call in runner.calls]
    assert ("git", "show", f"origin/main:{plan.relpath}") in [call.argv for call in runner.calls]


@pytest.mark.parametrize("allow", [True, False])
def test_command_never_bypasses_denial_or_remote_verification(configured_clone, allow):
    # Given: remote content that does not match the frozen note.
    plan = plan_note("test note", "frozen body", institutional=False, bucket_hint="area")
    runner = ReceiptGit(configured_clone.clone_dir, remote_content="wrong bytes")
    # When / Then: either authorization denial or a readback mismatch refuses completion.
    with pytest.raises(ObsidianWriteError):
        write_note(plan, configured_clone, runner, authorize_push=lambda candidate: allow)
    pushes = [call for call in runner.calls if call.argv[:2] == ("git", "push")]
    assert len(pushes) == int(allow)
