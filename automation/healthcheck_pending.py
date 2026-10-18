"""Read-only evidence for a wrapper refresh already planned by this release card."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from automation.release_spec import ReleaseSpecError, spec_from_record, wrapper_surface


def planned_refresh(path: Path, node: str, digest: str, head: str) -> bool:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        spec = spec_from_record(record)
        return (
            spec.head_sha == head and record.get("message_id") is not None
            and record.get("action_hash") == spec.action_hash()
            and dict(spec.surface_digests).get(wrapper_surface(node)) == digest
        )
    except (OSError, ValueError, TypeError, KeyError, AttributeError, ReleaseSpecError):
        return False


if __name__ == "__main__":
    path, node, digest, source = sys.argv[1:]
    try:
        head = (Path(source) / ".origin-sha").read_text().strip()
    except OSError:
        head = ""
    raise SystemExit(0 if planned_refresh(Path(path), node, digest, head) else 1)
