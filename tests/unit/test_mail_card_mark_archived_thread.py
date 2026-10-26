"""A withdrawn or superseded card is still marked when its thread was already closed (2026-10-26).

28fe93 was withdrawn at 19:29 KST inside a thread the release result had closed (archived) at
18:48 KST. Discord refuses edits in an archived thread (HTTP 400, code 50083), so the card kept its
live look and only ``MAIL-SUPERSEDE-MARK-FAIL ... err=HTTPError`` was printed. Separate file from
``test_mail_card_withdraw.py`` because this fixture models Discord's archived-thread refusal.
"""
from __future__ import annotations

import argparse
import importlib
import io
import json
from urllib.error import HTTPError

import pytest

from automation.interop import origin_notice
from tests.unit import test_mail_single_live_request as mail
from tests.unit.test_mail_card_v4_supersede import THREAD, _compose, _post
from tests.unit.test_mail_single_live_request import mail_env as mail_env

REASON = "시험 끝남"


def _refusal(status: int, code: int) -> HTTPError:
    body = io.BytesIO(json.dumps({"message": "refused", "code": code}).encode())
    return HTTPError("https://discord.invalid", status, "refused", None, body)  # type: ignore[arg-type]


@pytest.fixture
def closed(mail_env, monkeypatch: pytest.MonkeyPatch):
    """A posted pending card whose thread is archived (not locked) before the withdrawal."""
    fake = mail_env[0]
    fake.threads[THREAD] = "점심 메일"
    state = {"archived": False, "refusal": (400, 50083)}
    writes: list[tuple[str, str, dict | None]] = []
    original = fake.api

    def api(method, path, payload=None):
        parts = path.strip("/").split("/")
        if method == "PATCH" and len(parts) == 2 and parts[1] == THREAD:
            writes.append((method, path, payload))
            if payload == {"archived": False}:
                state["archived"] = False
            return {"id": THREAD}
        if method == "PATCH" and len(parts) == 4:
            if state["archived"]:
                raise _refusal(*state["refusal"])
            writes.append((method, path, payload))
            fake.contents[parts[3]] = str((payload or {}).get("content", ""))
            return {"id": parts[3]}
        if method == "POST":
            writes.append((method, path, payload))
            state["archived"] = False  # a new post reopens an unlocked archived thread
        return original(method, path, payload)

    monkeypatch.setattr(mail.triage_confirm, "_api", api)
    monkeypatch.setattr(mail.triage_confirm, "_origin_notice", lambda: origin_notice)
    monkeypatch.setattr(mail.triage_confirm, "_dm_transport",
                        lambda channel: mail.triage_confirm._CardReply(channel, ""))

    def no_send(*_args, **_kwargs):
        raise AssertionError("marking a card must never send mail")

    monkeypatch.setattr(mail.triage_gate, "_run_send", no_send)
    stored = _post(_compose("시험 본문"))
    writes.clear()
    state["archived"] = True
    return fake, stored, writes, state


def test_a_card_in_an_archived_thread_is_still_marked_withdrawn(closed) -> None:
    fake, stored, writes, _state = closed
    assert mail.triage_cli.cmd_withdraw(
        argparse.Namespace(draft=stored["id"], reason=REASON, by="유돌")) == 0
    content = fake.contents[stored["message_id"]]
    assert content.startswith("⛔") and "유돌" in content and REASON in content
    thread_patches = [p for m, path, p in writes if m == "PATCH" and path == f"/channels/{THREAD}"]
    assert thread_patches == [{"archived": False}]


def test_another_refusal_does_not_reopen_and_names_the_discord_code(closed, capsys) -> None:
    fake, stored, writes, state = closed
    state["refusal"] = (400, 50035)
    before = fake.contents[stored["message_id"]]
    importlib.import_module("triage_supersede")._mark(THREAD, stored["message_id"])
    assert fake.contents[stored["message_id"]] == before
    assert writes == []
    err = capsys.readouterr().err
    assert "MAIL-SUPERSEDE-MARK-FAIL" in err and "status=400" in err and "code=50035" in err
