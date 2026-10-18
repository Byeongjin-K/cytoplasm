"""Read-only inventory of undecided cards before deleting retired channels.

Never calls a producer, mutates a record, or creates a thread. Unknown reads fail
the inventory instead of declaring a channel safe to delete.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Final, TypeAlias
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from automation.interop.owner_message import LinkStatus, discord_link

Json: TypeAlias = None | bool | int | float | str | list["Json"] | dict[str, "Json"]
Api: TypeAlias = Callable[[str], Json]
_STORES: Final = (
    ("mail-reply", "mail-triage/drafts/*.json"),
    ("calendar", "calendar-gate/drafts/*.json"),
    ("todo", "todo-approvals/pending/*.json"),
    ("todo", "todo-approvals/archive/*/*.json"),
    ("wiki", "wiki-gate/drafts/*.json"),
    ("wiki", "wiki-gate/archive/**/*.json"),
    ("project-board", "project-board/proposals.json"),
    ("obsidian-write", "obsidian-write-requests/requests.json"),
    ("obsidian-write", "obsidian-write/requests/*.json"),
    ("obsidian-write", "memory-curator/relocations.json"),
    ("obsidian-write", "memory-relocate/relocations.json"),
)
_UNDECIDED: Final = frozenset({"pending", "proposed", "posted", "blocked"})


@dataclass(frozen=True, slots=True)
class PendingCard:
    kind: str
    id: str
    title: str
    link: str


def _records(raw: Json) -> Iterator[dict[str, Json]]:
    if not isinstance(raw, dict):
        raise ValueError("record store must be an object")
    for name in ("requests", "proposals", "relocations"):
        children = raw.get(name)
        if isinstance(children, (dict, list)):
            for item in children.values() if isinstance(children, dict) else children:
                yield from _records(item)
            return
    yield raw


def _string(record: dict[str, Json], *keys: str) -> str:
    return next((str(record[key]) for key in keys if record.get(key) not in (None, "")), "")


def pending(
    home: Path, old: frozenset[str], api: Api, owner_id: str,
    *, now: datetime | None = None,
) -> tuple[PendingCard, ...]:
    """Read exact stores, validate thread ancestry and check only the owner's reactions."""
    moment = now or datetime.now(UTC)
    cards: dict[tuple[str, str], PendingCard] = {}
    parents: dict[str, str] = {}
    for fallback_kind, pattern in _STORES:
        for path in sorted((home / ".hermes").glob(pattern)):
            for record in _records(json.loads(path.read_bytes())):
                if _string(record, "status", "state") not in _UNDECIDED:
                    continue
                expires = _string(record, "expires_at", "expires")
                if expires and datetime.fromisoformat(expires.replace("Z", "+00:00")) <= moment:
                    continue
                channel = _string(record, "channel_id", "dm_channel_id", "approval_thread_id")
                message = _string(record, "message_id", "confirm_message_id", "dm_message_id")
                if not channel or not message:
                    continue
                try:
                    if channel not in old:
                        if channel not in parents:
                            facts = api(f"/channels/{channel}")
                            if not isinstance(facts, dict):
                                raise ValueError("invalid channel facts")
                            parents[channel] = str(facts.get("parent_id", ""))
                        if parents[channel] not in old:
                            continue
                    decided = False
                    for emoji in ("✅", "⛔"):
                        users = api(f"/channels/{channel}/messages/{message}/reactions/"
                                    f"{quote(emoji, safe='')}?limit=100")
                        if not isinstance(users, list):
                            raise ValueError("invalid reaction facts")
                        decided |= any(isinstance(user, dict) and user.get("id") == owner_id
                                       and user.get("bot") is False for user in users)
                    if decided:
                        continue
                except HTTPError as error:
                    if error.code == 404:
                        continue
                    raise
                kind = _string(record, "kind") or fallback_kind
                key = _string(record, "id", "request_id", "draft_id", "target_id", "key", "entry_sha256")
                title = key
                if kind in {"mail-reply", "mail", "todo"}:
                    title = (_string(record, "title") if kind == "todo" else
                             _string(record, "subject", "title")) or key
                if kind == "obsidian-write":
                    title = PurePosixPath(_string(record, "relpath", "note_relpath")).name or key
                guild = _string(record, "approval_guild_id")
                link = discord_link(space="guild" if guild else "unknown", guild_id=guild or None,
                                    channel_id=channel, message_id=message)
                cards[(channel, message)] = PendingCard(
                    kind, key, " ".join(title.split())[:160],
                    link.url if link.status is LinkStatus.AVAILABLE and link.url else f"검색: {key}",
                )
    return tuple(cards[key] for key in sorted(cards))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m automation.interop.channel_move")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("pending")
    command.add_argument("--old", nargs="+", action="extend", required=True)
    args = parser.parse_args(argv)
    if any(not value.isascii() or not value.isdigit() for value in args.old):
        parser.error("--old requires decimal channel ids")
    home = Path.home()
    try:
        config = json.loads(Path(os.environ.get("INTEROP_CONFIG", home / ".hermes/interop/config.json")).read_bytes())
        token = os.environ.get("DISCORD_BOT_TOKEN", "")
        if not token:
            for line in (home / ".env.secrets").read_text(encoding="utf-8").splitlines():
                if line.startswith("DISCORD_BOT_TOKEN="):
                    token = line.partition("=")[2].strip().strip("'\"")
        owner = str(config.get("owner_id", ""))
        if not token or not owner:
            raise ValueError("missing credential")

        def api(path: str) -> Json:
            request = Request("https://discord.com/api/v10" + path, headers={
                "Authorization": f"Bot {token}", "User-Agent": "Autophagy read-only channel inventory",
            })
            with urlopen(request, timeout=30) as response:  # noqa: S310
                return json.loads(response.read())

        cards = pending(home, frozenset(args.old), api, owner)
        for card in cards:
            print(json.dumps(asdict(card), ensure_ascii=False))
        print(f"CHANNEL-MOVE-PENDING count={len(cards)}")
        return 0
    except (OSError, UnicodeError, ValueError) as error:
        print(f"CHANNEL-MOVE-PENDING-UNKNOWN reason={type(error).__name__}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
