"""Filesystem, git, peer-sandbox, and signed-approval adapters for W6-2."""

from __future__ import annotations

import re
import shutil
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from automation.codex_llm import CodexError, complete as codex_complete
from automation.repair.repair_ops_git import GitRepository, RepairOpsError
from automation.repair.repair_ops_approval import (
    ApprovalReaction,  # noqa: F401 - stable public adapter re-export
    ManualOwnerApproval,  # noqa: F401 - stable public adapter re-export
    SignedOwnerApproval,  # noqa: F401 - stable public adapter re-export
)
from automation.repair.repair_ops_core import RepairPlan, SandboxVerdict, safe_diagnosis
from automation.repair.repair_redaction import redact


__all__ = ("ApprovalReaction", "GitRepository", "ManualOwnerApproval", "SignedOwnerApproval")


OFFLINE_SCENARIOS: Final = (
    "w4-mail-triage",
)
SANDBOX_OUTPUT_TAIL: Final = 160
_SCENARIO_ID_LINE: Final = re.compile(r"^id:\s*.*$", re.MULTILINE)


def private_log(ticket_id: str, root: Path) -> str:
    """Read only the ticket's ops-owned logs, never agent-home secrets."""
    ticket_root = (root / ticket_id).resolve()
    if ticket_root.parent != root.resolve() or not ticket_root.is_dir():
        raise RepairOpsError("private repair ticket log is unavailable")
    return "\n".join(redact(path.read_text(encoding="utf-8")) for path in sorted(ticket_root.glob("*.log")))


def normalize_bank_scenario(ticket_id: str, scenario_path: Path) -> str:
    """Bind a private repair scenario to a strict registry-safe ticket slug."""
    ticket_slug = re.sub(r"[^a-z0-9]+", "-", ticket_id.removeprefix("t_").lower()).strip("-")
    scenario_id = f"w6-{ticket_slug or 'repair'}"
    updated, replacements = _SCENARIO_ID_LINE.subn(f"id: {scenario_id}", scenario_path.read_text(encoding="utf-8"), count=1)
    if replacements != 1:
        raise RepairOpsError("repair bank scenario must declare one top-level id")
    _ = scenario_path.write_text(updated, encoding="utf-8")
    return scenario_id


def _bank_scenario(ticket_id: str, directory: Path) -> Path | None:
    scenario = directory / "scenario.yaml"
    if not scenario.is_file():
        return None
    _ = normalize_bank_scenario(ticket_id, scenario)
    return scenario


@dataclass(frozen=True, slots=True)
class StaticPlanner:
    """Consume an ops-authored redacted plan; premium diagnosis remains off-repo."""

    plan_root: Path

    def plan(self, ticket_id: str, private_log: str) -> RepairPlan:
        """Load the plan and bind its diagnosis to a sanitized private-log summary."""
        directory = self.plan_root / ticket_id
        repro = directory / "repro.sh"
        patch = directory / "patch.diff"
        if not repro.is_file() or not patch.is_file():
            raise RepairOpsError("repair plan must contain repro.sh and patch.diff")
        return RepairPlan(ticket_id, safe_diagnosis(private_log), repro, patch, _bank_scenario(ticket_id, directory))


@dataclass(frozen=True, slots=True)
class CodexPlanner:
    """Use the mandated premium model while retaining a separately reviewable diff."""

    plan_root: Path

    def plan(self, ticket_id: str, private_log: str) -> RepairPlan:
        """Diagnose one ops-private log through openai-codex before proposing its patch."""
        directory = self.plan_root / ticket_id
        repro = directory / "repro.sh"
        patch = directory / "patch.diff"
        if not repro.is_file() or not patch.is_file():
            raise RepairOpsError("repair plan must contain repro.sh and patch.diff")
        excerpt = safe_diagnosis(private_log)
        try:
            diagnosis = codex_complete(f"Diagnose this redacted repair excerpt: {excerpt}")
        except CodexError as error:
            raise RepairOpsError(f"Codex OAuth diagnosis failed: {redact(str(error))[:180]}") from error
        return RepairPlan(
            ticket_id,
            safe_diagnosis(diagnosis),
            repro,
            patch,
            _bank_scenario(ticket_id, directory),
        )


@dataclass(frozen=True, slots=True)
class PeerSandbox:
    """Run the existing bank and the new reproduction independently as peer."""

    peer_host: str
    checkout: Path

    def validate(self, plan: RepairPlan) -> SandboxVerdict:
        """Stage only the patch and repro in an isolated peer clone before validation."""
        with tempfile.TemporaryDirectory(prefix="autophagy-repair-") as temporary:
            staging = Path(temporary)
            _ = staging.chmod(0o755)
            patch = staging / "patch.diff"
            repro = staging / "repro.sh"
            _ = shutil.copyfile(plan.patch_path, patch)
            _ = shutil.copyfile(plan.repro_path, repro)
            patch.chmod(0o644)
            repro.chmod(0o755)
            repro_result = self._run(self.staged_command(patch, f"bash {shlex.quote(str(repro))}"))
            bank_result = self._run(self.staged_command(patch, self._offline_bank_command()))
        return SandboxVerdict(
            bank_result.returncode == 0,
            repro_result.returncode == 0,
            bank_result.returncode,
            self._masked_tail(bank_result.stdout),
            self._masked_tail(bank_result.stderr),
            repro_result.returncode,
            self._masked_tail(repro_result.stdout),
            self._masked_tail(repro_result.stderr),
        )

    @staticmethod
    def _masked_tail(output: str) -> str:
        return " ".join(redact(output).split())[-SANDBOX_OUTPUT_TAIL:]

    def staged_command(self, patch: Path, check: str) -> str:
        return " && ".join(
            (
                "sandbox=$(mktemp -d)",
                "trap 'rm -rf \"$sandbox\"' EXIT",
                "mkdir \"$sandbox/home\"",
                f"HOME=\"$sandbox/home\" git config --global --add safe.directory {shlex.quote(str(self.checkout))}",
                f"HOME=\"$sandbox/home\" git config --global --add safe.directory {shlex.quote(str(self.checkout / '.git'))}",
                f"HOME=\"$sandbox/home\" git clone --shared {shlex.quote(str(self.checkout))} \"$sandbox/repo\"",
                "cd \"$sandbox/repo\"",
                f"git apply {shlex.quote(str(patch))}",
                check,
            )
        )

    @staticmethod
    def _offline_bank_command() -> str:
        """Run deterministic W4/W5 scenarios; live SSH/provider scenarios remain F3 coverage."""
        commands: list[str] = ["mkdir reports"]
        for scenario in OFFLINE_SCENARIOS:
            driver = "w4_local.sh" if scenario.startswith("w4-") else "w5_local.sh"
            commands.append(f"bash tests/e2e/drivers/{driver} tests/e2e/scenarios/{scenario}.yaml reports")
        return " && ".join(commands)

    def _run(self, command: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ("bash", "-lc", f"cd {self.checkout} && {command}"),
            capture_output=True,
            check=False,
            text=True,
            timeout=900,
        )
