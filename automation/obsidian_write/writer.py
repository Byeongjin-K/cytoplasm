"""Verified git upsert operations for the isolated Obsidian write clone."""

from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Final, Protocol

from automation.git_remote_url import GitRemoteUrlError, validate_remote_url
from automation.interop.effect_approval import ApprovalContext

from .config import READ_ONLY_MIRROR_DIR, ObsidianWriteConfig, ObsidianWriteError
from . import clone_lock, gate_binding
from .note import NotePlan, render_note

_GIT_TIMEOUT_SECONDS: Final = 120.0
_BLOB_FILTER: Final = "--filter=" + clone_lock.BLOB_FILTER


class _GitRunner(Protocol):
    def __call__(
        self,
        args: list[str],
        /,
        *,
        cwd: Path | None,
        env: dict[str, str],
        capture_output: bool,
        text: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]: ...


@dataclass(frozen=True, slots=True)
class WriteReceipt:
    """A successful remote read-back receipt for one deterministic note path."""

    relpath: PurePosixPath
    content_sha256: str
    remote_ref: str
    commit_sha: str = ""


@dataclass(frozen=True, slots=True)
class _GitInvocation:
    argv: tuple[str, ...]
    timeout: float = _GIT_TIMEOUT_SECONDS


@dataclass(frozen=True, slots=True)
class _WriteContext:
    config: ObsidianWriteConfig
    runner: _GitRunner
    environment: dict[str, str]

    def run(
        self,
        invocation: _GitInvocation,
        cwd: Path | None,
        step: str,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = self.runner(
                list(invocation.argv),
                cwd=cwd,
                env=self.environment,
                capture_output=True,
                text=True,
                timeout=invocation.timeout,
            )
        except FileNotFoundError as error:
            raise ObsidianWriteError(f"Obsidian write {step} command is unavailable", False) from error
        except (OSError, subprocess.SubprocessError) as error:
            raise ObsidianWriteError(f"Obsidian write {step} failed", True) from error
        if result.returncode != 0:
            raise ObsidianWriteError(f"Obsidian write {step} failed", True)
        return result


def write_note(
    plan: NotePlan,
    config: ObsidianWriteConfig,
    runner: _GitRunner = subprocess.run,
    *,
    approval_context: ApprovalContext | None = None,
    authorize_push: Callable[[NotePlan], bool] | None = None,
) -> WriteReceipt:
    """Upsert one note, push only it, then verify its remote content hash.

    The whole fetch → upsert → commit → push → verify span runs under one clone-wide
    lock: plaud_sync and memory_relocate drive this same clone from separate cron
    ticks, and an interleaved ``reset --hard`` silently discards a staged note.
    """
    _validate_write_target(config)
    with clone_lock.hold(config.clone_dir):
        return _write_locked(plan, config, runner, approval_context, authorize_push)


def _write_locked(
    plan: NotePlan,
    config: ObsidianWriteConfig,
    runner: _GitRunner,
    approval_context: ApprovalContext | None,
    authorize_push: Callable[[NotePlan], bool] | None = None,
) -> WriteReceipt:
    context = _WriteContext(config, runner, _git_environment(config))
    _ensure_write_clone(context)

    def step(argv: tuple[str, ...], name: str, /) -> str:
        return context.run(_GitInvocation(argv), config.clone_dir, name).stdout

    _ = clone_lock.ensure_blobless_fetch(step)
    _fetch(context, "fetch before upsert")
    remote_ref = f"origin/{config.branch}"
    _ = context.run(
        _GitInvocation(("git", "reset", "--hard", remote_ref)),
        config.clone_dir,
        "reset before upsert",
    )

    target = _target_path(config.clone_dir, plan.relpath)
    created = _existing_created_date(target)
    today = datetime.now(UTC).date().isoformat()
    content = render_note(plan, created=created or today, modified=today)
    content_changed = _atomic_upsert(target, content)

    relpath = plan.relpath.as_posix()
    if content_changed:
        _ = context.run(
            _GitInvocation(("git", "add", "--", relpath)),
            config.clone_dir,
            "stage note",
        )
        message = f"obsidian-write: upsert {plan.relpath.name}"
        _ = context.run(
            _GitInvocation(("git", "commit", "--only", "-m", message, "--", relpath)),
            config.clone_dir,
            "commit note",
        )
        _push(context, plan, approval_context, authorize_push)
    _fetch(context, "fetch for verification")
    remote_content = context.run(
        _GitInvocation(("git", "show", f"{remote_ref}:{relpath}")),
        config.clone_dir,
        "read remote note",
    ).stdout
    try:
        local_content = target.read_bytes()
    except OSError as error:
        raise ObsidianWriteError("Obsidian written note is unreadable", False) from error
    content_sha256 = hashlib.sha256(local_content).hexdigest()
    if hashlib.sha256(remote_content.encode("utf-8")).hexdigest() != content_sha256:
        raise ObsidianWriteError("Obsidian write remote verification did not match", True)
    commit_sha = ""
    if authorize_push is not None:
        commit_sha = context.run(
            _GitInvocation(("git", "rev-parse", remote_ref)),
            config.clone_dir, "read verified commit",
        ).stdout.strip()
    return WriteReceipt(plan.relpath, content_sha256, remote_ref, commit_sha)


def _fetch(context: _WriteContext, step: str) -> None:
    """Clear dead fetch temporaries, then transfer under the fetch-only budget."""
    _ = clone_lock.purge_stale_tmp_packs(context.config.clone_dir)
    _ = context.run(
        _GitInvocation(
            ("git", "fetch", "origin", context.config.branch),
            context.config.fetch_timeout_seconds,
        ),
        context.config.clone_dir,
        step,
    )


def _validate_write_target(config: ObsidianWriteConfig) -> None:
    clone_dir = config.clone_dir.expanduser().resolve(strict=False)
    mirror_dir = READ_ONLY_MIRROR_DIR.expanduser().resolve(strict=False)
    if clone_dir == mirror_dir:
        raise ObsidianWriteError("Obsidian write clone must not be the read-only mirror", False)
    if not config.repo_url.strip() or not config.branch.strip():
        raise ObsidianWriteError("Obsidian write configuration is incomplete", False)
    if not config.ssh_key_path.is_file() or not os.access(config.ssh_key_path, os.R_OK):
        raise ObsidianWriteError("Obsidian write deploy key is missing or unreadable", False)


def _git_environment(config: ObsidianWriteConfig) -> dict[str, str]:
    """Build GIT_SSH_COMMAND with a shell-quoted key path.

    git runs GIT_SSH_COMMAND through a shell and ``ssh_key_path`` comes from the
    agent-writable ``~/.hermes/obsidian-write/config.json``, so raw interpolation
    was arbitrary command execution. ``shlex.join`` matches the existing precedent
    in ``automation/install/apply.py`` and leaves ordinary key paths unchanged.
    """
    environment = dict(os.environ)
    environment["GIT_SSH_COMMAND"] = shlex.join(
        ("ssh", "-i", str(config.ssh_key_path), "-o", "IdentitiesOnly=yes")
    )
    return environment


def _ensure_write_clone(context: _WriteContext) -> None:
    config = context.config
    if (config.clone_dir / ".git").is_dir():
        return
    if config.clone_dir.exists():
        raise ObsidianWriteError("Obsidian write clone is incomplete", False)
    try:
        config.clone_dir.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as error:
        raise ObsidianWriteError("Obsidian write clone directory is unavailable", False) from error
    try:
        repo_url = validate_remote_url(config.repo_url, label="Obsidian write repo_url")
    except GitRemoteUrlError as error:
        raise ObsidianWriteError("Obsidian write repository URL is unsafe", False) from error
    # `--filter` leaves history's blobs on the server; `--` keeps a dash-leading URL
    # out of git's option namespace.
    destination = str(config.clone_dir)
    argv = ("git", "clone", _BLOB_FILTER, "--branch", config.branch, "--", repo_url, destination)
    _ = context.run(
        _GitInvocation(argv, config.fetch_timeout_seconds),
        None,
        "clone write repository",
    )
    try:
        _ = config.clone_dir.chmod(0o700)
    except OSError as error:
        raise ObsidianWriteError("Obsidian write clone permissions are unavailable", False) from error


def _target_path(clone_dir: Path, relpath: PurePosixPath) -> Path:
    if relpath.is_absolute() or ".." in relpath.parts:
        raise ObsidianWriteError("Obsidian note path is not safely relative", False)
    target = clone_dir / Path(relpath)
    try:
        _ = target.resolve(strict=False).relative_to(clone_dir.resolve(strict=False))
    except ValueError as error:
        raise ObsidianWriteError("Obsidian note path escapes the write clone", False) from error
    return target


def _existing_created_date(target: Path) -> str | None:
    if not target.is_file():
        return None
    try:
        for line in target.read_text(encoding="utf-8").splitlines():
            if line.startswith("> Created: "):
                return line.removeprefix("> Created: ").strip() or None
    except OSError as error:
        raise ObsidianWriteError("Obsidian existing note is unreadable", False) from error
    return None


def _atomic_upsert(target: Path, content: str) -> bool:
    try:
        if target.is_file() and target.read_text(encoding="utf-8") == content:
            return False
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=".obsidian-write-",
            delete=False,
        ) as temporary_file:
            _ = temporary_file.write(content)
            temporary_path = Path(temporary_file.name)
        temporary_path.chmod(0o600)
        _ = temporary_path.replace(target)
    except OSError as error:
        raise ObsidianWriteError("Obsidian note could not be written", False) from error
    return True


def _push(
    context: _WriteContext,
    plan: NotePlan,
    approval_context: ApprovalContext | None,
    authorize_push: Callable[[NotePlan], bool] | None = None,
) -> None:
    push_guard = context.config.push_guard
    if push_guard is not None:
        push_guard()
    command_allowed = authorize_push is not None and authorize_push(plan)
    if not command_allowed and not gate_binding.evaluate(plan, context=approval_context).allowed:
        raise ObsidianWriteError("Obsidian note push requires a valid owner approval", False)
    _ = context.run(
        _GitInvocation(("git", "push", "origin", f"HEAD:{context.config.branch}")),
        context.config.clone_dir,
        "push note",
    )
