"""Compact Gmail JSON via the node's gws credential; messages list/get only."""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
from html.parser import HTMLParser
from typing import TypeAlias

Json: TypeAlias = "str | int | bool | None | list[Json] | dict[str, Json]"
MESSAGE_ID = re.compile(r"[0-9a-f]{8,32}")


class GmailReadError(Exception):
    pass


def argv(operation: str, params: dict[str, Json]) -> list[str]:
    """The same userId/id/format --params shape as mail_forward.from_gmail."""
    if operation not in {"list", "get"}:
        raise GmailReadError("operation-refused")
    return ["gws", "gmail", "users", "messages", operation,
            "--params", json.dumps(params, ensure_ascii=False)]


def _read(operation: str, params: dict[str, Json]) -> dict[str, Json]:
    process = subprocess.run(argv(operation, params), capture_output=True, text=True,
                             stdin=subprocess.DEVNULL, env=dict(os.environ), timeout=60, check=False)
    if process.returncode:
        raise GmailReadError("gws-failed")
    start = process.stdout.find("{")
    if start < 0:
        raise GmailReadError("invalid-response")
    parsed = json.loads(process.stdout[start:])
    if not isinstance(parsed, dict):
        raise GmailReadError("invalid-response")
    return parsed


def _id(value: Json) -> str:
    if not isinstance(value, str) or MESSAGE_ID.fullmatch(value) is None:
        raise GmailReadError("invalid-id")
    return value


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag in {"br", "p", "div", "li"}:
            self.chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.chunks.append(data)


def _body(payload: dict[str, Json]) -> str:
    """Read inline text MIME parts, ignoring attachments; never fetch attachment bytes."""
    plain: list[str] = []
    html: list[str] = []
    queue = [payload]
    while queue:
        part = queue.pop(0)
        children = part.get("parts", [])
        if isinstance(children, list):
            queue.extend(child for child in children if isinstance(child, dict))
        body = part.get("body")
        if part.get("filename") or not isinstance(body, dict):
            continue
        data = body.get("data")
        if not isinstance(data, str) or part.get("mimeType") not in {"text/plain", "text/html"}:
            continue
        text = base64.b64decode(data + "=" * (-len(data) % 4), altchars=b"-_", validate=True).decode(
            "utf-8", errors="replace")
        (plain if part.get("mimeType") == "text/plain" else html).append(text)
    if plain:
        return "\n".join(plain).strip()
    parser = _HTMLText()
    parser.feed("\n".join(html))
    return "\n".join(" ".join(line.split()) for line in "".join(parser.chunks).splitlines()).strip()


def _mail(message_id: str, with_body: bool) -> dict[str, Json]:
    message = _read("get", {"userId": "me", "id": _id(message_id),
                            "format": "full" if with_body else "metadata"})
    payload = message.get("payload")
    if message.get("id") != message_id or not isinstance(payload, dict):
        raise GmailReadError("invalid-message")
    headers: dict[str, str] = {}
    entries = payload.get("headers", [])
    if not isinstance(entries, list):
        raise GmailReadError("invalid-headers")
    for entry in entries:
        if isinstance(entry, dict) and isinstance(entry.get("name"), str) and isinstance(entry.get("value"), str):
            name, value = entry["name"], entry["value"]
            headers.setdefault(name.lower(), value)
    mail: dict[str, Json] = {
        "id": message_id, "from": headers.get("from", ""), "subject": headers.get("subject", ""),
        "date": headers.get("date", ""), "snippet": str(message.get("snippet", "")),
    }
    if with_body:
        mail["body"] = _body(payload)
    return mail


def request(words: list[str]) -> dict[str, Json]:
    """The gate already validates these; recheck this directly executable boundary."""
    if not words:
        raise GmailReadError("usage")
    if words[0] == "get" and len(words) in {2, 3}:
        if len(words) == 3 and words[2] != "--body":
            raise GmailReadError("usage")
        return {"status": "ok", "mail": _mail(_id(words[1]), len(words) == 3)}
    if (words[0] != "list" or len(words) not in {2, 3}
            or not re.fullmatch(r"[0-9]{1,2}", words[1]) or not 1 <= int(words[1]) <= 50):
        raise GmailReadError("usage")
    params: dict[str, Json] = {"userId": "me", "maxResults": int(words[1])}
    if len(words) == 3:
        if not 1 <= len(words[2]) <= 200:
            raise GmailReadError("usage")
        params["q"] = words[2]
    result = _read("list", params)
    messages = result.get("messages", [])
    if not isinstance(messages, list) or len(messages) > int(words[1]):
        raise GmailReadError("invalid-list")
    mails: list[Json] = []
    for row in messages:
        if not isinstance(row, dict):
            raise GmailReadError("invalid-list")
        mails.append(_mail(_id(row.get("id")), False))
    return {"status": "ok", "mails": mails}


def main(words: list[str]) -> int:
    try:
        output = request(words)
    except (GmailReadError, OSError, ValueError, subprocess.TimeoutExpired):
        # Neither stdout/stderr from gws nor its exception text crosses this boundary.
        print(json.dumps({"status": "error", "error": "gmail-read-failed"}))
        return 3
    print(json.dumps(output, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
