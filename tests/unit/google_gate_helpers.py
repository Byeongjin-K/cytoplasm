"""Isolated loopback gate and fake executable shared by Google gate tests."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from automation.google_gate.shim import Paths

REPO = Path(__file__).resolve().parents[2]


def fake_gws(home: Path) -> Paths:
    paths = Paths(home)
    paths.binary.parent.mkdir(parents=True)
    paths.binary.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "value = os.environ.get('GOOGLE_WORKSPACE_CLI_TOKEN', '')\n"
        "Path(os.environ['GWS_RECORD']).write_text(json.dumps({\n"
        " 'present': bool(value), 'length': len(value), 'argv': sys.argv[1:]}))\n"
    )
    paths.binary.chmod(0o755)
    return paths


def shim_cli(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "automation.google_gate", "shim", *args, "--home", str(home)],
        cwd=REPO, env={**os.environ, "HOME": str(home)}, capture_output=True, text=True, check=False,
    )


@contextmanager
def gate(payload: bytes, status: int = 200) -> Iterator[int]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/_gate/access-token?grant=jobs":
                self.send_error(404)
                return
            self.send_response(status)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format: str, *args: str) -> None:
            return

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = Thread(target=server.serve_forever)
        worker.start()
        try:
            yield server.server_port
        finally:
            server.shutdown()
            worker.join(timeout=5)
            assert not worker.is_alive()


def payload() -> bytes:
    return json.dumps({
        "access_token": "synthetic-" + os.urandom(16).hex(),
        "expires_in": 300, "scope": "scope-a scope-b",
    }).encode()
