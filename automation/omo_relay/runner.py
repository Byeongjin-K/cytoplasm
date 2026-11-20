"""Restricted workstation entry point. Prompt/answer text is never persisted."""
from __future__ import annotations

import fcntl
import io
import json
import os
import select
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, TextIO

LIMIT = 256 * 1024
TIMEOUT = 300
CANCELLED = 130
_POLL_SECONDS = 0.5
_GRACE_SECONDS = 2.0
# --no-extensions does not stop omo's built-in memory: without --omo-senpi-memory-disabled every
# prompt over a few KB was copied into ~/.omo/memory/transient-runs and a shutdown reflection kept
# omo alive after its answer until the 300 s timeout (mail-daily-digest, 2026-11-12).
ARGV = ("omo", "-p", "--mode", "json", "--no-tools", "--no-extensions", "--no-skills",
        "--no-context-files", "--no-session", "--offline", "--omo-senpi-memory-disabled",
        "--system-prompt",
        "Answer the request using only the supplied text. You have no tools.")


def answer(stdout: str) -> dict[str, str]:
    """Read the last assistant message from omo's JSON event stream."""
    selected = {}
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "agent_end":
            messages = event.get("messages", [])
            for message in messages if isinstance(messages, list) else ():
                if isinstance(message, dict) and message.get("role") == "assistant":
                    selected = message
        if event.get("type") == "message_end":
            message = event.get("message")
            if isinstance(message, dict) and message.get("role") == "assistant":
                selected = message
    content = selected.get("content", [])
    text = "".join(part["text"] for part in content if isinstance(part, dict)
                   and part.get("type") == "text" and isinstance(part.get("text"), str)
                   ) if isinstance(content, list) else ""
    if selected.get("stopReason") in ("error", "aborted"):
        raise ValueError("model did not complete")
    return {"text": text, "provider": str(selected.get("provider") or "unknown"),
            "model": str(selected.get("model") or "unknown")}


class Cancelled(Exception):
    """The caller's SSH session closed while omo was still answering."""


def _hangup_probe(output: TextIO) -> Callable[[], bool]:
    try:
        fd = output.fileno()
    except (AttributeError, OSError, ValueError, io.UnsupportedOperation):
        return lambda: False
    watch = select.poll()
    watch.register(fd, 0)  # mask 0: report only POLLHUP/POLLERR, i.e. the reading side is gone
    return lambda: any(event & (select.POLLHUP | select.POLLERR) for _fd, event in watch.poll(0))


def _stop(process: subprocess.Popen[str]) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            return
        try:
            process.wait(_GRACE_SECONDS)
            return
        except subprocess.TimeoutExpired:
            continue


def _feed(fd: int, data: bytes) -> None:
    """Write the whole prompt and close, so omo sees EOF; a stopped omo just ends the write."""
    with os.fdopen(fd, "wb") as pipe:
        try:
            _ = pipe.write(data)
        except BrokenPipeError:
            return


def complete(argv: tuple[str, ...], *, text: str, cwd: str, timeout: float,
             output: TextIO) -> subprocess.CompletedProcess[str]:
    """Run omo in its own process group; stop the whole group on timeout or caller hang-up.

    A non-pty SSH session does not signal the forced command when the client goes away, so a
    caller that gave up (YooDori's recall after 45 s) used to leave omo holding a relay slot for
    up to 300 s. Owner Q433: a cut call stops here too.
    """
    hung_up = _hangup_probe(output)
    deadline = time.monotonic() + timeout
    # The prompt goes through its own pipe and writer thread, not communicate(input): a retried
    # communicate() stops writing stdin unless input is passed again, and passing it again is
    # refused, so a prompt over the 64 KB pipe buffer stayed half-sent with no EOF and omo waited
    # out the deadline (a 120 KB Plaud extraction prompt, 2026-11-20). Nothing touches the disk.
    read_end, write_end = os.pipe()
    writer = threading.Thread(target=_feed, args=(write_end, text.encode("utf-8")), daemon=True)
    try:
        process = subprocess.Popen(argv, stdin=read_end, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, cwd=cwd, start_new_session=True)
    except BaseException:
        os.close(write_end)
        raise
    finally:
        os.close(read_end)
    writer.start()
    with process:
        try:
            while True:
                try:
                    stdout, stderr = process.communicate(timeout=_POLL_SECONDS)
                    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
                except subprocess.TimeoutExpired:
                    if hung_up():
                        raise Cancelled from None
                    if time.monotonic() >= deadline:
                        raise subprocess.TimeoutExpired(argv, timeout) from None
        finally:
            if process.poll() is None:
                _stop(process)


def run(source: BinaryIO, output: TextIO, home: Path) -> int:
    started = time.monotonic()
    root = home / ".dori/omo-relay"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = source.read(LIMIT + 1)
    rc, model, out_bytes = 2, "unknown", 0
    result: subprocess.CompletedProcess[str] | None = None
    try:
        if len(payload) > LIMIT:
            return rc
        if not payload:
            # An authenticated SSH probe: no model call, no tools, no command dispatch.
            output.write('{"ready":true}\n')
            rc = 0
            return rc
        with ExitStack() as stack:
            for slot in range(2):
                fd = os.open(root / f"slot-{slot}.lock", os.O_CREAT | os.O_RDWR, 0o600)
                lock = stack.enter_context(os.fdopen(fd, "a"))
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    continue
            else:
                rc = 75
                return rc
            with tempfile.TemporaryDirectory(prefix="omo-relay-") as cwd:
                result = complete(ARGV, text=payload.decode("utf-8"), cwd=cwd, timeout=TIMEOUT,
                                  output=output)
            rc = result.returncode
            if rc:
                if any(token in result.stderr.lower() + result.stdout.lower()
                       for token in ("429", "too many requests", "rate limit")):
                    rc = 75
                return rc
            reply = answer(result.stdout)
            model = reply["model"]
            encoded = json.dumps(reply, ensure_ascii=False) + "\n"
            out_bytes = len(reply["text"].encode())
            output.write(encoded)
            return rc
    except subprocess.TimeoutExpired:
        rc = 124
        return rc
    except Cancelled:
        rc = CANCELLED
        return rc
    except ValueError:
        # Some providers report overload as a JSON assistant error with rc=0.
        rc = 75 if result is not None and any(
            token in result.stdout.lower() for token in ("429", "too many requests", "rate limit")
        ) else 2
        return rc
    except OSError:
        rc = 2
        return rc
    finally:
        metadata = {"at": datetime.now(timezone.utc).isoformat(),
                    "caller": os.environ.get("SSH_CONNECTION", "local").split()[0],
                    "rc": rc, "seconds": round(time.monotonic() - started, 3),
                    "model": model, "in_bytes": len(payload), "out_bytes": out_bytes}
        fd = os.open(root / "calls.jsonl", os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as log:
            fcntl.flock(log, fcntl.LOCK_EX)
            log.write(json.dumps(metadata, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    raise SystemExit(run(sys.stdin.buffer, sys.stdout, Path.home()))
