"""One managed-skill sync tick — the single tick implementation both deployments run.

Two deployments share this file, which is why the tick logic lives here and not in
either deployer:

- ``automation/managed_sync/deploy.sh`` pushes it to ``~/.hermes/scripts/managed_sync_watch.py``
  and registers the Hermes no-agent cron (the existing watcher convention);
- ``automation/managed_sync/systemd/autophagy-managed-sync.{service,timer}`` runs the same
  path out of the immutable release for installs driven by ``automation/install``
  (an OPT-IN component — it is not installed unless the operator asks for it).

Git-polling manual indexer (rag-ingest-watch class, 규약 (a) 예외): each tick runs
exactly one ``sync`` subcommand pass. Managed skills still stop at verify + quarantine;
the same shared-mirror tick separately verifies and atomically refreshes the roster.
Owner-gated steps stay owner-gated: this wrapper never invokes any other subcommand and
never passes any extra flag. **Delivery is automatic; MOUNTING is not** (D3) — a staged
release sits in quarantine until the subscriber's own ✅ mounts it, so nothing here
may reach the mount path.

No-agent cron contract (docs/guide/watcher-cron-설계규약.md):
- (b) secrets are self-loaded from ``~/.env.secrets`` (system env wins);
- (b-2) the child subprocess receives credentials via an explicit ``env=``;
- (c) the runtime root is resolved by the DG-4 order and exported to the child as BOTH
  ``AUTOPHAGY_REPO_ROOT`` and ``AUTOPHAGY_RUNTIME_ROOT`` plus ``PYTHONPATH``;
- (i) the owner notice is best-effort — a failed notice never undoes a staged release;
- overlapping ticks are serialised by the repo's standard ``FileKeyLease``; the loser
  exits 0 silently rather than reporting an incident that did not happen.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from automation.interop.owner_message import OwnerMessage


# Runtime root order (DG-4): AUTOPHAGY_REPO_ROOT override, else the release
# `current` symlink, else the resident mirror. Inlined by value because this
# wrapper sets sys.path BEFORE it can import automation.runtime_root.
def _runtime_root() -> Path:
    override = os.environ.get("AUTOPHAGY_REPO_ROOT", "").strip()
    if override:
        return Path(override).expanduser()
    current = Path("/srv/autophagy-agent-current")
    return current if current.exists() else Path("/srv/autophagy-agents")


REPO_ROOT = _runtime_root()
SECRETS_PATH = Path.home() / ".env.secrets"
LEASE_ROOT = Path.home() / ".hermes" / "managed-sync" / "leases"
LEASE_KEY = "managed-sync-tick"

sys.path.insert(0, str(REPO_ROOT))

from automation import owner_notice  # noqa: E402
from automation.interop.approval_lease import FileKeyLease  # noqa: E402
from automation.owner_notice import notify_owner  # noqa: E402


def load_secrets(path: Path = SECRETS_PATH) -> dict[str, str]:
    """Parse ``KEY=VALUE`` lines from ``~/.env.secrets`` (규약 (b))."""
    secrets: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return secrets
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        secrets[key.strip()] = value.strip().strip('"').strip("'")
    return secrets


def child_environment(secrets: dict[str, str]) -> dict[str, str]:
    """Build the child env explicitly (규약 (b-2), (c)) — never rely on its own fallback."""
    environment = dict(secrets)
    environment.update(os.environ)
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        str(REPO_ROOT)
        if existing_pythonpath is None
        else str(REPO_ROOT) + os.pathsep + existing_pythonpath
    )
    environment["AUTOPHAGY_REPO_ROOT"] = str(REPO_ROOT)
    environment["AUTOPHAGY_RUNTIME_ROOT"] = str(REPO_ROOT)
    return environment


def staged_notice(stdout: str) -> str | None:
    """Render one owner notice from the tick's ``SYNC-STAGED`` lines, or ``None``.

    Only newly staged releases are notified. The pipeline marks state after each
    successful stage, so a release produces exactly one ``SYNC-STAGED`` line ever —
    that is what keeps a 30-minute timer from re-announcing the same release forever.
    Rejections are deliberately NOT notified on a timer: an unverifiable release keeps
    failing every tick, so a per-tick DM would be a self-inflicted flood. Their reason
    is still exposed, on the tick's ``SYNC-FAILED reason=...`` line, which this wrapper
    always re-emits to the journal.
    """
    staged = [line for line in stdout.splitlines() if line.startswith("SYNC-STAGED ")]
    if not staged:
        return None
    lines = ["managed-sync: 새 관리형 스킬 릴리스가 격리(quarantine)에 도착했습니다."]
    for line in staged:
        fields: dict[str, str] = {}
        for part in line.split()[1:]:
            key, separator, value = part.partition("=")
            if separator:
                fields[key] = value
        digest = fields.get("digest", "")
        skill = fields.get("skill", "?")
        sequence = fields.get("sequence", "?")
        lines.append(f"- {skill} seq={sequence} digest={digest[:12]}")
    lines.append(
        "격리에서 꺼내 마운트하려면 본인의 ✅가 필요합니다 — 자동으로 동작하지 않습니다."
        + " 절차는 구독자 매뉴얼을 보세요."
    )
    return "\n".join(lines)


def staged_message(content: str) -> OwnerMessage | None:
    """격리 도착 결과. 관측 구간·승인 카드 좌표는 이 틱에 없다."""
    try:
        from automation.interop.owner_message import Action, OwnerMessage, Ref, Result
    except Exception:  # noqa: BLE001 - optional module initialization must preserve string delivery
        return None
    return OwnerMessage(
        subject_key="managed-sync", subject="관리형 스킬 격리 도착", fact=content,
        location=Ref(scope="resource", search=("관리형 릴리스", " ".join(content.splitlines()[1:-1]))),
        owner=Action(verb="none"), agent_next="격리 유지; 자동 마운트 없음",
        recovery="not_applicable", detail=Result(outcome="executed"),
    )


def run_sync_once() -> tuple[int, str]:
    """Run exactly one fetch/verify/quarantine pass; return its rc and stdout.

    Output is captured so the tick can read its own ``SYNC-STAGED`` lines, then
    re-emitted verbatim — the journal must keep every ``SYNC-FAILED reason=...`` line,
    which is how a badly signed release exposes why it was refused.
    """
    completed = subprocess.run(
        [sys.executable, "-m", "automation.managed_sync", "sync"],
        env=child_environment(load_secrets()),
        cwd=str(REPO_ROOT),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    return completed.returncode, completed.stdout


def run_roster_once() -> None:
    """Refresh the signed roster through its separate fixed-branch read path."""
    from automation.managed_sync.cli import config_path, load_config, roster_path
    from automation.managed_sync.roster_tick import run

    run(load_config(config_path()), roster_path())


def run_tick() -> int:
    """Own the tick lease, sync once, then notify best-effort. 0 = nothing to report."""
    with FileKeyLease(LEASE_ROOT).hold(LEASE_KEY) as owned:
        if not owned:
            return 0
        # Secrets reach the notice through os.environ (규약 (b-2) option ②); the child
        # still gets them explicitly through env= (option ①), never by inheritance alone.
        for key, value in load_secrets().items():
            _ = os.environ.setdefault(key, value)
        code, stdout = run_sync_once()
        if code == 0:
            run_roster_once()
        notice = staged_notice(stdout)
        if notice is not None:
            # (i) best-effort: a failed DM must not undo an already-quarantined release.
            message = staged_message(notice)
            if message is not None and getattr(owner_notice, "ACCEPTS_OWNER_MESSAGE", False):
                _ = notify_owner(notice, message=message, fault=True)
            else:
                _ = notify_owner(notice, fault=True)
        return code


if __name__ == "__main__":
    sys.exit(run_tick())
