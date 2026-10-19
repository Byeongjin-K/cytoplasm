"""Best-effort final replies and closure of release approval threads."""
from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import replace
from http.client import HTTPException
from typing import Literal, assert_never

from automation import skill_gate, skill_gate_surface
from automation.interop.approval_surface import ApprovalKind, ApprovalSurfaceError
from automation.interop.origin_notice import ThreadOutcome, close_thread
from automation.notice_bot import resolve_token
from automation.release_request_gate import existing_thread
from automation.release_retire import abandoned_message


def finish(version: str, outcome: ThreadOutcome) -> None:
    """Close only the matching version under the configured approval channel."""
    try:
        token = resolve_token()
        if not token:
            raise ApprovalSurfaceError("release thread credential unavailable")
        identity = skill_gate_surface.GateIdentity(
            token, skill_gate._api, skill_gate.GATE_DIR, skill_gate.INTEROP_CONFIG,
        )
        channel = skill_gate_surface.surface_for(ApprovalKind.RELEASE, identity).new().channel_id
        thread = existing_thread(skill_gate._api, channel, version)
        if thread is not None:
            close_thread(skill_gate._api, thread, outcome, record_id=f"release:{version}")
    except (OSError, HTTPException, ApprovalSurfaceError, ValueError, KeyError, TypeError, SystemExit) as error:
        print(f"THREAD-CLOSE-FAIL id=release:{version} err={type(error).__name__}", file=sys.stderr)


def reply(
    record: Mapping[str, str], reason: str,
    outcome: Literal[ThreadOutcome.CANCELLED, ThreadOutcome.EXPIRED],
) -> bool:
    """Reply first; only a successful reply may be followed by thread closure."""
    try:
        match outcome:
            case ThreadOutcome.CANCELLED:
                marker = "RELEASE-CANCEL-NOTICE-FAIL"
                content = f"⛔ 릴리스 {record['version']} 승인 취소 — 배포 미실행"
                message = abandoned_message(record, reason)
                if message is not None:
                    message = replace(message, subject="릴리스 승인 취소", fact="소유자가 승인 취소 · 배포 미실행")
            case ThreadOutcome.EXPIRED:
                marker = "RELEASE-ABANDON-NOTICE-FAIL"
                content = f"⛔ 만료 — {' '.join(reason.split())[:1000]}"
                message = abandoned_message(record, reason)
            case unreachable:
                assert_never(unreachable)
        if message is not None:
            from automation.interop.owner_message import Ref, render

            content = render(message, destination=Ref(scope="channel", channel_id=record["channel_id"]))
        skill_gate._api("POST", f"/channels/{record['channel_id']}/messages", {
            "content": content,
            "message_reference": {"message_id": record["message_id"], "fail_if_not_exists": True},
            "allowed_mentions": {"parse": [], "replied_user": False},
        })
    except (OSError, HTTPException, ValueError, KeyError, TypeError, SystemExit) as error:
        print(f"{marker} {type(error).__name__}", file=sys.stderr)
        return False
    finish(record["version"], outcome)
    return True
