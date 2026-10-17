"""Digest card 📄/✍️ reactions (2026-10-16 owner request).

The bot pre-attaches both emoji to every mail card; the watch tick acts only on
the owner's reaction, once per card and action. 📄 posts the full text split
under Discord's limit; ✍️ writes a draft through the existing draft gate and
never sends it. All names, ids and addresses are synthetic.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))

import digest_reaction_actions as actions  # noqa: E402
import mail_preflight  # noqa: E402
import triage_approval  # noqa: E402
import triage_cli  # noqa: E402
import triage_confirm  # noqa: E402
import triage_core  # noqa: E402
import triage_digest  # noqa: E402
import triage_llm  # noqa: E402
import triage_mode  # noqa: E402
import triage_pipeline  # noqa: E402
import triage_store  # noqa: E402
import triage_transport  # noqa: E402

OWNER_ID = "100000000000000001"
BOT = {"id": "200000000000000002", "bot": True}
OWNER = {"id": OWNER_ID}
STRANGER = {"id": "300000000000000003"}
_NOW = datetime(2026, 10, 16, 1, 0, tzinfo=UTC)
_BODY = (
    '---\nuid: "u-1"\nsubject: "합성 제목"\nfrom: "sender@example.invalid"\n'
    'to: "owner@example.invalid"\ndate: "2026-10-15T23:00:00"\n'
    'attachments:\n  - "계획서.pdf"\n  - "예산 표.xlsx"\n---\n\n'
)


def _mail(uid: str, text: str = "안녕하세요. 합성 본문입니다.") -> dict:
    return {
        "uid": uid, "subject": "합성 제목", "sender": "가상발신 <sender@example.invalid>",
        "date": "2026-10-15T23:00:00Z", "body": _BODY + text,
    }


def _seed(db: Path, uid: str = "u-1") -> None:
    triage_store.record_digest_run(db, "2026-10-15T23:00:00Z", [{
        "item_no": 1, "uid": uid, "subject": "합성 제목", "sender_masked": "h",
        "category": "important", "flags": "", "summary": "", "note": "", "recv_date": "",
        "reply_key": "1016-0800-1", "message_id": "card-1", "channel_id": "thread-1",
    }])


def _discord(
    monkeypatch: pytest.MonkeyPatch, users: dict[str, list[dict]],
) -> list[tuple[str, str]]:
    """Fake Discord: card reactions from ``users`` (emoji → reactors incl. the bot)."""
    def api(method: str, path: str, _payload: object = None) -> object:
        assert (method, path) == ("GET", "/channels/thread-1/messages/card-1")
        return {"id": "card-1", "reactions": [
            {"emoji": {"name": emoji}, "count": len(who), "me": BOT in who}
            for emoji, who in users.items()
        ]}

    posted: list[tuple[str, str]] = []
    monkeypatch.setattr(triage_confirm, "_api", api)
    monkeypatch.setattr(
        triage_confirm, "_reaction_users", lambda _c, _m, emoji: users[emoji],
    )
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER_ID)
    monkeypatch.setattr(
        triage_confirm, "post_in",
        lambda channel, body, reply_to="": posted.append((channel, body)) or f"m-{len(posted)}",
    )
    return posted


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRIAGE_DB", str(tmp_path / "triage.db"))
    monkeypatch.setenv("TRIAGE_GATE_DIR", str(tmp_path / "gate"))
    path = tmp_path / "triage.db"
    _seed(path)
    return path


def test_a_reaction_from_anyone_but_the_owner_does_nothing(
    db: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    posted = _discord(monkeypatch, {
        actions.BODY_EMOJI: [BOT, STRANGER], actions.DRAFT_EMOJI: [BOT, {**OWNER, "bot": True}],
    })
    monkeypatch.setattr(triage_transport, "_get_mail", lambda _uid: pytest.fail("read mail"))

    assert actions.run_tick(now=_NOW) == 0
    assert posted == []


def test_the_same_owner_reaction_seen_on_every_tick_posts_the_body_once(
    db: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    posted = _discord(monkeypatch, {actions.BODY_EMOJI: [BOT, OWNER], actions.DRAFT_EMOJI: [BOT]})
    monkeypatch.setattr(triage_transport, "_get_mail", _mail)

    first = actions.run_tick(now=_NOW)
    after_first = list(posted)
    second = actions.run_tick(now=_NOW)

    assert (first, second) == (1, 0)
    assert posted == after_first and len(posted) == 2
    assert posted[0][0] == "thread-1" and posted[0][1].startswith("📄 **전문** · 합성 제목")
    assert "합성 본문입니다" in posted[0][1]
    assert posted[1][1] == "📎 첨부 2건 (이름만)\n- 계획서.pdf\n- 예산 표.xlsx"


def test_a_failed_read_is_retried_later_and_capped(
    db: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    posted = _discord(monkeypatch, {actions.BODY_EMOJI: [BOT, OWNER]})
    calls: list[str] = []

    def flaky(uid: str) -> dict:
        calls.append(uid)
        raise OSError("wrapper down")

    monkeypatch.setattr(triage_transport, "_get_mail", flaky)

    runs = [actions.run_tick(now=_NOW) for _ in range(actions.MAX_ATTEMPTS + 2)]

    assert runs == [0] * (actions.MAX_ATTEMPTS + 2)
    assert len(calls) == actions.MAX_ATTEMPTS
    assert len(posted) == 1 and posted[0][1].startswith("⚠️ 📄 전문을 3번 시도했지만")


def test_cards_of_an_old_digest_are_not_read(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(triage_confirm, "_api", lambda *_a: pytest.fail("read an old card"))

    assert actions.run_tick(now=datetime(2026, 10, 30, tzinfo=UTC)) == 0


def test_a_long_body_is_split_under_the_message_limit_without_losing_text() -> None:
    paragraphs = [f"{n}번째 문단. " + "가나다라마바사 " * 40 for n in range(30)]

    parts = actions.body_parts(_mail("u-1", "\n\n".join(paragraphs)), "합성 제목")

    assert len(parts) > 3
    assert all(len(part) <= 2000 for part in parts)
    joined = "".join(parts)
    assert all(f"{n}번째 문단." in joined for n in range(30))
    assert parts[-1].startswith("📎 첨부 2건")


def test_mail_text_cannot_ping_the_channel() -> None:
    parts = actions.body_parts(_mail("u-1", "@everyone 확인 바랍니다"), "@here 제목")

    assert "@everyone" not in parts[0] and "@here" not in parts[0]


def test_the_draft_reaction_writes_a_draft_that_the_watch_tick_does_not_send(
    tmp_path: Path, db: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    posted = _discord(monkeypatch, {actions.DRAFT_EMOJI: [BOT, OWNER]})
    monkeypatch.setenv("TRIAGE_MAIL_HOME", str(tmp_path / "mail"))
    monkeypatch.setenv("TRIAGE_LLM_LOG", str(tmp_path / "llm-calls.jsonl"))
    monkeypatch.setenv("MAILON_ID", "owner@example.invalid")
    hermes = tmp_path / "hermes-stub"
    hermes.write_text(
        "#!/usr/bin/env python3\n"
        "print('{\"subject\": \"Re: 합성 제목\", \"body\": \"확인했습니다.\"}')\n",
        encoding="utf-8",
    )
    hermes.chmod(0o755)
    monkeypatch.setenv("AUTOPHAGY_HERMES_BIN", str(hermes))
    monkeypatch.setattr(triage_mode, "effective_mode", lambda: "full-go")
    monkeypatch.setattr(triage_cli, "_get_mail", _mail)
    monkeypatch.setattr(triage_llm, "classify", lambda **_k: (triage_core.Classification(
        category="important", reply_needed=True, schedule_needed=False, budget=False,
        schedule_text="", reason="synthetic"), "stub"))
    cards: list[str] = []
    monkeypatch.setattr(
        triage_pipeline, "_post_draft_for_approval",
        lambda draft, **_k: cards.append(draft["id"]) or "approval-card-1",
    )
    monkeypatch.setattr(triage_approval, "expire_retired_approval", lambda _d: False)
    monkeypatch.setattr(triage_cli, "_remind_pending", lambda *_a: None)
    monkeypatch.setattr(triage_confirm, "resolve_reaction", lambda _d: None)
    monkeypatch.setattr(
        mail_preflight, "execute_cli_draft", lambda *_a: pytest.fail("a draft was sent"),
    )

    assert triage_cli.cmd_watch(argparse.Namespace()) == 0
    assert triage_cli.cmd_watch(argparse.Namespace()) == 0

    records = [json.loads(p.read_text(encoding="utf-8"))
               for p in (tmp_path / "gate" / "drafts").glob("*.json")]
    assert [(r["uid"], r["status"]) for r in records] == [("u-1", "pending")]
    assert cards[0] == records[0]["id"]
    replies = [body for channel, body in posted if channel == "thread-1"]
    assert replies[0] == actions.DRAFT_ACK  # said once before the model call
    previews = replies[1:]
    assert len(previews) == 1
    assert previews[0].startswith(f"✍️ **회신 초안을 썼습니다** · draft `{records[0]['id']}`")
    assert "> 확인했습니다." in previews[0] and "아직 보내지 않았습니다" in previews[0]


def test_the_digest_attaches_both_emoji_to_each_card_and_records_its_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = tmp_path / "digest.db"
    monkeypatch.setenv("TRIAGE_DB", str(db))
    monkeypatch.setenv("MAILON_ID", "owner@example.invalid")
    monkeypatch.setattr(triage_llm, "classify", lambda **_k: (triage_core.Classification(
        category="normal", reply_needed=False, schedule_needed=False, budget=False,
        schedule_text="", reason="synthetic"), "stub"))
    monkeypatch.setattr(triage_llm, "summarize", lambda **_k: "요약")
    monkeypatch.setattr(triage_transport, "_list_mails", lambda _l, _s: [
        {"uid": "u-1", "date": "1"}, {"uid": "u-2", "date": "2"}])
    monkeypatch.setattr(triage_transport, "_get_mail", _mail)
    monkeypatch.setattr(triage_confirm, "dm_owner", lambda _body: "head")
    monkeypatch.setattr(triage_confirm, "digest_thread", lambda _a, _n: "thread-9")
    monkeypatch.setattr(triage_confirm, "post_in", lambda _t, _b: f"card-{_b[4]}")
    reacted: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        triage_confirm, "add_reaction",
        lambda message, emoji, channel: reacted.append((channel, message, emoji)),
    )

    assert triage_digest.run_digest(limit=10, sync=False, dry_run=False) == 0

    assert reacted == [
        ("thread-9", "card-1", actions.BODY_EMOJI), ("thread-9", "card-1", actions.DRAFT_EMOJI),
        ("thread-9", "card-2", actions.BODY_EMOJI), ("thread-9", "card-2", actions.DRAFT_EMOJI),
    ]
    cards = triage_store.recent_digest_cards(db, since="2000-01-01T00:00:00Z")
    assert [(c["message_id"], c["channel_id"]) for c in cards] == [
        ("card-1", "thread-9"), ("card-2", "thread-9")]


def test_a_failed_emoji_attach_never_fails_the_delivered_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("TRIAGE_DB", str(tmp_path / "digest.db"))
    monkeypatch.setenv("MAILON_ID", "owner@example.invalid")
    monkeypatch.setattr(triage_llm, "classify", lambda **_k: (triage_core.Classification(
        category="normal", reply_needed=False, schedule_needed=False, budget=False,
        schedule_text="", reason="synthetic"), "stub"))
    monkeypatch.setattr(triage_llm, "summarize", lambda **_k: "요약")
    monkeypatch.setattr(triage_transport, "_list_mails", lambda _l, _s: [{"uid": "u-1", "date": "1"}])
    monkeypatch.setattr(triage_transport, "_get_mail", _mail)
    monkeypatch.setattr(triage_confirm, "dm_owner", lambda _body: "head")
    monkeypatch.setattr(triage_confirm, "digest_thread", lambda _a, _n: "thread-9")
    monkeypatch.setattr(triage_confirm, "post_in", lambda _t, _b: "card-1")

    def refuse(*_a: object) -> None:
        raise OSError("rate limited")

    monkeypatch.setattr(triage_confirm, "add_reaction", refuse)

    assert triage_digest.run_digest(limit=10, sync=False, dry_run=False) == 0
    assert "DIGEST-REACTION-ATTACH-FAIL msg=card-1 err=OSError" in capsys.readouterr().err


def test_a_press_heard_by_the_gateway_runs_that_card_once_and_the_tick_does_not_repeat_it(
    db: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    posted = _discord(monkeypatch, {actions.BODY_EMOJI: [BOT, OWNER], actions.DRAFT_EMOJI: [BOT]})
    monkeypatch.setattr(triage_transport, "_get_mail", _mail)

    assert actions.run_card("card-1", now=_NOW) == 1
    assert actions.run_card("card-1", now=_NOW) == 0
    assert actions.run_tick(now=_NOW) == 0
    assert len(posted) == 2 and posted[0][1].startswith("📄 **전문** · 합성 제목")


def test_a_press_on_a_message_that_is_not_a_digest_card_reads_nothing(
    db: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(triage_confirm, "_api", lambda *_a: pytest.fail("read Discord"))

    assert actions.run_card("not-a-card", now=_NOW) == 0


@pytest.mark.parametrize(("mode", "ran"), [("full-go", ["card-7"]), ("no-go", [])])
def test_digest_react_stops_when_mail_is_no_go(
    monkeypatch: pytest.MonkeyPatch, mode: str, ran: list[str],
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(triage_mode, "effective_mode", lambda: mode)
    monkeypatch.setattr(actions, "run_card", lambda message_id: calls.append(message_id) or 1)

    args = triage_cli.build_parser().parse_args(["digest-react", "--message-id", "card-7"])

    assert args.func(args) == 0
    assert calls == ran


def test_the_watch_wrapper_runs_one_card_without_touching_the_failure_streak(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    import subprocess

    import mail_triage_watch as wrapper

    seen: list[list[str]] = []
    monkeypatch.setattr(wrapper, "CLI", tmp_path / "triage_cli.py")
    (tmp_path / "triage_cli.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(wrapper, "_announce", lambda **_k: pytest.fail("streak touched"))
    monkeypatch.setattr(subprocess, "run", lambda argv, **_k: seen.append(argv)
                        or subprocess.CompletedProcess(argv, 0, "", ""))
    monkeypatch.setattr(sys, "argv", ["mail_triage_watch.py", "--digest-card", "card-7"])

    assert wrapper.main() == 0
    assert seen[0][1:] == [str(tmp_path / "triage_cli.py"), "digest-react", "--message-id", "card-7"]
