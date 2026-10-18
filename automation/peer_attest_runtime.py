from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.request import Request, urlopen

from automation.peer_attestation import _DIGEST, _NONCE, _SKILL


API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://github.com/orientpine/autophagy-agents, 0)"


class AttestationMode(StrEnum):
    DISCORD = "discord"
    SIGNED = "signed"


@dataclass(frozen=True, slots=True)
class AttestRequest:
    skill: str
    staged_dir: Path
    expected_digest: str
    request_message_id: str
    deploy_nonce: str
    channel_id: str
    refresh: bool = False
    mode: AttestationMode = AttestationMode.DISCORD


class DiscordTransport(Protocol):
    def replies_after(
        self,
        channel_id: str,
        message_id: str,
    ) -> Sequence[Mapping[str, Any]]: ...

    def post_reply(self, channel_id: str, message_id: str, content: str) -> None: ...


@dataclass(frozen=True, slots=True)
class DiscordRestTransport:
    token: str

    def replies_after(
        self,
        channel_id: str,
        message_id: str,
    ) -> Sequence[Mapping[str, Any]]:
        messages = self.api(
            "GET",
            f"/channels/{channel_id}/messages?after={message_id}&limit=100",
        )
        if not isinstance(messages, list):
            return ()
        return cast(list[Mapping[str, Any]], messages)

    def post_reply(self, channel_id: str, message_id: str, content: str) -> None:
        payload = {
            "content": content,
            "message_reference": {
                "message_id": message_id,
                "channel_id": channel_id,
                "fail_if_not_exists": True,
            },
            "allowed_mentions": {"replied_user": False},
        }
        _ = self.api("POST", f"/channels/{channel_id}/messages", payload)

    def api(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        from automation.notice_bot import resolve_token

        request = Request(
            f"{API}{path}",
            data=None if payload is None else json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bot {resolve_token(self.token)}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            method=method,
        )
        with urlopen(request, timeout=30) as response:  # noqa: S310
            body = response.read().decode("utf-8")
        return json.loads(body) if body else None


def attest_channel_id(
    transport: DiscordRestTransport,
    gate_dir: Path,
    interop_config: Path,
) -> str:
    try:
        from automation import skill_gate_surface
        from automation.interop.approval_surface import (
            ApprovalKind,
            ApprovalSurfaceError,
        )
    except ImportError as error:
        raise OSError("approval surface resolver unavailable; pass --channel-id") from error
    identity = skill_gate_surface.GateIdentity(
        transport.token,
        transport.api,
        gate_dir,
        interop_config,
    )
    try:
        return skill_gate_surface.surface_for(
            ApprovalKind.SKILL_ATTEST,
            identity,
        ).new().channel_id
    except ApprovalSurfaceError as error:
        raise OSError(f"peer attestation surface unresolved: {error}") from error


def parse_request(argv: Sequence[str]) -> AttestRequest | None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill", required=True)
    parser.add_argument("--staged-dir", required=True)
    parser.add_argument("--hash", required=True)
    parser.add_argument("--request-message-id", required=True)
    parser.add_argument("--deploy-nonce", required=True)
    parser.add_argument("--channel-id", default="")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument(
        "--mode",
        choices=tuple(AttestationMode),
        default=AttestationMode.DISCORD,
    )
    args = parser.parse_args(argv)
    if (
        _SKILL.fullmatch(args.skill) is None
        or _DIGEST.fullmatch(args.hash) is None
        or _NONCE.fullmatch(args.deploy_nonce) is None
    ):
        return None
    return AttestRequest(
        args.skill,
        Path(args.staged_dir),
        args.hash,
        args.request_message_id,
        args.deploy_nonce,
        args.channel_id,
        bool(args.refresh),
        AttestationMode(args.mode),
    )
