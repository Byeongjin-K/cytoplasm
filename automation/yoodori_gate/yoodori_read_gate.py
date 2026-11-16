#!/usr/bin/python3 -I
"""SSH forced command: reads and card requests, never effects; logs name/rc/seconds only (todo-done adds its id)."""
from __future__ import annotations

import base64
import calendar
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
from dataclasses import dataclass, replace
from pathlib import Path

# sshd runs this file with -I: only its own declared helper directory is added.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from yoodori_request_runtime import (  # noqa: E402
    Refused, card_env, calendar_card, checked_value, job_env, named_attachment, original_name, stage_body,
    saved_attachment_names, stage_file, stage_mail_attachment, stage_name, stage_named,
)
import yoodori_jobs  # noqa: E402

HOME = Path.home()
LIVE = Path("/srv/autophagy-skills/live")
RELEASE = Path("/srv/autophagy-agent-current")
LOG = HOME / ".local/state/yoodori-gate.log"
MAIL_DRAFTS = HOME / ".hermes/mail-triage/drafts"
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
_DRAFT_ID = re.compile(r"[0-9a-f]{6}")
#: rc=124 is this gate's own timeout kill: the child ran and may have posted its card (36b605, 2026-11-04).
_CARD_CALL = re.compile(r"(\S+) (?:mail-compose|mail-draft) rc=(?:0|124) s=([0-9.]+)")
_STAMP = "%Y-%m-%dT%H:%M:%SZ"
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
# --to repeats like --cc/--bcc, one checked address each (owner 2026-11-03: five people in To).
_REPEATABLE = {"mail-compose": frozenset({"--to", "--cc", "--bcc", "--share-with", "--attachment"}),
               # Issue #241: several files on one Drive save card, one --title per --file when given.
               "drive-save": frozenset({"--file", "--title"})}
_DRIVE_FOLDER_ID = re.compile(r"[A-Za-z0-9_-]{10,128}")
_KIND = re.compile(r"[a-z][a-z0-9-]{0,31}")
#: Saved attachments one ``mail-attachment <uid> --all`` stages at most (the drive-save card cap).
MAX_ALL_ATTACHMENTS = 20
_FLAGS = {
    "mail-compose": {"--to": "email", "--cc": "email", "--bcc": "email",
                     "--subject": "short", "--body": "body", "--in-reply-to": "id",
                     "--forward": "id", "--forward-gmail": "short", "--reply-gmail": "short",
                     "--attachment": "file",
                     "--bcc-file": "file", "--bcc-column": "short",
                     "--drive-share": "file", "--share-with": "email", "--drive-folder": "short",
                     "--drive-name": "short"},
    "mail-draft": {"--instruction": "body"},
    "calendar-request": {"--text": "text", "--summary": "short"},
    "todo-request": {"--title": "short", "--notes": "body", "--due": "date"},
    "drive-save": {"--file": "file", "--summary": "short", "--title": "short",
                   "--project": "short", "--kind": "short", "--folder": "short"},
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
    "patent": ("patent-prep/scripts/patent_cli.py", frozenset({"status"}),
               frozenset({"create", "checklist", "draft", "export"})),
    "stt": ("speechtotext/scripts/speechtotext_cli.py", frozenset(),
            frozenset({"transcribe", "ingest", "polish"})),
    "meeting": ("meeting/scripts/meeting_cli.py", frozenset({"evidence"}), frozenset({"ingest"})),
}
JOB_COMMANDS = ("doc", "patent", "stt", "meeting", "mail-sync", "mail-attachment-text", "glossary-add")
# Owner Q359 (2026-10-22 11:19 KST): YooDori keeps the transcript correction ledger (the term glossary).
GLOSSARY_KINDS = ("lifelog", "meeting")
# Read jobs still start only from the owner's own recent message, but never touch the write ledger.
ORIGIN_READ_JOBS = frozenset({"mail-sync", "mail-attachment-text"})
LONG = frozenset({"proposal", "stt", "meeting"})
JOB_WAIT = 100
# Test replays, injected approvals and output-root overrides are never caller-controlled.
DENIED_FLAGS = frozenset({
    "--out", "--outputs-root", "--notes-root", "--recorded", "--meeting-recorded",
    "--recorded-response", "--response-file", "--injection-file", "--dm-target",
    "--write-body", "--forge-signature", "--user-id", "--notify-channel", "--notify-message-id",
    "--origin-channel-id", "--origin-message-id", "--origin-todo", "--allow-plaintext",
})
_FLAG = re.compile(r"--[a-z][a-z0-9-]{0,40}")
_OUT_NAME = re.compile(r"(?!\.)[A-Za-z0-9._-]{1,120}")
# Dashboard orders (owner 2026-10-21 Q351): YooDori writes back who took an order and how far it got.
ORDER_STATUSES = ("받음", "넘김", "처리 중", "끝")
_ORDER_NUMBER = re.compile(r"[1-9][0-9]{0,6}")
_POST_URL = re.compile(r"https://[A-Za-z0-9._~:/?#@!+,=%-]{1,290}")
_ORDER_FLAGS = {"--dori": 20, "--post-url": 300, "--note": 300}
# Dashboard todos (owner 2026-10-31 "처리되면 네가 대시보드에서 알아서 완료로 처리해줘"): completion only.
_TODO_ID = re.compile(r"pb-[0-9a-f]{12}-[1-9][0-9]{0,3}")
_TODO_NOTE_LIMIT = 200
# A card-approved dashboard todo as the owner's basis (owner 2026-11-03 06:09 KST Q2 "고친다: 카드 승인 할 일도 근거로
# 받음 (따로 PR, 추천)"): only to read that todo's own source mail's attachments and save them to his Drive.
# Outbound steps (mail, sharing) keep their ✅ card and never take a todo.
TODO_ORIGIN_COMMANDS = ("mail-attachment", "mail-attachment-text", "drive-save")
_NOTE_SHELL = re.compile(r"[;&|`$<>\\(){}*?!'\"]")

USAGE = ("YooDori gate: calendar <days 1-31> [query...], mail-list <n 1-50>,\n"
         "mail-get <uid> [--body], recall <question...>, status.\n"
         "Institutional mail search: mail-search <words...> [--limit n 1-50, default 20] (every folder,\n"
         "  subject/sender/saved body, all words must match, newest first; read-only).\n"
         "Institutional mail address by name: mail-resolve <name> (webmail autocomplete, read-only).\n"
         "Gmail reads: gmail-list <n 1-50> [query...], gmail-get <hex id> [--body].\n"
         "Obsidian read: note-get <PARA path>.\n"
         "Daily memory copy: memory-copy (note body on SSH stdin; fixed note, no origin).\n"
         "Daily conversation summary: day-summary <YYYY-MM-DD> (body on stdin; overwrites that day's note).\n"
         "Release reads: release-notes <vX.Y.Z>. Plaud notes written: plaud-written <n 1-50>.\n"
         "Correction ledger read: glossary-list [--kind lifelog|meeting] (wrong,right rows; no kind = all).\n"
         "Withdraw your own pending mail card: card-withdraw <draft id> <reason...> (only a card this gate\n"
         "  posted with mail-compose or mail-draft and the owner has not decided; never sends or approves).\n"
         "Dashboard order: order-status <n> <" + "|".join(ORDER_STATUSES) + ">"
         " [--dori <name>] [--post-url <https link>] [--note <text>].\n"
         "Dashboard todo done: todo-done <pb-<12 hex>-<n>> [--note <text, 1-200>] (completion only;\n"
         "  already done = no change, prints ALREADY; recorded as 유돌).\n"
         "Dashboard reads: todo-list [--open] [--json], order-list [--open] [--json] (read-only; no mail\n"
         "  sender, body or address, no order author login or device; long text is cut).\n"
         "Private upload: stage <name> [--name <display name>] (file bytes on SSH stdin, at most 64 MiB;\n"
         "  <name> is plain ASCII, --name is the Korean name a mail attachment shows, same extension).\n"
         "Received attachment: mail-attachment <uid> <file name as mail-get shows it>|--all + origin\n"
         "  (copies that file, or with --all every saved attachment (at most 20), of the owner's mail into\n"
         "  staging; prints one STAGED line per file).\n"
         "Card requests (origin channel/message ids required): " + ", ".join(CARD_COMMANDS) + "\n"
         "  drive-save --file <staged> [--file <staged> ...] [--title <t> per file] [--kind <category>]\n"
         "  [--project <p> | --folder <Drive folder id, owner-only>]: one card, one approval for all files;\n"
         "  a received attachment keeps its own name.\n"
         "  calendar-request --card: post the event card even for the owner's own message\n"
         "  (without --card his own event is written at once when the node switch is on).\n"
         "Owner-command jobs (origin = the owner's own message or his tap on a question card, checked on the node):\n"
         "  doc <" + "|".join(k for k in TOOLS if k not in {"patent", "stt", "meeting"}) + "> <sub> [args],\n"
         "  patent <create|checklist|draft|status|export> [args], stt <transcribe|ingest|polish> [args],\n"
         "  meeting <ingest|evidence> [args],\n"
         "  mail-sync (collect institutional mail now; waits for a running collection);\n"
         "  glossary-add <wrong> <right> [--kind lifelog|meeting] (the owner's transcript correction,\n"
         "  one row of the term glossary; later transcripts use it before the model);\n"
         "  mail-attachment-text <uid> <file name as mail-get shows it> (that attachment as text; scans,\n"
         "  old hwp and photos only when the node's OCR release switch is on);\n"
         "  each ends with --origin-channel-id N --origin-message-id N. Files: staged paths only;\n"
         "  a card-approved todo instead: mail-attachment, mail-attachment-text and drive-save (switch on)\n"
         "  may end with --origin-todo <pb-<12 hex>-<n>> for that todo's own source mail and its staged\n"
         "  attachments only (dashboard and card checked each time, approval at most 24 hours old);\n"
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
        if decoded in ("--forward-gmail", "--reply-gmail") and not _GMAIL_ID.fullmatch(value):
            raise Refused(f"{decoded} needs a hex Gmail message id")
        if name == "drive-save" and decoded == "--folder" and not _DRIVE_FOLDER_ID.fullmatch(value):
            raise Refused("--folder needs a Drive folder id")
        if name == "drive-save" and decoded == "--kind" and not _KIND.fullmatch(value):
            raise Refused("--kind needs a Drive category name")
        if decoded in values:
            repeats.append((decoded, value))
            continue
        values[decoded] = value
        if name == "mail-draft" and decoded == "--instruction" and (
            len(values[decoded]) > 2000 or _SHELL.search(values[decoded])
        ):
            raise Refused("instruction exceeds 2000 characters or contains shell characters")
    # A Gmail reply goes to the original's sender under its subject (gws +reply), so no --to.
    gmail_reply = name == "mail-compose" and "--reply-gmail" in values
    required = ("--body",) if gmail_reply else _REQUIRED[name]
    if any(flag not in values for flag in (*required, *_ORIGIN)):
        raise Refused("missing required request flag or owner message coordinates")
    if gmail_reply and set(values) - {"--reply-gmail", "--body", "--cc", *_ORIGIN}:
        raise Refused("--reply-gmail takes only --body and --cc (recipient and subject come from the original)")
    if name == "obsidian-request" and sum(flag in values for flag in ("--body", "--body-file")) != 1:
        raise Refused("choose exactly one of --body-file or --body-b64")
    if name == "mail-compose" and sum(f in values for f in ("--forward", "--forward-gmail", "--in-reply-to")) > 1:
        raise Refused("choose one of --forward, --forward-gmail or --in-reply-to")
    if name == "mail-compose" and "--drive-name" in values and "--drive-share" not in values:
        raise Refused("--drive-name goes with --drive-share")
    if name == "mail-compose" and "--drive-share" in values and "--drive-name" not in values:
        # A received attachment shares under its own name, not the staging name (live QA 2).
        received = original_name(STAGING, values["--drive-share"])
        if received:
            values["--drive-name"] = _value(received, "short")
    if name == "drive-save" and "--title" not in values:
        # A received attachment is saved under its own name, not the staging name mail-<uid>-<n>.
        files = [values["--file"], *(value for flag, value in repeats if flag == "--file")]
        received = [original_name(STAGING, path) for path in files]
        if any(received):
            titles = [_value(Path(shown).stem or Path(path).stem, "short") if shown else Path(path).stem
                      for path, shown in zip(files, received, strict=True)]
            values["--title"] = titles[0]
            repeats += [("--title", title) for title in titles[1:]]
    if name == "mail-compose":
        # A staged file goes out under its display name (stage --name or a received attachment's own).
        if "--attachment" in values:
            values["--attachment"] = named_attachment(STAGING, values["--attachment"])
        repeats = [(flag, named_attachment(STAGING, value) if flag == "--attachment" else value)
                   for flag, value in repeats]
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
    todo_id: str = ""
    todo_link: str = ""


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


def card_choice(words: list[str]) -> tuple[list[str], bool]:
    """``calendar-request --card``: the owner asked for a card, so Q233's cardless write is skipped.

    Only a flag position counts, so a value that reads "--card" stays a value.
    """
    if not words or words[0] != "calendar-request":
        return words, False
    rest, kept, chosen, index = words[1:], [words[0]], False, 0
    while index < len(rest):
        if rest[index] == "--card":
            if chosen:
                raise Refused("--card given twice")
            chosen, index = True, index + 1
            continue
        kept.extend(rest[index:index + 2])
        index += 2
    return kept, chosen


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


def todo_origin(words: list[str]) -> tuple[list[str], str]:
    """A trailing ``--origin-todo <id>`` in place of the owner message: (words without it, the id), or (words, "")."""
    if len(words) < 3 or words[-2] != "--origin-todo":
        return words, ""
    if words[0] not in TODO_ORIGIN_COMMANDS:
        raise Refused("--origin-todo is accepted only for " + ", ".join(TODO_ORIGIN_COMMANDS))
    if not _TODO_ID.fullmatch(words[-1]):
        raise Refused("--origin-todo needs <pb-<12 hex>-<n>>")
    if any(word in _ORIGIN for word in words[1:-2]):
        raise Refused("give either --origin-todo or the origin channel/message ids, not both")
    return words[:-2], words[-1]


def todo_basis(todo_id: str):
    """The dashboard and the card checked now (no use recorded yet)."""
    _, notice_bot = _release_modules()
    token = notice_bot.resolve_token(environ=job_env(HOME, RELEASE, ENV))
    return yoodori_jobs.verify_owner_todo(todo_id, _owner_id(), token)


def todo_target(name: str, argv: list[str], uid: str) -> None:
    """The command reads only the todo's own source mail: its uid, or a staged attachment of that mail.

    A staged ``mail-<uid>-<n>`` file is copied again from the mailbox before it is used, so an upload under
    that name (``stage``) cannot pass off other bytes as the source mail's attachment.
    """
    if name == "mail-attachment":
        target = argv[0]
    elif name == "mail-attachment-text":
        target = argv[3]
    else:
        # Every file of a batch, not only the first, must be a staged attachment of the source mail.
        for path in (Path(argv[i + 1]) for i, word in enumerate(argv[:-1]) if word == "--file"):
            match = re.fullmatch(r"mail-([0-9]{1,20})-[1-9][0-9]{0,3}(?:\.[A-Za-z][A-Za-z0-9]{0,9})?", path.name)
            received = original_name(STAGING, str(path))
            if match is None or match[1] != uid or path.parent != STAGING or not received:
                raise Refused("with --origin-todo, drive-save takes only a staged attachment of the todo's source mail")
            try:
                again, _digest, _size = stage_mail_attachment(HOME, STAGING, uid, received)
            except OSError:
                raise Refused("staging file unreadable") from None
            if again != path:
                raise Refused("with --origin-todo, drive-save takes only a staged attachment of the todo's source mail")
        return
    if target != uid:
        raise Refused("with --origin-todo, only the todo's own source mail can be read")


def stage_all_attachments(uid: str) -> int:
    """``mail-attachment <uid> --all`` (issue #241): every saved attachment, for one drive-save card.

    An attachment not saved on the node is reported and skipped; none staged at all is a refusal.
    """
    names = saved_attachment_names(HOME, uid)
    if len(names) > MAX_ALL_ATTACHMENTS:
        raise Refused(f"that mail has {len(names)} saved attachments; --all stages at most "
                      f"{MAX_ALL_ATTACHMENTS}, name them one by one")
    staged = 0
    for shown in names:
        try:
            path, digest, size = stage_mail_attachment(HOME, STAGING, uid, shown)
        except Refused as exc:
            print(f"NOT-STAGED original={shown} reason={exc}")
            continue
        staged += 1
        print(f"STAGED {path.name} sha256={digest} bytes={size} path={path} original={shown}")
    if not staged:
        raise Refused("no saved attachment of that mail could be staged")
    return 0


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
    if name == "mail-attachment-text":
        if len(rest) != 2 or not _UID.fullmatch(rest[0]):
            raise Refused("mail-attachment-text needs <uid> <file name> --origin-channel-id N --origin-message-id N")
        runtime = str(Path(__file__).resolve().with_name("yoodori_request_runtime.py"))
        argv = [py, runtime, "attachment-text", rest[0], _value(rest[1], "short"), "{OUT}"]
        # OCR may take its own 1800 s budget; the job keeps a margin above it.
        return Job(name, argv, False, f"메일 첨부 글 읽기 {rest[0]}", 2400, channel, message)
    if name == "glossary-add":
        kind, rest = _glossary_kind(rest)
        if len(rest) != 2 or any(word.startswith("-") for word in rest):
            raise Refused("glossary-add needs <wrong> <right> [--kind lifelog|meeting] "
                          "--origin-channel-id N --origin-message-id N")
        wrong, right = (_value(word, "short") for word in rest)
        argv = [py, "-m", "automation.term_glossary", "add", "--kind", kind, wrong, right]
        return Job(name, argv, True, f"용어집 {wrong} → {right}"[:160], 120, channel, message)
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


def _glossary_kind(args: list[str]) -> tuple[str, list[str]]:
    """``--kind`` anywhere in the words; the transcript ledger (lifelog) when it is not given."""
    if "--kind" not in args:
        return "lifelog", args
    at = args.index("--kind")
    if at + 1 >= len(args) or args[at + 1] not in GLOSSARY_KINDS:
        raise Refused("--kind is lifelog or meeting")
    return args[at + 1], [*args[:at], *args[at + 2:]]


def posted_by_gate(draft_id: str) -> bool:
    """True iff the draft was created during a mail-compose/mail-draft call this gate logged rc=0 or 124.

    Owner Q367: YooDori withdraws only its own cards. The gate log is written by this forced
    command alone, so a card raised another way (another Dori, a digest reaction, a release) has
    no matching call. A missing or unreadable draft or log refuses.
    """
    try:
        created = json.loads((MAIL_DRAFTS / f"{draft_id}.json").read_text(encoding="utf-8"))["created"]
        at = calendar.timegm(time.strptime(created, _STAMP))
        lines = LOG.read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError, KeyError, TypeError):
        return False
    for line in lines:
        call = _CARD_CALL.fullmatch(line)
        if call:
            try:
                end = calendar.timegm(time.strptime(call[1], _STAMP))
            except ValueError:
                continue
            if end - float(call[2]) - 1 <= at <= end:
                return True
    return False


def card_withdraw(args: list[str]) -> list[str]:
    if len(args) < 2 or not _DRAFT_ID.fullmatch(args[0]) or any(w.startswith("-") for w in args[1:]):
        raise Refused("card-withdraw needs <draft id> <reason...>")
    reason = _text(args[1:], 200)
    if not posted_by_gate(args[0]):
        raise Refused("not a mail card YooDori posted through this gate (or no such card)")
    return ["/usr/bin/python3", str(LIVE / "mail/scripts/triage_cli.py"), "withdraw",
            "--draft", args[0], "--reason", reason, "--by", "유돌"]


def order_status(args: list[str]) -> list[str]:
    """``order-update`` argv for one dashboard order; the writer is always 유돌."""
    if len(args) < 2 or not _ORDER_NUMBER.fullmatch(args[0]) or args[1] not in ORDER_STATUSES:
        raise Refused("order-status needs <n> <" + "|".join(ORDER_STATUSES) + ">")
    rest, values = args[2:], {}
    if len(rest) % 2:
        raise Refused("order-status flags take one value each")
    for flag, value in zip(rest[::2], rest[1::2]):
        if flag not in _ORDER_FLAGS or flag in values:
            raise Refused("unknown or repeated order-status flag")
        if not value.strip() or len(value) > _ORDER_FLAGS[flag] or _CTRL.search(value):
            raise Refused(f"{flag} must be 1-{_ORDER_FLAGS[flag]} characters on one line")
        if flag == "--post-url" and not _POST_URL.fullmatch(value):
            raise Refused("--post-url must be one https:// link")
        values[flag] = value
    pairs = [word for pair in values.items() for word in pair]
    return ["/usr/bin/python3", "-m", "automation.project_board", "order-update", args[0],
            "--status", args[1], *pairs, "--by", "유돌"]


def todo_done(args: list[str]) -> list[str]:
    """``progress <id> --status 완료`` argv for one dashboard todo; never deletes, reopens or edits other fields."""
    if not args or not _TODO_ID.fullmatch(args[0]):
        raise Refused("todo-done needs <pb-<12 hex>-<n>> [--note <text>]")
    rest = args[1:]
    if rest and (len(rest) != 2 or rest[0] != "--note"):
        raise Refused("todo-done takes only one --note <text>")
    note = rest[1] if rest else None
    if note is not None and (not note.strip() or len(note) > _TODO_NOTE_LIMIT or _CTRL.search(note)
                             or _NOTE_SHELL.search(note)):
        raise Refused(f"--note must be 1-{_TODO_NOTE_LIMIT} characters on one line without shell characters")
    return ["/usr/bin/python3", "-m", "automation.project_board", "progress", args[0], "--status", "완료",
            *(["--note", note] if note is not None else []), "--by", "유돌"]


def build(words: list[str]) -> tuple[str, list[str], Path]:
    if not words:
        raise Refused("empty command")
    name, args = words[0], words[1:]
    if name == "order-status":
        return name, order_status(args), RELEASE
    if name == "todo-done":
        return name, todo_done(args), RELEASE
    if name in {"todo-list", "order-list"}:
        if len(set(args)) != len(args) or any(word not in {"--open", "--json"} for word in args):
            raise Refused(f"{name} takes only [--open] [--json]")
        return name, ["/usr/bin/python3", "-m", "automation.project_board", name, *args], RELEASE
    if name == "card-withdraw":
        return name, card_withdraw(args), RELEASE
    if name == "release-notes":
        if len(args) != 1 or _RELEASE_TAG.fullmatch(args[0]) is None:
            raise Refused("release-notes needs <vX.Y.Z>")
        return name, args, HOME
    if name == "plaud-written":
        if len(args) != 1:
            raise Refused("plaud-written needs exactly <n>")
        return name, [_int(args[0], 1, 50)], HOME
    if name == "stage":
        if len(args) not in (1, 3) or (len(args) == 3 and args[1] != "--name"):
            raise Refused("stage needs one file name, optionally --name <display name>")
        return name, [stage_name(args[0]), *args[2:]], STAGING
    if name == "mail-attachment":
        rest, channel, message = _origin(args)
        if len(rest) != 2 or not _UID.fullmatch(rest[0]):
            raise Refused("mail-attachment needs <uid> <file name>|--all --origin-channel-id N --origin-message-id N")
        shown = rest[1] if rest[1] == "--all" else _value(rest[1], "short")
        return name, [rest[0], shown, channel, message], STAGING
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
    if name == "mail-search":
        limit = "20"
        if len(args) >= 2 and args[-2] == "--limit":
            limit, args = _int(args[-1], 1, 50), args[:-2]
        if not args or any(word.startswith("-") for word in args):
            raise Refused("mail-search needs <query...> [--limit n]")
        return name, [py, str(LIVE / "mail/scripts/mail_wrapper.py"), "search", "--limit", limit,
                      "--query", _text(args, 200)], HOME
    if name == "mail-resolve":
        if len(args) != 1 or args[0].startswith("-"):
            raise Refused("mail-resolve needs exactly one <name>")
        return name, [py, str(LIVE / "mail/scripts/mail_wrapper.py"), "resolve", "--name", _text(args, 40)], HOME
    if name == "note-get":
        if len(args) != 1:
            raise Refused("note-get needs exactly one PARA-relative path")
        return name, ["/usr/bin/cat", "--", str(note_path(args[0]))], HOME
    if name == "recall":
        return name, [py, str(LIVE / "recall/scripts/recall_cli.py"), "search", _text(args, 300)], HOME
    if name == "glossary-list":
        kinds = ["--kind", _glossary_kind(args)[0]] if "--kind" in args else []
        if args not in ([], kinds):
            raise Refused("glossary-list takes only [--kind lifelog|meeting]")
        return name, [py, "-m", "automation.term_glossary", "list", *kinds], RELEASE
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


def plaud_written(home: Path, count: int) -> dict:
    """The latest Plaud notes written without a card, for YooDori's one-line notice.

    Owner 2026-10-19 17:20 KST "좋아. 전사되면 간단하게 알림만 해줘": names and note paths only,
    read from the shared auto-write ledger; no note body leaves the node.
    """
    notes = []
    try:
        lines = (home / ".hermes/auto-write/ledger.jsonl").read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("kind") == "plaud" and isinstance(row.get("path"), str):
            notes.append({"id": str(row.get("source_id", "")), "title": Path(row["path"]).stem,
                          "path": row["path"], "recorded_at": str(row.get("recorded_at", "")),
                          "written_at": str(row.get("written_at", ""))})
    return {"status": "ok", "notes": notes[-count:]}


def _switched(kind: str) -> bool:
    """Unreadable or malformed node settings keep the card path."""
    try:
        auto_write, _notice = _release_modules()
        return bool(auto_write.enabled(kind))
    except (ImportError, OSError, ValueError):
        return False


def run_job(job: Job) -> int:
    env = job_env(HOME, RELEASE, ENV)
    # A todo basis was checked (dashboard, card, target) and its use recorded before the job was built.
    link = job.todo_link
    basis = None
    if job.name in ORIGIN_READ_JOBS and not job.todo_id:
        _, notice_bot = _release_modules()
        basis = yoodori_jobs.verify_owner_origin(
            job.channel_id, job.message_id, _owner_id(), notice_bot.resolve_token(environ=env), home=HOME)
        link = basis.link
    if job.write:
        auto_write, notice_bot = _release_modules()
        try:
            switched_on = auto_write.enabled(job.kind)
        except ValueError:
            switched_on = False
        if not switched_on:
            raise Refused(f"owner-command writes are off on this node (release switch auto-write-{job.kind})")
        if job.needs_origin and not job.todo_id:
            token = notice_bot.resolve_token(environ=env)
            basis = yoodori_jobs.verify_owner_origin(job.channel_id, job.message_id, _owner_id(), token, home=HOME)
            link = basis.link
    path = yoodori_jobs.new_job(HOME, {
        "name": job.name, "argv": job.argv, "write": job.write, "label": job.label,
        "timeout": job.timeout, "kind": job.kind, "marker": job.marker,
        "origin": {"channel_id": job.channel_id, "message_id": job.message_id, "link": link,
                   **({"kakao": list(basis.kakao), "sent_at": basis.sent_at} if basis and basis.kakao else {}),
                   **({"todo_id": job.todo_id} if job.todo_id else {})},
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
        if words and words[0] not in (*CARD_COMMANDS, *JOB_COMMANDS, "order-status") and len(raw) > 600:
            raise Refused("read command too long")
        words, todo = todo_origin(words)
        basis = None
        if todo:
            basis = todo_basis(todo)
            if basis.source_kind != "mail":
                raise Refused("with --origin-todo, only a todo that came from a mail can be read")
            words = [*words, "--origin-channel-id", basis.channel_id, "--origin-message-id", basis.message_id]
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
            words, card = card_choice(words)
            name, argv, cwd = build(words)
            if name in CARDLESS and not card and _switched(CARDLESS[name]):
                if name == "obsidian-request":
                    argv = stage_body(argv, STAGING)
                job = cardless_job(name, argv)
        if basis is not None:
            if name == "drive-save" and job is None:
                raise Refused("with --origin-todo, drive-save runs only when the node's drive-save owner-command "
                              "switch is on (no card is raised for a todo)")
            todo_target(name, job.argv if job is not None else argv, basis.source_id)
            yoodori_jobs.record_todo_use(basis.todo_id)
            if job is not None:
                job = replace(job, todo_id=basis.todo_id, todo_link=basis.link)
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
        elif name == "plaud-written":
            print(json.dumps(plaud_written(HOME, int(argv[0])), ensure_ascii=False))
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
            shown = argv[1] if len(argv) > 1 else None
            path, digest, size = stage_named(STAGING, argv[0], shown, sys.stdin.buffer)
            print(f"STAGED {argv[0]} sha256={digest} bytes={size} path={path}"
                  + (f" display={original_name(STAGING, str(path))}" if shown is not None else ""))
            rc = 0
        elif name == "mail-attachment":
            if basis is None:
                _, notice_bot = _release_modules()
                token = notice_bot.resolve_token(environ=job_env(HOME, RELEASE, ENV))
                yoodori_jobs.verify_owner_origin(argv[2], argv[3], _owner_id(), token, home=HOME)
            if argv[1] != "--all":
                path, digest, size = stage_mail_attachment(HOME, STAGING, argv[0], argv[1])
                print(f"STAGED {path.name} sha256={digest} bytes={size} path={path} original={argv[1]}")
                rc = 0
            else:
                rc = stage_all_attachments(argv[0])
        else:
            env = card_env(HOME, RELEASE, ENV) if name in (*CARD_COMMANDS, "card-withdraw") else ENV
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
    _log(f"{name} id={argv[4]}" if name == "todo-done" else name, rc, started)
    return rc


if __name__ == "__main__":
    sys.exit(main())
