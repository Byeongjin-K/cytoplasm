"""Loopback-only dashboard server: the live view, original sources, and the TODO check.

Local only by owner decision: a non-loopback bind is refused, not configurable. Every
request needs the token from the private state root (``?t=`` once, then an HttpOnly
SameSite=Strict cookie). A progress POST additionally needs the token in a header or form
field and, when the browser sends one, a same-origin ``Origin`` — another local page
cannot tick a todo through the owner's cookie.
"""

from __future__ import annotations

import hmac
import json
import logging
from collections.abc import Callable
from datetime import date
from html import escape
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Final
from urllib.parse import parse_qs, quote, unquote, urlparse

from .board_store import BoardStore
from .dashboard import render_dashboard
from .models import STATUSES, PendingView, ProjectBoardError

LOOPBACK_HOSTS: Final = frozenset({"127.0.0.1", "::1", "localhost"})
MIN_TOKEN: Final = 32
COOKIE: Final = "pb_token"
_CSP: Final = (
    "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; "
    "form-action 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"
)
_MAX_BODY: Final = 4096

logger = logging.getLogger("project_board.server")


class _Handler(BaseHTTPRequestHandler):
    token: str
    port: int
    store: BoardStore
    today: Callable[[], date]
    pending: Callable[[], tuple[PendingView, ...]]
    read_source: Callable[[str, str], str | None]
    attachment_path: Callable[[str, str, str], Path | None]
    on_change: Callable[[], None]

    def _send(self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", _CSP)
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _text(self, status: int, text: str) -> None:
        self._send(status, (text + "\n").encode("utf-8"), "text/plain; charset=utf-8")

    def _matches(self, value: str) -> bool:
        return bool(value) and hmac.compare_digest(value.encode("utf-8"), self.token.encode("utf-8"))

    def _cookie_ok(self) -> bool:
        jar = SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
        except Exception:  # noqa: BLE001 - a malformed cookie header is just "no token"
            return False
        morsel = jar.get(COOKIE)
        return morsel is not None and self._matches(morsel.value)

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        offered = query.get("t", [""])[0]
        if offered:
            if not self._matches(offered):
                self._text(401, "authentication required")
                return
            cookie = f"{COOKIE}={self.token}; HttpOnly; SameSite=Strict; Path=/"
            self._send(303, b"", "text/plain", {"Location": parsed.path or "/", "Set-Cookie": cookie})
            return
        if not self._cookie_ok():
            self._text(401, "authentication required — open the URL with ?t=<token> once")
            return
        parts = [unquote(part) for part in parsed.path.split("/") if part]
        if not parts:
            self._dashboard()
        elif parts[0] == "source" and len(parts) == 3:
            self._source(parts[1], parts[2])
        elif parts[0] == "source" and len(parts) == 5 and parts[3] == "attachment":
            self._attachment(parts[1], parts[2], parts[4])
        else:
            self._text(404, "not found")

    def _dashboard(self) -> None:
        snapshot = self.store.snapshot(today=self.today(), pending=self.pending())
        page = render_dashboard(snapshot, mode="served", token=self.token)
        self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")

    def _source(self, kind: str, source_id: str) -> None:
        text = self.read_source(kind, source_id)
        if text is None:
            self._text(404, "source not found")
            return
        page = (
            '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"><title>원문</title>'
            "<style>body{font:15px/1.6 -apple-system,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;"
            "margin:0 auto;max-width:900px;padding:16px;background:#f5f5f7;color:#1d1d1f}"
            "pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#fff;padding:16px;border-radius:12px}"
            "</style></head><body><p><a href=\"/\">← 대시보드</a></p>"
            f"<pre>{escape(text)}</pre></body></html>"
        )
        self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")

    def _attachment(self, kind: str, source_id: str, name: str) -> None:
        if "/" in name or "\\" in name or ".." in name or not name:
            self._text(404, "attachment not found")
            return
        path = self.attachment_path(kind, source_id, name)
        if path is None or not path.is_file():
            self._text(404, "attachment not found")
            return
        data = path.read_bytes()
        disposition = f"attachment; filename*=UTF-8''{quote(name, safe='')}"
        self._send(200, data, "application/octet-stream", {"Content-Disposition": disposition})

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        parts = [unquote(part) for part in urlparse(self.path).path.split("/") if part]
        if len(parts) != 2 or parts[0] != "todo":
            self._text(404, "not found")
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > _MAX_BODY:
            self._text(413, "request too large")
            return
        form = parse_qs(self.rfile.read(length).decode("utf-8", errors="replace"))
        if not self._cookie_ok():
            self._text(401, "authentication required")
            return
        supplied = self.headers.get("X-PB-Token") or form.get("token", [""])[0]
        origin = self.headers.get("Origin")
        allowed = {f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}", f"http://[::1]:{self.port}"}
        if not self._matches(supplied) or (origin is not None and origin not in allowed):
            self._text(403, "forbidden")
            return
        status = form.get("status", [""])[0] or None
        raw_progress = form.get("progress", [""])[0]
        try:
            if status is not None and status not in STATUSES:
                raise ProjectBoardError(f"status must be one of {', '.join(STATUSES)}")
            progress = int(raw_progress) if raw_progress else None
            todo = self.store.record_progress(parts[1], status=status, progress=progress)
        except (ProjectBoardError, ValueError) as error:
            self._text(400, str(error))
            return
        try:
            self.on_change()
        except Exception as error:  # noqa: BLE001 - the progress is recorded; the file rebuild is best-effort
            logger.warning("dashboard rebuild failed: %s", type(error).__name__)
        if "application/json" in (self.headers.get("Accept") or ""):
            body = json.dumps({"ok": True, "todo_id": todo.todo_id, "status": todo.status}, ensure_ascii=False)
            self._send(200, body.encode("utf-8"), "application/json; charset=utf-8")
            return
        self._send(303, b"", "text/plain", {"Location": f"/#todo-{todo.todo_id}"})

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - http.server API
        logger.info("%s %s", self.command, urlparse(self.path).path)


def make_server(
    *,
    host: str,
    port: int,
    token: str,
    store: BoardStore,
    today: Callable[[], date],
    pending: Callable[[], tuple[PendingView, ...]],
    read_source: Callable[[str, str], str | None],
    attachment_path: Callable[[str, str, str], Path | None],
    on_change: Callable[[], None],
) -> ThreadingHTTPServer:
    if host not in LOOPBACK_HOSTS:
        raise ProjectBoardError(f"dashboard is local only; refusing to bind {host}")
    if len(token) < MIN_TOKEN:
        raise ProjectBoardError(f"dashboard token must be at least {MIN_TOKEN} characters")
    handler = type(
        "BoundProjectBoardHandler",
        (_Handler,),
        {
            "token": token,
            "port": port,
            "store": store,
            "today": staticmethod(today),
            "pending": staticmethod(pending),
            "read_source": staticmethod(read_source),
            "attachment_path": staticmethod(attachment_path),
            "on_change": staticmethod(on_change),
        },
    )
    server = ThreadingHTTPServer((host, port), handler)
    handler.port = int(server.server_address[1])
    return server
