"""Best-effort final replies and closure of release approval threads."""
from __future__ import annotations

import re
import sys
from collections.abc import Mapping
from dataclasses import replace
from http.client import HTTPException
from typing import Final, Literal, assert_never

from automation import skill_gate, skill_gate_surface
from automation.interop.approval_directory import DiscordApi
from automation.interop.approval_surface import ApprovalKind, ApprovalSurfaceError
from automation.interop.origin_notice import ThreadOutcome, close_thread
from automation.notice_bot import resolve_token
from automation.release_request_gate import existing_thread
from automation.release_retire import abandoned_message

_OPEN_RELEASE: Final = re.compile(r"🏷️ 릴리스 v(\d+)\.(\d+)\.(\d+)")


def _version_key(name: str) -> tuple[int, int, int] | None:
    match = _OPEN_RELEASE.fullmatch(name)
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def superseded_threads(api: DiscordApi, channel_id: str, version: str) -> list[tuple[str, str]]:
    """Active, still-undecided release threads of older versions under this channel.

    The applied version's tree contains every older main commit, so an older card whose own
    deploy never sent an applied notice (blocked or failed run) is finished by this one.
    A name with a status prefix is already decided and is never matched.
    """
    current = _version_key(f"🏷️ 릴리스 {version}")
    raw = api("GET", f"/channels/{channel_id}")
    guild_id = raw.get("guild_id") if isinstance(raw, dict) else None
    if current is None or not isinstance(guild_id, str) or not guild_id.isdecimal():
        return []
    raw = api("GET", f"/guilds/{guild_id}/threads/active")
    threads = raw.get("threads") if isinstance(raw, dict) else None
    found: list[tuple[str, str]] = []
    for thread in threads if isinstance(threads, list) else []:
        if not isinstance(thread, dict) or thread.get("parent_id") != channel_id:
            continue
        name, thread_id = str(thread.get("name") or ""), thread.get("id")
        key = _version_key(name)
        if key is not None and key < current and isinstance(thread_id, str) and thread_id.isdecimal():
            found.append((thread_id, name))
    return found


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
        if outcome is ThreadOutcome.DONE:
            for older, name in superseded_threads(skill_gate._api, channel, version):
                close_thread(
                    skill_gate._api, older, ThreadOutcome.DONE,
                    record_id=f"release:{version}:superseded", base_name=name,
                )
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
