#!/usr/bin/env python3
"""Exercise the W6-2 owner-gated repair chain in an ops-owned temporary clone."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypeAlias, cast

from automation.interop.injection_adapter import InboundEvent, sign_event
from automation.regression_bank.bank_state import record_result
from automation.repair.repair_lifecycle import LifecycleState, RepairLifecycleStore
from automation.repair.repair_ops_core import RepairPhase
from automation.repair.repair_redaction import redact


FIXTURE_TARGET: Final = Path("automation/repair/_w62_live_fixture.py")
FIXTURE_BROKEN: Final = 'REPAIR_E2E_STATE = "broken"\n'
FIXTURE_REPAIRED: Final = 'REPAIR_E2E_STATE = "repaired"\n'


JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


@dataclass(frozen=True, slots=True)
class RunSpec:
    ticket_id: str
    event_user_id: str
    mirror_failure: bool
    regression_scenario: bool
    expected_phase: RepairPhase
    expected_state: LifecycleState


def _run(command: tuple[str, ...], cwd: Path, env: dict[str, str], timeout: int = 900) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, env=env, capture_output=True, check=False, text=True, timeout=timeout)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _require_success(result: subprocess.CompletedProcess[str], message: str) -> None:
    _require(result.returncode == 0, f"{message}: rc={result.returncode}")


def _write_repro(plan_dir: Path) -> Path:
    repro = plan_dir / "repro.sh"
    _ = repro.write_text(
        "\n".join(
            (
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'd="$(mktemp -d)"',
                "trap 'rm -rf \"$d\"' EXIT",
                f"grep -Fx {FIXTURE_REPAIRED.strip()!r} {FIXTURE_TARGET} > \"$d/verdict\"",
                "",
            )
        ),
        encoding="utf-8",
    )
    repro.chmod(0o755)
    return repro


def _write_scenario(plan_dir: Path, spec: RunSpec) -> Path:
    scenario = plan_dir / "scenario.yaml"
    expected_exit = 999 if spec.regression_scenario else 0
    _ = scenario.write_text(
        "\n".join(
            (
                "version: 1",
                f"id: {_scenario_id(spec)}",
                'title: "W6-2 temporary repair registration E2E"',
                "driver: tests/e2e/drivers/w4_local.sh",
                "actor: tests/e2e/drivers/w4_mail_triage_actor.py",
                "cases:",
                "  - id: triage_draft",
                "    kind: happy",
                "    steps:",
                "      - temporary repair registration checks the existing offline actor",
                "    expect:",
                f"      process_exit: {expected_exit}",
                "      error: null",
                "",
            )
        ),
        encoding="utf-8",
    )
    return scenario


def _scenario_id(spec: RunSpec) -> str:
    return f"w6-{spec.ticket_id[2:].lower()}"


def _write_patch(plan_dir: Path) -> Path:
    patch = plan_dir / "patch.diff"
    _ = patch.write_text(
        "\n".join(
            (
                f"--- a/{FIXTURE_TARGET}",
                f"+++ b/{FIXTURE_TARGET}",
                "@@ -1 +1 @@",
                f"-{FIXTURE_BROKEN.rstrip()}",
                f"+{FIXTURE_REPAIRED.rstrip()}",
                "",
            )
        ),
        encoding="utf-8",
    )
    return patch


def _configure_clone(source: Path, clone: Path, env: dict[str, str]) -> None:
    _require_success(_run(("git", "clone", "--shared", str(source), str(clone)), source, env), "temporary clone")
    _ = shutil.copytree(source / "automation" / "repair", clone / "automation" / "repair", dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    _require_success(_run(("git", "config", "user.name", "W6-2 repair E2E"), clone, env), "git user name")
    _require_success(_run(("git", "config", "user.email", "w6-2-e2e@example.invalid"), clone, env), "git user email")
    target = clone / FIXTURE_TARGET
    _ = target.write_text(FIXTURE_BROKEN, encoding="utf-8")
    _require_success(_run(("git", "add", "--", str(FIXTURE_TARGET)), clone, env), "stage fixture bug")
    _require_success(_run(("git", "commit", "-m", "test: add temporary W6-2 fixture bug"), clone, env), "commit fixture bug")


def _prepare_env(root: Path, clone: Path, plan_dir: Path, spec: RunSpec) -> tuple[dict[str, str], str, Path]:
    secret = "".join(("sk-", "w62", "-fixture", "-runtime"))
    log_dir = root / "logs" / spec.ticket_id
    log_dir.mkdir(parents=True)
    _ = (log_dir / "occurrence-1.log").write_text(f"RuntimeError {secret}", encoding="utf-8")
    bank_state = root / "bank-state.json"
    _ = record_result(bank_state, 0)
    patch = plan_dir / "patch.diff"
    action_hash = hashlib.sha256(f"repair:{spec.ticket_id}:{patch.name}".encode()).hexdigest()
    event = InboundEvent("w62-live-event", spec.event_user_id, "approvals", f"APPROVE repair {action_hash} ticket:{spec.ticket_id}")
    secret_bytes = bytes.fromhex("44" * 32)
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_MASTER": "1",
            "PYTHONPATH": str(clone),
            "E2E_TEST_MODE": "1",
            "REPAIR_DIAGNOSIS_PROVIDER": "static-e2e",
            "AUTOPHAGY_OWNER_ID": "cha",
            "REPAIR_CHECKOUT": str(clone),
            "REPAIR_LOG_ROOT": str(root / "logs"),
            "REPAIR_PLAN_ROOT": str(root / "plans"),
            "REPAIR_APPROVAL_LOG": str(root / "approvals.jsonl"),
            "REPAIR_STATE_ROOT": str(root / "state"),
            "REPAIR_BANK_STATE": str(bank_state),
            "REPAIR_E2E_EVENT_ID": event.event_id,
            "REPAIR_E2E_USER_ID": event.user_id,
            "REPAIR_E2E_CHANNEL_ID": event.channel_id,
            "REPAIR_E2E_TEXT": event.text,
            "REPAIR_E2E_SIGNATURE": sign_event(event, secret_bytes),
            "REPAIR_E2E_SECRET": secret_bytes.hex(),
        }
    )
    marker = root / f"mirror-skipped-{spec.ticket_id}"
    if spec.mirror_failure:
        shim = root / f"shim-{spec.ticket_id}"
        shim.mkdir()
        sudo = shim / "sudo"
        _ = sudo.write_text(
            "\n".join(
                (
                    "#!/usr/bin/env bash",
                    'if [[ "$*" == *"hermes kanban"* ]]; then',
                    '  : > "$REPAIR_E2E_MIRROR_MARKER"',
                    "  exit 1",
                    "fi",
                    'exec /usr/bin/sudo "$@"',
                    "",
                )
            ),
            encoding="utf-8",
        )
        sudo.chmod(0o755)
        environment["PATH"] = f"{shim}:{environment['PATH']}"
        environment["REPAIR_E2E_MIRROR_MARKER"] = str(marker)
    return environment, secret, marker


def _phase(stdout: str) -> str:
    raw = cast(JsonValue, json.loads(stdout))
    if not isinstance(raw, dict):
        raise RuntimeError("repair CLI emitted invalid JSON")
    phase = raw.get("phase")
    if not isinstance(phase, str):
        raise RuntimeError("repair CLI omitted outcome phase")
    return phase


def _bank_count(clone: Path, env: dict[str, str]) -> int:
    result = _run(("bash", "tests/e2e/run_bank.sh", "--list"), clone, env)
    _require_success(result, "list bank")
    return len(result.stdout.splitlines())


def _replay_full_bank_failure(clone: Path, plan_dir: Path, spec: RunSpec, env: dict[str, str]) -> str:
    target = clone / "tests/e2e/scenarios" / f"{_scenario_id(spec)}.yaml"
    _ = shutil.copyfile(plan_dir / "scenario.yaml", target)
    try:
        result = _run(("bash", "tests/e2e/run_bank.sh", "--all"), clone, env)
        stdout = " ".join(redact(result.stdout).split())[-320:]
        stderr = " ".join(redact(result.stderr).split())[-320:]
        return f"rc={result.returncode} stdout_tail={stdout!r} stderr_tail={stderr!r}"
    finally:
        target.unlink()


def _run_case(source: Path, root: Path, spec: RunSpec) -> None:
    clone = root / spec.ticket_id
    environment = os.environ.copy()
    environment["GIT_MASTER"] = "1"
    _configure_clone(source, clone, environment)
    plan_dir = root / "plans" / spec.ticket_id
    plan_dir.mkdir(parents=True)
    repro = _write_repro(plan_dir)
    _ = _write_patch(plan_dir)
    _ = _write_scenario(plan_dir, spec)
    red = _run(("bash", str(repro)), clone, environment)
    _require(red.returncode != 0, "fixture repro was not RED before patch application")
    environment, secret, _marker = _prepare_env(root, clone, plan_dir, spec)
    result = _run(("python3", "automation/repair/repair_ops_cli.py", spec.ticket_id), clone, environment)
    _require_success(result, "repair CLI")
    record = RepairLifecycleStore(root / "state").read(spec.ticket_id)
    phase = _phase(result.stdout)
    replay = _replay_full_bank_failure(clone, plan_dir, spec, environment) if phase != spec.expected_phase.value else ""
    _require(
        phase == spec.expected_phase.value,
        f"unexpected repair phase={phase} lifecycle={record.state.value} reason={record.reason} sandbox={record.sandbox_checks} replay={replay}",
    )
    _require(record.state is spec.expected_state, "unexpected durable lifecycle state")
    _require("bank rc=0" in record.sandbox_checks and "repro rc=0" in record.sandbox_checks, "sandbox evidence missing")
    _require(secret not in record.sandbox_checks, "sensitive fixture escaped lifecycle store")
    if spec.expected_phase is RepairPhase.COMPLETED:
        _require(_run(("bash", str(repro)), clone, environment).returncode == 0, "fixture repro was not GREEN after apply")
        _require(_bank_count(clone, environment) == 13, "repair scenario was not registered")
        patch_docs = list((clone / "docs/patch").glob(f"*-{spec.ticket_id}.md"))
        _require(len(patch_docs) == 1 and secret not in patch_docs[0].read_text(encoding="utf-8"), "patch document was missing or unredacted")
        return
    _require(_bank_count(clone, environment) == 12, "non-completed repair changed the bank")
    _require(_run(("bash", str(repro)), clone, environment).returncode != 0, "non-completed repair changed fixture")
    if spec.expected_phase is RepairPhase.REOPENED:
        history = _run(("git", "log", "--format=%s"), clone, environment)
        _require_success(history, "inspect rollback history")
        _require('Revert "fix: apply repair ticket"' in history.stdout, "regression repair was not reverted")


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] != "--source":
        raise SystemExit("usage: repair_ops_live_e2e.py --source REPOSITORY")
    source = Path(argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="autophagy-w62-live-") as temporary:
        root = Path(temporary)
        _run_case(source, root, RunSpec("t_w62owner", "cha", True, False, RepairPhase.COMPLETED, LifecycleState.DONE))
        _run_case(source, root, RunSpec("t_w62bot", "peer-bot", False, False, RepairPhase.AWAITING_APPROVAL, LifecycleState.AWAITING_APPROVAL))
        _run_case(source, root, RunSpec("t_w62regress", "cha", True, True, RepairPhase.REOPENED, LifecycleState.REOPENED))
    print("W6-2-LIVE-E2E: PASS owner-apply-register-done; bot-no-apply; regression-revert-reopened; mirror-skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
