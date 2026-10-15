"""Loopback-only dashboard server: the live view, original sources, and the TODO check.

Original sources and attachments are served only for evidence an approved card cites, and only
the attachment names that card recorded — never an arbitrary stored mail or file.

Local only by owner decision: a non-loopback bind is refused, not configurable. Every
request needs the token from the private state root (``?t=`` once, then an HttpOnly
SameSite=Strict cookie). A progress POST additionally needs the token in a header or form
field and, when the browser sends one, a same-origin ``Origin`` — another local page
cannot tick a todo through the owner's cookie.

Tailnet access goes through ``tailscale serve`` on another host, which overwrites the
``Tailscale-User-Login`` header with the caller's real identity. A request that arrives under a
configured tailnet origin, or carries that header at all, must come from an allowlisted login.
By owner decision (2026-10-14) that login is enough on the tailnet path — no token — unless
``dashboard.tailnet.token`` turns the token back on. The accepted risk: a local process on the
node or the gateway can reach the loopback port and forge the header. A tokenless progress POST
must carry a configured tailnet ``Origin``, so another site open in the owner's browser cannot
tick a todo through ``tailscale serve``.
"""

from __future__ import annotations

import hmac
import json
import logging
import threading
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
IDENTITY_HEADER: Final = "Tailscale-User-Login"

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
    tailnet_origins: frozenset[str] = frozenset()
    tailnet_logins: frozenset[str] = frozenset()
    tailnet_token: bool = False

    def _via_tailnet(self) -> bool:
        host = (self.headers.get("Host") or "").strip().lower()
        return self.headers.get(IDENTITY_HEADER) is not None or f"https://{host}" in self.tailnet_origins

    def _identity_ok(self) -> bool:
        if not self._via_tailnet():
            return True
        login = self.headers.get(IDENTITY_HEADER)
        return login is not None and login.strip().lower() in self.tailnet_logins

    def _tokenless(self) -> bool:
        """An allowlisted tailnet login stands in for the token unless the config asks for both."""
        return not self.tailnet_token and self._via_tailnet() and self._identity_ok()

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
        if not self._identity_ok():
            self._text(403, "forbidden")
            return
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
        if not self._tokenless() and not self._cookie_ok():
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
        page = render_dashboard(snapshot, mode="served", token="" if self._tokenless() else self.token)
        self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")

    def _source(self, kind: str, source_id: str) -> None:
        text = self.read_source(kind, source_id) if self.store.approved_attachments(kind, source_id) is not None else None
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
        if name not in (self.store.approved_attachments(kind, source_id) or ()):
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
        if not self._identity_ok():
            self._text(403, "forbidden")
            return
        parts = [unquote(part) for part in urlparse(self.path).path.split("/") if part]
        if len(parts) != 2 or parts[0] not in ("todo", "project"):
            self._text(404, "not found")
            return
        act = self._record if parts[0] == "todo" else self._archive
        length = int(self.headers.get("Content-Length") or 0)
        if length > _MAX_BODY:
            self._text(413, "request too large")
            return
        form = parse_qs(self.rfile.read(length).decode("utf-8", errors="replace"))
        if self._tokenless():
            if self.headers.get("Origin") not in self.tailnet_origins:
                self._text(403, "forbidden")
                return
            act(parts[1], form)
            return
        if not self._cookie_ok():
            self._text(401, "authentication required")
            return
        supplied = self.headers.get("X-PB-Token") or form.get("token", [""])[0]
        origin = self.headers.get("Origin")
        allowed = {f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}", f"http://[::1]:{self.port}",
                   *self.tailnet_origins}
        if not self._matches(supplied) or (origin is not None and origin not in allowed):
            self._text(403, "forbidden")
            return
        act(parts[1], form)

    def _archive(self, project: str, form: dict[str, list[str]]) -> None:
        """보관 / 보관 해제 — the same authority as a progress tick; who is the tailnet login, else the token holder."""
        value = form.get("archived", [""])[0]
        if value not in ("0", "1"):
            self._text(400, "archived must be 0 or 1")
            return
        login = self.headers.get(IDENTITY_HEADER) if self._via_tailnet() else None
        who = login.strip().lower() if login else "로컬 대시보드(토큰)"
        try:
            changed = self.store.set_archived(project, value == "1", by=who)
        except ProjectBoardError as error:
            self._text(400, str(error))
            return
        try:
            self.on_change()
        except Exception as error:  # noqa: BLE001 - the mark is recorded; the file rebuild is best-effort
            logger.warning("dashboard rebuild failed: %s", type(error).__name__)
        body = json.dumps({"ok": True, "project": project, "archived": value == "1", "changed": changed}, ensure_ascii=False)
        self._send(200, body.encode("utf-8"), "application/json; charset=utf-8")

    def _record(self, todo_id: str, form: dict[str, list[str]]) -> None:
        status = form.get("status", [""])[0] or None
        raw_progress = form.get("progress", [""])[0]
        try:
            if status is not None and status not in STATUSES:
                raise ProjectBoardError(f"status must be one of {', '.join(STATUSES)}")
            progress = int(raw_progress) if raw_progress else None
            todo = self.store.record_progress(todo_id, status=status, progress=progress)
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


def exit_on_new_release(server: ThreadingHTTPServer, pointer: Path, stop: threading.Event, interval: float = 60.0) -> None:
    """Shut the server down once ``pointer`` resolves elsewhere, so systemd restarts it on the new release."""
    started = pointer.resolve()
    while not stop.wait(interval):
        if pointer.resolve() != started:
            logger.info("release moved; exiting so the unit restarts on it")
            server.shutdown()
            return


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
    tailnet_origins: tuple[str, ...] = (),
    tailnet_logins: tuple[str, ...] = (),
    tailnet_token: bool = False,
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
            "tailnet_origins": frozenset(o.lower() for o in tailnet_origins),
            "tailnet_logins": frozenset(x.lower() for x in tailnet_logins),
            "tailnet_token": tailnet_token,
        },
    )
    server = ThreadingHTTPServer((host, port), handler)
    handler.port = int(server.server_address[1])
    return server
