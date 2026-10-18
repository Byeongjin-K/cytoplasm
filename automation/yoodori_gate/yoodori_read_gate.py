#!/usr/bin/python3 -I
"""SSH forced command: reads and card requests, never effects; logs name/rc/seconds only."""
from __future__ import annotations

import base64
import json
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# sshd runs this file with -I: only its own declared helper directory is added.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from yoodori_request_runtime import Refused, card_env, calendar_card  # noqa: E402

HOME = Path.home()
LIVE = Path("/srv/autophagy-skills/live")
RELEASE = Path("/srv/autophagy-agent-current")
LOG = HOME / ".local/state/yoodori-gate.log"
TIMEOUT = 150
ENV = {
    "HOME": str(HOME),
    "USER": "agent",
    "LOGNAME": "agent",
    "LANG": "C.UTF-8",
    "PATH": f"{HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin",
}
_INT = re.compile(r"[0-9]{1,3}")
_UID = re.compile(r"[0-9]{1,20}")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")
_SHELL = re.compile(r"[;&|`$<>\\]")
STAGING = HOME / ".local/share/yoodori-staging"
CARD_COMMANDS = (
    "mail-compose", "calendar-request", "todo-request",
    "drive-save", "command-request", "obsidian-request",
)
GMAIL_READ_COMMANDS = ("gmail-list", "gmail-get")
_GMAIL_ID = re.compile(r"[0-9a-f]{8,32}")
GMAIL_WRITE_WORDS = frozenset({
    "send", "drafts", "modify", "labels", "trash", "delete", "batchmodify",
    "insert", "import", "watch", "settings",
})
REFUSED_WORDS = frozenset({
    "approve", "execute", "send", "apply", "watch", "resolve", "confirm",
    "--yes", "mailon", "deploy", "sudo", "sh", "bash", "dash", "zsh",
    "python", "python3", "perl", "ruby", "node", "bun", "env",
}) | GMAIL_WRITE_WORDS
_ORIGIN = {"--origin-channel-id": "id", "--origin-message-id": "id"}
_FLAGS = {
    "mail-compose": {"--to": "email", "--cc": "email", "--bcc": "email",
                     "--subject": "short", "--body": "body", "--in-reply-to": "id"},
    "calendar-request": {"--text": "text", "--summary": "short"},
    "todo-request": {"--title": "short", "--notes": "body", "--due": "date"},
    "drive-save": {"--file": "file", "--summary": "short", "--title": "short",
                   "--project": "short"},
    "command-request": {"--summary": "short", "--target": "short", "--argv-b64": "argv"},
    "obsidian-request": {"--relpath": "relpath", "--body-file": "file", "--title": "short"},
}
_REQUIRED = {
    "mail-compose": ("--to", "--subject", "--body"),
    "calendar-request": ("--text",),
    "todo-request": ("--title",),
    "drive-save": ("--file", "--summary"),
    "command-request": ("--summary", "--target", "--argv-b64"),
    "obsidian-request": ("--relpath", "--body-file"),
}

USAGE = ("YooDori gate: calendar <days 1-31> [query...], mail-list <n 1-50>,\n"
         "mail-get <uid> [--body], recall <question...>, status.\n"
         "Gmail reads: gmail-list <n 1-50> [query...], gmail-get <hex id> [--body].\n"
         "Card requests (origin channel/message ids required): " + ", ".join(CARD_COMMANDS) + "\n")


def _int(value: str, low: int, high: int) -> str:
    if not _INT.fullmatch(value) or not low <= int(value) <= high:
        raise Refused(f"number out of range {low}-{high}")
    return str(int(value))


def _text(words: list[str], limit: int) -> str:
    text = " ".join(words).strip()
    if not text or len(text) > limit:
        raise Refused(f"text must be 1-{limit} characters")
    return text


def _decode(value: str) -> str:
    try:
        return base64.b64decode(value, validate=True).decode("utf-8")
    except (ValueError, UnicodeError):
        raise Refused("invalid UTF-8 base64") from None


def _value(value: str, kind: str) -> str:
    limit = 8000 if kind in {"body", "argv"} else 1000 if kind == "text" else 240
    if not value.strip() or len(value) > limit or re.search(r"[\x00-\x08\x0b-\x1f\x7f]", value):
        raise Refused("invalid value length or control character")
    if kind not in {"body", "argv"} and ("\n" in value or "\r" in value or "\t" in value):
        raise Refused("single-line value required")
    if kind == "id" and (not _UID.fullmatch(value) or int(value) == 0):
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
        path = Path(value)
        try:
            if (not path.is_absolute() or ".." in path.parts or path.is_symlink()
                    or not path.resolve().is_relative_to(STAGING.resolve())
                    or not path.is_file() or path.stat().st_size > 64 * 1024 * 1024):
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


def _card(name: str, args: list[str]) -> tuple[str, list[str], Path]:
    allowed = {**_FLAGS[name], **_ORIGIN}
    values: dict[str, str] = {}
    if len(args) % 2 or len(args) > 32:
        raise Refused("card requests take flag/value pairs only")
    for flag, raw in zip(args[::2], args[1::2]):
        decoded = flag.removesuffix("-b64") if flag != "--argv-b64" else flag
        if decoded not in allowed or decoded in values:
            raise Refused("unknown or repeated flag")
        kind = allowed[decoded]
        if flag != decoded and kind not in {"body", "text", "short"}:
            raise Refused("encoding only supported for text")
        values[decoded] = _value(_decode(raw) if flag != decoded else raw, kind)
    if any(flag not in values for flag in (*_REQUIRED[name], *_ORIGIN)):
        raise Refused("missing required request flag or owner message coordinates")
    tails: list[str] = []
    if name == "command-request":
        try:
            command = json.loads(_decode(values.pop("--argv-b64")))
        except ValueError:
            raise Refused("argv must encode a JSON string array") from None
        if (not isinstance(command, list) or not 1 <= len(command) <= 32
                or not all(isinstance(v, str) for v in command)):
            raise Refused("argv must encode 1-32 strings")
        for word in command:
            _value(word, "body")
            tokens = re.split(r"[/_.=-]", word.lower())
            if (word.lower() in REFUSED_WORDS or set(tokens) & REFUSED_WORDS
                    or _SHELL.search(word) or _CTRL.search(word)):
                raise Refused("execution-family or shell command refused")
        if not re.fullmatch(r"/usr/bin/[a-z][a-z0-9-]*", command[0]):
            raise Refused("command executable must be under /usr/bin")
        tails = ["--", *command]
    py = "/usr/bin/python3"
    skill = {"mail-compose": ("mail", "triage_cli.py", "compose"),
             "calendar-request": ("calendar", "calendar_cli.py", "draft-create"),
             "todo-request": ("todo", "todo_cli.py", "request")}
    if name in skill:
        folder, script, sub = skill[name]
        argv = [py, str(LIVE / folder / "scripts" / script), sub]
    else:
        module = "automation.obsidian_write" if name == "obsidian-request" else "automation.effect_request"
        argv = [py, "-m", module, "request"]
        if name != "obsidian-request":
            argv += ["drive-save" if name == "drive-save" else "command"]
    return name, argv + [v for pair in values.items() for v in pair] + tails, RELEASE


def build(words: list[str]) -> tuple[str, list[str], Path]:
    if not words:
        raise Refused("empty command")
    name, args = words[0], words[1:]
    if name in CARD_COMMANDS:
        return _card(name, args)
    py = "/usr/bin/python3"
    if name == "gmail-list":
        if not args or any(word.startswith("--") for word in args[1:]):
            raise Refused("gmail-list needs <n> [query...]")
        return name, [py, str(Path(__file__).with_name("gmail_read.py")), "list",
                      _int(args[0], 1, 50), *([_text(args[1:], 200)] if args[1:] else [])], HOME
    if name == "gmail-get":
        if (not args or not _GMAIL_ID.fullmatch(args[0]) or len(args) > 2
                or (len(args) == 2 and args[1] != "--body")):
            raise Refused("gmail-get needs <hex id> [--body]")
        return name, [py, str(Path(__file__).with_name("gmail_read.py")), "get", *args], HOME
    if name == "calendar":
        if not args:
            raise Refused("calendar needs <days>")
        argv = [py, str(LIVE / "calendar/scripts/calendar_cli.py"), "list", "--days", _int(args[0], 1, 31)]
        if args[1:]:
            argv += ["--query", _text(args[1:], 80)]
        return name, argv, HOME
    if name == "mail-list":
        if len(args) != 1:
            raise Refused("mail-list needs exactly <n>")
        return name, [py, str(LIVE / "mail/scripts/mail_wrapper.py"), "list", "--limit", _int(args[0], 1, 50)], HOME
    if name == "mail-get":
        if not args or len(args) > 2 or not _UID.fullmatch(args[0]) or (len(args) == 2 and args[1] != "--body"):
            raise Refused("mail-get needs <uid> [--body]")
        return name, [py, str(LIVE / "mail/scripts/mail_wrapper.py"), "get", *args], HOME
    if name == "recall":
        return name, [py, str(LIVE / "recall/scripts/recall_cli.py"), "search", _text(args, 300)], HOME
    if name == "status":
        if args:
            raise Refused("status takes no arguments")
        return name, [py, "-m", "automation.doctor"], RELEASE
    raise Refused(f"unknown command: {name[:40]}")


def _log(name: str, rc: int, started: float) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp} {name} rc={rc} s={time.monotonic() - started:.1f}\n")
    except OSError:
        pass


def main() -> int:
    started = time.monotonic()
    raw = os.environ.get("SSH_ORIGINAL_COMMAND", "")
    try:
        if _CTRL.search(raw) or len(raw) > 48000:
            raise Refused("control characters or too long")
        if _SHELL.search(raw):
            raise Refused("shell metacharacters are not accepted")
        try:
            words = shlex.split(raw)
        except ValueError as exc:
            raise Refused(f"cannot parse: {exc}") from None
        if words and words[0] not in CARD_COMMANDS and len(raw) > 600:
            raise Refused("read command too long")
        name, argv, cwd = build(words)
    except Refused as exc:
        sys.stderr.write(f"YOODORI-GATE-REFUSED {exc}\n{USAGE}")
        _log("refused", 2, started)
        return 2
    try:
        env = card_env(HOME, RELEASE, ENV) if name in CARD_COMMANDS else ENV
        if name == "calendar-request":
            rc = calendar_card(argv, RELEASE, env, TIMEOUT)
        else:
            rc = subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, timeout=TIMEOUT, check=False).returncode
    except (OSError, UnicodeError, Refused):
        sys.stderr.write("YOODORI-GATE-REFUSED request environment or draft unavailable\n")
        rc = 2
    except subprocess.TimeoutExpired:
        sys.stderr.write(f"YOODORI-GATE-TIMEOUT {name} after {TIMEOUT}s\n")
        rc = 124
    _log(name, rc, started)
    return rc


if __name__ == "__main__":
    sys.exit(main())
