"""Git mutation boundary for owner-approved repairs."""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Protocol

from automation.typing_compat import override

from automation.regression_bank.bank_state import DEFAULT_STATE_PATH, allows_patch_application
from automation.regression_bank.scenario_registry import RegistrationStatus, ScenarioRegistry
from automation.repair.repair_redaction import redact
from automation.repair.repair_patch_diff import PatchBindingError, parse_patch_changes


class RepairOpsError(RuntimeError):
    """Raised when a repair adapter rejects an unsafe operational request."""


@dataclass(frozen=True, slots=True)
class PatchDigestMismatch(RepairOpsError):
    """The final patch bytes differ from the owner's approved artifact."""

    expected: str
    actual: str

    @override
    def __str__(self) -> str:
        return "repair patch digest differs from owner approval"


class GitRunner(Protocol):
    """Execute a git command at an explicitly selected repository root."""

    def run(self, argv: tuple[str, ...], *, cwd: Path, input: bytes | None = None) -> subprocess.CompletedProcess[str]: ...


@dataclass(frozen=True, slots=True)
class SubprocessGitRunner:
    """Run git with the repair process's fixed subprocess contract."""

    def run(self, argv: tuple[str, ...], *, cwd: Path, input: bytes | None = None) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(argv, cwd=cwd, input=input, capture_output=True, check=False, timeout=900)
        return subprocess.CompletedProcess(
            argv, result.returncode,
            result.stdout.decode("utf-8", errors="replace"),
            result.stderr.decode("utf-8", errors="replace"),
        )


ALLOWED_PREFIXES: Final = ("automation/", "configs/", "tests/", "docs/")
FORBIDDEN_PARTS: Final = ("secret", "credential", ".ssh", "/home/", "/srv/autophagy-private/")
OFFLINE_SCENARIOS: Final = ("w4-mail-triage",)


@dataclass(frozen=True, slots=True)
class GitRepository:
    """Apply repository-only patches inside the dedicated repair work clone."""

    work_clone: Path
    bank_state_path: Path = DEFAULT_STATE_PATH
    runner: GitRunner = field(default_factory=SubprocessGitRunner)
    expected_patch_sha256: str | None = field(default=None, kw_only=True)

    def apply(self, patch_path: Path) -> str:
        """Validate diff paths, apply it, and create the repair commit."""
        content = patch_path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if self.expected_patch_sha256 is not None and digest != self.expected_patch_sha256:
            raise PatchDigestMismatch(self.expected_patch_sha256, digest)
        paths = self._assert_scope(content)
        _ = self._git("apply", "-", input=content)
        _ = self._git("add", "--", *paths)
        _ = self._git("commit", "-m", "fix: apply repair ticket")
        return self._git("rev-parse", "HEAD").stdout.strip()

    def register_bank(self, scenario_path: Path) -> str | None:
        """Merge a validated repair scenario into the patch commit without duplicate bank growth."""
        result = ScenarioRegistry(self.work_clone).register(scenario_path)
        if result.status is RegistrationStatus.MERGED:
            return None
        _ = self._git("add", "--", str(result.path.relative_to(self.work_clone)))
        _ = self._git("commit", "--amend", "--no-edit")
        return self._git("rev-parse", "HEAD").stdout.strip()

    def bank_passes(self, scenario_path: Path | None) -> bool:
        """Run the sandbox-compatible baseline and registered scenario after applying."""
        command = ["bash", "tests/e2e/run_bank.sh"]
        for scenario in OFFLINE_SCENARIOS:
            command.extend(("--scenario", scenario))
        if scenario_path is not None:
            scenario_id = ScenarioRegistry(self.work_clone).validate(scenario_path).scenario_id
            if (self.work_clone / "tests/e2e/scenarios" / f"{scenario_id}.yaml").is_file():
                command.extend(("--scenario", scenario_id))
        completed = subprocess.run(
            tuple(command),
            cwd=self.work_clone,
            capture_output=True,
            check=False,
            text=True,
            timeout=900,
        )
        return completed.returncode == 0

    def bank_allows_apply(self) -> bool:
        """Fail closed before mutation unless the weekly full bank last recorded a pass."""
        return allows_patch_application(self.bank_state_path)

    def revert(self, commit: str) -> None:
        """Undo a regressing repair with an auditable git revert commit."""
        _ = self._git("revert", "--no-edit", commit)

    def _assert_scope(self, content: bytes) -> tuple[str, ...]:
        try:
            changes = parse_patch_changes(content)
        except PatchBindingError as error:
            raise RepairOpsError("repair patch scope cannot be parsed") from error
        paths = tuple(dict.fromkeys(
            path for change in changes for path in (change.old_path, change.new_path)
            if path is not None
        ))
        if not paths or any(not path.startswith(ALLOWED_PREFIXES) for path in paths):
            raise RepairOpsError("repair patch may modify repository code or config only")
        if any(part in path.lower() for path in paths for part in FORBIDDEN_PARTS):
            raise RepairOpsError("repair patch may not access secrets or home directories")
        return tuple(paths)

    def _git(self, *args: str, input: bytes | None = None) -> subprocess.CompletedProcess[str]:
        completed = (
            self.runner.run(("git", *args), cwd=self.work_clone)
            if input is None else self.runner.run(("git", *args), cwd=self.work_clone, input=input)
        )
        if completed.returncode != 0:
            raise RepairOpsError(f"git operation failed: {redact(completed.stderr)[:180]}")
        return completed
