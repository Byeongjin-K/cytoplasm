"""Release-bound node configuration and key provisioning for Q245."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import stat
import subprocess
import sys
from pathlib import Path

from automation.credential_rotation.files import atomic_write
from automation.node_config import NodeConfig, NodeConfigError, load_node_config
from automation.omo_relay.client import MODE, enabled

ALGORITHM = "omo-relay-v1"
MARKER = "OMO-RELAY"
NODE_CONFIG = ".hermes/omo-relay/node.toml"


def switch(home: Path) -> bool:
    try:
        value = (home / ".hermes/release-flags/omo-relay").read_bytes()
    except FileNotFoundError:
        return False
    if value != b"on":
        raise ValueError("invalid omo-relay switch")
    return True


def payload(config: NodeConfig) -> str:
    """A partial node-config override: only approved relay coordinates, no private key."""
    return (f"omo_relay_target = {json.dumps(config.omo_relay_target)}\n"
            f"omo_relay_key = {json.dumps(str(config.omo_relay_key))}\n")


def digest(config: NodeConfig) -> str:
    return hashlib.sha256(payload(config).encode()).hexdigest()


def release_mode(home: Path) -> str:
    return digest(load_node_config()) if switch(home) else "off"


def wanted_identity(*, omo_relay_mode: str = "off") -> str:
    return f"route={omo_relay_mode}"


def installed(home: Path) -> str:
    if not enabled(home):
        return wanted_identity()
    config = load_node_config(home / NODE_CONFIG)
    info = config.omo_relay_key.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.geteuid():
        raise ValueError("relay key metadata")
    return wanted_identity(omo_relay_mode=digest(config))


def install(home: Path, mode: str, text: str) -> None:
    """Only invoked after release authorization; existing private keys never leave the node."""
    if mode == "off":
        (home / MODE).unlink(missing_ok=True)
        (home / NODE_CONFIG).unlink(missing_ok=True)
        return
    import tomllib

    raw = tomllib.loads(text)
    if set(raw) != {"omo_relay_target", "omo_relay_key"}:
        raise ValueError("invalid relay config")
    root = home / ".hermes/omo-relay"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Validate through the shared node-config parser before creating keys or switching.
    temporary = root / "candidate.toml"
    atomic_write(temporary, text.encode())
    try:
        config = load_node_config(temporary)
        if digest(config) != mode:
            raise ValueError("relay config differs from approved digest")
        key = config.omo_relay_key
        if key.parent != home / ".ssh":
            raise ValueError("relay key must be in agent .ssh")
        if not key.name or key.name in ("authorized_keys", "known_hosts", "config"):
            raise ValueError("relay key must use a dedicated filename")
        key.parent.mkdir(mode=0o700, exist_ok=True)
        if not key.exists() and not key.is_symlink():
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
                           capture_output=True, text=True, check=True, timeout=30)
        info = key.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            raise ValueError("relay key is not an owned regular file")
        key.chmod(0o600)
        public = subprocess.run(["ssh-keygen", "-y", "-f", str(key)],
                                capture_output=True, text=True, check=True, timeout=30).stdout.strip()
        atomic_write(home / NODE_CONFIG, payload(config).encode())
        atomic_write(home / MODE, b"on")
        if installed(home) != wanted_identity(omo_relay_mode=mode):
            raise ValueError("relay install readback")
        print(public)
    finally:
        temporary.unlink(missing_ok=True)


def deploy(repo: Path, host: str, runtime: str, head: str) -> int:
    """The existing release gate authorizes the exact route before SSH writes."""
    mode = release_mode(Path.home())
    config = load_node_config()
    if mode != "off":
        approved = subprocess.run(
            [str(repo / "automation/release_approval_remote.sh"), "authorize-omo-relay",
             "--hash", mode, "--head", head], check=False,
        )
        if approved.returncode:
            return 4
    command = shlex.join(("sudo", "-n", "-u", config.agent_account, "-H", "env",
                          f"PYTHONPATH={runtime}", "python3", "-B", "-m",
                          "automation.omo_relay_release", "install", "--digest", mode))
    result = subprocess.run(["ssh", host, command], input=payload(config) if mode != "off" else "",
                            text=True, capture_output=True, check=False, timeout=60)
    if result.returncode:
        print("OMO-RELAY-DEPLOY-FAIL", file=sys.stderr)
        return 4
    for line in result.stdout.splitlines():
        if line.startswith("ssh-ed25519 "):
            print(line)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--show", action="store_true")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("switch")
    node = sub.add_parser("install")
    node.add_argument("--digest", required=True)
    apply = sub.add_parser("apply")
    for field in ("repo", "host", "runtime", "head"):
        apply.add_argument(f"--{field}", required=True)
    args = parser.parse_args(argv)
    try:
        if args.show:
            print(f"{MARKER} have={installed(Path.home())}")
            return 0
        if args.command == "switch":
            print(release_mode(Path.home()))
            return 0
        if args.command == "install":
            install(Path.home(), args.digest, sys.stdin.read(8193))
            return 0
        if args.command == "apply":
            return deploy(Path(args.repo), args.host, args.runtime, args.head)
        return 2
    except (OSError, ValueError, NodeConfigError, subprocess.SubprocessError):
        print("OMO-RELAY-REFUSED", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
