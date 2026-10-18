"""Long-card approval through the real lifecycle and an offline HTTP surface."""
from __future__ import annotations

import argparse
import hashlib
import json
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import Literal, assert_never

import pytest

from tests.unit import test_mail_single_live_request as mail
from tests.unit.test_mail_single_live_request import mail_env as mail_env

_API_REQUEST = mail.triage_confirm._api


@pytest.fixture
def attachment_wire(mail_env, monkeypatch):
    """Use the existing Discord state machine behind a real loopback HTTP server."""
    fake, _, _ = mail_env
    uploads = []
    attachments = {}
    downloads = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: str) -> None:
            return

        def handle_request(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            payload = None
            if self.headers.get_content_type() == "multipart/form-data":
                mime = BytesParser(policy=policy.default).parsebytes(
                    f"Content-Type: {self.headers['Content-Type']}\r\n\r\n".encode() + raw
                )
                parts = list(mime.iter_parts())
                assert [part.get_param("name", header="content-disposition") for part in parts] == [
                    "payload_json", "files[0]",
                ]
                encoded = parts[0].get_payload(decode=True)
                assert isinstance(encoded, bytes)
                payload = json.loads(encoded)
                data = parts[1].get_payload(decode=True)
                assert isinstance(data, bytes)
                filename = parts[1].get_filename()
                uploads.append((payload, filename, data))
            elif raw:
                payload = json.loads(raw)
            if self.path.startswith("/attachment/"):
                # The live CDN rejects urllib's default identity with HTTP 403.
                if not self.headers.get("User-Agent", "").startswith("DiscordBot ("):
                    self.send_response(403)
                    self.end_headers()
                    return
                assert self.headers.get("Authorization") is None
                result = downloads[self.path]
                self.send_response(200)
                self.end_headers()
                self.wfile.write(result)
                return
            result = fake.api(self.command, self.path, payload)
            if self.command == "POST" and uploads and payload is uploads[-1][0]:
                _, filename, data = uploads[-1]
                route = f"/attachment/{result['id']}"
                downloads[route] = data
                attachments[result['id']] = [{
                    "filename": filename, "size": len(data), "url": base + route,
                }]
            if self.command == "GET" and isinstance(result, dict) and result.get("id") in attachments:
                result["attachments"] = attachments[result["id"]]
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())

        do_GET = handle_request
        do_POST = handle_request
        do_PUT = handle_request
        do_DELETE = handle_request

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        base = f"http://127.0.0.1:{server.server_port}"
        monkeypatch.setattr(mail.triage_confirm, "API", base)
        monkeypatch.setattr(mail.triage_confirm, "_api", _API_REQUEST)
        monkeypatch.setattr(mail.triage_confirm, "bot_token", lambda: "offline")
        worker = Thread(target=server.serve_forever)
        worker.start()
        try:
            yield fake, uploads, attachments, downloads
        finally:
            server.shutdown()
            worker.join(timeout=5)
            assert not worker.is_alive()


def long_draft():
    return mail.triage_gate.create_draft(
        uid="long-mail", sender="sender@example.invalid", mail_subject="review",
        to="recipient@example.invalid", subject="long review", body="가" * 2500,
        quote="original quoted message", cc="copy@example.invalid", kind="compose",
        category="important", flags=(),
    )


def test_long_draft_posts_one_attachment_when_card_exceeds_limit(attachment_wire):
    # Given a long draft with a quote, backed by real on-disk records.
    fake, uploads, _, _ = attachment_wire
    draft = long_draft()
    expected = (draft["body"] + "\n\n" + draft["quote"]).encode()
    # When the public producer posts through the real urllib multipart wire.
    message_id = mail.triage_approval.post_for_approval(draft)
    # Then the single approval message owns the exact reviewed bytes and metadata.
    assert fake.posts == 1
    [(_, filename, data)] = uploads
    assert data == expected
    stored = mail.triage_gate.load_draft(draft["id"])
    assert stored["approval_attachment"] == {
        "filename": filename, "size": len(expected), "sha256": hashlib.sha256(expected).hexdigest(),
    }
    assert stored["approval_format"] == "attachment-v1"
    assert stored["sha256"] == draft["sha256"] == mail.triage_approval.triage_core.draft_sha256(stored)
    assert len(fake.contents[message_id]) <= 2000
    assert draft["sha256"] not in fake.contents[message_id]
    assert hashlib.sha256(expected).hexdigest() not in fake.contents[message_id]
    assert stored["card_sha256"] == hashlib.sha256(fake.contents[message_id].strip().encode()).hexdigest()


@pytest.mark.parametrize("defect", ["missing", "filename", "size", "sha", "record", "url", "malformed-url"])
@pytest.mark.parametrize("consumer", ["probe", "resolve"])
def test_attachment_mismatch_refuses_approval_when_owner_reacted(
    attachment_wire,
    defect: Literal["missing", "filename", "size", "sha", "record", "url", "malformed-url"],
    consumer: Literal["probe", "resolve"],
) -> None:
    # Given a posted request with owner approval but corrupted attachment evidence.
    fake, _, attachments, downloads = attachment_wire
    draft = long_draft()
    message_id = mail.triage_approval.post_for_approval(draft)
    stored = mail.triage_gate.load_draft(draft["id"])
    fake.approved.add(message_id)
    match defect:
        case "missing":
            attachments[message_id] = []
        case "filename":
            attachments[message_id][0]["filename"] = "wrong.txt"
        case "size":
            attachments[message_id][0]["size"] += 1
        case "sha":
            downloads[f"/attachment/{message_id}"] = b"x" * len(downloads[f"/attachment/{message_id}"])
        case "record":
            stored.pop("approval_attachment")
        case "url":
            attachments[message_id][0]["url"] = "file:///unreadable"
        case "malformed-url":
            attachments[message_id][0]["url"] = "https://[broken"
        case unreachable:
            assert_never(unreachable)
    # When / Then both public consumers fail closed before honoring the decision.
    if consumer == "resolve":
        with pytest.raises(mail.triage_gate.GateError):
            mail.triage_confirm.resolve_reaction(stored)
    else:
        probe = mail.triage_approval.MailApprovalGate(stored).probe(mail.triage_approval.request_of(stored))
        assert probe is mail.triage_approval.lifecycle().Probe.BINDING_MISMATCH


@pytest.mark.parametrize("consumer", ["probe", "resolve"])
def test_attachment_allows_approval_when_bytes_match(attachment_wire, consumer):
    # Given an intact uploaded attachment and the owner's approval.
    fake, _, _, _ = attachment_wire
    draft = long_draft()
    message_id = mail.triage_approval.post_for_approval(draft)
    stored = mail.triage_gate.load_draft(draft["id"])
    fake.approved.add(message_id)
    # When / Then the real consumer accepts the verified attachment.
    if consumer == "resolve":
        assert mail.triage_confirm.resolve_reaction(stored) == mail.triage_confirm.APPROVE_EMOJI
    else:
        probe = mail.triage_approval.MailApprovalGate(stored).probe(mail.triage_approval.request_of(stored))
        assert probe is mail.triage_approval.lifecycle().Probe.APPROVED


@pytest.mark.parametrize("intact", [True, False])
def test_watch_checks_attachment_when_post_and_reaction_happen_in_one_tick(attachment_wire, monkeypatch, intact):
    # Given an unposted long draft and an owner who reacts as soon as it appears.
    fake, _, attachments, _ = attachment_wire
    draft = long_draft()
    original_api = fake.api
    sent = []

    def immediate_reaction(method, path, payload=None):
        result = original_api(method, path, payload)
        if method == "POST" and path.endswith("/messages") and result["id"] in fake.contents:
            fake.approved.add(result["id"])
        if not intact and method == "GET" and "/messages/" in path:
            for message_id in attachments:
                attachments[message_id] = []
        return result

    monkeypatch.setattr(fake, "api", immediate_reaction)
    monkeypatch.setattr(mail.triage_mode, "effective_mode", lambda: "full-go")
    monkeypatch.setattr(mail.triage_cli.mail_preflight, "execute_cli_draft", lambda draft, approval: sent.append(draft["id"]))
    monkeypatch.setattr(mail.triage_cli, "_notify_sent", lambda *_args: None)
    # When the real watch CLI handler posts and resolves in the same invocation.
    assert mail.triage_cli.cmd_watch(argparse.Namespace()) == 0
    # Then even its pre-post in-memory draft cannot bypass attachment verification.
    assert sent == ([draft["id"]] if intact else [])


def test_multipart_refuses_commit_when_response_has_no_message_id(mail_env, monkeypatch):
    # Given an attachment post whose HTTP response lacks a bound Discord id.
    draft = {**long_draft(), "approval_format": "attachment-v1"}
    monkeypatch.setattr(mail.triage_confirm, "bot_token", lambda: "offline")
    monkeypatch.setattr(mail.triage_confirm, "_send", lambda _request: {})
    intent = mail.triage_approval.lifecycle().ApprovalIntent("mail:compose:long-mail", draft["sha256"], "offline")
    # When / Then a typed refusal prevents reactions and commit.
    with pytest.raises(mail.triage_gate.GateError):
        mail.triage_approval.MailApprovalGate(draft).post(intent)


def test_existing_long_request_is_verified_when_a_sibling_requests_approval(attachment_wire):
    # Given an approved long request and a different short draft for the same key.
    fake, _, attachments, _ = attachment_wire
    draft = long_draft()
    message_id = mail.triage_approval.post_for_approval(draft)
    fake.approved.add(message_id)
    attachments[message_id] = []
    sibling = {**draft, "id": "sibling", "body": "short"}
    sibling["sha256"] = mail.triage_approval.triage_core.draft_sha256(sibling)
    # When the lifecycle inspects the existing request, not the candidate's metadata.
    probe = mail.triage_approval.MailApprovalGate(sibling).probe(
        mail.triage_approval.request_of(mail.triage_gate.load_draft(draft["id"]))
    )
    # Then the missing old attachment is not ignored.
    assert probe is mail.triage_approval.lifecycle().Probe.BINDING_MISMATCH
