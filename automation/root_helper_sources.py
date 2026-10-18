"""Repository files the node keeps as root-owned static copies (MD-1).

A change to one of them is not live until the owner reruns its provisioner as root, so
release.sh refuses the next card (HELPER-DRIFT). The list is read from the healthcheck probe
that compares those copies, so it cannot drift from what is actually watched.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

MARKER = "ROOT-HELPER-BLOCK"
_ENTRY = re.compile(r'\|((?:automation|configs)/[^|"\s]+)\|(provision-[a-z-]+\.sh)"')


def sources(repo: Path) -> dict[str, str]:
    """Repository path -> the provisioner that installs its root copy."""
    text = (repo / "automation/release_helper_probe.sh").read_text(encoding="utf-8")
    return {path: provisioner for path, provisioner in _ENTRY.findall(text)}


def touched(repo: Path, changed: list[str]) -> dict[str, str]:
    known = sources(repo)
    return {path: known[path] for path in changed if path in known}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    repo = Path(args[0]) if args else Path.cwd()
    hits = touched(repo, [line.strip() for line in sys.stdin if line.strip()])
    for path, provisioner in sorted(hits.items()):
        print(f"{path} ({provisioner})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
