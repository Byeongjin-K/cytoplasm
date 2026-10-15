"""Read-only, bounded grace for independently converging release surfaces."""
from __future__ import annotations

import argparse
import re
import stat
import sys
import time
from pathlib import Path
from typing import Final

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from automation.skill_mount_drift import DriftError, inspect_mounts  # noqa: E402
from automation.skill_review import skill_digest  # noqa: E402

GRACE_SECONDS: Final = 900
_SHA: Final = re.compile(r"[0-9a-f]{40,64}")


def previous_release(current: Path, *, now: float) -> Path | None:
    """Return the preceding installed generation only inside the switch window.

    The store creates a new symlink on activation and a new directory on installation.
    Require BOTH to be fresh: repeated activation must not restart an old generation's
    grace. Directory mtimes order retained installations, not git author dates. Refuse
    rollback (current is not newest), ties, future times, and incomplete markers.
    """
    link = current.lstat()
    if not stat.S_ISLNK(link.st_mode):
        raise DriftError("current is not a release symlink")
    release = current.resolve(strict=True)
    installed = release.stat().st_mtime_ns
    for stamp in (link.st_mtime, installed / 1_000_000_000):
        if not 0 <= now - stamp < GRACE_SECONDS:
            raise DriftError("release switch is outside grace")
    generations: list[tuple[int, Path]] = []
    for path in release.parent.iterdir():
        if _SHA.fullmatch(path.name) and not path.is_symlink() and path.is_dir():
            if (path / ".origin-sha").read_text(encoding="utf-8").strip() != path.name:
                raise DriftError("release marker does not match its generation")
            generations.append((path.stat().st_mtime_ns, path))
    generations.sort(key=lambda entry: entry[0], reverse=True)
    if not generations or generations[0][1] != release:
        raise DriftError("current is not the newest installed release")
    if len({stamp for stamp, _path in generations}) != len(generations):
        raise DriftError("release installation order is ambiguous")
    return generations[1][1] if len(generations) > 1 else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current", type=Path, required=True)
    surface = parser.add_mutually_exclusive_group(required=True)
    surface.add_argument("--receipt-sha")
    surface.add_argument("--missing-receipt", action="store_true")
    surface.add_argument("--live-root", type=Path)
    args = parser.parse_args(argv)
    try:
        identity = args.current.lstat()
        release = args.current.resolve(strict=True)
        previous = previous_release(args.current, now=time.time())
        if args.live_root is not None:
            report = inspect_mounts(args.current, args.live_root)
            if previous is None or report.clean or report.unmounted or report.orphaned:
                return 1
            for name, _expected, mounted in report.stale:
                source = previous / "skills" / name
                if not (source / "SKILL.md").is_file() or skill_digest(source) != mounted:
                    return 1
        elif not args.missing_receipt:
            if previous is None or args.receipt_sha != previous.name:
                return 1
        # Re-check the pointer after reading the surfaces; a switch mid-probe is unknown.
        after = args.current.lstat()
        if (after.st_ino, after.st_mtime_ns) != (identity.st_ino, identity.st_mtime_ns):
            return 1
        if args.current.resolve(strict=True) != release:
            return 1
    except (OSError, UnicodeError, DriftError):
        return 1
    print(f"RELEASE-SWITCH-GRACE-WARN: release surfaces are converging; grace < {GRACE_SECONDS}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
