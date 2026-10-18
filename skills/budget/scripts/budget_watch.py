#!/usr/bin/env python3
"""Run the governed budget watcher; the timer runner owns repeated failures.

Success is silent. A failed child returns a failed tick with masked detail,
without reading or writing the retired watch-failure ledger."""
from __future__ import annotations

import importlib
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

# The streak helper is deployed flat beside this wrapper (~/.hermes/scripts/), so the
# first insert covers the node. In the repo it lives under the mail skill instead — one
# shared copy, no forks (계획 CR-1) — so the second insert covers pytest and a local
# run from a checkout. A partial deploy must not take the watcher down, hence the
# fallback (mail_triage_watch.py precedent).
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mail" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import watch_failure_streak
except ImportError:  # pragma: no cover — only reachable on a half-deployed node
    watch_failure_streak = None

WATCH_NAME: Final = "budget-watch"
#: `*/30` — three consecutive failures ≈ 1.5h. The 2026-08-23 23:30 Sheets 503 healed on
#: the next tick and must stay silent; a revoked permission or a deleted tab must not.
FAILURE_NOTICE_THRESHOLD: Final = 3

# 마운트 판정은 governed live 정의 하나(automation/skill_mount.py)에서만 온다. 노드에서
# 이 래퍼는 ~/.hermes/scripts/ 에 평평하게 배포되므로 코드 루트를 값으로 되짚는다 —
# 체크아웃 → 릴리스 current → 미러 (test_skill_runtime_root_fallback.py 와 같은 관용구).
for _root in (
    *Path(__file__).resolve().parents,
    Path(os.environ.get("AUTOPHAGY_RUNTIME_ROOT") or "/srv/autophagy-agent-current"),
    Path("/srv/autophagy-agents"),
):
    if (_root / "automation" / "skill_mount.py").is_file():
        sys.path.insert(0, str(_root))
        break
try:
    from automation.skill_mount import skill_scripts
except ImportError:  # pragma: no cover — 릴리스가 아직 이 정의에 수렴하지 않은 노드
    _SCRIPTS = None  # 판정할 수 없으면 마운트 없음으로 fail-closed 한다
else:
    _SCRIPTS = skill_scripts("budget", env_var="BUDGET_SCRIPTS")
_ENV_SECRETS: Final = Path.home() / ".env.secrets"
CLI = None if _SCRIPTS is None else _SCRIPTS / "budget_cli.py"
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LONG_DIGITS = re.compile(r"\d{5,}")


def _redact(text: str) -> str:
    return _LONG_DIGITS.sub("[MASKED-NUM]", _EMAIL.sub("[MASKED-EMAIL]", text))


def _load_env_secrets(path: Path = _ENV_SECRETS) -> None:
    """no-agent cron gets no secrets in os.environ, so the parent loads them itself.

    Measured 2026-08-18: with `BUDGET_SHEET_ID` present in ~/.env.secrets the tick
    still died on `GATE-REFUSED BUDGET_SHEET_ID가 없습니다 (fail-closed)`, while the
    same wrapper run after `set -a; . ~/.env.secrets` exited 0 — the configuration
    was fine and simply never reached the child. Same shape as
    todo_confirm_reaction_watch._load_env_secrets.

    budget was only the watcher whose missing configuration happened to be *visible*
    (a sheet id the gate names out loud). Since 2026-08-20 the same contract is checked
    across every watcher a deploy script pushes to ~/.hermes/scripts/, by
    tests/unit/test_watcher_secret_propagation.py — five more were failing it.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def _announce(*, ok: bool, detail: str = "") -> bool:
    """The timer runner owns failure counting; report the actual failing exit."""
    if not ok:
        print(_redact(f"{WATCH_NAME} error: {detail}")[:300])
    return False


def _expire_pending_requests() -> None:
    """만료 처리는 watch 자식보다 먼저 끝내 재게시 경쟁을 막고, 실패는 tick을 바꾸지 않는다."""
    if _SCRIPTS is None:
        return
    scripts = str(_SCRIPTS)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    try:
        budget_confirm = importlib.import_module("budget_confirm")
        budget_confirm.expire_pending_drafts(datetime.now(UTC))
    except Exception as error:  # noqa: BLE001 — 만료 보조 경로가 cron 종료를 바꿔서는 안 된다
        print(f"BUDGET-EXPIRY-FAIL err={type(error).__name__}", file=sys.stderr)


def main() -> int:
    _load_env_secrets()
    if CLI is None or not CLI.exists():
        return 0 if _announce(ok=False, detail="budget skill is not mounted") else 1
    _expire_pending_requests()
    result = subprocess.run(  # noqa: S603 — fixed argv, agent-owned script
        [sys.executable, str(CLI), "watch"],
        capture_output=True, text=True, timeout=600, check=False,
        cwd=str(Path.home()),
        # Rule (b-2): state the child's environment explicitly rather than letting
        # it fall back — the credentials only exist because we just self-loaded them.
        env={**os.environ},
    )
    if result.returncode == 0:
        _announce(ok=True)  # silent tick unless it closes an open incident
        return 0  # stdout of the child intentionally dropped
    tail = (result.stderr or result.stdout).strip().splitlines()
    detail = tail[-1] if tail else f"rc={result.returncode}"
    recorded = _announce(ok=False, detail=f"rc={result.returncode}: {_redact(detail)[:200]}")
    return 0 if recorded else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001 — cron crash path: immediate masked line
        try:
            print(f"budget-watch error: {_redact(str(error))}"[:300])
        except BrokenPipeError:
            pass
        sys.exit(1)
