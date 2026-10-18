"""Fixed-environment card processes; no shell or effect execution."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


class Refused(Exception):
    pass


def card_env(home: Path, release: Path, baseline: dict[str, str]) -> dict[str, str]:
    """Load only the card producer credentials, never SSH configuration overrides."""
    values = dict(baseline)
    for line in (home / ".env.secrets").read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().removeprefix("export ").partition("=")
        if sep and key in {"DISCORD_BOT_TOKEN", "AUTOPHAGY_OWNER_ID"}:
            values[key] = value.strip().strip("\"'")
    values.update(PYTHONPATH=str(release), AUTOPHAGY_REPO_ROOT=str(release),
                  AUTOPHAGY_RUNTIME_ROOT=str(release))
    return values


def calendar_card(argv: list[str], cwd: Path, env: dict[str, str], timeout: int) -> int:
    """Create one draft, then post that exact id with the existing card producer."""
    draft = subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                           timeout=timeout, check=False, capture_output=True, text=True)
    if draft.returncode:
        sys.stderr.write("YOODORI-GATE-DRAFT-FAILED calendar-request\n")
        return draft.returncode
    ids = re.findall(r"^DRAFT-CREATED id=([0-9a-f]{6}) ", draft.stdout, re.MULTILINE)
    if len(ids) != 1:
        raise Refused("calendar did not return exactly one draft id")
    return subprocess.run([*argv[:2], "post-confirm", "--draft", ids[0]], cwd=cwd,
                          env=env, stdin=subprocess.DEVNULL, timeout=timeout, check=False).returncode
