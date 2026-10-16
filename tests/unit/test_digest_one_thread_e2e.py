"""The owner's screen for one digest day, end to end through the real code (owner Q187).

A simulated Discord server stands in for the network: channels, threads, message replies,
names, archive state and reactions. Everything else is the shipped code: the mail digest,
the calendar CLI it hands events to, the ✍️ reaction tick, the mail watch tick that
resolves ⛔ and marks threads, both skills' reminder delivery, and a draft asked outside
the digest. ``transcript()`` prints what the owner sees; each assertion is one QA item of
the lane plan (Q1-Q7). All ids, names and addresses are synthetic.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import subprocess
import sys
from datetime import UTC, datetime
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))
sys.path.insert(0, str(_REPO / "skills" / "calendar" / "scripts"))

import calendar_cli  # noqa: E402
import calendar_confirm  # noqa: E402
import calendar_gate  # noqa: E402
import digest_reaction_actions  # noqa: E402
import triage_cli  # noqa: E402
import triage_confirm  # noqa: E402
import triage_core  # noqa: E402
import triage_digest  # noqa: E402
import triage_digest_thread  # noqa: E402
import triage_llm  # noqa: E402
import triage_mode  # noqa: E402
import triage_transport  # noqa: E402

OWNER = "1529100000000000001"
BOT = "1529100000000000002"
GUILD = "1529100000000000003"
AGENT_CHAT = "1529100000000000004"
DAY1 = datetime(2026, 10, 17, 8, 0, tzinfo=ZoneInfo("Asia/Seoul"))
DAY2 = datetime(2026, 10, 18, 8, 0, tzinfo=ZoneInfo("Asia/Seoul"))


def _calendar_watcher():
    """Calendar's own watcher module (coordination ships a file with the same name)."""
    import importlib.util

    path = _REPO / "skills" / "calendar" / "scripts" / "confirm_reaction_watch.py"
    spec = importlib.util.spec_from_file_location("calendar_confirm_reaction_watch_e2e", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class World:
    """A tiny Discord: what the bot posts, edits, renames and archives."""

    def __init__(self) -> None:
        self.next_id = 1529100000000001000
        self.channels: dict[str, dict] = {
            AGENT_CHAT: {"id": AGENT_CHAT, "guild_id": GUILD, "name": "agent-chat", "type": 0},
        }
        self.messages: dict[str, dict] = {}
        self.order: list[str] = []
        self.threads_created: list[str] = []

    def _id(self) -> str:
        self.next_id += 1
        return str(self.next_id)

    def _missing(self) -> HTTPError:
        return HTTPError("https://discord.invalid", 404, "missing", Message(), None)

    def __call__(self, method: str, path: str, payload: dict | None = None) -> object:
        bare = path.split("?", 1)[0]
        parts = bare.strip("/").split("/")
        payload = payload or {}
        if method == "POST" and bare == "/users/@me/channels":
            return {"id": AGENT_CHAT}
        if parts[0] == "guilds" and parts[-1] == "active":
            return {"threads": [c for c in self.channels.values() if c.get("parent_id") == AGENT_CHAT]}
        if parts[0] != "channels":
            raise AssertionError(f"unexpected call {method} {path}")
        channel = parts[1]
        if len(parts) == 2:
            if channel not in self.channels:
                raise self._missing()
            if method == "PATCH":
                self.channels[channel].update({k: v for k, v in payload.items() if k in {"name", "archived"}})
            return dict(self.channels[channel])
        if parts[2] == "threads" or (len(parts) == 5 and parts[4] == "threads"):
            thread_id = parts[3] if len(parts) == 5 else self._id()
            if thread_id in self.channels:
                raise HTTPError("https://discord.invalid", 400, "thread exists", Message(), None)
            self.channels[thread_id] = {"id": thread_id, "guild_id": GUILD, "name": payload["name"],
                                        "parent_id": channel, "type": 11, "archived": False}
            self.threads_created.append(thread_id)
            return dict(self.channels[thread_id])  # Discord answers with the whole channel
        if parts[2] == "messages" and len(parts) == 3:
            if method == "GET":
                return [dict(self.messages[m]) for m in self.order if self.messages[m]["channel_id"] == channel]
            message_id = self._id()
            reference = (payload.get("message_reference") or {}).get("message_id", "")
            self.messages[message_id] = {"id": message_id, "channel_id": channel, "content": payload.get("content", ""),
                                         "reply_to": reference, "reactions": {}}
            self.order.append(message_id)
            return {"id": message_id}
        message = self.messages.get(parts[3])
        if message is None:
            raise self._missing()
        if len(parts) == 4:
            if method == "PATCH":
                message["content"] = payload["content"]
                return dict(message)
            if method == "DELETE":
                self.order.remove(parts[3])
                del self.messages[parts[3]]
                return None
            return {"id": message["id"], "content": message["content"], "reactions": [
                {"emoji": {"name": emoji}, "count": len(users), "me": BOT in users}
                for emoji, users in message["reactions"].items()]}
        emoji = unquote(parts[5])
        if method == "PUT":
            message["reactions"].setdefault(emoji, []).append(BOT)
            return None
        return [{"id": user, "bot": user == BOT} for user in message["reactions"].get(emoji, [])]

    def react(self, message_id: str, emoji: str) -> None:
        self.messages[message_id]["reactions"].setdefault(emoji, []).append(OWNER)

    def in_channel(self, channel: str) -> list[dict]:
        return [self.messages[m] for m in self.order if self.messages[m]["channel_id"] == channel]

    def transcript(self) -> str:
        lines = []
        for message in self.in_channel(AGENT_CHAT):
            lines.append(f"[#agent-chat] {message['content'].splitlines()[0]}")
            thread = self.channels.get(message["id"])
            if thread is None:
                continue
            state = " (archived)" if thread.get("archived") else ""
            lines.append(f"  └ thread «{thread['name']}»{state}")
            for item in self.in_channel(message["id"]):
                head = item["content"].splitlines()[0] if item["content"] else "(empty)"
                under = f" ↳ reply to {item['reply_to'][-4:]}" if item["reply_to"] else ""
                emoji = "".join(e for e in item["reactions"])
                lines.append(f"     {item['id'][-4:]}{under}: {head}  {emoji}".rstrip())
        return "\n".join(lines)


class _Transport:
    def __init__(self, sim: World, channel: str) -> None:
        self.sim, self.channel = sim, channel

    def send(self, body: str) -> tuple[object, ...]:
        from types import SimpleNamespace

        posted = self.sim("POST", f"/channels/{self.channel}/messages", {"content": body})
        return (SimpleNamespace(message_id=posted["id"]),)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    sim = World()
    config = tmp_path / "interop.json"
    config.write_text(json.dumps({"agent_chat_channel_id": AGENT_CHAT, "owner_id": OWNER}), encoding="utf-8")
    for key, value in {
        "INTEROP_CONFIG": str(config), "DISCORD_BOT_TOKEN": "DUMMY-token", "AUTOPHAGY_REPO_ROOT": str(_REPO),
        "AUTOPHAGY_DIGEST_THREAD_DIR": str(tmp_path / "digest-threads"),
        "TRIAGE_GATE_DIR": str(tmp_path / "mail-gate"), "TRIAGE_DB": str(tmp_path / "mail.db"),
        "CALENDAR_GATE_DIR": str(tmp_path / "cal-gate"), "CALENDAR_PENDING_CONFIRMS": str(tmp_path / "pending.jsonl"),
        "CALENDAR_APPROVAL_LOG": str(tmp_path / "approvals.jsonl"), "CALENDAR_GWS_BIN": "fixture-gws",
        "CALENDAR_PEERS_FILE": str(tmp_path / "absent-peers.yaml"),
        "TRIAGE_CALENDAR_CLI": str(_REPO / "skills/calendar/scripts/calendar_cli.py"),
        "TRIAGE_LLM_LOG": str(tmp_path / "llm.jsonl"),
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("E2E_TEST_MODE", raising=False)
    import urllib.request

    def offline(*_a, **_k):
        raise AssertionError("a path left the simulated Discord")

    monkeypatch.setattr(urllib.request, "urlopen", offline)
    monkeypatch.setattr(triage_confirm, "urlopen", offline, raising=False)
    monkeypatch.setattr(triage_confirm, "_dm_transport", lambda channel: _Transport(sim, channel))
    monkeypatch.setattr(triage_confirm, "_api", sim)
    monkeypatch.setattr(calendar_confirm, "_api", sim)
    monkeypatch.setattr(calendar_confirm, "owner_id", lambda: OWNER)
    monkeypatch.setattr(triage_mode, "effective_mode", lambda: "full-go")
    monkeypatch.setattr(triage_llm, "summarize", lambda **_kw: "합성 요약")
    monkeypatch.setattr(triage_llm, "draft_reply", lambda **_kw: ("Re: 합성", "합성 회신 본문", "fixture"))
    return sim


def _mails(*rows: tuple[str, str, bool]) -> list[dict]:
    return [{"uid": uid, "subject": subject, "sender": "가상발신 <sender@example.invalid>",
             "date": "2026-10-16T23:00:00Z",
             "body": '---\nto: "owner@example.invalid"\n---\n\n' + subject} for uid, subject, _ in rows]


def _digest(monkeypatch: pytest.MonkeyPatch, now: datetime, mails: list[dict], schedule: dict[str, str]) -> None:
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)

    monkeypatch.setattr(triage_digest, "datetime", Clock)
    monkeypatch.setattr(calendar_cli, "datetime", Clock)
    monkeypatch.setattr(triage_transport, "_list_mails", lambda *_a: mails)
    monkeypatch.setattr(triage_transport, "_get_mail", lambda uid: next(m for m in mails if m["uid"] == uid))
    monkeypatch.setattr(triage_cli, "_get_mail", lambda uid: next(m for m in mails if m["uid"] == uid))
    monkeypatch.setattr(triage_transport, "_sent_mails", lambda *_a: [])
    monkeypatch.setattr(triage_llm, "classify", lambda **kw: (triage_core.Classification(
        "important", kw["subject"] not in schedule.values(), kw["subject"] in schedule.values(), False,
        next((text for text, subject in schedule.items() if subject == kw["subject"]), ""), "fixture"), "fixture"))
    assert triage_digest.run_digest(limit=20, sync=False, dry_run=False) == 0


@pytest.fixture(autouse=True)
def _calendar_in_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """The calendar CLI runs in this process with the env the mail digest gave it."""
    def run(argv, **kwargs):
        if argv[0] == "fixture-gws":
            return subprocess.CompletedProcess(argv, 0, "{}", "")
        assert Path(argv[1]).name == "calendar_cli.py", argv
        out, err = io.StringIO(), io.StringIO()
        with monkeypatch.context() as child, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            for key in ("CALENDAR_DIGEST_REPLY_TO", "CALENDAR_DIGEST_REPLY_CHANNEL"):
                child.setenv(key, (kwargs.get("env") or {}).get(key, ""))
            child.setattr(sys, "argv", argv[1:])
            rc = calendar_cli.main()
        return subprocess.CompletedProcess(argv, rc, out.getvalue(), err.getvalue())

    monkeypatch.setattr(triage_transport.subprocess, "run", run)


def test_one_digest_day_on_the_owners_screen(world: World, monkeypatch: pytest.MonkeyPatch,
                                            capsys: pytest.CaptureFixture[str]) -> None:
    schedule = {"10월 20일 오후 3시 합성 검토 회의": "합성 검토 회의 참석 요청"}
    _digest(monkeypatch, DAY1, _mails(("u-1", "합성 의견 요청", False), ("u-2", "합성 검토 회의 참석 요청", True)),
            schedule)

    parent = world.in_channel(AGENT_CHAT)[0]
    thread = world.channels[parent["id"]]
    inside = world.in_channel(thread["id"])
    cards = [m for m in inside if m["content"].startswith("### ")]
    calendar_card = next(m for m in inside if "캘린더 변경" in m["content"])
    # Q1: same shape as every approval thread.
    assert parent["content"].splitlines()[0] == "## 📬 기관메일 · 다이제스트 10-17 08:00 · 2건"
    assert "🔔 승인 대기" in parent["content"] and "대상:" not in parent["content"]
    assert thread["name"] == "📬 기관메일 · 다이제스트 10-17 08:00 · 2건"
    assert "✅" not in inside[0]["content"] and "신규 2건" in inside[0]["content"]
    # Q3: the event card is in the same thread, under its mail card, which links it.
    assert calendar_card["reply_to"] == cards[1]["id"]
    assert "[승인 카드](" in world.messages[cards[1]["id"]]["content"]

    # Q2: ✍️ on mail 1 -> the reply card stands under it in the same thread, no new thread.
    world.react(cards[0]["id"], "✍️")
    threads_before = list(world.threads_created)
    assert digest_reaction_actions.run_tick(now=datetime(2026, 10, 16, 23, 5, tzinfo=UTC)) == 1  # one action ran
    reply_card = next(m for m in world.in_channel(thread["id"]) if "sha256:" in m["content"] and m is not calendar_card)
    assert reply_card["reply_to"] == cards[0]["id"]
    preview = next(m for m in world.in_channel(thread["id"]) if m["content"].startswith("✍️"))
    assert preview["reply_to"] == cards[0]["id"]
    assert world.threads_created == threads_before

    # Q4: ⛔ on the reply card -> result under it, the thread stays open and gets the 🔔 mark.
    world.react(reply_card["id"], "⛔")
    triage_cli.cmd_watch(argparse.Namespace())
    result = [m for m in world.in_channel(thread["id"]) if m["reply_to"] == reply_card["id"]]
    assert result and not thread.get("archived")
    assert thread["name"].startswith("🔔 ")

    # Q6: a mail reminder and a calendar reminder at the same moment -> one message, two lines.
    triage_digest_thread.deliver_reminder(thread["id"], "⏰ 승인 대기 3시간 · 메일 카드")
    watcher = _calendar_watcher()
    watcher._deliver_reminder(watcher.DiscordApi(OWNER), thread["id"],
                                            "⏰ 승인 대기 3시간 · 캘린더 카드")
    reminders = [m for m in world.in_channel(thread["id"]) if m["content"].startswith("⏰")]
    assert len(reminders) == 1 and len(reminders[0]["content"].splitlines()) == 2

    # Q5: next day's digest opens; once the event card is decided the old thread closes as done.
    _digest(monkeypatch, DAY2, _mails(("u-3", "합성 공지", False)), {})
    triage_cli.cmd_watch(argparse.Namespace())
    assert thread["name"].startswith("🔔 ")  # the event card still waits
    entry_draft = next(p.stem for p in (Path(calendar_gate._gate_dir()) / "drafts").glob("*.json"))
    with monkeypatch.context() as child:
        child.setattr(sys, "argv", ["calendar_cli.py", "discard", "--draft", entry_draft])
        calendar_cli.main()
    triage_cli.cmd_watch(argparse.Namespace())
    assert thread["name"] == "✅ 완료 · 📬 기관메일 · 다이제스트 10-17 08:00 · 2건" and thread["archived"] is True

    # Q7: a reply asked outside the digest still gets its own request thread.
    monkeypatch.setattr(triage_cli, "_get_mail", lambda uid: _mails(("u-9", "합성 별건", False))[0])
    created = len(world.threads_created)
    triage_cli.cmd_draft(argparse.Namespace(uid="u-9", instruction="합성 회신", attachment=[], no_post=False,
                                            with_evidence=False, reply_all=False))
    assert len(world.threads_created) == created + 1

    with capsys.disabled():
        print("\n" + world.transcript())
