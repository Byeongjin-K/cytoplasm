#!/usr/bin/python3 -I
"""SSH forced command: reads and card requests, never effects; logs name/rc/seconds only."""
from __future__ import annotations

import base64
import http.client
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# sshd runs this file with -I: only its own declared helper directory is added.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from yoodori_request_runtime import (  # noqa: E402
    Refused, card_env, calendar_card, checked_value, job_env, stage_body, stage_file, stage_name,
)
import yoodori_jobs  # noqa: E402

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
_REPLY_KEY = re.compile(r"[0-9]{4}-[0-9]{4}-[0-9]{1,3}")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")
_SHELL = re.compile(r"[;&|`$<>\\]")
STAGING = HOME / ".local/share/yoodori-staging"
CARD_COMMANDS = (
    "mail-compose", "calendar-request", "todo-request",
    "drive-save", "command-request", "obsidian-request",
    "mail-draft",
)
GMAIL_READ_COMMANDS = ("gmail-list", "gmail-get")
_GMAIL_ID = re.compile(r"[0-9a-f]{8,32}")
_RELEASE_TAG = re.compile(r"v[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}")
RELEASE_NOTES_LIMIT = 1024 * 1024
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
_REPEATABLE = {"mail-compose": frozenset({"--cc", "--bcc", "--share-with", "--attachment"})}
_FLAGS = {
    "mail-compose": {"--to": "email", "--cc": "email", "--bcc": "email",
                     "--subject": "short", "--body": "body", "--in-reply-to": "id",
                     "--forward": "id", "--forward-gmail": "short", "--attachment": "file",
                     "--bcc-file": "file", "--bcc-column": "short",
                     "--drive-share": "file", "--share-with": "email", "--drive-folder": "short"},
    "mail-draft": {"--instruction": "body"},
    "calendar-request": {"--text": "text", "--summary": "short"},
    "todo-request": {"--title": "short", "--notes": "body", "--due": "date"},
    "drive-save": {"--file": "file", "--summary": "short", "--title": "short",
                   "--project": "short"},
    "command-request": {"--summary": "short", "--target": "short", "--argv-b64": "argv"},
    "obsidian-request": {"--relpath": "relpath", "--body-file": "file", "--body": "body", "--title": "short"},
}
_REQUIRED = {
    "mail-compose": ("--to",),
    "mail-draft": ("--instruction",),
    "calendar-request": ("--text",),
    "todo-request": ("--title",),
    "drive-save": ("--file", "--summary"),
    "command-request": ("--summary", "--target", "--argv-b64"),
    "obsidian-request": ("--relpath",),
}

# Owner-command tools: (script, read subcommands, write subcommands). Writes run without a
# card (owner 2026-10-18 Q233; patent export 18:29 KST "5번도 카드 없이 해") only for a
# verified owner message; outbound steps inside them still raise their own cards.
TOOLS: dict[str, tuple[str, frozenset[str], frozenset[str]]] = {
    "doctype": ("doctype/scripts/doctype_cli.py", frozenset({"list", "show"}),
                frozenset({"register-from-example", "draft", "refine"})),
    "report": ("report/scripts/report_cli.py", frozenset({"evidence"}),
               frozenset({"report", "slides", "script"})),
    "proposal": ("proposal/scripts/proposal_cli.py",
                 frozenset({"version", "delta", "sections", "evidence", "research", "corpus",
                            "status", "prompt-preview", "preflight"}),
                 frozenset({"create", "section-add", "draft", "contribute", "assemble", "compose",
                            "figures", "images", "render", "refine", "publish", "improve",
                            "review", "visual-review"})),
    "procurement": ("procurement/scripts/procure_cli.py",
                    frozenset({"templates-list", "templates-show", "collect-status", "preflight", "verify"}),
                    frozenset({"collect-start", "collect-answer", "generate", "register", "review"})),
    "prompt": ("prompt/scripts/prompt_cli.py", frozenset({"search", "get"}), frozenset({"add"})),
    "topics": ("topics/scripts/topics_cli.py", frozenset({"list", "evidence", "suggest"}),
               frozenset({"add", "remove"})),
    "patent": ("patent-prep/scripts/patent_cli.py", frozenset({"status"}),
               frozenset({"create", "checklist", "draft", "export"})),
    "stt": ("speechtotext/scripts/speechtotext_cli.py", frozenset(),
            frozenset({"transcribe", "ingest", "polish"})),
    "meeting": ("meeting/scripts/meeting_cli.py", frozenset({"evidence"}), frozenset({"ingest"})),
}
JOB_COMMANDS = ("doc", "patent", "stt", "meeting", "wiki-write", "mail-sync")
LONG = frozenset({"proposal", "stt", "meeting"})
JOB_WAIT = 100
# Test replays, injected approvals and output-root overrides are never caller-controlled.
DENIED_FLAGS = frozenset({
    "--out", "--outputs-root", "--notes-root", "--recorded", "--meeting-recorded",
    "--recorded-response", "--response-file", "--injection-file", "--dm-target",
    "--write-body", "--forge-signature", "--user-id", "--notify-channel", "--notify-message-id",
    "--origin-channel-id", "--origin-message-id", "--allow-plaintext",
})
_FLAG = re.compile(r"--[a-z][a-z0-9-]{0,40}")
_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,80}")
_OUT_NAME = re.compile(r"(?!\.)[A-Za-z0-9._-]{1,120}")
WIKI_FLAGS = {"--title": "short", "--tags": "short", "--links": "short", "--slug": "short",
              "--edit": "short", "--body": "body"}

USAGE = ("YooDori gate: calendar <days 1-31> [query...], mail-list <n 1-50>,\n"
         "mail-get <uid> [--body], recall <question...>, status.\n"
         "Gmail reads: gmail-list <n 1-50> [query...], gmail-get <hex id> [--body].\n"
         "Wiki reads: wiki-search <words...>, wiki-get <slug>. Obsidian read: note-get <PARA path>.\n"
         "Daily memory copy: memory-copy (note body on SSH stdin; fixed note, no origin).\n"
         "Daily conversation summary: day-summary <YYYY-MM-DD> (body on stdin; overwrites that day's note).\n"
         "Release reads: release-notes <vX.Y.Z>.\n"
         "Private upload: stage <name> (file bytes on SSH stdin, at most 64 MiB).\n"
         "Card requests (origin channel/message ids required): " + ", ".join(CARD_COMMANDS) + "\n"
         "Owner-command jobs (origin = the owner's own message, checked on the node):\n"
         "  doc <" + "|".join(k for k in TOOLS if k not in {"patent", "stt", "meeting"}) + "> <sub> [args],\n"
         "  patent <create|checklist|draft|status|export> [args], stt <transcribe|ingest|polish> [args],\n"
         "  meeting <ingest|evidence> [args], wiki-write [--edit <slug>] --title .. --body-b64 ..,\n"
         "  mail-sync (collect institutional mail now; waits for a running collection);\n"
         "  each ends with --origin-channel-id N --origin-message-id N. Files: staged paths only;\n"
         "  --<flag>-b64 for multi-line text; --out-name <name> for an output file.\n"
         "  job <id> (status/output), job-file <id> <name> (bytes on stdout).\n")


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
    return checked_value(value, kind, STAGING)


def _owner_id() -> str:
    """The trusted card-only SSH entry uses the configured owner, never a caller's id."""
    try:
        value = json.loads((HOME / ".hermes/interop/config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Refused("owner config unavailable") from None
    if not isinstance(value, dict) or not isinstance(value.get("owner_id"), str):
        raise Refused("owner config invalid")
    return _value(value["owner_id"], "id")


def _card(name: str, args: list[str]) -> tuple[str, list[str], Path]:
    allowed = {**_FLAGS[name], **_ORIGIN}
    repeatable = _REPEATABLE.get(name, frozenset())
    values: dict[str, str] = {}
    repeats: list[tuple[str, str]] = []
    if len(args) % 2 or len(args) > 64:
        raise Refused("card requests take flag/value pairs only")
    for flag, raw in zip(args[::2], args[1::2]):
        if name == "mail-draft" and flag == "--instruction":
            raise Refused("mail draft instruction requires --instruction-b64")
        if name == "obsidian-request" and flag == "--body":
            raise Refused("inline Obsidian body requires --body-b64")
        decoded = flag.removesuffix("-b64") if flag != "--argv-b64" else flag
        if decoded not in allowed or (decoded in values and decoded not in repeatable):
            raise Refused("unknown or repeated flag")
        kind = allowed[decoded]
        if flag != decoded and kind not in {"body", "text", "short"}:
            raise Refused("encoding only supported for text")
        value = _value(_decode(raw) if flag != decoded else raw, kind)
        if decoded == "--forward-gmail" and not _GMAIL_ID.fullmatch(value):
            raise Refused("--forward-gmail needs a hex Gmail message id")
        if decoded in values:
            repeats.append((decoded, value))
            continue
        values[decoded] = value
        if name == "mail-draft" and decoded == "--instruction" and (
            len(values[decoded]) > 2000 or _SHELL.search(values[decoded])
        ):
            raise Refused("instruction exceeds 2000 characters or contains shell characters")
    if any(flag not in values for flag in (*_REQUIRED[name], *_ORIGIN)):
        raise Refused("missing required request flag or owner message coordinates")
    if name == "obsidian-request" and sum(flag in values for flag in ("--body", "--body-file")) != 1:
        raise Refused("choose exactly one of --body-file or --body-b64")
    if name == "mail-compose" and sum(f in values for f in ("--forward", "--forward-gmail", "--in-reply-to")) > 1:
        raise Refused("choose one of --forward, --forward-gmail or --in-reply-to")
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
             "mail-draft": ("mail", "triage_cli.py", "reply-request"),
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
    pairs = [v for pair in values.items() for v in pair] + [v for pair in repeats for v in pair]
    return name, argv + pairs + tails, RELEASE


@dataclass(frozen=True, slots=True)
class Job:
    name: str
    argv: list[str]
    write: bool
    label: str
    timeout: int
    channel_id: str
    message_id: str
    kind: str = "yoodori"
    marker: str = ""
    needs_origin: bool = True


# Q233 (2026-10-18 17:40 KST "6개 다 카드 없이 바로 + 하루 요약에 남기기"): with that producer's
# release switch on, an owner-message card request runs its cardless command instead.
CARDLESS = {"calendar-request": "calendar", "todo-request": "todo",
            "obsidian-request": "obsidian", "drive-save": "drive-save"}
WRITTEN = "OWNER-COMMAND-WRITTEN"
# Owner Q241 (2026-10-18 20:55 KST): YooDori's omo memory is the source of truth and gets one
# automatic Obsidian copy a day, so recall and node skills see it. Fixed path, no origin message.
MEMORY_COPY_RELPATH = "000_PARA/Resource/유돌 기억 자동 사본.md"
MIRROR = HOME / ".hermes/obsidian-mirror"
MAX_NOTE = 512 * 1024


def memory_copy_job(body: Path) -> Job:
    argv = ["/usr/bin/python3", "-m", "automation.obsidian_write", "write-now",
            "--relpath", MEMORY_COPY_RELPATH, "--body-file", str(body),
            "--title", "유돌 기억 자동 사본",
            "--origin-channel-id", "owner-policy", "--origin-message-id", "Q241-daily-memory-copy"]
    return Job("memory-copy", argv, True, "유돌 기억 자동 사본", 600, "owner-policy",
               "Q241-daily-memory-copy", "yoodori-memory", WRITTEN, False)


# Owner Q244 (2026-10-18 21:03 KST): one daily note of the owner's #yoodori conversation
# (decisions, todos, facts) so recall indexes it. Same switch and summary path as Q241.
DAY_SUMMARY_DIR = "000_PARA/Resource/유돌 대화 요약"
_DAY = re.compile(r"20[0-9]{2}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])")
# Outside people's contacts must not reach the vault: an address or phone number refuses the note.
_CONTACT = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+|\b0\d{1,2}[- .]?\d{3,4}[- .]?\d{4}\b")


def day_summary_job(day: str, body: Path) -> Job:
    if _CONTACT.search(body.read_text(encoding="utf-8")):
        raise Refused("summary contains an e-mail address or phone number")
    relpath = f"{DAY_SUMMARY_DIR}/{day}.md"
    argv = ["/usr/bin/python3", "-m", "automation.obsidian_write", "write-now",
            "--relpath", relpath, "--body-file", str(body), "--title", f"유돌 대화 요약 {day}",
            "--origin-channel-id", "owner-policy", "--origin-message-id", "Q244-daily-conversation-summary"]
    return Job("day-summary", argv, True, f"유돌 대화 요약 {day}", 600, "owner-policy",
               "Q244-daily-conversation-summary", "yoodori-memory", WRITTEN, False)


def note_path(relpath: str) -> Path:
    """A vault-relative note read from the node's read-only mirror, never outside it."""
    _value(relpath, "relpath")
    path = MIRROR / relpath
    try:
        real = path.resolve()
        if (not real.is_relative_to(MIRROR.resolve()) or path.is_symlink() or not real.is_file()
                or real.stat().st_size > MAX_NOTE):
            raise Refused("no such note in the mirror")
    except OSError:
        raise Refused("note unreadable") from None
    return real


def cardless_job(name: str, argv: list[str]) -> Job:
    """The same validated request, re-pointed at the producer's owner-command subcommand."""
    first = next(i for i, word in enumerate(argv) if word.startswith("--"))
    values = dict(zip(argv[first::2], argv[first + 1::2]))
    channel, message = values["--origin-channel-id"], values["--origin-message-id"]
    if name == "calendar-request":
        argv = [*argv[:2], "owner-create", *argv[3:]]
        label = "형 일정 1건"
    elif name == "todo-request":
        argv = [*argv[:2], "write-now", *argv[3:]]
        label = f"할 일 {values.get('--title', '')}"
    elif name == "obsidian-request":
        argv = [*argv[:3], "write-now", *argv[4:]]
        label = f"노트 {Path(values.get('--relpath', '')).name}"
    else:
        argv = [*argv[:3], "save-now", *argv[5:]]
        label = f"Drive 저장 {values.get('--summary', '')}"
    return Job(name, argv, True, label[:160], 600, channel, message, CARDLESS[name], WRITTEN)


def _origin(args: list[str]) -> tuple[list[str], str, str]:
    if len(args) < 4 or args[-4] != "--origin-channel-id" or args[-2] != "--origin-message-id":
        raise Refused("owner-command jobs end with --origin-channel-id N --origin-message-id N")
    return args[:-4], _value(args[-3], "id"), _value(args[-1], "id")


def _job_path(value: str) -> str:
    """A file argument is a staged upload or an earlier job's output, nothing else."""
    path = Path(value)
    jobs = yoodori_jobs.jobs_root(HOME).resolve()
    try:
        real = path.resolve()
        if (path.is_absolute() and ".." not in path.parts and not path.is_symlink()
                and real.is_relative_to(jobs) and real.parent.name == "out" and real.is_file()):
            return value
    except OSError:
        raise Refused("file unreadable") from None
    return _value(value, "file")


def _tool_args(tokens: list[str]) -> list[str]:
    out: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if token.startswith("--"):
            if not _FLAG.fullmatch(token):
                raise Refused("malformed flag")
            flag = token.removesuffix("-b64")
            if flag in DENIED_FLAGS:
                raise Refused(f"flag not allowed through the gate: {flag}")
            if token.endswith("-b64") or token == "--out-name":
                if index >= len(tokens):
                    raise Refused("flag needs a value")
                raw = tokens[index]
                index += 1
                if token == "--out-name":
                    if not _OUT_NAME.fullmatch(raw):
                        raise Refused("--out-name needs a plain file name")
                    out += ["--out", "{OUT}/" + raw]
                else:
                    out += [flag, _value(_decode(raw), "body")]
                continue
            out.append(flag)
            continue
        if token.startswith("/"):
            out.append(_job_path(token))
        elif token.startswith("~") or ".." in token.split("/"):
            raise Refused("relative or home paths are not accepted; stage the file first")
        else:
            out.append(_value(token, "text"))
    return out


def _label(tool: str, sub: str, args: list[str]) -> str:
    for key in ("--slug", "--name", "--title", "--label", "--file", "--report", "--template-name", "--edit"):
        if key in args[:-1]:
            value = args[args.index(key) + 1]
            return f"{tool} {sub} {Path(value).name if value.startswith('/') else value}"[:160]
    return f"{tool} {sub}"


def build_job(words: list[str]) -> Job:
    name, rest = words[0], words[1:]
    rest, channel, message = _origin(rest)
    py = "/usr/bin/python3"
    if name == "mail-sync":
        # Owner 2026-10-19: collect institutional mail now when he asks (a fresh MailOn login,
        # so only from his own message); waits for a running scheduled collection.
        if rest:
            raise Refused("mail-sync takes no arguments besides the origin")
        argv = [py, str(LIVE / "mail/scripts/mail_sync_watch.py"), "--now"]
        return Job(name, argv, False, "기관메일 지금 수집", 1800, channel, message)
    if name == "wiki-write":
        values: dict[str, str] = {}
        if len(rest) % 2:
            raise Refused("wiki-write takes flag/value pairs only")
        for flag, raw in zip(rest[::2], rest[1::2]):
            decoded = flag.removesuffix("-b64")
            if decoded not in WIKI_FLAGS or decoded in values or (flag != decoded and decoded != "--body"):
                raise Refused("unknown or repeated wiki flag (body needs --body-b64)")
            if decoded == "--body" and flag == decoded:
                raise Refused("wiki body requires --body-b64")
            values[decoded] = _value(_decode(raw) if flag != decoded else raw, WIKI_FLAGS[decoded])
        for key in ("--slug", "--edit"):
            if key in values and not _SLUG.fullmatch(values[key]):
                raise Refused("slug must be lowercase letters, digits and hyphens")
        if "--edit" not in values and "--title" not in values:
            raise Refused("wiki-write needs --title for a new note or --edit <slug>")
        argv = [py, str(LIVE / "wiki/scripts/wiki_cli.py"), "write",
                *[v for pair in values.items() for v in pair],
                "--origin-channel-id", channel, "--origin-message-id", message]
        label = f"위키 {values.get('--edit') or values.get('--slug') or '새 노트'}"
        return Job(name, argv, True, label[:160], 300, channel, message)
    if name == "doc":
        if not rest or rest[0] not in TOOLS or rest[0] in {"patent", "stt", "meeting"}:
            raise Refused("doc needs one of: " + ", ".join(k for k in TOOLS if k not in {"patent", "stt", "meeting"}))
        tool, rest = rest[0], rest[1:]
    else:
        tool = name
    script, reads, writes = TOOLS[tool]
    if not rest or rest[0] not in reads | writes:
        raise Refused(f"{tool} subcommands: " + ", ".join(sorted(reads | writes)))
    sub, args = rest[0], _tool_args(rest[1:])
    cli_sub = sub
    if tool == "patent" and sub == "export":
        cli_sub = "export-now"
        args += ["--origin-channel-id", channel, "--origin-message-id", message]
    if tool == "report" and sub in writes:
        args += ["--outputs-root", "{OUT}"]
    if (tool, sub) in {("stt", "ingest"), ("meeting", "ingest")}:
        args += ["--notify-channel", channel, "--notify-message-id", message]
    argv = [py, str(LIVE / script), cli_sub, *args]
    timeout = 4 * 3600 if tool in LONG else 1800
    return Job(name, argv, sub in writes, _label(tool, sub, args), timeout, channel, message)


def build(words: list[str]) -> tuple[str, list[str], Path]:
    if not words:
        raise Refused("empty command")
    name, args = words[0], words[1:]
    if name == "release-notes":
        if len(args) != 1 or _RELEASE_TAG.fullmatch(args[0]) is None:
            raise Refused("release-notes needs <vX.Y.Z>")
        return name, args, HOME
    if name == "stage":
        if len(args) != 1:
            raise Refused("stage needs exactly one file name")
        return name, [stage_name(args[0])], STAGING
    if name in CARD_COMMANDS:
        if name == "mail-draft":
            if not args or not _REPLY_KEY.fullmatch(args[0]):
                raise Refused("mail-draft needs a digest reply key")
            _, argv, cwd = _card(name, args[1:])
            return name, [*argv[:3], "--key", args[0], "--author-id", _owner_id(), *argv[3:]], cwd
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
    if name == "note-get":
        if len(args) != 1:
            raise Refused("note-get needs exactly one PARA-relative path")
        return name, ["/usr/bin/cat", "--", str(note_path(args[0]))], HOME
    if name == "wiki-search":
        return name, [py, str(LIVE / "wiki/scripts/wiki_cli.py"), "query", _text(args, 200)], HOME
    if name == "wiki-get":
        if len(args) != 1 or not _SLUG.fullmatch(args[0]):
            raise Refused("wiki-get needs exactly one <slug>")
        return name, [py, str(LIVE / "wiki/scripts/wiki_cli.py"), "show", args[0]], HOME
    if name == "recall":
        return name, [py, str(LIVE / "recall/scripts/recall_cli.py"), "search", _text(args, 300)], HOME
    if name == "status":
        if args:
            raise Refused("status takes no arguments")
        return name, [py, "-m", "automation.doctor"], RELEASE
    raise Refused(f"unknown command: {name[:40]}")


def _release_modules():
    if str(RELEASE) not in sys.path:
        sys.path.insert(1, str(RELEASE))
    from automation import auto_write, notice_bot

    return auto_write, notice_bot


def _fetch_release_notes(tag: str) -> bytes:
    """Read the public release API without a credential, with a bounded response."""
    if str(RELEASE) not in sys.path:
        sys.path.insert(1, str(RELEASE))
    from automation.install.wizard_screens import DEFAULT_ORIGIN_URL

    repo = DEFAULT_ORIGIN_URL.removeprefix("https://github.com/").removesuffix(".git")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/releases/tags/{tag}",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "cytoplasm-yoodori"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read(RELEASE_NOTES_LIMIT + 1)


def release_notes(tag: str, *, fetcher: Callable[[str], bytes] = _fetch_release_notes) -> str:
    """Parse one bounded public release body; failure never exposes network diagnostics."""
    if _RELEASE_TAG.fullmatch(tag) is None:
        raise Refused("release-notes needs <vX.Y.Z>")
    try:
        raw = fetcher(tag)
        if len(raw) > RELEASE_NOTES_LIMIT:
            raise ValueError("response too large")
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("invalid release")
        body = parsed.get("body")
        if not isinstance(body, str) or not body.strip():
            raise ValueError("empty release")
        return body
    except (OSError, ValueError, UnicodeError, http.client.HTTPException):
        raise Refused("release notes unavailable") from None


def _switched(kind: str) -> bool:
    """Unreadable or malformed node settings keep the card path."""
    try:
        auto_write, _notice = _release_modules()
        return bool(auto_write.enabled(kind))
    except (ImportError, OSError, ValueError):
        return False


def run_job(job: Job) -> int:
    env = job_env(HOME, RELEASE, ENV)
    link = ""
    if job.name == "mail-sync":
        _, notice_bot = _release_modules()
        link = yoodori_jobs.verify_owner_origin(
            job.channel_id, job.message_id, _owner_id(), notice_bot.resolve_token(environ=env)).link
    if job.write:
        auto_write, notice_bot = _release_modules()
        try:
            switched_on = auto_write.enabled(job.kind)
        except ValueError:
            switched_on = False
        if not switched_on:
            raise Refused(f"owner-command writes are off on this node (release switch auto-write-{job.kind})")
        if job.needs_origin:
            token = notice_bot.resolve_token(environ=env)
            link = yoodori_jobs.verify_owner_origin(job.channel_id, job.message_id, _owner_id(), token).link
    path = yoodori_jobs.new_job(HOME, {
        "name": job.name, "argv": job.argv, "write": job.write, "label": job.label,
        "timeout": job.timeout, "kind": job.kind, "marker": job.marker,
        "origin": {"channel_id": job.channel_id, "message_id": job.message_id, "link": link},
    })
    yoodori_jobs.launch(path, env)
    finished = yoodori_jobs.wait(path, JOB_WAIT)
    rc, text = yoodori_jobs.report(path)
    sys.stdout.write(text)
    return rc if finished else 0


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
        if words and words[0] not in (*CARD_COMMANDS, *JOB_COMMANDS) and len(raw) > 600:
            raise Refused("read command too long")
        job = build_job(words) if words and words[0] in JOB_COMMANDS else None
        if job is not None:
            name, argv, cwd = job.name, job.argv, HOME
        elif words == ["memory-copy"]:
            name, argv, cwd = "memory-copy", [], HOME
        elif words and words[0] == "day-summary":
            if len(words) != 2 or not _DAY.fullmatch(words[1]):
                raise Refused("day-summary <YYYY-MM-DD>")
            name, argv, cwd = "day-summary", [words[1]], HOME
        elif words and words[0] in {"job", "job-file"}:
            if len(words) != (2 if words[0] == "job" else 3):
                raise Refused("job <id> | job-file <id> <name>")
            name, argv, cwd = words[0], words[1:], HOME
            yoodori_jobs.job_dir(HOME, argv[0])
        else:
            name, argv, cwd = build(words)
            if name in CARDLESS and _switched(CARDLESS[name]):
                if name == "obsidian-request":
                    argv = stage_body(argv, STAGING)
                job = cardless_job(name, argv)
    except (Refused, yoodori_jobs.JobRefused) as exc:
        sys.stderr.write(f"YOODORI-GATE-REFUSED {exc}\n{USAGE}")
        _log("refused", 2, started)
        return 2
    try:
        if name == "memory-copy":
            path, _digest, _size = stage_file(STAGING, "memory-copy.md", sys.stdin.buffer)
            rc = run_job(memory_copy_job(path))
        elif name == "day-summary":
            path, _digest, _size = stage_file(STAGING, f"day-summary-{argv[0]}.md", sys.stdin.buffer)
            rc = run_job(day_summary_job(argv[0], path))
        elif job is not None:
            rc = run_job(job)
        elif name == "release-notes":
            print(release_notes(argv[0]))
            rc = 0
        elif name == "job":
            rc, text = yoodori_jobs.report(yoodori_jobs.job_dir(HOME, argv[0]))
            sys.stdout.write(text)
        elif name == "job-file":
            target = yoodori_jobs.output_file(yoodori_jobs.job_dir(HOME, argv[0]), argv[1])
            sys.stdout.flush()
            with target.open("rb") as handle:
                while chunk := handle.read(65536):
                    sys.stdout.buffer.write(chunk)
            rc = 0
        elif name == "stage":
            _path, digest, size = stage_file(STAGING, argv[0], sys.stdin.buffer)
            print(f"STAGED {argv[0]} sha256={digest} bytes={size}")
            rc = 0
        else:
            env = card_env(HOME, RELEASE, ENV) if name in CARD_COMMANDS else ENV
            if name == "obsidian-request":
                argv = stage_body(argv, STAGING)
            if name == "calendar-request":
                rc = calendar_card(argv, RELEASE, env, TIMEOUT)
            else:
                rc = subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, timeout=TIMEOUT, check=False).returncode
    except (Refused, yoodori_jobs.JobRefused) as exc:
        sys.stderr.write(f"YOODORI-GATE-REFUSED {exc}\n")
        rc = 2
    except (OSError, UnicodeError):
        sys.stderr.write("YOODORI-GATE-REFUSED request environment or draft unavailable\n")
        rc = 2
    except subprocess.TimeoutExpired:
        sys.stderr.write(f"YOODORI-GATE-TIMEOUT {name} after {TIMEOUT}s\n")
        rc = 124
    _log(name, rc, started)
    return rc


if __name__ == "__main__":
    sys.exit(main())
