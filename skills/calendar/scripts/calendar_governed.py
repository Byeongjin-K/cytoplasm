"""캘린더 변경 명령이 관리자 배포 사본에서만 실행되도록 판정한다.

배포됨과 실행됨은 같지 않을 수 있으므로 automation 판정을 실행 시점에
지연 import해, 낡은 저장소 사본이 외부효과를 일으키지 않게 한다.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

GOVERNED_LIVE_ROOT = Path("/srv/autophagy-skills/live")
LIVE_ROOT_ENV = "AUTOPHAGY_SKILL_LIVE_ROOT"
SKILL_NAME = "calendar"
STALE_COPY_MARKER = "STALE-SKILL-COPY-BLOCK"
MUTATING_SUBCOMMANDS: frozenset[str] = frozenset({
    "draft-create", "draft-update", "draft-delete", "confirm", "post-confirm",
    "discard", "sign", "owner-create",
})


def _automation_root() -> Path:
    """automation/skill_mount.py를 포함하는 런타임 checkout을 찾는다."""
    configured = os.environ.get("AUTOPHAGY_REPO_ROOT")
    if configured:
        return Path(configured).expanduser()
    for candidate in (Path("/srv/autophagy-agent-current"), *Path(__file__).resolve().parents):
        if (candidate / "automation" / "skill_mount.py").is_file():
            return candidate
    return Path("/srv/autophagy-agent-current")


def refusal(script: Path, *, env: Mapping[str, str] | None = None) -> str | None:
    """변경 명령이 governed 사본인지 canonical 판정으로 확인한다."""
    root = _automation_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from automation.skill_mount import governed_copy_refusal
    except ImportError:
        environment = os.environ if env is None else env
        live_root = Path(environment.get(LIVE_ROOT_ENV, "").strip() or GOVERNED_LIVE_ROOT).expanduser()
        governed = live_root / SKILL_NAME / "scripts"
        if not governed.is_dir():
            return None
        return f"{STALE_COPY_MARKER}: calendar 배포 사본을 판정할 수 없어 실행하지 않는다"
    return governed_copy_refusal(SKILL_NAME, script, env=env)


def guard(script: Path, subcommand: str, *, env: Mapping[str, str] | None = None) -> None:
    """Block mutating commands when invoked from a non-governed copy."""
    if subcommand not in MUTATING_SUBCOMMANDS:
        return
    message = refusal(script, env=env)
    if message is None:
        return
    print(message, file=sys.stderr)
    raise SystemExit(3)
