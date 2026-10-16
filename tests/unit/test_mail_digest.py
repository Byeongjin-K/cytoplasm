from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))

from skills.mail.scripts import triage_store  # noqa: E402

import triage_core  # noqa: E402
import triage_cli  # noqa: E402
import triage_confirm  # noqa: E402
import triage_digest  # noqa: E402
import triage_gate  # noqa: E402
import triage_llm  # noqa: E402
import triage_mode  # noqa: E402
import triage_transport  # noqa: E402

CANARY = "PSEUDOSECRET-cafe0123"  # synthetic; synthetic body marker


def test_record_digest_run_and_items_round_trip(tmp_path: Path) -> None:
    db = tmp_path / "triage.db"
    items = [
        {
            "item_no": 2,
            "uid": "uid-2",
            "subject": "Synthetic second",
            "sender_masked": "sha256:sender-2",

            "category": "normal",
            "flags": "",
            "summary": "Second summary",
            "note": "Second note",
            "recv_date": "2026-07-18T09:02:00Z",
        },
        {
            "item_no": 1,
            "uid": "uid-1",
            "subject": "Synthetic first",
            "sender_masked": "sha256:sender-1",

            "category": "important",
            "flags": "reply_needed",
            "summary": "First summary",
            "note": "First note",
            "recv_date": "2026-07-18T09:01:00Z",
        },
    ]

    run_id = triage_store.record_digest_run(db, "2026-07-18T09:00:00Z", items)

    assert triage_store.latest_digest_items(db, run_id) == [items[1], items[0]]


def test_digested_uids_returns_union_across_runs(tmp_path: Path) -> None:
    db = tmp_path / "triage.db"
    first = {
        "item_no": 1,
        "uid": "uid-shared",
        "subject": "Synthetic first",
        "sender_masked": "sha256:sender-1",

        "category": "normal",
        "flags": "",
        "summary": "First summary",
        "note": "First note",
        "recv_date": "2026-07-18T09:01:00Z",
    }
    second = {**first, "uid": "uid-new", "item_no": 2}

    triage_store.record_digest_run(db, "2026-07-18T09:00:00Z", [first])
    triage_store.record_digest_run(db, "2026-07-18T10:00:00Z", [second])

    assert triage_store.digested_uids(db) == {"uid-shared", "uid-new"}


def test_latest_digest_items_returns_last_run_only(tmp_path: Path) -> None:
    db = tmp_path / "triage.db"
    old_item = {
        "item_no": 1,
        "uid": "uid-old",
        "subject": "Synthetic old",
        "sender_masked": "sha256:sender-old",

        "category": "normal",
        "flags": "",
        "summary": "Old summary",
        "note": "Old note",
        "recv_date": "2026-07-18T08:00:00Z",
    }
    latest_item = {**old_item, "uid": "uid-latest", "subject": "Synthetic latest"}

    triage_store.record_digest_run(db, "2026-07-18T08:00:00Z", [old_item])
    triage_store.record_digest_run(db, "2026-07-18T09:00:00Z", [latest_item])

    assert triage_store.latest_digest_items(db) == [latest_item]


def test_record_digest_run_zero_items_ok(tmp_path: Path) -> None:
    db = tmp_path / "triage.db"

    run_id = triage_store.record_digest_run(db, "2026-07-18T09:00:00Z", [])

    with sqlite3.connect(db) as connection:
        item_count = connection.execute(
            "SELECT item_count FROM digest_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        item_rows = connection.execute(
            "SELECT COUNT(*) FROM digest_items WHERE run_id = ?", (run_id,)
        ).fetchone()
    assert item_count == (0,)
    assert item_rows == (0,)
    assert triage_store.latest_digest_items(db) == []



# --- W4-6 digest engine (triage_digest) -------------------------------------------



def _classify_stub(category: str, *, schedule: bool = False, schedule_text: str = ""):
    def classify(**kwargs):  # noqa: ARG001
        cls = triage_core.Classification(
            category=category, reply_needed=True, schedule_needed=schedule,
            budget=False, schedule_text=schedule_text, reason="synthetic",
        )
        return cls, "stub-provider"

    return classify


def _detail(uid: str, *, subject: str = "Synthetic subject") -> dict:
    return {
        "uid": uid, "subject": subject, "sender": "발신자 <p@inst.example>",
        "body": "Synthetic body", "date": "2026-07-18T09:01:00Z",
    }


def test_select_new_mails_excludes_digested_and_processed() -> None:
    mails = [
        {"uid": "uid-digested", "date": "2026-07-18T09:01:00Z"},
        {"uid": "uid-processed", "date": "2026-07-18T09:02:00Z"},
        {"uid": "uid-fresh", "date": "2026-07-18T09:03:00Z"},
    ]

    selected = triage_digest.select_new_mails(mails, {"uid-digested"}, {"uid-processed"})

    assert [mail["uid"] for mail in selected] == ["uid-fresh"]


def test_select_new_mails_orders_by_recv_date_ascending() -> None:
    mails = [
        {"uid": "uid-b", "date": "2026-07-18T09:02:00Z"},
        {"uid": "uid-c", "date": "2026-07-18T09:03:00Z"},
        {"uid": "uid-a", "date": "2026-07-18T09:01:00Z"},
    ]

    selected = triage_digest.select_new_mails(mails, set(), set())

    assert [mail["uid"] for mail in selected] == ["uid-a", "uid-b", "uid-c"]


def test_build_item_preserves_keyword_text_in_store_and_owner_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(triage_llm, "classify", _classify_stub("important"))
    monkeypatch.setattr(triage_llm, "summarize", lambda **kwargs: f"요약 {CANARY}")
    monkeypatch.setattr(
        triage_transport, "_delegate_schedule",
        lambda *args: pytest.fail("delegation must not run without schedule_needed"),
    )
    detail = _detail("uid-9", subject=f"특허 patent 기밀 {CANARY}")

    dm_item, store_item = triage_digest.build_item(detail, 1)

    assert CANARY in dm_item["subject"] and CANARY in dm_item["summary"]
    assert store_item["summary"] == dm_item["summary"]
    assert store_item["subject"] == detail["subject"]
    assert CANARY in json.dumps(store_item, ensure_ascii=False)
    assert dm_item["sender_masked"] == triage_core.mask_value(detail["sender"])
    assert store_item["sender_masked"] == dm_item["sender_masked"]


def test_owner_digest_shows_real_sender_and_cc_without_persisting_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a private owner digest item with a real sender and Cc recipient.
    monkeypatch.setattr(triage_llm, "classify", _classify_stub("normal"))
    monkeypatch.setattr(triage_llm, "summarize", lambda **kwargs: "Synthetic summary")
    detail = {
        **_detail("uid-private"),
        "sender": 'Synthetic Sender <sender@example.invalid>',
        "body": (
            '---\nto: "owner@example.invalid"\n'
            'cc: "Synthetic Copy <copy@example.invalid>"\n---\n\nSynthetic body'
        ),
    }

    # When: the private card is built and rendered.
    dm_item, store_item = triage_digest.build_item(detail, 1)
    rendered = triage_digest.render_digest_dm([dm_item], kst_now=_KST_NOW)

    # Then: the owner sees sender+Cc, while persistence retains only the sender hash.
    assert "Synthetic Sender <sender@example.invalid>" in dm_item["sender"]
    assert dm_item["cc"] == ("copy@example.invalid",)
    assert "발신 Synthetic Sender" in rendered
    assert "참조 copy@\u200bexample.invalid" in rendered
    stored = json.dumps(store_item, ensure_ascii=False)
    assert "sender@example.invalid" not in stored
    assert "copy@example.invalid" not in stored


def test_build_item_summary_failure_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(triage_llm, "classify", _classify_stub("normal"))

    def summarize(**kwargs):
        raise triage_llm.LlmCallError("stub summarize failure")

    monkeypatch.setattr(triage_llm, "summarize", summarize)

    dm_item, store_item = triage_digest.build_item(_detail("uid-7"), 1)

    assert dm_item["summary"] == "(요약 실패)"
    assert store_item["summary"] == "(요약 실패)"
    assert store_item["uid"] == "uid-7"  # item kept — fail-open listing


def test_build_item_retries_summary_once_before_falling_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one mail whose first summary call fails at the transport level.
    monkeypatch.setattr(triage_llm, "classify", _classify_stub("normal"))
    calls: list[str] = []

    def summarize(**kwargs):
        calls.append(kwargs["uid_opaque"])
        if len(calls) == 1:
            raise triage_llm.LlmCallError("stub summarize transport failure")
        return "Synthetic summary"

    monkeypatch.setattr(triage_llm, "summarize", summarize)

    # When: that mail is built into one digest item.
    dm_item, store_item = triage_digest.build_item(_detail("uid-8"), 1)

    # Then: the retry result is used — one transient failure must not cost the summary.
    assert dm_item["summary"] == "Synthetic summary"
    assert store_item["summary"] == "Synthetic summary"
    assert len(calls) == 2


def test_build_item_logs_masked_reason_when_summary_keeps_failing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # Given: a mail whose summary never parses, with the routing log redirected to tmp.
    log = tmp_path / "llm-calls.jsonl"
    monkeypatch.setenv("TRIAGE_LLM_LOG", str(log))
    monkeypatch.setattr(triage_llm, "classify", _classify_stub("normal"))
    calls: list[str] = []

    def summarize(**kwargs):
        calls.append(kwargs["uid_opaque"])
        raise triage_core.LlmParseError("digest summary is empty")

    monkeypatch.setattr(triage_llm, "summarize", summarize)
    detail = {**_detail("uid-8"), "sender": "Synthetic Sender <sender@example.invalid>"}

    # When: the item is built after both attempts fail.
    dm_item, _store_item = triage_digest.build_item(detail, 1)

    # Then: the item survives with the fallback and the reason is recorded, masked —
    # the cron drops stderr, so the log line is the only forensic trace.
    assert dm_item["summary"] == "(요약 실패)"
    assert len(calls) == 2
    raw = log.read_text(encoding="utf-8")
    records = [json.loads(line) for line in raw.splitlines() if line.strip()]
    assert len(records) == 1
    assert records[0]["purpose"] == "digest_summary_failed"
    assert records[0]["uid"] == triage_core.mask_value("uid-8")
    assert records[0]["error"].startswith("LlmParseError")
    assert "Synthetic subject" not in raw
    assert "example.invalid" not in raw


def test_build_item_retries_only_the_current_classification_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one mail whose first classification response is invalid JSON, then succeeds.
    attempts: list[str] = []

    def classify(**kwargs):
        attempts.append(kwargs["subject"])
        if len(attempts) == 1:
            raise triage_core.LlmParseError("no JSON object in LLM response")
        return _classify_stub("normal")(**kwargs)

    monkeypatch.setattr(triage_llm, "classify", classify)
    monkeypatch.setattr(triage_llm, "summarize", lambda **kwargs: "Synthetic summary")

    # When: that mail is built into one digest item.
    dm_item, store_item = triage_digest.build_item(_detail("uid-retry"), 1)

    # Then: only classification repeats, and the recovered verdict has no failure marker.
    assert attempts == ["Synthetic subject", "Synthetic subject"]
    assert dm_item["category"] == "normal"
    assert "classification_failed" not in dm_item["flags"]
    assert store_item["uid"] == "uid-retry"


@pytest.mark.parametrize(
    "error",
    [
        triage_llm.LlmCallError("codex 호출 실패: timed out"),
        triage_core.LlmParseError("no JSON object in LLM response"),
    ],
)
def test_build_item_classify_failure_fails_open(
    monkeypatch: pytest.MonkeyPatch, error: Exception,
) -> None:
    # classify() failing (a one-off request error OR unparseable model output) —
    # NOT a tier outage, which fails the whole tick closed — must NOT abort
    # the whole digest: keep the item, mark it important + classification-failed,
    # and NEVER delegate a calendar draft off a fabricated verdict.

    def classify(**kwargs):  # noqa: ARG001
        raise error

    monkeypatch.setattr(triage_llm, "classify", classify)
    monkeypatch.setattr(triage_llm, "summarize", lambda **kwargs: "Synthetic summary")
    monkeypatch.setattr(
        triage_transport, "_delegate_schedule",
        lambda *args: pytest.fail("a failed classification must never delegate calendar"),
    )

    dm_item, store_item = triage_digest.build_item(_detail("uid-cf"), 1)

    assert dm_item["category"] == "important"  # conservative — surfaces the mail
    assert "classification_failed" in dm_item["flags"]
    assert store_item["uid"] == "uid-cf"  # item kept — fail-open listing
    assert dm_item["note"] == "" and store_item["note"] == ""  # no calendar delegation
    assert dm_item["summary"] == "Synthetic summary"  # summary path still runs


def test_build_item_logs_classify_failure_after_retry_exhaustion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    log = tmp_path / "llm-calls.jsonl"
    monkeypatch.setenv("TRIAGE_LLM_LOG", str(log))

    def classify(**kwargs):  # noqa: ARG001
        raise triage_llm.LlmCallError("classify timed out")

    monkeypatch.setattr(triage_llm, "classify", classify)
    monkeypatch.setattr(triage_llm, "summarize", lambda **kwargs: "Synthetic summary")

    dm_item, store_item = triage_digest.build_item(_detail("uid-classify-log"), 1)

    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    assert records[0]["purpose"] == "classify_failed"
    assert records[0]["uid"] == triage_core.mask_value("uid-classify-log")
    assert records[0]["error"].startswith("LlmCallError")
    assert dm_item["category"] == "important"
    assert "classification_failed" in dm_item["flags"]
    assert store_item["uid"] == "uid-classify-log"


def test_render_digest_dm_shows_classification_failed_badge() -> None:
    text = triage_digest.render_digest_dm(
        [{
            "item_no": 1, "uid": "uid-cf", "subject": "Synthetic",
            "sender_masked": "sha256:s", "sensitive": 0, "category": "important",
            "flags": ("classification_failed",), "summary": "Synthetic summary",
            "note": "", "recv_date": "2026-07-18T09:01:00Z",
        }],
        kst_now=datetime(2026, 7, 18, 9, 30, tzinfo=ZoneInfo("Asia/Seoul")),
    )
    assert "⚠️ 분류 실패" in text

_CC_BODY = (
    '---\nuid: "uid-cc"\nto: "other@inst.example"\n'
    'cc: "owner@inst.example"\ndate: "2026-07-18T09:01:00"\n---\n\nSynthetic body'
)
_TO_BODY = _CC_BODY.replace('to: "other@inst.example"', 'to: "owner@inst.example"').replace(
    'cc: "owner@inst.example"', 'cc: ""'
)


def test_build_item_cc_only_mail_suppresses_reply_and_flags_cc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the owner is only a Cc recipient and the LLM says reply_needed
    monkeypatch.setenv("MAILON_ID", "owner@inst.example")
    monkeypatch.setattr(triage_llm, "classify", _classify_stub("important"))
    monkeypatch.setattr(triage_llm, "summarize", lambda **kwargs: "Synthetic summary")
    detail = {**_detail("uid-cc"), "body": _CC_BODY}
    # When: the digest item is built
    dm_item, store_item = triage_digest.build_item(detail, 1)
    # Then: reply_needed is suppressed and the cc marker is carried on both rows
    assert dm_item["flags"] == ("cc",)
    assert store_item["flags"] == "cc"


def test_build_item_owner_dm_keeps_real_sender_and_cc_private(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a private owner-DM digest item with named sender and Cc recipients.
    monkeypatch.setenv("MAILON_ID", "owner@inst.example")
    monkeypatch.setattr(triage_llm, "classify", _classify_stub("normal"))
    monkeypatch.setattr(triage_llm, "summarize", lambda **kwargs: "Synthetic summary")
    body = (
        '---\nuid: "uid-private"\nto: "owner@inst.example"\n'
        'cc: "참조자 <copy@inst.example>"\n---\n\nSynthetic body'
    )
    detail = {
        **_detail("uid-private"),
        "sender": "발신자 <sender@inst.example>",
        "body": body,
    }

    # When: the item is projected for owner delivery and persistent storage.
    dm_item, store_item = triage_digest.build_item(detail, 1)
    rendered = triage_digest.render_digest_dm([dm_item], kst_now=_KST_NOW)

    # Then: only the owner projection contains real sender/Cc; storage stays redacted.
    assert dm_item["sender"] == "발신자 <sender@inst.example>"
    assert dm_item["cc"] == "참조자 <copy@inst.example>"
    assert "발신 발신자" in rendered and "sender@inst.example" not in rendered
    assert "참조 참조자" in rendered
    assert "sender" not in store_item and "cc" not in store_item
    assert store_item["sender_masked"] == triage_core.mask_value(detail["sender"])


def test_build_item_mass_notice_suppresses_reply_without_overriding_cc_logic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a To-recipient bulk notice that the LLM incorrectly marks reply-needed.
    monkeypatch.setenv("MAILON_ID", "owner@inst.example")
    monkeypatch.setattr(triage_llm, "classify", _classify_stub("normal"))
    monkeypatch.setattr(triage_llm, "summarize", lambda **kwargs: "Synthetic summary")
    detail = {
        **_detail("uid-notice", subject="정기 뉴스레터 안내"),
        "sender": "noreply@inst.example",
        "body": _TO_BODY + "\n수신 거부와 구독 해지는 공지 페이지에서 처리하세요.",
    }

    # When: the digest item applies deterministic recipient/search signals.
    dm_item, store_item = triage_digest.build_item(detail, 1)

    # Then: bulk notice suppresses reply while the independent Cc badge remains absent.
    assert dm_item["flags"] == ()
    assert store_item["flags"] == ""


def test_build_item_to_recipient_keeps_reply_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the owner is a To recipient of a reply-needed mail
    monkeypatch.setenv("MAILON_ID", "owner@inst.example")
    monkeypatch.setattr(triage_llm, "classify", _classify_stub("important"))
    monkeypatch.setattr(triage_llm, "summarize", lambda **kwargs: "Synthetic summary")
    detail = {**_detail("uid-to"), "body": _TO_BODY}
    # When: the digest item is built
    dm_item, store_item = triage_digest.build_item(detail, 1)
    # Then: behavior is unchanged — reply_needed stays, no cc marker
    assert dm_item["flags"] == ("reply_needed",)
    assert store_item["flags"] == "reply_needed"


def test_build_item_delegates_calendar_for_important_schedule_needed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str]] = []

    def delegate(schedule_text: str, uid_opaque: str, digest_day: str) -> triage_transport.ScheduleNote:
        calls.append((schedule_text, uid_opaque, digest_day))
        return triage_transport.ScheduleNote(
            "calendar:abc123", "https://discord.com/channels/11/22/33",
            "2026-07-20T10:00:00+09:00", "2026-07-20T11:00:00+09:00", "장비 회의",
        )

    monkeypatch.setattr(triage_transport, "_delegate_schedule", delegate)
    monkeypatch.setattr(triage_llm, "summarize", lambda **kwargs: "Synthetic summary")
    monkeypatch.setattr(
        triage_llm, "classify",
        _classify_stub("important", schedule=True, schedule_text="7/20 10:00 장비 회의"),
    )

    dm_item, store_item = triage_digest.build_item(_detail("uid-5"), 1, digest_day="2026-07-20")

    # The digest day travels with the delegation so the calendar side groups the card by day.
    assert calls == [("7/20 10:00 장비 회의", triage_core.mask_value("uid-5"), "2026-07-20")]
    assert dm_item["note"] == "calendar:abc123"
    assert store_item["note"] == "calendar:abc123"
    # The card link and event reach the rendered card, not the stored row.
    assert dm_item["calendar_card"] == "https://discord.com/channels/11/22/33"
    assert dm_item["calendar_when"] == "7/20(월) 10:00–11:00"
    assert dm_item["calendar_title"] == "장비 회의"
    assert "calendar_card" not in store_item

    monkeypatch.setattr(
        triage_llm, "classify",
        _classify_stub("normal", schedule=True, schedule_text="7/20 10:00 장비 회의"),
    )

    dm_item, store_item = triage_digest.build_item(_detail("uid-5"), 2)

    assert len(calls) == 1  # category "normal" never delegates
    assert dm_item["note"] == "" and store_item["note"] == ""


def test_render_digest_dm_card_format() -> None:
    items = [
        {
            "item_no": 1, "uid": "uid-1", "subject": "Synthetic first",
            "sender_masked": "sha256:sender1", "sensitive": 0, "category": "important",
            "flags": ("reply_needed",), "summary": "First summary", "note": "",
            "recv_date": "2026-07-18T09:01:00Z",
        },
        {
            "item_no": 2, "uid": "uid-2", "subject": "Synthetic second",
            "sender_masked": "sha256:sender2", "sensitive": 1, "category": "normal",
            "flags": ("schedule_needed",), "summary": "Second summary",
            "note": "calendar:abc123", "recv_date": "2026-07-18T09:02:00Z",
        },
    ]

    text = triage_digest.render_digest_dm(
        items, kst_now=datetime(2026, 7, 18, 9, 30, tzinfo=ZoneInfo("Asia/Seoul"))
    )

    lines = text.splitlines()
    assert lines[0] == "## 📬 기관메일 다이제스트"
    assert lines[1] == "2026-07-18 09:30 KST · 신규 2건"
    assert "### 1. Synthetic first" in lines
    assert "### 2. Synthetic second" in lines
    assert text.index("### 1.") < text.index("### 2.")
    assert "🔴 중요 · ↩️ 회신 필요" in lines
    assert "🔵 일반 · 📅 일정" in lines
    assert "> 요약 · First summary" in lines
    assert "수신 07-18 18:01" in lines
    assert "UID" not in text and "sha256" not in text
    # A draft whose card link is unknown names the thread instead of a bare hash.
    assert "🗓️ 일정 초안 · 승인 카드는 그날 「캘린더 · 다이제스트」 스레드" in lines
    assert "calendar:abc123" not in text
    assert "---" in lines  # 머리글과 회신 안내 구분선
    assert "N번 메일" in text
    assert "✅" not in text.split("### 1.")[0] and "승인 카드" in text


def test_render_digest_dm_empty_says_no_new_mail() -> None:
    text = triage_digest.render_digest_dm(
        [], kst_now=datetime(2026, 7, 18, 9, 30, tzinfo=ZoneInfo("Asia/Seoul"))
    )

    lines = text.splitlines()
    assert lines[0] == "## 📬 기관메일 다이제스트"
    assert lines[1].endswith("신규 0건")
    assert lines[-1] == "신규 메일 없음"
    assert "회신 지시" not in text
    assert "---" not in lines


def _card_item(**overrides) -> dict:
    base = {
        "item_no": 1, "uid": "uid-x", "subject": "Synthetic subject",
        "sender_masked": "sha256:senderx", "sensitive": 0, "category": "normal",
        "flags": (), "summary": "Synthetic summary", "note": "",
        "recv_date": "2026-07-18T09:01:00Z",
    }
    return {**base, **overrides}


_KST_NOW = datetime(2026, 7, 18, 9, 30, tzinfo=ZoneInfo("Asia/Seoul"))


def test_render_digest_dm_escapes_markdown_and_neutralizes_mentions() -> None:
    # Given: mail-derived subject/summary carrying Discord markdown and mentions
    item = _card_item(
        subject="@everyone **bold** [link](x) `tick` |sp|",
        summary="line\nbreak _under_",
    )
    # When: the digest card is rendered
    text = triage_digest.render_digest_dm([item], kst_now=_KST_NOW)
    # Then: markdown is escaped, mentions are ZWSP-neutralized, newlines collapse
    assert "\\*\\*bold\\*\\*" in text
    assert "\\[link\\]" in text
    assert "\\`tick\\`" in text
    assert "\\|sp\\|" in text
    assert "@\u200beveryone" in text
    assert "@everyone" not in text.replace("@\u200beveryone", "")
    assert "> 요약 · line break \\_under\\_" in text.splitlines()


def test_render_digest_dm_recv_time_kst_variants() -> None:
    # Given: Z-suffixed, naive(UTC 가정), and unparseable recv_date values
    zulu = _card_item(recv_date="2026-07-18T09:01:00Z")
    naive = _card_item(recv_date="2026-07-18T09:01:00")
    broken = _card_item(recv_date="not-a-date")
    empty = _card_item(recv_date="")
    # When: each variant is rendered
    lines_zulu = triage_digest.render_digest_dm([zulu], kst_now=_KST_NOW).splitlines()
    lines_naive = triage_digest.render_digest_dm([naive], kst_now=_KST_NOW).splitlines()
    lines_broken = triage_digest.render_digest_dm([broken], kst_now=_KST_NOW).splitlines()
    lines_empty = triage_digest.render_digest_dm([empty], kst_now=_KST_NOW).splitlines()
    # Then: parseable dates show KST, unparseable ones omit 수신 without crashing
    assert "수신 07-18 18:01" in lines_zulu
    assert "수신 07-18 18:01" in lines_naive
    assert not any(line.startswith("수신") for line in lines_broken + lines_empty)


def test_render_digest_dm_badge_maps() -> None:
    # Given: spam category with budget+cc flags, and an unknown category/flag pair
    spam = _card_item(category="spam", flags=("budget", "cc"), )
    unknown = _card_item(category="weird", flags=("mystery",))
    # When: both are rendered
    text_spam = triage_digest.render_digest_dm([spam], kst_now=_KST_NOW)
    text_unknown = triage_digest.render_digest_dm([unknown], kst_now=_KST_NOW)
    # Then: known keys map to Korean emoji badges, unknown keys fall back to raw text
    assert "🗑️ 스팸 · 💳 예산 · 👀 참조(CC)" in text_spam.splitlines()
    assert "weird · mystery" in text_unknown.splitlines()


def _patch_cli_digest_engine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    db = tmp_path / "triage.db"
    monkeypatch.setenv("TRIAGE_DB", str(db))
    monkeypatch.setattr(triage_mode, "effective_mode", lambda: "full-go")
    monkeypatch.setattr(
        triage_digest.triage_transport,
        "_list_mails",
        lambda _limit, _sync: [{"uid": "uid-cli", "date": "2026-07-18T09:01:00Z"}],
    )
    monkeypatch.setattr(
        triage_digest.triage_transport,
        "_get_mail",
        lambda _uid: _detail("uid-cli", subject="Synthetic CLI subject"),
    )

    def build_item(  # noqa: ARG001
        detail: dict, item_no: int, *, digest_day: str = ""
    ) -> tuple[dict, dict]:
        shared = {
            "item_no": item_no,
            "uid": detail["uid"],
            "sender_masked": "sha256:sender-cli",

            "category": "important",
            "note": "",
            "recv_date": detail["date"],
        }
        return (
            {**shared, "subject": detail["subject"], "summary": "Synthetic summary", "flags": ("reply_needed",)},
            {**shared, "subject": detail["subject"], "summary": "Synthetic summary", "flags": "reply_needed"},
        )

    monkeypatch.setattr(triage_digest, "build_item", build_item)
    return db


def test_run_digest_sync_fallback_warns_and_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: mailon sync fails, but the local database listing remains available
    _patch_cli_digest_engine(monkeypatch, tmp_path)
    calls: list[tuple[int, bool]] = []

    def list_mails(limit: int, sync: bool) -> list[dict]:
        calls.append((limit, sync))
        if sync:
            raise triage_gate.GateError("synthetic sync failure", 4)
        return [{"uid": "uid-fallback", "date": "2026-07-18T09:01:00Z"}]

    monkeypatch.setattr(triage_digest.triage_transport, "_list_mails", list_mails)
    sent: list[str] = []
    monkeypatch.setattr(triage_confirm, "dm_owner", lambda body: sent.append(body) or "dm-1")

    # When: the digest runs with sync enabled
    rc = triage_digest.run_digest(limit=10, sync=True, dry_run=False)

    # Then: local data is sent with an explicit warning and the run is recorded
    assert rc == 0
    assert calls == [(10, True), (10, False)]
    assert "⚠️ mailon 동기화 실패 — 로컬 DB 기준" in sent[0]
    assert "DIGEST run=" in capsys.readouterr().out


def test_run_digest_one_bad_classify_still_completes_and_delivers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Two new mails; the FIRST fails classification. The whole digest must still
    # deliver BOTH items and record the run — one bad mail never strands the rest.
    db = tmp_path / "triage.db"
    monkeypatch.setenv("TRIAGE_DB", str(db))
    monkeypatch.setattr(triage_mode, "effective_mode", lambda: "full-go")
    mails = [
        {"uid": "uid-bad", "date": "2026-07-18T09:01:00Z"},
        {"uid": "uid-good", "date": "2026-07-18T09:02:00Z"},
    ]
    monkeypatch.setattr(
        triage_digest.triage_transport, "_list_mails", lambda _limit, _sync: mails
    )
    monkeypatch.setattr(
        triage_digest.triage_transport,
        "_get_mail",
        lambda uid: _detail(uid, subject=f"Subject {uid}"),
    )
    monkeypatch.setattr(triage_llm, "summarize", lambda **kwargs: "Synthetic summary")

    def classify(**kwargs):
        if kwargs.get("subject") == "Subject uid-bad":
            raise triage_core.LlmParseError("no JSON object in LLM response")
        cls = triage_core.Classification(
            category="normal", reply_needed=False, schedule_needed=False,
            budget=False, schedule_text="", reason="synthetic",
        )
        return cls, "stub-provider"

    monkeypatch.setattr(triage_llm, "classify", classify)
    sent: list[str] = []
    monkeypatch.setattr(triage_confirm, "dm_owner", lambda body: sent.append(body) or "dm-1")

    rc = triage_digest.run_digest(limit=10, sync=False, dry_run=False)

    assert rc == 0
    assert len(sent) == 3  # header + one message per mail
    assert sent[1].startswith("### 1. Subject uid-bad")
    assert sent[2].startswith("### 2. Subject uid-good")
    assert "⚠️ 분류 실패" in sent[1]  # the bad item is flagged, not dropped
    assert "DIGEST run=" in capsys.readouterr().out
    # Both mails are recorded so neither is re-digested next tick.
    assert triage_store.digested_uids(db) == {"uid-bad", "uid-good"}


def test_run_digest_sync_false_gate_error_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the non-sync listing itself fails at the gate
    _patch_cli_digest_engine(monkeypatch, tmp_path)
    calls: list[tuple[int, bool]] = []

    def list_mails(limit: int, sync: bool) -> list[dict]:
        calls.append((limit, sync))
        raise triage_gate.GateError("synthetic local listing failure", 4)

    monkeypatch.setattr(triage_digest.triage_transport, "_list_mails", list_mails)

    # When/Then: no-sync propagates the original gate error without retrying
    with pytest.raises(triage_gate.GateError, match="synthetic local listing failure"):
        triage_digest.run_digest(limit=10, sync=False, dry_run=False)
    assert calls == [(10, False)]


def test_run_digest_dm_failure_gate_error_includes_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the digest has one item but owner DM delivery fails
    db = _patch_cli_digest_engine(monkeypatch, tmp_path)

    def fail_dm(_body: str) -> str:
        raise RuntimeError("synthetic DM failure")

    monkeypatch.setattr(triage_confirm, "dm_owner", fail_dm)

    # When/Then: delivery failure is a structured gate marker and records nothing
    with pytest.raises(triage_gate.GateError) as error_info:
        triage_digest.run_digest(limit=10, sync=False, dry_run=False)
    message = str(error_info.value)
    assert "DIGEST-FAIL stage=deliver retry_safe=false code=discord_delivery_failed" in message
    with sqlite3.connect(db) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'digest_runs'"
        ).fetchone() == (0,)


def test_cmd_digest_dry_run_prints_and_records_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: a synthetic full-go digest source and a dry-run invocation
    db = _patch_cli_digest_engine(monkeypatch, tmp_path)
    monkeypatch.setattr(triage_confirm, "dm_owner", lambda _body: pytest.fail("dry-run sent DM"))
    monkeypatch.setattr(sys, "argv", ["triage_cli", "digest", "--dry-run"])
    # When: the owner previews the digest
    rc = triage_cli.main()
    # Then: the digest is rendered but no digest run is persisted
    assert rc == 0
    assert "기관메일 다이제스트" in capsys.readouterr().out
    with sqlite3.connect(db) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'digest_runs'"
        ).fetchone() == (0,)


def test_cmd_digest_sends_dm_then_records_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: a synthetic digest and an owner DM capture
    db = _patch_cli_digest_engine(monkeypatch, tmp_path)
    sent: list[str] = []
    monkeypatch.setattr(triage_confirm, "dm_owner", lambda body: sent.append(body) or "dm-1")
    monkeypatch.setattr(sys, "argv", ["triage_cli", "digest", "--no-sync"])
    # When: the digest command runs
    rc = triage_cli.main()
    # Then: the header and the one mail message precede a persisted run
    assert rc == 0
    assert len(sent) == 2
    assert "DIGEST run=" in capsys.readouterr().out
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM digest_runs").fetchone() == (1,)


def test_cmd_digest_dm_failure_records_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a synthetic digest whose owner DM fails
    db = _patch_cli_digest_engine(monkeypatch, tmp_path)

    def fail_dm(_body: str) -> str:
        raise RuntimeError("synthetic DM failure")

    monkeypatch.setattr(triage_confirm, "dm_owner", fail_dm)
    monkeypatch.setattr(sys, "argv", ["triage_cli", "digest"])
    # When: the digest command runs
    rc = triage_cli.main()
    # Then: the failed delivery leaves no digest run behind
    assert rc == 4
    with sqlite3.connect(db) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'digest_runs'"
        ).fetchone() == (0,)


def test_cmd_digest_refuses_no_go(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: no-go mode and a DM transport that must not be reached
    _patch_cli_digest_engine(monkeypatch, tmp_path)
    dm_calls: list[str] = []
    monkeypatch.setattr(triage_mode, "effective_mode", lambda: "no-go")
    monkeypatch.setattr(triage_confirm, "dm_owner", lambda body: dm_calls.append(body) or "dm-1")
    monkeypatch.setattr(sys, "argv", ["triage_cli", "digest"])
    # When: the digest command runs
    rc = triage_cli.main()
    # Then: no-go uses exit 3 and never attempts the informational DM
    assert rc == 3
    assert dm_calls == []


def test_cmd_digest_items_prints_latest_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: one recorded synthetic digest run
    db = tmp_path / "triage.db"
    monkeypatch.setenv("TRIAGE_DB", str(db))
    triage_store.record_digest_run(
        db,
        "2026-07-18T09:00:00Z",
        [{
            "item_no": 1,
            "uid": "uid-items",
            "subject": "sha256:masked-subject",
            "sender_masked": "sha256:sender-items",

            "category": "important",
            "flags": "reply_needed",
            "summary": "",
            "note": "",
            "recv_date": "2026-07-18T09:01:00Z",
        }],
    )
    monkeypatch.setattr(sys, "argv", ["triage_cli", "digest-items"])
    # When: the owner lists the latest digest items
    rc = triage_cli.main()
    # Then: the stable mapping line exposes only the stored subject
    assert rc == 0
    assert capsys.readouterr().out.strip() == (
        "ITEM no=1 uid=uid-items category=important "
        "flags=reply_needed subject=sha256:masked-subject"
    )


def test_cmd_digest_items_no_runs_exits_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: an empty digest database
    monkeypatch.setenv("TRIAGE_DB", str(tmp_path / "triage.db"))
    monkeypatch.setattr(sys, "argv", ["triage_cli", "digest-items"])
    # When: the owner lists digest items
    rc = triage_cli.main()
    # Then: the missing run is a mode/config-style exit 3
    assert rc == 3
    assert "GATE-REFUSED" in capsys.readouterr().err


def test_run_digest_build_stage_llm_failure_emits_structured_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a digest whose item build fails at the LLM classify step (the
    # 2026-07-31 incident: a classify timeout killed the whole tick pre-delivery)
    db = _patch_cli_digest_engine(monkeypatch, tmp_path)
    dm_calls: list[str] = []
    monkeypatch.setattr(triage_confirm, "dm_owner", lambda body: dm_calls.append(body) or "dm-1")

    def fail_build(  # noqa: ARG001
        _detail: dict, _item_no: int, *, rules: tuple
    ) -> tuple[dict, dict]:
        raise triage_llm.LlmCallError("codex 호출 실패: leak@example.com uid=1234567 timed out")

    monkeypatch.setattr(triage_digest, "build_item", fail_build)

    # When/Then: the build failure surfaces as one redacted structured marker,
    # the owner DM is never attempted, and nothing is recorded.
    with pytest.raises(triage_gate.GateError) as error_info:
        triage_digest.run_digest(limit=10, sync=False, dry_run=False)
    marker = str(error_info.value)
    assert marker.splitlines() == [marker]  # exactly one line
    assert "DIGEST-FAIL stage=build retry_safe=false code=llm_call_failed" in marker
    assert "leak@example.com" not in marker and "1234567" not in marker
    assert error_info.value.exit_code == 4
    assert dm_calls == []
    with sqlite3.connect(db) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'digest_runs'"
        ).fetchone() == (0,)


def test_run_digest_deliver_failure_emits_structured_marker_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: item build succeeds but the owner digest DM delivery fails
    db = _patch_cli_digest_engine(monkeypatch, tmp_path)
    dm_calls: list[str] = []

    def fail_dm(body: str) -> str:
        dm_calls.append(body)
        raise RuntimeError("discord down: agent@corp.example.org 987654321")

    monkeypatch.setattr(triage_confirm, "dm_owner", fail_dm)

    # When/Then: the delivery failure is one redacted structured marker, the DM
    # is attempted exactly once (never re-sent from inside the app), no run stored.
    with pytest.raises(triage_gate.GateError) as error_info:
        triage_digest.run_digest(limit=10, sync=False, dry_run=False)
    marker = str(error_info.value)
    assert marker.splitlines() == [marker]  # exactly one line
    assert "DIGEST-FAIL stage=deliver retry_safe=false code=discord_delivery_failed" in marker
    assert "agent@corp.example.org" not in marker and "987654321" not in marker
    assert error_info.value.exit_code == 4
    assert len(dm_calls) == 1  # the owner DM must never be sent twice
    with sqlite3.connect(db) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'digest_runs'"
        ).fetchone() == (0,)


def test_dm_owner_prefers_the_agent_chat_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: the shared directory resolves the configured agent-chat channel.
    import triage_approval

    class _Directory:
        def agent_chat(self) -> str:
            return "chat-1"

        def owner_dm(self) -> str:
            pytest.fail("agent-chat delivery must not open the owner DM")

    class _Sent:
        message_id = "m-1"

    opened: list[str] = []

    def fake_transport(channel_id: str):
        opened.append(channel_id)

        class _Transport:
            def send(self, content: str) -> list[_Sent]:
                return [_Sent()]

        return _Transport()

    monkeypatch.setattr(triage_approval, "approval_directory", lambda: _Directory())
    monkeypatch.setattr(triage_confirm, "_dm_transport", fake_transport)

    # When: an owner notice (the digest body rides this path) is delivered.
    message_id = triage_confirm.dm_owner("다이제스트")

    # Then: it lands in the agent-chat channel, never the DM.
    assert (opened, message_id) == (["chat-1"], "m-1")


def test_dm_owner_falls_back_to_the_owner_dm_when_agent_chat_is_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the directory has no agent-chat channel configured.
    import triage_approval

    class _Directory:
        def agent_chat(self) -> str:
            raise RuntimeError("agent_chat_channel_id is not configured")

        def owner_dm(self) -> str:
            return "dm-1"

    class _Sent:
        message_id = "m-2"

    opened: list[str] = []

    def fake_transport(channel_id: str):
        opened.append(channel_id)

        class _Transport:
            def send(self, content: str) -> list[_Sent]:
                return [_Sent()]

        return _Transport()

    monkeypatch.setattr(triage_approval, "approval_directory", lambda: _Directory())
    monkeypatch.setattr(triage_confirm, "_dm_transport", fake_transport)

    # When / Then: delivery still reaches the owner through the DM.
    assert (triage_confirm.dm_owner("다이제스트"), opened) == ("m-2", ["dm-1"])
