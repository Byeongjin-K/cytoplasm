"""Opt-in Kakao owner attestations from the watcher's authenticated local socket."""
from __future__ import annotations

import json
import math
import pwd
import re
import socket
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypedDict

REFERENCE: Final = re.compile(r"kakao:([0-9]{1,20})/([0-9]{1,20})")
DIGITS: Final = re.compile(r"[0-9]{1,20}")
WIRE_LIMIT: Final = 16384


@dataclass(frozen=True, slots=True)
class KakaoRelay:
    bot_id: str
    owner_ids: tuple[str, ...]
    watcher_user: str
    socket: str
    relay_user: str


class KakaoReference(TypedDict):
    chat: str
    log: str
    sent_at: float


@dataclass(frozen=True, slots=True)
class ProofRequest:
    chat: str
    log: str
    text: str


@dataclass(frozen=True, slots=True)
class Attestation:
    author_id: str
    sent_at: float


@dataclass(frozen=True, slots=True)
class KakaoRefused(Exception):
    reason: str

    def __str__(self) -> str:
        return f"kakao relay refused: {self.reason}"


def load_kakao_relay(path: Path) -> KakaoRelay | None:
    """Missing or malformed opt-in settings never break other origin paths."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    match value:
        case {"kakao_relay": {"bot_id": str(bot), "owner_ids": list(owners),
                              "watcher_user": str(user), "relay_user": str(relay_user), "socket": str(name)}}:
            try:
                socket_bytes = name.encode()
            except UnicodeError:
                return None
            if (DIGITS.fullmatch(bot) and owners
                    and all(isinstance(owner, str) and owner for owner in owners)
                    and user and "\0" not in user and relay_user and "\0" not in relay_user and relay_user != user
                    and name != "off" and name and "\0" not in name and len(socket_bytes) <= 107):
                return KakaoRelay(bot, tuple(owners), user, name, relay_user)
    return None


def socket_attestation(relay: KakaoRelay, request: ProofRequest) -> Attestation:
    """One bounded exchange; authenticate the server before sending any text."""
    try:
        wire = (json.dumps({"chat": request.chat, "log": request.log, "text": request.text},
                           ensure_ascii=False) + "\n").encode()
    except UnicodeError:
        raise KakaoRefused("malformed relay text") from None
    if len(wire) > WIRE_LIMIT:
        raise KakaoRefused("request too large")
    deadline = time.monotonic() + 3
    try:
        uid = pwd.getpwnam(relay.watcher_user).pw_uid
        # The relay Dori's agent could stop a watcher in its own account and answer in its place.
        if uid == pwd.getpwnam(relay.relay_user).pw_uid:
            raise KakaoRefused("watcher shares the relay account")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
            peer.settimeout(max(0.001, deadline - time.monotonic()))
            peer.connect("\0" + relay.socket)
            _, peer_uid, _ = struct.unpack("3i", peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if peer_uid != uid:
                raise KakaoRefused("watcher uid mismatch")
            peer.settimeout(max(0.001, deadline - time.monotonic()))
            peer.sendall(wire)
            reply = bytearray()
            while b"\n" not in reply:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise KakaoRefused("socket timeout")
                peer.settimeout(remaining)
                chunk = peer.recv(WIRE_LIMIT + 1 - len(reply))
                if not chunk:
                    raise KakaoRefused("incomplete reply")
                reply.extend(chunk)
                if len(reply) > WIRE_LIMIT:
                    raise KakaoRefused("reply too large")
        value = json.loads(bytes(reply).decode("utf-8"))
    except (OSError, ValueError, KeyError, struct.error):
        raise KakaoRefused("attestation unavailable or malformed") from None
    match value:
        case {"found": True, "chat": str(chat), "log": str(log), "author_id": author,
              "author_kind": "owner", "quoted": True, "sent_at": sent}:
            if (chat == request.chat and log == request.log
                    and type(author) in (str, int) and str(author) in relay.owner_ids
                    and type(sent) in (int, float)):
                try:
                    stamp = float(sent)
                except OverflowError:
                    raise KakaoRefused("malformed timestamp") from None
                if math.isfinite(stamp):
                    return Attestation(str(author), stamp)
    raise KakaoRefused("record is missing, unquoted, or not the owner")


Attest = Callable[[KakaoRelay, ProofRequest], Attestation]


def verify_references(
    relay: KakaoRelay, text: str, *, now: float, max_age: float, attest: Attest,
) -> tuple[KakaoReference, ...]:
    """Every distinct cited message must be recent and authenticated."""
    refs = tuple(dict.fromkeys(REFERENCE.findall(text)))
    if not 1 <= len(refs) <= 5:
        raise KakaoRefused("expected 1 to 5 references")
    verified: list[KakaoReference] = []
    for chat, log in refs:
        record = attest(relay, ProofRequest(chat, log, text))
        if not 0 <= now - record.sent_at <= max_age:
            raise KakaoRefused("record outside 24 hours")
        verified.append({"chat": chat, "log": log, "sent_at": record.sent_at})
    return tuple(verified)
