"""Kakao watcher wire protocol and the opt-in owner-origin security boundary."""
from __future__ import annotations

import json
import os
import pwd
import socket
import threading
import uuid
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from automation.owner_kakao import Attestation, KakaoRelay, load_kakao_relay
from automation.owner_origin import DISCORD_EPOCH_MS, OriginRefused, verify_owner_origin
from automation.yoodori_gate import yoodori_jobs as jobs
from automation.yoodori_gate import yoodori_read_gate as gate

NOW = 1700000000.0
POSTED = NOW - 60
MESSAGE = str(int(POSTED * 1000 - DISCORD_EPOCH_MS) << 22)
CHANNEL = "123"
OWNER = "111"
BOT = "444"
TEXT = "synthetic owner request\n-# 카톡 근거 kakao:123/456"


@pytest.fixture
def watcher() -> Iterator[tuple[KakaoRelay, dict, list[dict]]]:
    """A real abstract socket, accepting any number of sequential proof requests."""
    name = f"cyto-kakao-test-{uuid.uuid4().hex}"
    reply = {"found": True, "chat": "123", "log": "456", "author_id": "222",
             "author_kind": "owner", "sent_at": NOW - 120, "quoted": True}
    requests: list[dict] = []
    errors: list[BaseException] = []
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind("\0" + name)
        server.listen()
        server.settimeout(5)
        def serve() -> None:
            try:
                while True:
                    with server.accept()[0] as peer:
                        peer.settimeout(3)
                        raw = bytearray()
                        while b"\n" not in raw:
                            chunk = peer.recv(16385)
                            if not chunk:
                                break
                            raw.extend(chunk)
                        if raw == b"stop\n":
                            return
                        if raw:
                            requests.append(json.loads(raw))
                            peer.sendall((json.dumps(reply) + "\n").encode())
            except (OSError, ValueError) as error:
                errors.append(error)
        thread = threading.Thread(target=serve)
        thread.start()
        try:
            relay_user = [account.pw_name for account in pwd.getpwall() if account.pw_uid != os.getuid()][-1]
            yield KakaoRelay(BOT, ("222",), pwd.getpwuid(os.getuid()).pw_name, name, relay_user), reply, requests
        finally:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stop:
                stop.connect("\0" + name)
                stop.sendall(b"stop\n")
            thread.join(timeout=5)
            assert not thread.is_alive()
            assert errors == []


def discord_message(**changes) -> dict:
    return {"id": MESSAGE, "channel_id": CHANNEL, "author": {"id": BOT, "bot": True},
            "content": TEXT, **changes}


def fetch_message(message: dict, *, channel: dict | None = None):
    def fetch(path: str, token: str) -> dict:
        if "/messages/" in path:
            return message
        return channel if channel is not None else {"type": 0, "guild_id": "333"}
    return fetch


@pytest.mark.parametrize("sent_at", [NOW - 120, NOW - 30, NOW - 86400])
def test_authenticated_relay_records_only_references_and_oldest_time(watcher, sent_at: float) -> None:
    relay, reply, requests = watcher
    reply["sent_at"] = sent_at
    origin = verify_owner_origin(CHANNEL, MESSAGE, OWNER, "test", now=NOW,
                                 fetch=fetch_message(discord_message()), kakao_relay=relay)
    assert (origin.link, origin.sent_at, origin.kakao) == (
        f"https://discord.com/channels/333/{CHANNEL}/{MESSAGE}", min(POSTED, sent_at),
        ({"chat": "123", "log": "456", "sent_at": sent_at},))
    assert requests == [{"chat": "123", "log": "456", "text": TEXT}]


@pytest.mark.parametrize(("changes", "reason"), [
    ({"author": {"id": "555", "bot": True}}, "configured bot"),
    ({"author": {"id": BOT, "bot": False}}, "configured bot"),
    ({"webhook_id": "999"}, "configured bot"),
    ({"id": "99"}, "configured bot"),
    ({"channel_id": "99"}, "configured bot"),
    ({"content": "no reference"}, "1 to 5"),
    ({"content": " ".join(f"kakao:123/{n}" for n in range(6))}, "1 to 5"),
])
def test_discord_relay_identity_and_reference_bounds(watcher, changes: dict, reason: str) -> None:
    relay, _, requests = watcher
    with pytest.raises(OriginRefused, match=reason):
        verify_owner_origin(CHANNEL, MESSAGE, OWNER, "test", now=NOW,
                            fetch=fetch_message(discord_message(**changes)), kakao_relay=relay)
    assert requests == []


@pytest.mark.parametrize("changes", [
    {"author_kind": "member"}, {"author_id": "999"}, {"quoted": False}, {"found": False},
    {"sent_at": NOW - 86401}, {"sent_at": NOW + 1}, {"sent_at": True}, {"sent_at": float("nan")},
    {"chat": "999"}, {"log": "999"}, {"found": 1}, {"quoted": 1}, {"sent_at": "yesterday"},
])
def test_each_watcher_attestation_must_be_owner_quoted_recent_and_matching(watcher, changes: dict) -> None:
    relay, reply, _ = watcher
    reply.update(changes)
    with pytest.raises(OriginRefused, match="kakao relay refused"):
        verify_owner_origin(CHANNEL, MESSAGE, OWNER, "test", now=NOW,
                            fetch=fetch_message(discord_message()), kakao_relay=relay)


def test_old_relay_is_refused_before_asking_watcher(watcher) -> None:
    relay, _, requests = watcher
    with pytest.raises(OriginRefused, match="24 hours"):
        verify_owner_origin(CHANNEL, MESSAGE, OWNER, "test", now=NOW + 86400, card_answers=True,
                            fetch=fetch_message(discord_message()), kakao_relay=relay)
    assert requests == []


def test_socket_peer_uid_must_match_configured_watcher(watcher) -> None:
    relay, _, requests = watcher
    other = next(account for account in pwd.getpwall() if account.pw_uid != os.getuid())
    with pytest.raises(OriginRefused, match="uid mismatch"):
        verify_owner_origin(CHANNEL, MESSAGE, OWNER, "test", now=NOW,
                            fetch=fetch_message(discord_message()),
                            kakao_relay=replace(relay, watcher_user=other.pw_name))
    assert requests == []


def test_socket_served_by_the_relay_account_is_refused(watcher) -> None:
    # An agent-owned replacement: the relay Dori's own account serves the socket while the watcher runs elsewhere.
    relay, _, requests = watcher
    other = next(account for account in pwd.getpwall() if account.pw_uid != os.getuid())
    with pytest.raises(OriginRefused, match="uid mismatch"):
        verify_owner_origin(CHANNEL, MESSAGE, OWNER, "test", now=NOW, fetch=fetch_message(discord_message()),
                            kakao_relay=replace(relay, watcher_user=other.pw_name,
                                                relay_user=pwd.getpwuid(os.getuid()).pw_name))
    assert requests == []


def test_watcher_in_the_relay_account_is_refused(watcher) -> None:
    relay, _, requests = watcher
    with pytest.raises(OriginRefused, match="shares the relay account"):
        verify_owner_origin(CHANNEL, MESSAGE, OWNER, "test", now=NOW, fetch=fetch_message(discord_message()),
                            kakao_relay=replace(relay, relay_user=relay.watcher_user))
    assert requests == []


@pytest.mark.parametrize("card_answers", [False, True])
def test_feature_off_and_release_never_accept_relay(watcher, card_answers: bool) -> None:
    _, _, requests = watcher
    with pytest.raises(OriginRefused, match="owner's own"):
        verify_owner_origin(CHANNEL, MESSAGE, OWNER, "test", now=NOW, card_answers=card_answers,
                            any_card_bot=card_answers, fetch=fetch_message(discord_message()))
    assert requests == []


def test_channel_must_be_placeable_before_watcher_receives_text(watcher) -> None:
    relay, _, requests = watcher
    with pytest.raises(OriginRefused, match="channel could not be placed"):
        verify_owner_origin(CHANNEL, MESSAGE, OWNER, "test", now=NOW, kakao_relay=relay,
                            fetch=fetch_message(discord_message(), channel={"type": 0}))
    assert requests == []


def test_duplicate_references_are_asked_once_and_all_distinct_references_are_checked(watcher) -> None:
    relay, _, requests = watcher
    content = TEXT + "\nkakao:123/456\nkakao:123/457"
    with pytest.raises(OriginRefused, match="record is missing"):
        verify_owner_origin(CHANNEL, MESSAGE, OWNER, "test", now=NOW, kakao_relay=relay,
                            fetch=fetch_message(discord_message(content=content)))
    assert [(r["chat"], r["log"]) for r in requests] == [("123", "456"), ("123", "457")]


def test_missing_socket_fails_closed(watcher) -> None:
    relay, _, _ = watcher
    with pytest.raises(OriginRefused, match="attestation unavailable"):
        verify_owner_origin(CHANNEL, MESSAGE, OWNER, "test", now=NOW,
                            kakao_relay=replace(relay, socket=relay.socket + "-missing"),
                            fetch=fetch_message(discord_message()))


def test_five_distinct_references_are_accepted_with_injectable_attestation(watcher) -> None:
    relay, _, _ = watcher
    origin = verify_owner_origin(CHANNEL, MESSAGE, OWNER, "test", now=NOW, kakao_relay=relay,
        fetch=fetch_message(discord_message(content=" ".join(f"kakao:123/{n}" for n in range(5)))),
        attest=lambda relay, request: Attestation("222", NOW - 120))
    assert origin.kakao == tuple({"chat": "123", "log": str(n), "sent_at": NOW - 120} for n in range(5))


@pytest.mark.parametrize("value", [None, [], {}, {"bot_id": BOT}, {"bot_id": BOT, "owner_ids": []},
    {"bot_id": BOT, "owner_ids": ["222"], "watcher_user": "watcher", "socket": "off"},
    {"bot_id": BOT, "owner_ids": [222], "watcher_user": "watcher", "socket": "proof"},
    {"bot_id": BOT, "owner_ids": ["222"], "watcher_user": "watcher", "socket": "proof"},
    {"bot_id": BOT, "owner_ids": ["222"], "watcher_user": "watcher", "relay_user": "watcher", "socket": "proof"}])
def test_malformed_relay_config_is_off(tmp_path: Path, value) -> None:
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"kakao_relay": value}))
    assert load_kakao_relay(config) is None


def test_yoodori_loads_opt_in_from_its_owner_config(watcher, tmp_path: Path) -> None:
    relay, _, _ = watcher
    config = tmp_path / ".hermes/interop/config.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"owner_id": OWNER, "kakao_relay": {
        "bot_id": relay.bot_id, "owner_ids": list(relay.owner_ids),
        "watcher_user": relay.watcher_user, "relay_user": relay.relay_user, "socket": relay.socket}}))
    origin = jobs.verify_owner_origin(CHANNEL, MESSAGE, OWNER, "test", now=NOW, home=tmp_path,
                                      fetch=fetch_message(discord_message()))
    assert origin.kakao == ({"chat": "123", "log": "456", "sent_at": NOW - 120},)


@pytest.mark.parametrize("words", [
    ["mail-sync"], ["mail-attachment-text", "777", "synthetic.pdf"],
    ["doc", "report", "report", "--query", "synthetic", "--title", "synthetic"],
])
def test_gate_persists_kakao_basis_for_read_and_write_jobs(watcher, tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch, words: list[str]) -> None:
    relay, _, _ = watcher
    config = tmp_path / ".hermes/interop/config.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"owner_id": OWNER, "kakao_relay": {
        "bot_id": relay.bot_id, "owner_ids": list(relay.owner_ids),
        "watcher_user": relay.watcher_user, "relay_user": relay.relay_user, "socket": relay.socket}}))
    monkeypatch.setattr(gate, "HOME", tmp_path)
    monkeypatch.setattr(gate, "_owner_id", lambda: OWNER)
    monkeypatch.setattr(gate, "_switched", lambda kind: True)
    monkeypatch.setattr(gate, "_release_modules", lambda: (
        SimpleNamespace(enabled=lambda kind: True), SimpleNamespace(resolve_token=lambda environ: "test")))
    monkeypatch.setattr(gate.yoodori_jobs, "verify_owner_origin", lambda *args, **kwargs:
        jobs.verify_owner_origin(*args, **kwargs, now=NOW, fetch=fetch_message(discord_message())))
    monkeypatch.setattr(gate.yoodori_jobs, "launch", lambda path, env: None)
    monkeypatch.setattr(gate.yoodori_jobs, "wait", lambda path, seconds: False)
    job = gate.build_job([*words, "--origin-channel-id", CHANNEL, "--origin-message-id", MESSAGE])
    gate.run_job(job)
    [spec] = [json.loads(path.read_text()) for path in jobs.jobs_root(tmp_path).rglob("spec.json")]
    assert spec["origin"] == {"channel_id": CHANNEL, "message_id": MESSAGE,
        "link": f"https://discord.com/channels/333/{CHANNEL}/{MESSAGE}",
        "kakao": [{"chat": "123", "log": "456", "sent_at": NOW - 120}], "sent_at": NOW - 120}
