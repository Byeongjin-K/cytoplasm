"""Read pinned fork assets and stage a node variant without nft units."""
from __future__ import annotations

import ast
import ipaddress
import subprocess
from pathlib import Path
from typing import Final

from automation.dori_pin import load_pin
from automation.google_gate.token_client import GateError

FORK_DIR: Final = Path("skills/dori-mode/setups/egress-gate")
PYTHON_FILES: Final = ("gate.py", "gatectl.py", "summary.py")
UNITS: Final = ("dori-gate.service", "dori-gate-summary.service", "dori-gate-summary.timer")
# A pinned gate older than forward mode and job grants would ignore `grants`, `token_clients` and null ports.
GATE_FEATURES: Final = (b"google_forward", b"token_clients", b"/_gate/access-token")


def command(argv: tuple[str, ...]) -> bytes:
    result = subprocess.run(argv, capture_output=True, check=False)
    if result.returncode:
        raise GateError("command-failed")
    return result.stdout


def pinned_assets(checkout: Path, destination: Path) -> None:
    pin = load_pin()
    head = command(("git", "-C", str(checkout), "rev-parse", "HEAD")).decode().strip()
    if head != pin.commit:
        raise GateError("fork-pin-mismatch")
    for name in (*PYTHON_FILES, *UNITS):
        relative = FORK_DIR / (Path("systemd") / name if name in UNITS else Path(name))
        # Read the pinned object, not dirty/untracked working-tree bytes.
        content = command(("git", "-C", str(checkout), "show", f"{pin.commit}:{relative}"))
        if name in PYTHON_FILES:
            try:
                ast.parse(content, filename=name)
            except (SyntaxError, ValueError):
                raise GateError("fork-python-invalid") from None
        if name == "gate.py" and not all(feature in content for feature in GATE_FEATURES):
            raise GateError("fork-gate-too-old")
        (destination / name).write_bytes(content)


def authorized_key(path: Path, pc_ip: str) -> str:
    try:
        ip = str(ipaddress.ip_address(pc_ip))
        pieces = path.read_text(encoding="ascii").strip().split()
    except (ValueError, OSError):
        raise GateError("relay-key-invalid") from None
    if len(pieces) < 2 or not pieces[0].startswith(("ssh-", "ecdsa-")):
        raise GateError("relay-key-invalid")
    command(("ssh-keygen", "-l", "-f", str(path)))
    # Discard comments and refuse multi-line or pre-existing options.
    if len(path.read_text(encoding="ascii").strip().splitlines()) != 1:
        raise GateError("relay-key-invalid")
    return (
        f'restrict,port-forwarding,permitopen="127.0.0.1:3129",'
        f'command="/usr/bin/false",from="{ip}" {pieces[0]} {pieces[1]}\n'
    )


def node_units(staging: Path, gate_user: str, writable: tuple[Path, ...]) -> None:
    for name in UNITS:
        path = staging / name
        text = path.read_text(encoding="utf-8")
        if "nft" in text.lower():
            raise GateError("nft-unit-refused")
        if name.endswith(".service"):
            if "User=dori-gate\n" not in text or "Group=dori-gate\n" not in text:
                raise GateError("fork-unit-layout")
            text = text.replace("User=dori-gate\n", f"User={gate_user}\n")
            text = text.replace("Group=dori-gate\n", f"Group={gate_user}\n")
            paths = " ".join('"' + str(p).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'
                             for p in writable)
            text = text.replace("StateDirectory=dori-gate\n", f"ReadWritePaths={paths}\n")
            if "/opt/dori-gate/" not in text or "--config /etc/dori-gate/config.json" not in text:
                raise GateError("fork-unit-layout")
        path.write_text(text, encoding="utf-8")
