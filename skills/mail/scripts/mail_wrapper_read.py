"""Read-path infrastructure shared by the mail wrapper's public commands."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
from pathlib import Path

from mailon_interface import (
    MAILON_ENV_ALLOWLIST,
    MAILON_INTERFACE,
    RESOLVE_GROUP_PRIORITY,
    SYSTEM_ENV_KEEP,
)

#: How long a timed-out mailon gets after SIGTERM to record its run and close its browser.
TERMINATE_GRACE_SECONDS = 30


def _cfg() -> dict:
    home = Path(os.environ.get("HOME", "~")).expanduser()
    # The vendored mailon package runs from a versioned runtime release outside
    # the git checkout (~/.hermes/mailon-runtime/current -> releases/<digest>);
    # `current` is deploy-managed so runtime writes (data/, logs/) never dirty
    # the checkout. Resolve the symlink first, then derive every path from the
    # SAME release so the wrapper read-path and mailon write-path cannot diverge
    # (split-brain). MAIL_WRAPPER_REPO overrides the runtime root (tests, cutover).
    default_repo = home / ".hermes" / "mailon-runtime" / "current"
    repo = Path(os.environ.get("MAIL_WRAPPER_REPO", default_repo))
    resolved = repo.resolve() if repo.is_symlink() else repo
    return {
        "repo": resolved,
        "python": Path(os.environ.get("MAIL_WRAPPER_PYTHON", resolved / ".venv/bin/python")),
        "env_file": Path(os.environ.get("MAIL_WRAPPER_ENV_FILE", home / ".env.secrets")),
        "db": Path(os.environ.get("MAIL_WRAPPER_DB", resolved / "data/state.db")),
        "mails_dir": Path(os.environ.get("MAIL_WRAPPER_MAILS_DIR", resolved / "data/mails")),
        "timeout": int(os.environ.get("MAIL_WRAPPER_TIMEOUT", "900")),
        "mask_salt": os.environ.get("MAIL_WRAPPER_MASK_SALT", ""),
    }


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        key = key.strip()
        if key in MAILON_ENV_ALLOWLIST:
            values[key] = raw.strip().strip("'\"")
    return values


def build_subprocess_env(cfg: dict) -> dict[str, str]:
    """Return the least-privilege environment for a mailon read subprocess."""
    env = {key: os.environ[key] for key in SYSTEM_ENV_KEEP if key in os.environ}
    file_values = parse_env_file(cfg["env_file"])
    for key in MAILON_ENV_ALLOWLIST:
        if key in os.environ:
            env[key] = os.environ[key]
        elif key in file_values:
            env[key] = file_values[key]
    env.setdefault("HEADLESS", "true")
    local_bin = str(Path(os.environ.get("HOME", "~")).expanduser() / ".local/bin")
    path = env.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    if local_bin not in path.split(":"):
        env["PATH"] = f"{local_bin}:{path}"
    return env


def mask_value(value: str, salt: str = "") -> str:
    digest = hashlib.sha256((salt + value).encode("utf-8")).hexdigest()
    return f"sha256:{digest[:16]}"


def classify_stderr(stderr: str) -> str:
    for name, marker in MAILON_INTERFACE["failure_signatures"].items():
        if marker in stderr:
            return name
    return "unclassified"


def run_mailon(cfg: dict, argv: list[str]) -> tuple[int, str, str]:
    """Run only the cached read-only mailon command surface."""
    cmd = [str(cfg["python"]), "-m", MAILON_INTERFACE["module"], *argv]
    if argv[0] not in MAILON_INTERFACE["read_only_commands"]:
        raise ValueError(f"stage-1 wrapper is READ-ONLY; refusing: {argv[0]}")
    try:
        proc = subprocess.Popen(
            cmd, cwd=cfg["repo"], env=build_subprocess_env(cfg),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
    except FileNotFoundError:
        return -6, "", "mailon python missing"
    try:
        out, err = proc.communicate(timeout=cfg["timeout"])
    except subprocess.TimeoutExpired:
        # SIGTERM first: mailon then records the run as failed and closes its browser.
        # A bare SIGKILL left rows `running` and a browser alive for 15 h (2026-10-13).
        proc.terminate()
        try:
            proc.communicate(timeout=TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
        return -7, "", "mailon subprocess timeout"
    return proc.returncode, out, err


def _db_rows(cfg: dict, where: str, params: tuple, limit: int | None) -> list[dict]:
    if not cfg["db"].is_file():
        return []
    uri = f"file:{cfg['db']}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        sql = (
            "SELECT uid, folder, subject, sender, recv_date, markdown_path "
            f"FROM messages WHERE {where} "
            "ORDER BY recv_date DESC, saved_at DESC"
        )
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _render_mail(row: dict, masked: bool, salt: str) -> dict:
    out = {
        "uid": row["uid"], "folder": row["folder"], "date": row["recv_date"],
        "subject": row["subject"] or "", "sender": row["sender"] or "",
        "markdown_path": row["markdown_path"],
    }
    if masked:
        out["subject"] = mask_value(out["subject"], salt)
        out["sender"] = mask_value(out["sender"], salt)
        out["markdown_path"] = mask_value(out["markdown_path"] or "", salt)
    return out



def rank_candidates(candidates: list[dict]) -> list[dict]:
    """Order `resolve` candidates deterministically: organization > contacts > history.

    Measured: one person returned three candidates carrying two distinct addresses, with
    nothing in the response saying which was current. A caller that picks arbitrarily
    sends to the wrong recipient, and that is an irreversible external effect — the
    owner approval gate catches it with human eyes, but a gate is a last line of
    defence, not a decision procedure. Unknown groups sort last rather than raise.
    """
    order = {group: index for index, group in enumerate(RESOLVE_GROUP_PRIORITY)}
    return sorted(
        candidates, key=lambda item: order.get(str(item.get("group", "")), len(order))
    )


def distinct_addresses(candidates: list[dict]) -> int:
    """How many different addresses the candidate set actually carries."""
    return len({str(item.get("email", "")).strip().lower() for item in candidates if item.get("email")})


def render_candidate(candidate: dict, masked: bool, salt: str) -> dict:
    item = {
        "group": str(candidate.get("group", "")), "name": str(candidate.get("name", "")),
        "email": str(candidate.get("email", "")), "org": str(candidate.get("org", "")),
    }
    if masked:
        item.update({key: mask_value(item[key], salt) for key in ("name", "email", "org")})
    return item