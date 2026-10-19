from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Protocol

import pytest

from automation.interop.effect_approval import (
    ApprovalBinding,
    ApprovalContext,
    SignedApprovalEvent,
    approval_challenge,
    record_signed_e2e_approval,
)
from automation.interop.injection_adapter import InboundEvent, sign_event
from automation.obsidian_write import (
    READ_ONLY_MIRROR_DIR,
    NotePlan,
    ObsidianWriteConfig,
    ObsidianWriteError,
    WriteReceipt,
    plan_note,
    write_note,
)
from automation.obsidian_write import clone_lock, gate_binding

_OWNER_ID = "owner"
_E2E_SECRET = b"obsidian-write-tests"


@dataclass(frozen=True, slots=True)
class CapturedCall:
    argv: tuple[str, ...]
    cwd: Path | None
    environment: dict[str, str]
    timeout: float | None
    #: Fetch temporaries present at the moment of the call, so ordering is observable.
    tmp_packs: tuple[str, ...] = ()


def tmp_pack_names(clone_dir: Path) -> tuple[str, ...]:
    pack_dir = clone_dir / ".git" / "objects" / "pack"
    if not pack_dir.is_dir():
        return ()
    return tuple(sorted(path.name for path in pack_dir.glob("tmp_pack_*")))


class FakeGitRunner:
    def __init__(
        self,
        clone_dir: Path,
        *,
        failure_at: int | None = None,
        remote_content: str | None = None,
        partial_filter: str = "",
        litter_on: tuple[str, ...] | None = None,
    ) -> None:
        self.calls: list[CapturedCall] = []
        self._clone_dir: Path = clone_dir
        self._failure_at: int | None = failure_at
        self._remote_content: str | None = remote_content
        self._partial_filter: str = partial_filter
        self._litter_on: tuple[str, ...] | None = litter_on

    def __call__(
        self,
        argv: list[str],
        /,
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        capture_output: bool = False,
        text: bool = False,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        assert env is not None, "git invocations must carry explicit env"
        assert capture_output is True
        assert text is True
        self.calls.append(
            CapturedCall(tuple(argv), cwd, dict(env), timeout, tmp_pack_names(self._clone_dir))
        )

        if tuple(argv[1:2]) == ("clone",):
            _ = self._clone_dir.mkdir(mode=0o700)
            _ = (self._clone_dir / ".git").mkdir()

        if self._litter_on is not None and tuple(argv[: len(self._litter_on)]) == self._litter_on:
            # Reproduce a fetch killed mid-transfer: residue appears during the run.
            pack_dir = self._clone_dir / ".git" / "objects" / "pack"
            _ = pack_dir.mkdir(parents=True, exist_ok=True)
            _ = (pack_dir / f"tmp_pack_{len(self.calls)}").write_bytes(b"\0" * 512)

        if self._failure_at == len(self.calls):
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="failed")

        if tuple(argv[1:3]) == ("config", "--default"):
            return subprocess.CompletedProcess(argv, 0, stdout=self._partial_filter, stderr="")

        if tuple(argv[1:2]) == ("show",):
            remote_content = self._remote_content
            if remote_content is None:
                remote_spec = argv[-1]
                _remote_ref, separator, relpath = remote_spec.partition(":")
                assert separator == ":"
                remote_content = (self._clone_dir / PurePosixPath(relpath)).read_text(
                    encoding="utf-8"
                )
            return subprocess.CompletedProcess(argv, 0, stdout=remote_content, stderr="")

        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")


class GitRunner(Protocol):
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
    ) -> subprocess.CompletedProcess[str]: ...


@pytest.fixture
def configured_clone(tmp_path: Path) -> ObsidianWriteConfig:
    # Given: an isolated write clone and a readable, non-production key path.
    clone_dir = tmp_path / "obsidian-write"
    _ = clone_dir.mkdir(mode=0o700)
    _ = (clone_dir / ".git").mkdir()
    key_path = tmp_path / "obsidian-write-key"
    _ = key_path.write_text("test key material", encoding="utf-8")
    _ = key_path.chmod(0o600)
    return ObsidianWriteConfig(
        repo_url="git@example.invalid:owner/vault.git",
        clone_dir=clone_dir,
        ssh_key_path=key_path,
        branch="main",
    )


def write_approved(
    plan: NotePlan,
    config: ObsidianWriteConfig,
    runner: GitRunner,
) -> WriteReceipt:
    context = ApprovalContext(config.clone_dir.parent / "approvals.jsonl", _OWNER_ID, True)
    decision = gate_binding.evaluate(plan, context=context)
    event = InboundEvent(
        event_id="obsidian-write-approval",
        user_id=_OWNER_ID,
        channel_id="approvals",
        text=approval_challenge(decision.action_hash, decision.target_id),
    )
    assert record_signed_e2e_approval(
        context,
        ApprovalBinding(decision.action_hash, decision.target_id),
        SignedApprovalEvent(event, sign_event(event, _E2E_SECRET), _E2E_SECRET),
    )
    return write_note(plan, config, runner, approval_context=context)


def test_write_note_fetches_upserts_commits_only_target_pushes_and_verifies_remote(
    configured_clone: ObsidianWriteConfig,
) -> None:
    # Given
    plan = plan_note("실험 기록", "관찰 결과", institutional=False, bucket_hint="area")
    runner = FakeGitRunner(configured_clone.clone_dir)

    # When
    receipt = write_approved(plan, configured_clone, runner)

    # Then
    target = configured_clone.clone_dir / plan.relpath
    assert receipt.relpath == plan.relpath
    assert receipt.content_sha256 == hashlib.sha256(target.read_bytes()).hexdigest()
    assert target.read_text(encoding="utf-8").startswith("# 실험 기록\n\n>[!info]\n")
    assert "> Author:" in target.read_text(encoding="utf-8")
    assert "> Created:" in target.read_text(encoding="utf-8")
    assert "> Modified:" in target.read_text(encoding="utf-8")
    assert "> Location:" in target.read_text(encoding="utf-8")
    assert "> Tag:" in target.read_text(encoding="utf-8")
    relpath = plan.relpath.as_posix()
    assert [call.argv for call in runner.calls] == [
        ("git", "config", "--default", "", "--get", "remote.origin.partialclonefilter"),
        ("git", "config", "remote.origin.promisor", "true"),
        ("git", "config", "remote.origin.partialclonefilter", "blob:none"),
        ("git", "fetch", "origin", "main"),
        ("git", "reset", "--hard", "origin/main"),
        ("git", "add", "--", relpath),
        (
            "git",
            "commit",
            "--only",
            "-m",
            f"obsidian-write: upsert {plan.relpath.name}",
            "--",
            relpath,
        ),
        ("git", "push", "origin", "HEAD:main"),
        ("git", "fetch", "origin", "main"),
        ("git", "show", f"origin/main:{relpath}"),
    ]


def test_write_note_updates_the_deterministic_target_without_duplicate(
    configured_clone: ObsidianWriteConfig,
) -> None:
    # Given
    plan = plan_note("같은 제목", "새 본문", institutional=False, bucket_hint="resource")
    target = configured_clone.clone_dir / plan.relpath
    _ = target.parent.mkdir(parents=True)
    _ = target.write_text("old content", encoding="utf-8")
    runner = FakeGitRunner(configured_clone.clone_dir)

    # When
    _ = write_approved(plan, configured_clone, runner)

    # Then
    assert target.read_text(encoding="utf-8") != "old content"
    assert tuple(configured_clone.clone_dir.rglob("*.md")) == (target,)


@pytest.mark.parametrize(
    ("institutional", "bucket_hint"),
    ((False, "project"), (False, None), (True, "archive")),
)
def test_write_note_never_invokes_subprocess_with_read_only_mirror_path(
    configured_clone: ObsidianWriteConfig,
    institutional: bool,
    bucket_hint: str | None,
) -> None:
    # Given
    plan = plan_note("mirror isolation", "body", institutional=institutional, bucket_hint=bucket_hint)
    runner = FakeGitRunner(configured_clone.clone_dir)

    # When
    _ = write_approved(plan, configured_clone, runner)

    # Then: this is a property over personal, inbox, and institutional placements.
    mirror_path = str(READ_ONLY_MIRROR_DIR)
    assert all(
        mirror_path not in argument
        for call in runner.calls
        for argument in (*call.argv, "" if call.cwd is None else str(call.cwd))
    )


def test_write_note_refuses_read_only_mirror_clone_before_subprocess(
    configured_clone: ObsidianWriteConfig,
) -> None:
    # Given
    mirror_config = ObsidianWriteConfig(
        repo_url=configured_clone.repo_url,
        clone_dir=READ_ONLY_MIRROR_DIR,
        ssh_key_path=configured_clone.ssh_key_path,
    )
    runner = FakeGitRunner(configured_clone.clone_dir)
    plan = plan_note("blocked", "body", institutional=False, bucket_hint="area")

    # When / Then
    with pytest.raises(ObsidianWriteError, match="read-only mirror") as captured:
        _ = write_note(plan, mirror_config, runner)
    assert captured.value.retryable is False
    assert runner.calls == []


def test_write_note_refuses_missing_key_before_subprocess(tmp_path: Path) -> None:
    # Given
    config = ObsidianWriteConfig(
        repo_url="git@example.invalid:owner/vault.git",
        clone_dir=tmp_path / "obsidian-write",
        ssh_key_path=tmp_path / "missing-key",
    )
    runner = FakeGitRunner(config.clone_dir)
    plan = plan_note("blocked", "body", institutional=False, bucket_hint="area")

    # When / Then
    with pytest.raises(ObsidianWriteError, match="deploy key") as captured:
        _ = write_note(plan, config, runner)
    assert captured.value.retryable is False
    assert runner.calls == []


def test_write_note_marks_failed_push_retryable(configured_clone: ObsidianWriteConfig) -> None:
    # Given
    plan = plan_note("push failure", "body", institutional=False, bucket_hint="area")
    runner = FakeGitRunner(configured_clone.clone_dir, failure_at=8)

    # When / Then
    with pytest.raises(ObsidianWriteError, match="push") as captured:
        _ = write_approved(plan, configured_clone, runner)
    assert captured.value.retryable is True
    assert runner.calls[-1].argv[:2] == ("git", "push")


def test_write_note_refuses_mismatched_remote_readback(configured_clone: ObsidianWriteConfig) -> None:
    # Given
    plan = plan_note("readback mismatch", "expected body", institutional=False, bucket_hint="area")
    runner = FakeGitRunner(configured_clone.clone_dir, remote_content="unexpected body")

    # When / Then
    with pytest.raises(ObsidianWriteError, match="verification") as captured:
        _ = write_approved(plan, configured_clone, runner)
    assert captured.value.retryable is True
    assert runner.calls[-1].argv[:2] == ("git", "show")


def test_write_note_calls_push_guard_immediately_before_push(
    configured_clone: ObsidianWriteConfig,
) -> None:
    # Given
    plan = plan_note("gate boundary", "body", institutional=False, bucket_hint="area")
    runner = FakeGitRunner(configured_clone.clone_dir)
    guard_calls: list[tuple[str, int]] = []

    def push_guard() -> None:
        guard_calls.append(("before-push", len(runner.calls)))

    # When
    _ = write_approved(plan, replace(configured_clone, push_guard=push_guard), runner)

    # Then: whatever the surrounding call count is, the very next git call is the push.
    assert len(guard_calls) == 1
    assert runner.calls[guard_calls[0][1]].argv[:2] == ("git", "push")


def test_write_note_yields_when_another_writer_holds_the_clone(
    configured_clone: ObsidianWriteConfig,
) -> None:
    # Given: memory_relocate's tick lands while plaud_sync still holds the same clone.
    plan = plan_note("lock contention", "body", institutional=False, bucket_hint="area")
    runner = FakeGitRunner(configured_clone.clone_dir)

    # When / Then
    with clone_lock.hold(configured_clone.clone_dir):
        with pytest.raises(ObsidianWriteError, match="another writer") as captured:
            _ = write_note(plan, configured_clone, runner)
    assert captured.value.retryable is True, "the next cron tick must be allowed to retry"
    assert runner.calls == [], "a yielding writer must not touch the shared clone at all"


def test_write_note_purges_stale_tmp_packs_before_every_fetch(
    configured_clone: ObsidianWriteConfig,
) -> None:
    # Given: residue left by a fetch killed at the old 120 s timeout, plus fresh residue
    # dropped mid-run so the verification fetch is covered too.
    pack_dir = configured_clone.clone_dir / ".git" / "objects" / "pack"
    _ = pack_dir.mkdir(parents=True)
    stale = pack_dir / "tmp_pack_fromLastTick"
    _ = stale.write_bytes(b"\0" * 4096)
    plan = plan_note("pack hygiene", "body", institutional=False, bucket_hint="area")
    runner = FakeGitRunner(configured_clone.clone_dir, litter_on=("git", "push"))

    # When
    _ = write_approved(plan, configured_clone, runner)

    # Then: every fetch starts from a clean pack directory.
    fetches = [call for call in runner.calls if call.argv[1:2] == ("fetch",)]
    assert len(fetches) == 2
    assert [call.tmp_packs for call in fetches] == [(), ()]
    assert not stale.exists()
    assert tmp_pack_names(configured_clone.clone_dir) == ()


def test_write_note_applies_the_fetch_timeout_only_to_the_transfer_step(
    configured_clone: ObsidianWriteConfig,
) -> None:
    # Given
    config = replace(configured_clone, fetch_timeout_seconds=1800.0)
    plan = plan_note("timeout split", "body", institutional=False, bucket_hint="area")
    runner = FakeGitRunner(config.clone_dir)

    # When
    _ = write_approved(plan, config, runner)

    # Then: a slow pack transfer must not be killed, but a hung local op still is.
    timeouts = {call.argv[1]: call.timeout for call in runner.calls}
    assert timeouts["fetch"] == 1800.0
    assert timeouts["reset"] == 120.0
    assert timeouts["push"] == 120.0
    assert timeouts["show"] == 120.0


def test_write_note_creates_new_clones_without_blobs(tmp_path: Path) -> None:
    # Given: the very first run, before any clone exists.
    key_path = tmp_path / "obsidian-write-key"
    _ = key_path.write_text("test key material", encoding="utf-8")
    _ = key_path.chmod(0o600)
    config = ObsidianWriteConfig(
        repo_url="git@example.invalid:owner/vault.git",
        clone_dir=tmp_path / "obsidian-write",
        ssh_key_path=key_path,
        branch="main",
        fetch_timeout_seconds=1500.0,
    )
    plan = plan_note("first clone", "body", institutional=False, bucket_hint="area")
    runner = FakeGitRunner(config.clone_dir, partial_filter="blob:none")

    # When
    _ = write_approved(plan, config, runner)

    # Then
    clone_call = next(call for call in runner.calls if call.argv[1:2] == ("clone",))
    assert "--filter=blob:none" in clone_call.argv
    assert clone_call.timeout == 1500.0


def test_write_note_converts_an_existing_full_clone_to_blobless_fetches(
    configured_clone: ObsidianWriteConfig,
) -> None:
    # Given: the deployed clone that has been pulling a 770 MB pack every tick.
    plan = plan_note("clone conversion", "body", institutional=False, bucket_hint="area")
    runner = FakeGitRunner(configured_clone.clone_dir)

    # When
    _ = write_approved(plan, configured_clone, runner)

    # Then: converted before the first fetch, so this very tick already benefits.
    assert [call.argv for call in runner.calls if call.argv[1:2] == ("config",)] == [
        ("git", "config", "--default", "", "--get", "remote.origin.partialclonefilter"),
        ("git", "config", "remote.origin.promisor", "true"),
        ("git", "config", "remote.origin.partialclonefilter", "blob:none"),
    ]
    assert runner.calls[0].argv[1:2] == ("config",)


def test_write_note_leaves_an_already_converted_clone_alone(
    configured_clone: ObsidianWriteConfig,
) -> None:
    # Given
    plan = plan_note("already converted", "body", institutional=False, bucket_hint="area")
    runner = FakeGitRunner(configured_clone.clone_dir, partial_filter="blob:none\n")

    # When
    _ = write_approved(plan, configured_clone, runner)

    # Then: the conversion is a one-off, not a per-tick config write.
    assert [call.argv for call in runner.calls if call.argv[1:2] == ("config",)] == [
        ("git", "config", "--default", "", "--get", "remote.origin.partialclonefilter"),
    ]
