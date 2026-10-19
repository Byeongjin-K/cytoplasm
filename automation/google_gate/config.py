"""Render the fork's node-only Google listener without reading credentials."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Final, NewType

from automation.doctor.guides import GWS_SCOPES
from automation.google_gate.token_client import GateError

Uid = NewType("Uid", int)
DiscordId = NewType("DiscordId", str)
Email = NewType("Email", str)
JOBS_SCOPES: Final = tuple(GWS_SCOPES.split(","))
DEFAULT_SCOPES: Final = (
    "openid", "https://www.googleapis.com/auth/userinfo.email",
    *(f"https://www.googleapis.com/auth/{name}" for name in (
        "calendar", "drive", "documents", "tasks", "gmail.readonly",
    )),
)


def uid(text: str) -> Uid:
    if not re.fullmatch(r"[0-9]+", text) or not 0 < int(text) < 2**32 - 1:
        raise GateError("uid-invalid")
    return Uid(int(text))


def discord_id(text: str) -> DiscordId:
    if not re.fullmatch(r"[0-9]{17,20}", text):
        raise GateError("id-invalid")
    return DiscordId(text)


def email(text: str) -> Email:
    if len(text) > 254 or not re.fullmatch(
        r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
        r"@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
        r"[A-Za-z]{2,63}", text,
    ):
        raise GateError("email-invalid")
    return Email(text)


def absolute_path(text: str) -> str:
    if not Path(text).is_absolute() or any(ord(c) < 32 for c in text):
        raise GateError("path-invalid")
    return text


def add_arguments(parser: argparse.ArgumentParser, required: bool = True) -> None:
    for role in ("relay", "agent", "operator"):
        parser.add_argument(f"--{role}-uid", type=uid, required=required)
    for name in ("owner-id", "owner-guild", "approval-channel"):
        parser.add_argument(f"--{name}", type=discord_id, required=required)
    parser.add_argument("--owner-email", type=email, action="extend", nargs="+", required=required)
    parser.add_argument("--bot-token-file", type=absolute_path, required=required)
    parser.add_argument("--state", type=absolute_path, default="/var/lib/dori-gate")
    parser.add_argument("--dori-name", default="도리")
    for name in ("dir", "remote", "key", "prefix"):
        parser.add_argument(f"--vault-{name}")


def render(args: argparse.Namespace) -> str:
    if len({args.relay_uid, args.agent_uid, args.operator_uid}) != 3:
        raise GateError("uids-must-differ")
    vault = (args.vault_dir, args.vault_remote, args.vault_key, args.vault_prefix)
    if any(vault) and not all(vault):
        raise GateError("vault-incomplete")
    data = {
        "state": args.state, "dori_name": args.dori_name,
        "listen": {"connect": None, "google": 3129, "discord": None},
        "clients": [args.relay_uid], "admins": [args.operator_uid],
        "token_clients": [args.agent_uid],
        "proc_tcp_paths": ["/proc/net/tcp", "/proc/net/tcp6"],
        "owner_id": args.owner_id, "owner_guild": args.owner_guild,
        "owner_emails": args.owner_email,
        "owner_proof": {"token_file": args.bot_token_file, "max_age_hours": 24},
        "approval": {"channel_id": args.approval_channel, "poll_seconds": 20},
        "limits": {"deletes_per_hour": 10, "writes_per_owner_message": 30},
        "google_scopes": DEFAULT_SCOPES,
        "grants": {"jobs": {"scopes": JOBS_SCOPES, "token_file": "jobs-token.json"}},
        "connect_allow": [], "discord_api_base": "https://discord.com/api/v10",
        "tokeninfo_url": "https://oauth2.googleapis.com/tokeninfo",
    }
    if all(vault):
        absolute_path(args.vault_dir)
        absolute_path(args.vault_key)
        if (not args.vault_remote or any(c.isspace() for c in args.vault_remote)
                or args.vault_remote.startswith("-") or not args.vault_prefix
                or Path(args.vault_prefix).is_absolute() or ".." in Path(args.vault_prefix).parts):
            raise GateError("vault-invalid")
        data["vault"] = {"dir": args.vault_dir, "remote": args.vault_remote,
                         "ssh_key": args.vault_key, "allowed_prefix": args.vault_prefix}
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"
