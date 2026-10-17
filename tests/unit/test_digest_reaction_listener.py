"""Digest 📄/✍️ presses heard live on the gateway's Discord client (2026-10-17).

The listener only decides "the owner pressed 📄/✍️ here" and starts the mail wrapper for
that card without making the gateway wait; every other rule stays in the mail skill.
All ids are synthetic.
"""
from __future__ import annotations

import asyncio
import enum
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from automation.interop import digest_reaction_listener as listener  # noqa: E402

OWNER = "100000000000000001"
BOT = "200000000000000002"


def _payload(emoji: str, user: str = OWNER, message: str = "card-1") -> SimpleNamespace:
    return SimpleNamespace(emoji=SimpleNamespace(name=emoji), user_id=int(user),
                           message_id=message, channel_id="thread-1")


def _press(payload: SimpleNamespace) -> list[tuple[str, str]]:
    spawned: list[tuple[str, str]] = []

    async def spawn(message_id: str, emoji: str, _received: float) -> None:
        spawned.append((message_id, emoji))

    async def run() -> None:
        handler = listener.make_handler(owner=lambda: OWNER, self_id=lambda: BOT, spawn=spawn)
        await handler(payload)
        await asyncio.sleep(0)

    asyncio.run(run())
    return spawned


@pytest.mark.parametrize("emoji", ["\U0001f4c4", "\u270d\ufe0f", "\u270d"])
def test_the_owner_pressing_body_or_draft_starts_that_card_at_once(emoji: str) -> None:
    assert _press(_payload(emoji)) == [("card-1", emoji)]


@pytest.mark.parametrize(("emoji", "user"), [
    ("\U0001f4c4", "300000000000000003"),  # someone else
    ("\U0001f4c4", BOT),  # the bot's own pre-attached emoji
    ("\u2705", OWNER),  # an approval emoji is the approval watchers' business
    ("\U0001f44d", OWNER),
])
def test_other_presses_start_nothing(emoji: str, user: str) -> None:
    assert _press(_payload(emoji, user)) == []


def test_no_owner_configured_starts_nothing() -> None:
    assert not listener.is_owner_press(emoji_name="\U0001f4c4", user_id="", owner_id="",
                                      self_id=BOT)


def test_a_broken_payload_never_raises_into_discord() -> None:
    assert _press(SimpleNamespace()) == []


class _Client:
    def __init__(self) -> None:
        self.listeners: list[tuple[object, str]] = []

    def add_listener(self, func: object, name: str) -> None:
        self.listeners.append((func, name))


def test_the_listener_is_added_once_per_client_and_again_on_a_new_client() -> None:
    first, second = _Client(), _Client()

    assert listener.attach(first, print) is True
    assert listener.attach(first, print) is False
    assert listener.attach(second, print) is True
    assert first.listeners == [(print, "on_raw_reaction_add")]
    assert listener.attach(None, print) is False


def test_the_discord_client_is_found_on_the_gateway_runner() -> None:
    client = _Client()
    platform = enum.Enum("Platform", {"TELEGRAM": "telegram", "DISCORD": "discord"})
    runner = SimpleNamespace(adapters={
        platform.TELEGRAM: SimpleNamespace(_client="other"),
        platform.DISCORD: SimpleNamespace(_client=client),
    })

    assert listener.discord_client(runner) is client
    assert listener.discord_client(None) is None


def test_install_arms_only_the_gateway_of_the_account_with_the_mail_watcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapper = tmp_path / "mail_triage_watch.py"
    monkeypatch.setattr(listener, "WRAPPER", wrapper)
    monkeypatch.setattr(listener.threading, "Thread", lambda **_k: SimpleNamespace(start=lambda: None))
    monkeypatch.setattr(sys, "argv", ["hermes", "gateway", "run"])
    monkeypatch.delenv("SYSTEMD_EXEC_PID", raising=False)

    assert listener.install(runner_of=lambda: None) is False  # peer: no mail watcher
    wrapper.write_text("", encoding="utf-8")
    assert listener.install(runner_of=lambda: None) is True
    monkeypatch.setenv(listener.KILL_ENV, "0")
    assert listener.install(runner_of=lambda: None) is False
    monkeypatch.delenv(listener.KILL_ENV)
    monkeypatch.setattr(sys, "argv", ["hermes", "cron", "list"])
    assert listener.install(runner_of=lambda: None) is False


def test_the_wrapper_runs_for_the_card_and_the_delay_is_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = tmp_path / "argv.json"
    wrapper = tmp_path / "mail_triage_watch.py"
    wrapper.write_text(
        "import json, sys\n"
        f"open({str(seen)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
        "print('boom 12345678901', file=sys.stderr)\n"
        "sys.exit(2)\n",
        encoding="utf-8",
    )
    log = tmp_path / "logs" / "events.jsonl"
    monkeypatch.setattr(listener, "WRAPPER", wrapper)
    monkeypatch.setattr(listener, "EVENT_LOG", log)

    asyncio.run(listener.run_wrapper("card-9", "\U0001f4c4", 1_000.0))

    assert json.loads(seen.read_text(encoding="utf-8")) == ["--digest-card", "card-9", "--emoji", "\U0001f4c4"]
    entry = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["message_id"] == "card-9" and entry["rc"] == 2
    assert entry["error"] == "boom [N]"
    assert entry["received_at"].startswith("1970-01-01T00:16:40")
