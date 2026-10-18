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
KNOWN_HOSTS = ".hermes/omo-relay/known_hosts"
HOST_KEY_SOURCE = Path("/etc/ssh/ssh_host_ed25519_key.pub")


def host_key_line(config: NodeConfig, source: Path = HOST_KEY_SOURCE) -> str:
    """Pin the relay host key from the release host itself, which is the relay target.

    The node trusts exactly this line through a dedicated known_hosts file, so a first
    connection never relies on accept-new and a different host fails closed.
    """
    host = config.omo_relay_target.rpartition("@")[2]
    fields = source.read_text().split()
    if not host or len(fields) < 2 or not fields[0].startswith("ssh-"):
        raise ValueError("relay host key unavailable")
    return f"{host} {fields[0]} {fields[1]}"


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


def digest(config: NodeConfig, host_key: str = "") -> str:
    return hashlib.sha256((payload(config) + host_key).encode()).hexdigest()


def release_mode(home: Path) -> str:
    if not switch(home):
        return "off"
    config = load_node_config()
    return digest(config, host_key_line(config))


def wanted_identity(*, omo_relay_mode: str = "off") -> str:
    return f"route={omo_relay_mode}"


def installed(home: Path) -> str:
    if not enabled(home):
        return wanted_identity()
    config = load_node_config(home / NODE_CONFIG)
    known = (home / KNOWN_HOSTS).read_text().strip()
    info = config.omo_relay_key.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.geteuid():
        raise ValueError("relay key metadata")
    return wanted_identity(omo_relay_mode=digest(config, known))


def install(home: Path, mode: str, text: str, host_key: str = "") -> None:
    """Only invoked after release authorization; existing private keys never leave the node."""
    if mode == "off":
        (home / MODE).unlink(missing_ok=True)
        (home / NODE_CONFIG).unlink(missing_ok=True)
        (home / KNOWN_HOSTS).unlink(missing_ok=True)
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
        if digest(config, host_key) != mode:
            raise ValueError("relay config differs from approved digest")
        host = config.omo_relay_target.rpartition("@")[2]
        parts = host_key.split()
        if len(parts) != 3 or parts[0] != host or not parts[1].startswith("ssh-"):
            raise ValueError("invalid relay host key")
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
        atomic_write(home / KNOWN_HOSTS, (host_key + "\n").encode())
        (home / KNOWN_HOSTS).chmod(0o600)
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
    host_key = host_key_line(config) if mode != "off" else ""
    command = shlex.join(("sudo", "-n", "-u", config.agent_account, "-H", "env",
                          f"PYTHONPATH={runtime}", "python3", "-B", "-m",
                          "automation.omo_relay_release", "install", "--digest", mode,
                          "--host-key", host_key))
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
    node.add_argument("--host-key", default="")
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
            install(Path.home(), args.digest, sys.stdin.read(8193), args.host_key)
            return 0
        if args.command == "apply":
            return deploy(Path(args.repo), args.host, args.runtime, args.head)
        return 2
    except (OSError, ValueError, NodeConfigError, subprocess.SubprocessError):
        print("OMO-RELAY-REFUSED", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
