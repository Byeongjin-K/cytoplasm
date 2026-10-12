"""Resolve the mail being forwarded (FW) into a quote and byte-exact attachment copies.

A forward is a new mail that carries the original: ``triage_cli compose --forward``
hands what this module returns to the same ``compose_and_post`` → ``create_draft``
path a new mail takes. Recipients, the 10-file / 25 MiB attachment limits, the
owner ✅ card and the pre-send sha256 manifest check are therefore exactly those
of a new mail — forwarding grants nothing a new mail would not.

Two sources, both read-only:

* institution mail (MailOn): the stored markdown (``mail_wrapper get --body``) and
  the attachment rows of the MailOn ``state.db`` with their downloaded files;
* Gmail: ``gws gmail users messages get`` / ``attachments get``.

The original files are copied into a private per-forward directory so the
approved bytes cannot move with a MailOn runtime release; the copy is checked
against the source digest (MailOn) or the declared Gmail part size. Any
attachment that is not fully available refuses the forward — a forward that
silently drops a file is worse than no forward.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Final, Literal

import mail_quote
import mail_wrapper
import triage_core
import triage_gate
from triage_mode import gate_dir

Origin = Literal["kimm", "gmail"]

FORWARD_DIR: Final = "forward"
GWS_TIMEOUT_SECONDS: Final = 120
_SUBJECT_PREFIXES: Final = ("fw:", "fwd:", "전달:")
# Scraper artifacts the MailOn runtime records as attachments (same list the Drive archive skips).
_BOGUS_NAMES: Final = frozenset({"Save", "Download all"})

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class ForwardRefused(triage_gate.GateError):
    """The original cannot be forwarded exactly; nothing was drafted (exit 2)."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"FORWARD-REFUSED reason={reason} — {detail}", 2)


@dataclass(frozen=True, slots=True)
class ForwardSource:
    """What a forward adds to a new mail: subject, quoted original, original files."""

    origin: Origin
    ref: str
    subject: str
    quote: str
    attachments: tuple[Path, ...]
    directory: Path

    def record(self) -> dict[str, object]:
        """The draft field (bound into the draft hash) that marks this send as a forward."""
        return {
            "source": self.origin,
            "ref_opaque": triage_core.mask_value(self.ref),
            "attachment_count": len(self.attachments),
        }

    def discard(self) -> None:
        """Remove the private copies when no draft ends up owning them."""
        shutil.rmtree(self.directory, ignore_errors=True)


def forward_subject(original_subject: str) -> str:
    """``FW: <original>`` unless the original already carries a forward prefix."""
    stripped = original_subject.strip()
    if stripped.lower().startswith(_SUBJECT_PREFIXES):
        return stripped
    return f"FW: {stripped}" if stripped else "FW:"


def from_mailon(uid: str, detail: Mapping[str, object]) -> ForwardSource:
    """Institution mail ``uid``: quote its stored document and copy its downloaded files."""
    if not detail.get("body"):
        raise ForwardRefused("original-unavailable", "기관메일 원문(markdown)을 읽을 수 없습니다")
    cfg = mail_wrapper._cfg()
    rows = _attachment_rows(Path(cfg["db"]), uid)
    declared = _declared_attachments(str(detail.get("body") or ""))
    names = [*declared, *(name for name in rows if name not in declared)]
    names = [name for name in names if name not in _BOGUS_NAMES]
    sources: list[tuple[str, Path]] = []
    for name in names:
        row = rows.get(name)
        if row is None or row["status"] != "ok":
            status = "missing" if row is None else str(row["status"])
            raise ForwardRefused(
                "attachment-incomplete",
                f"원 첨부 `{name}` 이 내려받아지지 않았습니다(status={status}) — 동기화 후 다시 요청하세요",
            )
        local = _mailon_file(Path(cfg["repo"]), uid, name, row["local_path"])
        if local is None:
            raise ForwardRefused("attachment-missing", f"원 첨부 `{name}` 파일이 없습니다")
        sources.append((name, local))
    original = mail_quote.parse_original(detail)
    directory = _new_directory()
    try:
        copies = tuple(
            _copy_exact(directory, index, name, source) for index, (name, source) in enumerate(sources)
        )
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return ForwardSource(
        "kimm", uid, forward_subject(original.subject), mail_quote.render_quote(original), copies, directory,
    )


def from_gmail(message_id: str, *, runner: Runner = subprocess.run) -> ForwardSource:
    """Gmail message ``message_id``: read it through gws and write its parts byte-for-byte."""
    message = _gws_json(runner, "messages", {"userId": "me", "id": message_id, "format": "full"})
    payload = message.get("payload")
    if not isinstance(payload, dict):
        raise ForwardRefused("gmail-read-failed", "Gmail 메시지 형식을 읽을 수 없습니다")
    headers = _headers(payload)
    parts = list(_walk(payload))
    original = mail_quote.Original(
        sender=headers.get("from", ""), to=headers.get("to", ""), cc=headers.get("cc", ""),
        date=headers.get("date", ""), subject=headers.get("subject", ""), body=_gmail_text(parts),
    )
    directory = _new_directory()
    try:
        copies: list[Path] = []
        for part in parts:
            filename = str(part.get("filename") or "")
            body = part.get("body") if isinstance(part.get("body"), dict) else {}
            if not filename:
                continue
            data = _gmail_attachment_bytes(runner, message_id, filename, body)
            declared = body.get("size")
            if not isinstance(declared, int) or declared != len(data):
                raise ForwardRefused(
                    "attachment-size-mismatch",
                    f"Gmail 첨부 `{filename}` 크기가 선언({declared})과 다릅니다({len(data)})",
                )
            copies.append(_write_exact(directory, len(copies), filename, data))
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return ForwardSource(
        "gmail", message_id, forward_subject(original.subject), mail_quote.render_quote(original),
        tuple(copies), directory,
    )


def _attachment_rows(db: Path, uid: str) -> dict[str, dict[str, object]]:
    if not db.is_file():
        raise ForwardRefused("state-db-missing", "기관메일 상태 DB가 없습니다")
    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT filename, status, local_path FROM attachments WHERE uid = ? ORDER BY rowid",
            (uid,),
        ).fetchall()
    finally:
        connection.close()
    return {str(row["filename"]): dict(row) for row in rows}


def _declared_attachments(document: str) -> list[str]:
    """The ``attachments:`` list of the MailOn front matter (writer.build_markdown)."""
    lines = document.splitlines()
    if not lines or lines[0].strip() != "---":
        return []
    names: list[str] = []
    inside = False
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if line.startswith("attachments:"):
            inside = True
            continue
        if inside and line.startswith("  - "):
            names.append(_unquote(line[4:].strip()))
        elif inside:
            break
    return names


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return value


def _mailon_file(repo: Path, uid: str, name: str, local_path: object) -> Path | None:
    candidates = []
    if isinstance(local_path, str) and local_path:
        supplied = Path(local_path)
        candidates.append(supplied if supplied.is_absolute() else repo / supplied)
    candidates.append(repo / "data" / "attachments" / uid / name)
    for candidate in candidates:
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    return None


def _gws_json(runner: Runner, resource: str, params: Mapping[str, str]) -> dict[str, object]:
    argv = ["gws", "gmail", "users", "messages"]
    if resource == "attachments":
        argv.append("attachments")
    argv += ["get", "--params", json.dumps(dict(params), ensure_ascii=False)]
    try:
        process = runner(
            argv, capture_output=True, text=True, timeout=GWS_TIMEOUT_SECONDS, check=False,
            env=dict(os.environ),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ForwardRefused("gmail-read-failed", f"gws 실행 실패 ({type(error).__name__})") from None
    if process.returncode != 0:
        raise ForwardRefused("gmail-read-failed", f"gws rc={process.returncode}")
    try:
        parsed = json.loads(process.stdout)
    except json.JSONDecodeError:
        raise ForwardRefused("gmail-read-failed", "gws 응답이 JSON 이 아닙니다") from None
    if not isinstance(parsed, dict):
        raise ForwardRefused("gmail-read-failed", "gws 응답 형식이 올바르지 않습니다")
    return parsed


def _gmail_attachment_bytes(
    runner: Runner, message_id: str, filename: str, body: Mapping[str, object],
) -> bytes:
    data = body.get("data")
    if not isinstance(data, str):
        attachment_id = body.get("attachmentId")
        if not isinstance(attachment_id, str) or not attachment_id:
            raise ForwardRefused("attachment-missing", f"Gmail 첨부 `{filename}` 의 데이터가 없습니다")
        fetched = _gws_json(
            runner, "attachments", {"userId": "me", "messageId": message_id, "id": attachment_id},
        )
        data = fetched.get("data")
        if not isinstance(data, str):
            raise ForwardRefused("attachment-missing", f"Gmail 첨부 `{filename}` 의 데이터가 없습니다")
    return _b64url(data)


def _b64url(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _headers(payload: Mapping[str, object]) -> dict[str, str]:
    found: dict[str, str] = {}
    for header in payload.get("headers") or []:
        if isinstance(header, dict):
            name = str(header.get("name") or "").lower()
            if name and name not in found:
                found[name] = str(header.get("value") or "")
    return found


def _walk(part: Mapping[str, object]) -> Sequence[Mapping[str, object]]:
    found: list[Mapping[str, object]] = [part]
    for child in part.get("parts") or []:
        if isinstance(child, dict):
            found.extend(_walk(child))
    return found


def _gmail_text(parts: Sequence[Mapping[str, object]]) -> str:
    for mime in ("text/plain", "text/html"):
        for part in parts:
            body = part.get("body") if isinstance(part.get("body"), dict) else {}
            if part.get("mimeType") == mime and not part.get("filename") and isinstance(body.get("data"), str):
                text = _b64url(body["data"]).decode("utf-8", errors="replace")
                return text.strip() if mime == "text/plain" else _html_text(text)
    return ""


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        self.chunks.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"br", "p", "div", "tr", "li"}:
            self.chunks.append("\n")


def _html_text(html: str) -> str:
    parser = _Text()
    parser.feed(html)
    lines = [" ".join(line.split()) for line in "".join(parser.chunks).splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _new_directory() -> Path:
    root = gate_dir() / FORWARD_DIR
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = root / secrets.token_hex(8)
    directory.mkdir(mode=0o700)
    return directory


def _slot(directory: Path, index: int, name: str) -> Path:
    safe = name.replace("/", "_").replace("\\", "_").replace("\0", "_").strip()
    if safe in {"", ".", ".."}:
        safe = f"attachment-{index + 1}"
    slot = directory / str(index)
    slot.mkdir(mode=0o700)
    return slot / safe


def _copy_exact(directory: Path, index: int, name: str, source: Path) -> Path:
    target = _slot(directory, index, name)
    shutil.copyfile(source, target)
    target.chmod(0o600)
    if _sha256(source) != _sha256(target):
        raise ForwardRefused("attachment-copy-mismatch", f"원 첨부 `{name}` 복사본이 원본과 다릅니다")
    return target


def _write_exact(directory: Path, index: int, name: str, data: bytes) -> Path:
    target = _slot(directory, index, name)
    target.write_bytes(data)
    target.chmod(0o600)
    if target.read_bytes() != data:
        raise ForwardRefused("attachment-copy-mismatch", f"Gmail 첨부 `{name}` 기록본이 원본과 다릅니다")
    return target


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
