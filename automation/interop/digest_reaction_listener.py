"""Digest card reactions, heard live on the gateway's own Discord connection (2026-10-17).

Owner (voice, 10/17 10:43 KST): "다이제스트 왔을 때 이모지 달면 반응이 너무 느려 즉각적으로
반응할 수 있겠죠". The 📄/✍️ press was read only by the ``mail-triage-watch`` cron, every
2 minutes, so a press waited up to two minutes plus the tick (measured 10/17: three 📄
pressed before the 10:40:50 tick were answered 10:42:46–10:43:07).

Hermes passes no reaction event to plugins (its ``gateway_platform_event`` fires only for
edits, deletes and threads), but its Discord client already receives them — the adapter
asks for ``Intents.default()``, which includes reactions. So this module adds one
``on_raw_reaction_add`` listener to that existing client: no second connection, no new
service. On the owner's 📄/✍️ it starts the mail watcher wrapper for that one card
(``mail_triage_watch.py --digest-card <id>``) as a child process and returns at once, so
the gateway's event loop never waits on mail or the model.

The listener only says "the owner pressed here"; the mail skill reads the reaction back
from Discord and keeps every rule of the tick — owner (non-bot) only, digest cards only,
once per (card, action) through the same SQLite claim, so a press seen here and on the
next tick still runs once. The cron stays as the safety net for presses missed while the
gateway restarts.

Armed only in the gateway main process of the account that owns the mail watcher (the
wrapper file must exist — peer has none), and off with ``DIGEST_REACTION_LISTENER=0``.
Hermes recreates its client on reconnect, so a daemon thread re-attaches to whichever
client is current. Every handled press appends one line (received, finished, seconds,
exit code) to ``EVENT_LOG`` — that is how the delay is measured.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import threading
import time
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

LOGGER: Final = logging.getLogger("autophagy.interop")
WRAPPER: Final = Path("~/.hermes/scripts/mail_triage_watch.py").expanduser()
EVENT_LOG: Final = Path("~/.hermes/mail-triage/logs/digest-reaction-events.jsonl").expanduser()
KILL_ENV: Final = "DIGEST_REACTION_LISTENER"
#: 📄 and ✍ (the variation selector U+FE0F is stripped before comparing).
EMOJIS: Final = frozenset({"\U0001f4c4", "\u270d"})
MARK: Final = "_autophagy_digest_reactions"
ATTACH_POLL_SECONDS: Final = 5.0
_LONG_DIGITS: Final = re.compile(r"\d{5,}")

Spawn = Callable[[str, str, float], Coroutine[Any, Any, None]]


def is_owner_press(*, emoji_name: str, user_id: str, owner_id: str, self_id: str) -> bool:
    """True for the owner's 📄/✍️ — never the bot's own pre-attached reaction."""
    return (
        bool(owner_id)
        and user_id == owner_id
        and user_id != self_id
        and emoji_name.replace("\ufe0f", "") in EMOJIS
    )


def owner_id() -> str:
    """The interop owner id; empty when the config cannot be read (then nothing fires)."""
    path = Path(os.environ.get("INTEROP_CONFIG", "~/.hermes/interop/config.json")).expanduser()
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("owner_id")
    except (OSError, ValueError, AttributeError):
        return ""
    return value if isinstance(value, str) else ""


def make_handler(
    *, owner: Callable[[], str], self_id: Callable[[], str], spawn: Spawn,
) -> Callable[[Any], Coroutine[Any, Any, None]]:
    """The ``on_raw_reaction_add`` coroutine; it never raises into discord.py."""

    async def on_raw_reaction_add(payload: Any) -> None:
        received = time.time()
        try:
            emoji = getattr(getattr(payload, "emoji", None), "name", None) or ""
            if not is_owner_press(
                emoji_name=str(emoji), user_id=str(getattr(payload, "user_id", "")),
                owner_id=owner(), self_id=self_id(),
            ):
                return
            message_id = str(getattr(payload, "message_id", ""))
            asyncio.get_running_loop().create_task(spawn(message_id, str(emoji), received))
        except Exception as error:  # noqa: BLE001 — a press must never break the gateway
            LOGGER.warning("digest reaction listener failed error=%s", type(error).__name__)

    return on_raw_reaction_add


def _record(entry: dict[str, object]) -> None:
    try:
        EVENT_LOG.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with EVENT_LOG.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as error:
        LOGGER.warning("digest reaction log failed error=%s", type(error).__name__)


def _stamp(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat(timespec="milliseconds")


async def run_wrapper(message_id: str, emoji: str, received: float) -> None:
    """Run the mail wrapper for one card; log when it was heard and when it finished."""
    started = time.time()
    rc: int | None = None
    error = ""
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable, str(WRAPPER), "--digest-card", message_id,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE, cwd=str(Path.home()),
        )
        _, stderr = await process.communicate()
        rc = process.returncode
        if rc:
            lines = stderr.decode("utf-8", "replace").strip().splitlines()
            error = _LONG_DIGITS.sub("[N]", lines[-1] if lines else "")[:200]
    except Exception as exc:  # noqa: BLE001 — logged, the cron tick is the safety net
        error = type(exc).__name__
    finished = time.time()
    _record({
        "received_at": _stamp(received), "started_at": _stamp(started),
        "finished_at": _stamp(finished), "seconds": round(finished - received, 2),
        "message_id": message_id, "emoji": emoji, "rc": rc, "error": error,
    })
    LOGGER.warning("digest reaction handled rc=%s seconds=%.1f", rc, finished - received)


def discord_client(runner: Any) -> Any:
    """The Discord adapter's live client on the gateway runner, or None."""
    adapters = getattr(runner, "adapters", None) or {}
    for platform, adapter in dict(adapters).items():
        name = str(getattr(platform, "value", platform)).lower()
        if name == "discord":
            return getattr(adapter, "_client", None)
    return None


def attach(client: Any, handler: Callable[..., Any]) -> bool:
    """Add the listener once per client object; True when it was added now."""
    if client is None or getattr(client, MARK, False):
        return False
    client.add_listener(handler, "on_raw_reaction_add")
    setattr(client, MARK, True)
    return True


def _self_id_of(get_client: Callable[[], Any]) -> Callable[[], str]:
    def self_id() -> str:
        user = getattr(get_client(), "user", None)
        return str(getattr(user, "id", "") or "")

    return self_id


def _gateway_runner() -> Any:
    try:
        from gateway import run as gateway_run  # the vendored Hermes gateway module
    except ImportError:
        return None
    return gateway_run._gateway_runner_ref()  # noqa: SLF001 — the gateway's own weakref


def _watch(runner_of: Callable[[], Any], handler: Callable[..., Any], current: list[Any]) -> None:
    while True:
        try:
            client = discord_client(runner_of())
            if client is not None:
                current[0] = client
                if attach(client, handler):
                    LOGGER.warning("digest reaction listener attached")
        except Exception as error:  # noqa: BLE001 — keep watching for the next client
            LOGGER.warning("digest reaction listener attach failed error=%s", type(error).__name__)
        time.sleep(ATTACH_POLL_SECONDS)


def install(*, runner_of: Callable[[], Any] = _gateway_runner) -> bool:
    """Arm the listener in the gateway main process; True when the watcher thread started."""
    from automation.gateway_generation import _is_gateway_process  # noqa: PLC0415

    if os.environ.get(KILL_ENV, "").strip() == "0":
        return False
    if not _is_gateway_process(sys.argv, os.environ, os.getpid()) or not WRAPPER.is_file():
        return False
    current: list[Any] = [None]
    handler = make_handler(owner=owner_id, self_id=_self_id_of(lambda: current[0]),
                           spawn=run_wrapper)
    threading.Thread(target=_watch, args=(runner_of, handler, current), daemon=True,
                     name="digest-reaction-listener").start()
    return True
