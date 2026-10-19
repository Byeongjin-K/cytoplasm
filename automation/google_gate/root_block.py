"""Owner root action, deliberately absent from release and deployment hooks."""
from __future__ import annotations

import argparse
import os
import pwd
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from automation.dori_pin import PinError
from automation.google_gate import config
from automation.google_gate.root_assets import (
    PYTHON_FILES, UNITS, authorized_key, command, node_units, pinned_assets,
)
from automation.google_gate.token_client import GateError


def username(text: str) -> str:
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,30}", text):
        raise GateError("username-invalid")
    return text


def install_file(source: Path, target: Path, owner: str) -> None:
    command(("install", "-m", "0600", "-o", owner, "-g", owner, str(source), str(target)))


def account(name: str, home: Path, uid: int | None) -> None:
    try:
        entry = pwd.getpwnam(name)
    except KeyError:
        args = ("--uid", str(uid)) if uid is not None else ()
        command(("useradd", "--system", "--user-group", "--home-dir", str(home),
                 "--shell", "/usr/sbin/nologin", *args, name))
        return
    if (entry.pw_dir != str(home) or entry.pw_shell != "/usr/sbin/nologin"
            or uid is not None and entry.pw_uid != uid or entry.pw_uid == 0):
        raise GateError("account-differs")


def apply(args: argparse.Namespace, staging: Path) -> None:
    state = Path(args.state)
    relay_home = Path("/var/lib") / args.relay_user
    account(args.gate_user, state, None)
    account(args.relay_user, relay_home, args.relay_uid)
    for directory in (state, relay_home, relay_home / ".ssh"):
        owner = args.gate_user if directory == state else args.relay_user
        command(("install", "-d", "-m", "0700", "-o", owner, "-g", owner, str(directory)))
    if args.vault_dir:
        command(("install", "-d", "-m", "0700", "-o", args.gate_user,
                 "-g", args.gate_user, args.vault_dir))
        install_file(Path(args.vault_key), state / "vault-key", args.gate_user)
    command(("install", "-d", "-m", "0755", "/opt/dori-gate"))
    command(("install", "-d", "-m", "0750", "-o", "root", "-g", args.gate_user, "/etc/dori-gate"))
    for name in PYTHON_FILES:
        command(("install", "-m", "0755", str(staging / name), f"/opt/dori-gate/{name}"))
    for name in UNITS:
        command(("install", "-m", "0644", str(staging / name), f"/etc/systemd/system/{name}"))
    install_file(Path(args.bot_token_file), state / "owner-bot-token", args.gate_user)
    install_file(Path(args.client_secret_file), state / "client_secret.json", args.gate_user)
    install_file(staging / "config.json", Path("/etc/dori-gate/config.json"), args.gate_user)
    keys = relay_home / ".ssh/authorized_keys"
    line = (staging / "authorized_keys").read_text(encoding="ascii")
    existing = keys.read_text(encoding="ascii") if keys.exists() else ""
    if line.strip() not in existing.splitlines():
        # Refuse a second unrestricted copy of the same key.
        material = line.split()[-1]
        if any(material in row.split() for row in existing.splitlines()):
            raise GateError("relay-key-already-present")
        with keys.open("a", encoding="ascii") as stream:
            stream.write(("\n" if existing and not existing.endswith("\n") else "") + line)
    os.chmod(keys, 0o600)
    relay = pwd.getpwnam(args.relay_user)
    os.chown(keys, relay.pw_uid, relay.pw_gid)
    command(("systemctl", "daemon-reload"))
    command(("systemctl", "enable", "--now", "dori-gate.service", "dori-gate-summary.timer"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--apply", action="store_true")
    modes.add_argument("--rollback", action="store_true")
    config.add_arguments(parser, required=False)
    parser.add_argument("--fork-checkout", type=Path)
    parser.add_argument("--client-secret-file", type=Path)
    parser.add_argument("--relay-key-file", type=Path)
    parser.add_argument("--pc-ip")
    parser.add_argument("--gate-user", type=username, default="dori-gate")
    parser.add_argument("--relay-user", type=username, default="dori-gate-relay")
    try:
        args = parser.parse_args()
        if (args.apply or args.rollback) and os.geteuid() != 0:
            raise GateError("root-required")
        if args.rollback:
            command(("systemctl", "disable", "--now", *reversed(UNITS)))
            print("GOOGLE_GATE_ROLLBACK_OK")
            return 0
        for key in ("fork_checkout", "client_secret_file", "relay_key_file", "pc_ip",
                    "relay_uid", "agent_uid", "operator_uid", "owner_id", "owner_guild",
                    "owner_email", "approval_channel", "bot_token_file"):
            if getattr(args, key) is None:
                raise GateError("arguments-required")
        if args.gate_user == args.relay_user:
            raise GateError("users-must-differ")
        secret_paths = [Path(args.bot_token_file), args.client_secret_file]
        if args.vault_key:
            secret_paths.append(Path(args.vault_key))
        for path in secret_paths:
            if not path.is_file() or path.stat().st_size == 0:
                raise GateError("secret-file-required")
        for path in (args.state, args.vault_dir, args.vault_key):
            if path is not None and any(c.isspace() for c in path):
                raise GateError("path-invalid")
        state = Path(args.state)
        if state.parent != Path("/var/lib") or state.name != args.gate_user:
            raise GateError("state-must-be-gate-home")
        writable = (state,)
        if args.vault_dir:
            directory = Path(args.vault_dir)
            if (len(directory.parts) < 3 or directory.parts[1] in {"home", "root"}
                    or directory in state.parents):
                raise GateError("vault-directory-invalid")
            writable = (*writable, directory)
        if any(path != path.resolve() for path in writable):
            raise GateError("directory-must-be-canonical")
        values = vars(args).copy()
        values["bot_token_file"] = str(Path(args.state) / "owner-bot-token")
        if args.vault_key:
            values["vault_key"] = str(Path(args.state) / "vault-key")
        rendered = config.render(argparse.Namespace(**values))
        line = authorized_key(args.relay_key_file, args.pc_ip)
        with tempfile.TemporaryDirectory(prefix="google-gate-") as temporary:
            staging = Path(temporary)
            os.chmod(staging, 0o700)
            pinned_assets(args.fork_checkout, staging)
            node_units(staging, args.gate_user, writable)
            (staging / "config.json").write_text(rendered, encoding="utf-8")
            (staging / "authorized_keys").write_text(line, encoding="ascii")
            if args.apply:
                apply(args, staging)
        print("GOOGLE_GATE_INSTALL_OK" if args.apply else "GOOGLE_GATE_DRYRUN_OK")
        return 0
    except GateError as error:
        print(f"GOOGLE-GATE-ROOT-REFUSED {error.code}", file=sys.stderr)
    except PinError:
        print("GOOGLE-GATE-ROOT-REFUSED pin-invalid", file=sys.stderr)
    except (OSError, ValueError, subprocess.SubprocessError):
        print("GOOGLE-GATE-ROOT-REFUSED filesystem-or-command", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
