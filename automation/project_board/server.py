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
must carry a tailnet ``Origin`` — a configured one, or the page's own (``https://`` + the ``Host``
the browser addressed) — so another site open in the owner's browser cannot tick a todo through
``tailscale serve``. The same-origin rule keeps saving working after the node moves to another
tailnet host and the configured origin goes stale (2026-10-21: every save was refused with 403).
"""

from __future__ import annotations

import gzip
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
from .dashboard import DEFAULT_BRAND, LOCAL_ACTOR, project_href, render_dashboard, stamp_etag, todo_href
from .dashboard_theme import icon, notice_page, page_shell
from .models import STATUSES, PendingView, ProjectBoardError
from .sessions import SessionsState
from .ledger_view import load_ledger

LOOPBACK_HOSTS: Final = frozenset({"127.0.0.1", "::1", "localhost"})
MIN_TOKEN: Final = 32
COOKIE: Final = "pb_token"
_CSP: Final = (
    "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; "
    "form-action 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"
)
_MAX_BODY: Final = 4096
# opened from the dashboard: go back to that very screen and say its name; opened directly: the plain link
_BACK_JS: Final = (
    "<script>(function(){var a=document.querySelector('[data-back]'),n='';"
    "try{n=sessionStorage.getItem('pb-back')||'';}catch(e){}"
    "if(!a||!n||history.length<2){return;}a.querySelector('span').textContent=n;"
    "a.addEventListener('click',function(e){e.preventDefault();history.back();});})();</script>"
)
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
    brand: str = DEFAULT_BRAND
    sessions: Callable[[], SessionsState]

    def _via_tailnet(self) -> bool:
        host = (self.headers.get("Host") or "").strip().lower()
        return self.headers.get(IDENTITY_HEADER) is not None or f"https://{host}" in self.tailnet_origins

    def _identity_ok(self) -> bool:
        if not self._via_tailnet():
            return True
        login = self.headers.get(IDENTITY_HEADER)
        return login is not None and login.strip().lower() in self.tailnet_logins

    def _actor(self) -> str:
        """Who acted, recorded on every check and archive: the tailnet login, else the node-local address."""
        login = self.headers.get(IDENTITY_HEADER) if self._via_tailnet() else None
        return login.strip().lower() if login else f"{LOCAL_ACTOR}({self.client_address[0]})"

    def _same_origin(self, origin: str | None) -> bool:
        """The page's own tailnet origin: the browser sent ``Origin`` for the very ``Host`` it addressed."""
        host = (self.headers.get("Host") or "").strip().lower()
        return bool(host) and origin is not None and origin.strip().lower() == f"https://{host}"

    def _tokenless(self) -> bool:
        """An allowlisted tailnet login stands in for the token unless the config asks for both."""
        return not self.tailnet_token and self._via_tailnet() and self._identity_ok()

    def _send(self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
        headers = dict(extra or {})
        if content_type.startswith("text/html") and "gzip" in (self.headers.get("Accept-Encoding") or ""):
            body = gzip.compress(body, compresslevel=6, mtime=0)
            headers.update({"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", _CSP)
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _text(self, status: int, text: str) -> None:
        self._send(status, (text + "\n").encode("utf-8"), "text/plain; charset=utf-8")

    def _page(self, status: int, title: str, message: str, *, home: bool = True) -> None:
        """A browser-facing refusal in the dashboard's own look; API callers only read the status."""
        action = '<a class="btn primary" href="/">대시보드로 돌아가기</a>' if home else ""
        page = notice_page(title, message, self.brand, action=action)
        self._send(status, page.encode("utf-8"), "text/html; charset=utf-8")

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
            self._page(403, "이 계정으로는 열 수 없습니다", "대시보드를 볼 수 있는 계정으로 다시 로그인해 주세요.", home=False)
            return
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        offered = query.get("t", [""])[0]
        if offered:
            if not self._matches(offered):
                self._page(401, "접속 주소가 맞지 않습니다", "주소의 접속 키가 다르거나 바뀌었습니다. 대시보드를 설치한 곳에서 새 주소를 받아 열어 주세요.", home=False)
                return
            cookie = f"{COOKIE}={self.token}; HttpOnly; SameSite=Strict; Path=/"
            self._send(303, b"", "text/plain", {"Location": parsed.path or "/", "Set-Cookie": cookie})
            return
        if not self._tokenless() and not self._cookie_ok():
            self._page(401, "로그인이 필요합니다", "처음 한 번은 설치할 때 받은 접속\u00a0주소로 열어야\u00a0합니다. 그\u00a0뒤로는\u00a0이\u00a0브라우저가\u00a0기억합니다.", home=False)
            return
        parts = [unquote(part) for part in parsed.path.split("/") if part]
        if not parts:
            self._dashboard()
        elif parts[0] == "source" and len(parts) == 3:
            self._source(parts[1], parts[2])
        elif parts[0] == "source" and len(parts) == 5 and parts[3] == "attachment":
            self._attachment(parts[1], parts[2], parts[4])
        else:
            self._page(404, "찾는 화면이 없습니다", "주소가 바뀌었거나 없는 화면입니다.")

    def _dashboard(self) -> None:
        snapshot = self.store.snapshot(today=self.today(), pending=self.pending())
        etag, page = stamp_etag(render_dashboard(
            snapshot, mode="served", token="" if self._tokenless() else self.token, brand=self.brand,
            sessions=self.sessions(), ledger=load_ledger()))
        offered = {tag.strip().removeprefix("W/") for tag in (self.headers.get("If-None-Match") or "").split(",")}
        if etag in offered:  # the open page's 2-minute check: nothing it shows has changed
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        self._send(200, page.encode("utf-8"), "text/html; charset=utf-8", {"ETag": etag})

    def _source(self, kind: str, source_id: str) -> None:
        text = self.read_source(kind, source_id) if self.store.approved_attachments(kind, source_id) is not None else None
        if text is None:
            self._page(404, "원문을 찾을 수 없습니다", "승인된 근거가 아니거나 원문이 더는 보관되어 있지 않습니다.")
            return
        first, _, rest = text.partition("\n")
        heading = first[2:].strip() if first.startswith("# ") else ""
        title = f"<h1>{escape(heading)}</h1>" if heading else ""
        body = (
            f'<main class="reader"><a class="back" href="/#sources" data-back>{icon("chevron-left")}<span>근거 목록</span></a>'
            f'{self._source_links(kind, source_id)}'
            f'<div class="card">{title}<pre>{escape(rest.strip() if heading else text)}</pre></div></main>{_BACK_JS}'
        )
        self._send(200, page_shell(heading or "원문", body, self.brand).encode("utf-8"), "text/html; charset=utf-8")

    def _source_links(self, kind: str, source_id: str) -> str:
        """The project and the todos this source made, so the reader is never a dead end."""
        snapshot = self.store.snapshot(today=self.today())
        made = [t for t in snapshot.todos if t.source_kind == kind and t.source_id == source_id]
        if not made:
            return ""
        project = made[0].project
        todos = ", ".join(
            f'<a href="/{escape(todo_href(t.todo_id), quote=True)}">{escape(t.title)}</a>'
            for t in made
        )
        link = f'<a href="/{escape(project_href(project), quote=True)}">{escape(project)}</a>'
        return (f'<div class="card src-links"><div class="names">{icon("projects")}<span>프로젝트 {link}</span></div>'
                f'<div class="names">{icon("list")}<span>만든 할\u00a0일: {todos}</span></div></div>')

    def _attachment(self, kind: str, source_id: str, name: str) -> None:
        if "/" in name or "\\" in name or ".." in name or not name:
            self._page(404, "첨부 파일을 찾을 수 없습니다", "승인된 근거의 첨부가 아니거나 파일이 더는 보관되어 있지 않습니다.")
            return
        if name not in (self.store.approved_attachments(kind, source_id) or ()):
            self._page(404, "첨부 파일을 찾을 수 없습니다", "승인된 근거의 첨부가 아니거나 파일이 더는 보관되어 있지 않습니다.")
            return
        path = self.attachment_path(kind, source_id, name)
        if path is None or not path.is_file():
            self._page(404, "첨부 파일을 찾을 수 없습니다", "승인된 근거의 첨부가 아니거나 파일이 더는 보관되어 있지 않습니다.")
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
            origin = self.headers.get("Origin")
            if origin not in self.tailnet_origins and not (self._via_tailnet() and self._same_origin(origin)):
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
        cross = origin is not None and origin not in allowed and not (self._via_tailnet() and self._same_origin(origin))
        if not self._matches(supplied) or cross:
            self._text(403, "forbidden")
            return
        act(parts[1], form)

    def _archive(self, project: str, form: dict[str, list[str]]) -> None:
        """보관 / 보관 해제 — the same authority and the same recorded identity as a progress tick."""
        value = form.get("archived", [""])[0]
        if value not in ("0", "1"):
            self._text(400, "archived must be 0 or 1")
            return
        try:
            changed = self.store.set_archived(project, value == "1", by=self._actor())
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
            todo = self.store.record_progress(todo_id, status=status, progress=progress, by=self._actor())
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
    brand: str = DEFAULT_BRAND,
    sessions: Callable[[], SessionsState] = lambda: "missing",
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
            "brand": brand or DEFAULT_BRAND,
            "sessions": staticmethod(sessions),
        },
    )
    server = ThreadingHTTPServer((host, port), handler)
    handler.port = int(server.server_address[1])
    return server
