"""Compatible subset of the board CLI used by repair, meeting and proposal."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.ledger.store import STATUSES, LedgerError, Store  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cytoplasm-ledger")
    parser.add_argument("--board")
    commands = parser.add_subparsers(dest="command", required=True)
    boards = commands.add_parser("boards").add_subparsers(dest="boards", required=True)
    create_board = boards.add_parser("create")
    create_board.add_argument("slug")
    create_board.add_argument("--name", default="")
    list_boards = boards.add_parser("list")
    list_boards.add_argument("--all", action="store_true")
    list_boards.add_argument("--json", action="store_true")
    create = commands.add_parser("create")
    create.add_argument("title")
    create.add_argument("--body", default="")
    create.add_argument("--created-by", default="")
    create.add_argument("--idempotency-key")
    create.add_argument("--initial-status", choices=STATUSES, default="ready")
    create.add_argument("--json", action="store_true")
    show = commands.add_parser("show")
    show.add_argument("id")
    show.add_argument("--json", action="store_true")
    listing = commands.add_parser("list")
    listing.add_argument("--json", action="store_true")
    listing.add_argument("--archived", action="store_true")
    listing.add_argument("--status", choices=STATUSES)
    for name in ("block", "unblock", "complete", "comment"):
        command = commands.add_parser(name)
        command.add_argument("id")
        if name in ("block", "comment"):
            command.add_argument("text")
        command.add_argument("--kind", default="needs_input")
        command.add_argument("--result", default="")
        command.add_argument("--author", default="")
    args = parser.parse_args(argv)
    ledger = Store()
    try:
        match args.command:
            case "boards":
                result = ledger.boards() if args.boards == "list" else ledger.create_board(args.slug, args.name)
            case "create":
                result = ledger.create(args.title, board=args.board or ledger.current_board(), body=args.body, dedup=args.idempotency_key,
                                       status=args.initial_status, created_by=args.created_by)
            case "show":
                result = ledger.show(args.id)
                if not args.json:
                    task = result["task"]
                    print(f"id: {task['id']}\nstatus: {task['status']}\ntitle: {task['title']}")
                    return 0
            case "list":
                result = [card for card in ledger.cards(args.board or ledger.current_board())
                          if (args.archived or card["status"] != "archived")
                          and (not args.status or card["status"] == args.status)]
            case "block" | "unblock" | "complete" | "comment":
                ledger.change(args.id, args.command, text=getattr(args, "text", args.result),
                              kind=args.kind, author=args.author)
                result = {"id": args.id}
            case _:
                return 2
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (LedgerError, OSError, sqlite3.Error) as error:
        print(f"LEDGER-REFUSED: {error}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
