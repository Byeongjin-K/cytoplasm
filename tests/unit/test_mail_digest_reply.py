"""Digest readability (2026-10-08) and per-mail reply requests.

Separate file because ``test_mail_digest.py`` already holds ~1000 lines of the
older card contract; these pin the repaired shape: names decoded once, Cc
collapsed, one Discord message per mail, and ``reply-request`` reusing the
existing draft gate. All names/addresses are synthetic.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))

import mail_contacts  # noqa: E402
import triage_cli  # noqa: E402
import triage_confirm  # noqa: E402
import triage_core  # noqa: E402
import triage_digest  # noqa: E402
import triage_llm  # noqa: E402
import triage_mode  # noqa: E402
import triage_store  # noqa: E402
import triage_transport  # noqa: E402

_KST_NOW = datetime(2026, 10, 8, 8, 0, tzinfo=ZoneInfo("Asia/Seoul"))
OWNER = "owner@example.invalid"
OWNER_ID = "100000000000000001"
# Shapes copied from the node's mailon frontmatter (anonymized): the webmail already
# wraps names in escaped quotes, and YAML escapes them once more.
_CC_YAML = (
    r'cc: "\"one@example.invalid\" <one@example.invalid>, '
    r'\"\\\"가상일\\\"\" <a1@example.invalid>, \"가상이\" <a2@example.invalid>, '
    r'\"가상삼\" <a3@example.invalid>, \"가상사\" <a4@example.invalid>, '
    r'\"owner@example.invalid\" <owner@example.invalid>"'
)
_BODY = f'---\nuid: "u-1"\nto: "{OWNER}"\n{_CC_YAML}\n---\n\nSynthetic body'
_SENDER = '"\\"가상발신\\"" <sender@example.invalid>'


def _stub_llm(monkeypatch: pytest.MonkeyPatch, summary: str = "합성 요약") -> None:
    def classify(**_kwargs: object) -> tuple[triage_core.Classification, str]:
        return triage_core.Classification(
            category="important", reply_needed=True, schedule_needed=False,
            budget=False, schedule_text="", reason="synthetic",
        ), "stub"

    monkeypatch.setenv("MAILON_ID", OWNER)
    monkeypatch.setattr(triage_llm, "classify", classify)
    monkeypatch.setattr(triage_llm, "summarize", lambda **_kwargs: summary)


def _detail(uid: str, subject: str = "합성 제목") -> dict:
    return {
        "uid": uid, "subject": subject, "sender": _SENDER, "body": _BODY,
        "date": "2026-10-07T23:10:00Z",
    }


def test_double_escaped_names_decode_once_and_cc_collapses() -> None:
    contacts = mail_contacts.parse_contacts(mail_contacts.yaml_scalar(_CC_YAML[4:]))

    assert [name for name, _ in contacts] == ["", "가상일", "가상이", "가상삼", "가상사", ""]
    assert mail_contacts.sender_label(_SENDER) == "가상발신"
    assert mail_contacts.cc_label(
        mail_contacts.yaml_scalar(_CC_YAML[4:]), owner=OWNER,
    ) == "one@example.invalid, 가상일, 가상이 외 2명"


def test_rfc2047_and_bare_addresses_are_readable() -> None:
    encoded = "=?UTF-8?B?6rCA7IOB67Cc7Iug?= <enc@example.invalid>, bare@example.invalid"

    assert mail_contacts.cc_label(encoded) == "가상발신, bare@example.invalid"


def test_card_shows_names_only_without_internal_identifiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_llm(monkeypatch)
    dm_item, _store = triage_digest.build_item(_detail("u-1"), 3)

    card = triage_digest.render_item(dm_item, kst_now=_KST_NOW)

    assert card == (
        "### 3. 합성 제목\n"
        "-# 📄 누르면 전문 · ✍️ 누르면 회신 초안 · ↩️ 답장하면 지시대로 초안 · "
        "회신 키 `1008-0800-3`\n"
        "🔴 중요 · ↩️ 회신 필요\n"
        "> 요약 · 합성 요약\n"
        "수신 10-08 08:10 · 발신 가상발신\n"
        "참조 one@\u200bexample.invalid, 가상일, 가상이 외 2명"
    )
    assert "\\" not in card and "UID" not in card and "sha256" not in card


def test_long_digest_splits_on_mail_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_llm(monkeypatch, summary="긴 요약 " * 400)
    items = [triage_digest.build_item(_detail(f"u-{n}"), n)[0] for n in range(1, 13)]

    parts = triage_digest.render_digest_parts(items, kst_now=_KST_NOW)

    assert len(parts) == 13
    assert all(len(part) <= 2000 for part in parts)
    assert [part.count("\n### ") + part.startswith("### ") for part in parts[1:]] == [1] * 12
    assert "답장(Reply)" in parts[0]


def test_run_digest_sends_one_message_per_mail_and_records_reply_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_llm(monkeypatch)
    db = tmp_path / "triage.db"
    monkeypatch.setenv("TRIAGE_DB", str(db))
    mails = [{"uid": "u-1", "date": "1"}, {"uid": "u-2", "date": "2"}]
    monkeypatch.setattr(triage_transport, "_list_mails", lambda _limit, _sync: mails)
    monkeypatch.setattr(triage_transport, "_get_mail", _detail)
    monkeypatch.setattr(triage_digest, "datetime", _FrozenDatetime)
    channel: list[str] = []
    threaded: list[tuple[str, str]] = []
    monkeypatch.setattr(triage_confirm, "dm_owner", lambda body: channel.append(body) or "head")
    monkeypatch.setattr(
        triage_confirm, "digest_thread",
        lambda anchor, name: "thread-1" if (anchor, name) == ("head", "📬 기관메일 다이제스트 10-08 08:00 · 2건") else "",
    )
    monkeypatch.setattr(
        triage_confirm, "post_in",
        lambda thread, body: threaded.append((thread, body)) or f"m-{len(threaded)}",
    )

    assert triage_digest.run_digest(limit=10, sync=False, dry_run=False) == 0

    # Then: one header message in the channel, every mail inside the thread hung on it.
    assert len(channel) == 1 and channel[0].startswith("## 📬")
    assert [(thread, body.split("\n")[0]) for thread, body in threaded] == [
        ("thread-1", "### 1. 합성 제목"), ("thread-1", "### 2. 합성 제목"),
    ]
    assert triage_store.digest_item_by_reply_key(db, "1008-0800-2") == {
        "run_id": 1, "item_no": 2, "uid": "u-2", "message_id": "m-2",
    }


def test_run_digest_posts_flat_when_no_thread_can_be_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_llm(monkeypatch)
    monkeypatch.setenv("TRIAGE_DB", str(tmp_path / "triage.db"))
    monkeypatch.setattr(triage_transport, "_list_mails", lambda _l, _s: [{"uid": "u-1", "date": "1"}])
    monkeypatch.setattr(triage_transport, "_get_mail", _detail)
    channel: list[str] = []
    monkeypatch.setattr(triage_confirm, "dm_owner", lambda body: channel.append(body) or "m")
    monkeypatch.setattr(triage_confirm, "digest_thread", lambda _anchor, _name: "")
    monkeypatch.setattr(triage_confirm, "post_in", lambda *_a: pytest.fail("no thread"))

    assert triage_digest.run_digest(limit=10, sync=False, dry_run=False) == 0
    assert len(channel) == 2 and channel[1].startswith("### 1.")


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz: object = None) -> datetime:  # noqa: ARG003
        return _KST_NOW


def test_old_database_gains_reply_columns(tmp_path: Path) -> None:
    import sqlite3

    db = tmp_path / "old.db"
    with sqlite3.connect(db) as connection:
        connection.execute(
            "CREATE TABLE digest_items (run_id INTEGER NOT NULL, item_no INTEGER NOT NULL, "
            "uid TEXT NOT NULL, subject TEXT NOT NULL, sender_masked TEXT NOT NULL, "
            "sensitive INTEGER NOT NULL, category TEXT NOT NULL, flags TEXT NOT NULL, "
            "summary TEXT NOT NULL, note TEXT NOT NULL, recv_date TEXT NOT NULL, "
            "PRIMARY KEY (run_id, item_no))"
        )
        connection.execute(
            "INSERT INTO digest_items VALUES (1, 1, 'u-old', 's', 'h', 0, 'normal', '', '', '', '')"
        )

    assert triage_store.digest_item_by_reply_key(db, "1008-0800-1") is None
    assert triage_store.latest_digest_items(db) == []


def _reply_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRIAGE_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("TRIAGE_DB", str(tmp_path / "triage.db"))
    monkeypatch.setenv("TRIAGE_MAIL_HOME", str(tmp_path / "mail"))
    monkeypatch.setenv("TRIAGE_LLM_LOG", str(tmp_path / "llm-calls.jsonl"))
    monkeypatch.setenv("TRIAGE_MAILON_PYTHON", "python3")
    monkeypatch.setattr(triage_mode, "effective_mode", lambda: "full-go")
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER_ID)
    monkeypatch.setattr(triage_cli, "_get_mail", lambda uid: _detail(uid))
    _stub_llm(monkeypatch)
    hermes = tmp_path / "hermes-stub"
    hermes.write_text(
        "#!/usr/bin/env python3\n"
        "print('{\"subject\": \"Re: x\", \"body\": \"확인했습니다.\"}')\n",
        encoding="utf-8",
    )
    hermes.chmod(0o755)
    monkeypatch.setenv("AUTOPHAGY_HERMES_BIN", str(hermes))
    triage_store.record_digest_run(tmp_path / "triage.db", "2026-10-07T23:00:00Z", [{
        "item_no": 3, "uid": "u-3", "subject": "s", "sender_masked": "h", "sensitive": 0,
        "category": "important", "flags": "", "summary": "", "note": "", "recv_date": "",
        "reply_key": "1008-0800-3", "message_id": "m-3",
    }])
    return tmp_path / "gate" / "drafts"


def _reply(monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["triage_cli", "reply-request", *argv])
    return triage_cli.main()


def test_reply_request_refuses_non_owner_and_unknown_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    drafts = _reply_env(tmp_path, monkeypatch)

    stranger = _reply(monkeypatch, "--key", "1008-0800-3", "--author-id", "999",
                      "--instruction", "x", "--no-post")
    unknown = _reply(monkeypatch, "--key", "0101-0000-9", "--author-id", OWNER_ID,
                     "--instruction", "x", "--no-post")

    err = capsys.readouterr().err
    assert (stranger, unknown) == (2, 3)
    assert "REPLY-REQUEST-IGNORED not-owner" in err and "REPLY-REQUEST-UNKNOWN" in err
    assert not drafts.exists() or list(drafts.glob("*.json")) == []


def test_reply_request_drafts_once_per_mail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    drafts = _reply_env(tmp_path, monkeypatch)
    args = ("--key", "1008-0800-3", "--author-id", OWNER_ID,
            "--instruction", "참석 가능하다고 회신", "--no-post")

    first = _reply(monkeypatch, *args)
    out = capsys.readouterr().out
    second = _reply(monkeypatch, *args)

    assert first == 0 and "REPLY-REQUEST key=1008-0800-3 item=3" in out and "DRAFTED draft=" in out
    records = [json.loads(path.read_text(encoding="utf-8")) for path in drafts.glob("*.json")]
    assert [record["uid"] for record in records] == ["u-3"]
    assert second == 2 and "초안이 이미 있음" in capsys.readouterr().err
