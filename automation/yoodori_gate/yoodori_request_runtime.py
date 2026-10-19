"""Fixed-environment card processes; no shell or effect execution."""
from __future__ import annotations

import hashlib
import io
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

MAX_STAGE_BYTES = 64 * 1024 * 1024


class Refused(Exception):
    pass


def stage_name(name: str) -> str:
    if not re.fullmatch(r"(?!\.)[A-Za-z0-9._-]{1,120}", name):
        raise Refused("stage name must be 1-120 plain characters, with no leading dot")
    return name


def stage_file(root: Path, name: str, source: BinaryIO) -> tuple[Path, str, int]:
    """Consume bounded SSH stdin, then atomically replace one private staged file."""
    destination = root / stage_name(name)
    if root.is_symlink():
        raise Refused("staging directory must not be a symlink")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    digest, size = hashlib.sha256(), 0
    with tempfile.NamedTemporaryFile(mode="wb", dir=root, prefix=".upload-", delete=False) as output:
        temporary = Path(output.name)
        try:
            os.fchmod(output.fileno(), 0o600)
            while chunk := source.read(min(65536, MAX_STAGE_BYTES - size + 1)):
                size += len(chunk)
                if size > MAX_STAGE_BYTES:
                    raise Refused("stage file exceeds 64 MiB")
                output.write(chunk)
                digest.update(chunk)
            output.flush()
            os.fsync(output.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    return destination, digest.hexdigest(), size


def stage_mail_attachment(home: Path, staging: Path, uid: str, filename: str) -> tuple[Path, str, int]:
    """Copy one saved attachment of a mail in the owner's mailbox into the private staging folder.

    Owner 2026-10-19 (live QA 2): a Drive share or a new mail may carry a file that arrived by mail,
    and only staged paths are accepted. The bytes stay on the node; YooDori gets path, digest, size.
    """
    data = home / ".hermes/mailon-runtime/state/data"
    try:
        db = sqlite3.connect(f"file:{data / 'state.db'}?mode=ro", uri=True)
        try:
            rows = db.execute("SELECT filename, local_path FROM attachments WHERE uid = ? AND status = 'ok' "
                              "ORDER BY rowid", (uid,)).fetchall()
        finally:
            db.close()
    except sqlite3.Error:
        raise Refused("mailbox index unreadable") from None
    names = [str(row[0]) for row in rows]
    if filename not in names:
        raise Refused("no saved attachment by that name in that mail of the owner's mailbox")
    index = names.index(filename)
    local = rows[index][1]
    candidates = [Path(local) if Path(local).is_absolute() else home / local] if local else []
    candidates.append(data / "attachments" / uid / filename)
    runtime = (home / ".hermes/mailon-runtime").resolve()
    source = next((path for path in candidates if not path.is_symlink() and path.is_file()
                   and path.resolve().is_relative_to(runtime)), None)
    if source is None:
        raise Refused("that attachment is not saved on the node")
    if source.stat().st_size > MAX_STAGE_BYTES:
        raise Refused("attachment exceeds 64 MiB")
    # Letter-first extensions only: a recorded name may still end in the page's size ("x.wrl 37.86 MB").
    extensions = re.findall(r"\.([A-Za-z][A-Za-z0-9]{0,9})\b", filename)
    name = f"mail-{uid}-{index + 1}" + (f".{extensions[-1]}" if extensions else "")
    with source.open("rb") as handle:
        staged = stage_file(staging, name, handle)
    # The staged name is plain ASCII; the original name rides beside it so a Drive share keeps it.
    stage_file(staging, name + ".name", io.BytesIO(filename.encode("utf-8")))
    return staged


def original_name(staging: Path, staged: str) -> str:
    """The received attachment's own name recorded beside a staged copy, or empty."""
    path = Path(staged)
    sidecar = path.with_name(path.name + ".name")
    try:
        if (path.parent.resolve() != staging.resolve() or sidecar.is_symlink()
                or not sidecar.is_file()):
            return ""
        return sidecar.read_text(encoding="utf-8").strip()[:240]
    except (OSError, UnicodeError):
        return ""


def stage_body(argv: list[str], root: Path) -> list[str]:
    """Translate the gate's validated inline body to the producer's existing file flag."""
    index = next((i for i in range(4, len(argv), 2) if argv[i] == "--body"), None)
    if index is None:
        return argv
    body = argv[index + 1].encode("utf-8")
    path, _digest, _size = stage_file(root, f"note-{hashlib.sha256(body).hexdigest()}.md", io.BytesIO(body))
    return [*argv[:index], "--body-file", str(path), *argv[index + 2:]]


def checked_value(value: str, kind: str, staging: Path) -> str:
    limit = 8000 if kind in {"body", "argv"} else 1000 if kind == "text" else 240
    if not value.strip() or len(value) > limit or re.search(r"[\x00-\x08\x0b-\x1f\x7f]", value):
        raise Refused("invalid value length or control character")
    if kind not in {"body", "argv"} and ("\n" in value or "\r" in value or "\t" in value):
        raise Refused("single-line value required")
    if kind == "id" and (not re.fullmatch(r"[0-9]{1,20}", value) or int(value) == 0):
        raise Refused("numeric id required")
    if kind == "email" and not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._%+-]{0,63}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
        r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+", value,
    ):
        raise Refused("one email address required")
    if kind == "date":
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError
        except ValueError:
            raise Refused("RFC3339 due date required") from None
    if kind == "file":
        if "/" not in value:
            # The STAGED name alone means that file in the staging folder (2026-10-19: a bare name
            # was refused and YooDori could not tell which path to give).
            value = str(staging / stage_name(value))
        path = Path(value)
        try:
            if (not path.is_absolute() or ".." in path.parts or path.is_symlink()
                    or not path.resolve().is_relative_to(staging.resolve())
                    or not path.is_file() or path.stat().st_size > MAX_STAGE_BYTES):
                raise Refused("file must be a staged regular file of at most 64 MiB")
        except OSError:
            raise Refused("staging file unreadable") from None
    if kind == "relpath":
        parts = value.split("/")
        if (parts[0] not in {"000_PARA", "001_KIMM_PARA"} or len(parts) < 2
                or not value.endswith(".md") or any(not p or p.startswith(".") for p in parts)
                or re.search(r'[*"\\<>:|?#^\[\]]', value)):
            raise Refused("safe PARA-relative markdown path required")
    return value


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


_ENV_KEY = re.compile(r"[A-Z_][A-Z0-9_]{0,63}")
_KEEP = frozenset({"HOME", "USER", "LOGNAME", "PATH", "LANG"})


def job_env(home: Path, release: Path, baseline: dict[str, str]) -> dict[str, str]:
    """Skill jobs get the account's own settings files, as Hermes gave them; they stay on the node."""
    values = dict(baseline)
    for source in (home / ".hermes/.env", home / ".env.secrets"):
        try:
            lines = source.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            continue
        for line in lines:
            key, sep, value = line.strip().removeprefix("export ").partition("=")
            key = key.strip()
            if sep and _ENV_KEY.fullmatch(key) and key not in _KEEP:
                values[key] = value.split(" #", 1)[0].strip().strip("\"'")
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
