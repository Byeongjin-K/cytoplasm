"""Subprocess adapter: real retirement/decision, fake Git planning and Discord owner."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation import owner_notice, release_approval, skill_gate
from automation.interop.approval_types import Probe
from automation.interop.owner_message import OwnerMessage
from automation.release_spec import spec_from_record
from automation.interop.approval_surface import (
    ApprovalBinding, ApprovalKind, ApprovalSurface, POLICY_VERSION,
)


class OwnerGate:
    """The external owner's decision is attached to a message, never to the tip."""

    def outstanding(self, key: str) -> tuple[str, ...]:
        return ("live",)

    def probe(self, request: str) -> Probe:
        record = json.loads((skill_gate.GATE_DIR / "pending/release.json").read_text())
        if record["message_id"] == "new-card":
            return Probe(os.environ["NEW_PROBE"])
        return Probe.APPROVED


def main() -> int:
    skill_gate.GATE_DIR = Path(os.environ["GATE_DIR"])
    skill_gate.APPROVAL_LOG = skill_gate.GATE_DIR / "approvals.jsonl"
    release_approval._gate = lambda spec: OwnerGate()
    skill_gate._api = lambda method, path, payload: {"id": "status-reply"}

    def notice(body: str, *, message: OwnerMessage | None = None, fault: bool = False) -> bool:
        with (skill_gate.GATE_DIR / "notices").open("a", encoding="utf-8") as stream:
            stream.write("notice\n")
        return True

    owner_notice.notify_owner = notice
    command = sys.argv[1]
    with (skill_gate.GATE_DIR / "calls").open("a", encoding="utf-8") as stream:
        stream.write(command + "\n")
    if command == "plan":
        print((skill_gate.GATE_DIR / "seed.json").read_text())
        return 0
    if command == "request":
        path = skill_gate.GATE_DIR / "pending/release.json"
        if path.exists():
            return 0 if json.loads(path.read_text())["head_sha"] == os.environ["TIP"] else 6
        seed = json.loads((skill_gate.GATE_DIR / "seed.json").read_text())
        spec = replace(spec_from_record(seed), head_sha=os.environ["TIP"], version="v1.2.4")
        binding = ApprovalBinding(ApprovalKind.RELEASE, ApprovalSurface.SKILL_APPROVALS, "999", POLICY_VERSION)
        path.write_text(spec.serialize(spec.new_record("new-card", binding)), encoding="utf-8")
        return 0
    return release_approval.main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
