"""Reply and follow-up drafts quote the original mail like a mail client's reply.

RED-first contract for the owner request (2026-09-01): today a reply draft sends
ONLY the newly written text. A reply must carry the owner-reviewed reply text
FOLLOWED by the quoted original (Outlook-style Korean header block + the
original body); ``draft --reply-all`` copies the original To/Cc minus the owner
and the sender into Cc; ``compose --in-reply-to`` quotes a prior mail and the
quoted text stays in the send payload. The approval message never dumps the
quote (Discord 2,000-char limit) — it notes it in one line.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))
sys.path.insert(0, str(_REPO / "skills" / "mail" / "vendor"))

import mail_quote  # noqa: E402
import triage_cli  # noqa: E402
import triage_core  # noqa: E402
import triage_gate  # noqa: E402
from mailon.writer import Mail, build_markdown  # noqa: E402

OWNER = "owner@example.invalid"
SENDER = "가상 발신자 <peer@example.invalid>"
ORIGINAL_TO = f"{OWNER}, 동료 <colleague@example.invalid>"
ORIGINAL_CC = "cc-one@example.invalid"
ORIGINAL_SUBJECT = '견적 "확인" 요청'
ORIGINAL_BODY = "다음 주 회의 참석 가능 여부 회신 부탁드립니다.\n\n감사합니다."
REPLY_TEXT = "참석 가능합니다. 감사합니다."
SEPARATOR = "-----원본 메시지-----"
EXPECTED_HEADER = (
    f"{SEPARATOR}\n"
    f"보낸 사람: {SENDER}\n"
    "보낸 날짜: 2026-08-30 15:12\n"
    f"받는 사람: {ORIGINAL_TO}\n"
    f"참조: {ORIGINAL_CC}\n"
    f"제목: {ORIGINAL_SUBJECT}\n"
)


def _markdown(tmp_path: Path, *, body: str = ORIGINAL_BODY, cc: str = ORIGINAL_CC) -> str:
    """The exact document the vendored mailon writer stores (what ``get --body`` returns)."""
    mail = Mail(
        uid="u-1", folder="inbox", subject=ORIGINAL_SUBJECT, sender=SENDER,
        to=ORIGINAL_TO, cc=cc, date=datetime(2026, 8, 30, 15, 12), body_text=body,
    )
    return build_markdown(mail, tmp_path, tmp_path / "data" / "mails" / "2026" / "08" / "x.md")


def _detail(tmp_path: Path, **overrides: object) -> dict:
    detail: dict = {
        "uid": "u-1", "subject": ORIGINAL_SUBJECT, "sender": SENDER,
        "date": "2026-08-30T15:12:00", "body": _markdown(tmp_path),
    }
    detail.update(overrides)
    return detail


def _write_stub(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


def _setup_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, detail: dict) -> None:
    mode_file = tmp_path / "runtime" / "mail-mode.json"
    mode_file.parent.mkdir(parents=True, exist_ok=True)
    mode_file.write_text(json.dumps({"mode": "full-go", "source": "test"}), encoding="utf-8")
    monkeypatch.setenv("TRIAGE_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("TRIAGE_DB", str(tmp_path / "triage.db"))
    monkeypatch.setenv("TRIAGE_MAIL_HOME", str(tmp_path / "mail"))
    monkeypatch.setenv("TRIAGE_MAIL_MODE_FILE", str(mode_file))
    monkeypatch.setenv("TRIAGE_MAIL_MODE_REPO", str(tmp_path / "absent-repo-mode.json"))
    monkeypatch.setenv("TRIAGE_LLM_LOG", str(tmp_path / "llm-calls.jsonl"))
    monkeypatch.setenv("TRIAGE_MAILON_PYTHON", "python3")
    monkeypatch.delenv("TRIAGE_REPLY_PROMPT", raising=False)
    monkeypatch.delenv("TRIAGE_CLASSIFY_PROMPT", raising=False)
    monkeypatch.delenv("E2E_TEST_MODE", raising=False)
    monkeypatch.delenv("MAILON_ID", raising=False)
    # 분류도 초안도 같은 승인 티어(Codex OAuth)로 간다 — 대역 하나가 둘 다 답한다.
    hermes = _write_stub(
        tmp_path / "hermes-stub",
        "#!/usr/bin/env python3\n"
        "import sys\n"
        # 공유 클라이언트 argv: [bin, -z, PROMPT, --provider, ...]
        "prompt = sys.argv[sys.argv.index('-z') + 1]\n"
        "if '\"category\"' in prompt:\n"
        "    print('{\"category\": \"important\", \"reply_needed\": true, "
        "\"schedule_needed\": false, \"budget\": false, "
        "\"schedule_text\": \"\", \"reason\": \"test\"}')\n"
        "else:\n"
        f"    print('{{\"subject\": \"\", \"body\": \"{REPLY_TEXT}\"}}')\n",
    )
    monkeypatch.setenv("AUTOPHAGY_HERMES_BIN", str(hermes))
    monkeypatch.setattr(triage_cli, "_get_mail", lambda _uid: dict(detail))


def _run_cli(monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["triage_cli", *argv])
    return triage_cli.main()


def _only_draft() -> tuple[Path, dict]:
    paths = [
        path
        for directory in (triage_gate._public_drafts_dir(), triage_gate._legacy_drafts_dir())
        if directory.is_dir()
        for path in directory.glob("*.json")
    ]
    assert len(paths) == 1, paths
    return paths[0], json.loads(paths[0].read_text(encoding="utf-8"))


def _sent_body(record: dict) -> str:
    argv = list(record["argv"])
    return argv[argv.index("--body") + 1]


# --- C1: reply draft = reply text + quoted original ---------------------------------

def test_reply_draft_sends_reply_text_followed_by_quoted_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a synced institutional mail (vendor markdown) and the owner's instruction
    _setup_env(tmp_path, monkeypatch, _detail(tmp_path))
    # When: the reply draft is created without posting
    assert _run_cli(monkeypatch, "draft", "--uid", "u-1", "--instruction", "참석한다고 답해줘", "--no-post") == 0
    _path, record = _only_draft()
    sent = _sent_body(record)
    # Then: the frozen send body is the reviewed reply text, then the quoted original
    assert record["body"] == REPLY_TEXT
    assert sent == f"{REPLY_TEXT}\n\n{EXPECTED_HEADER}\n{ORIGINAL_BODY}"
    assert record["quote"] == sent[len(REPLY_TEXT) + 2:]
    assert record["subject"] == f"Re: {ORIGINAL_SUBJECT}"
    # And: plain reply copies nobody; the hash binds the quote
    assert record["cc"] == ""
    assert "--cc" not in record["argv"]
    assert record["sha256"] == triage_core.draft_sha256(record)


def test_reply_draft_quote_never_leaks_markdown_scaffolding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_env(tmp_path, monkeypatch, _detail(tmp_path))
    assert _run_cli(monkeypatch, "draft", "--uid", "u-1", "--instruction", "x", "--no-post") == 0
    _path, record = _only_draft()
    sent = _sent_body(record)
    for scaffold in ("uid:", "collected_at:", "## Body", "**From**", "# " + ORIGINAL_SUBJECT):
        assert scaffold not in sent, scaffold


# --- C2: reply-all -------------------------------------------------------------------

def test_reply_all_copies_original_recipients_minus_owner_and_sender(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_env(tmp_path, monkeypatch, _detail(tmp_path))
    monkeypatch.setenv("MAILON_ID", OWNER)
    assert _run_cli(
        monkeypatch, "draft", "--uid", "u-1", "--instruction", "x", "--no-post", "--reply-all",
    ) == 0
    _path, record = _only_draft()
    assert record["cc"] == "colleague@example.invalid, cc-one@example.invalid"
    argv = list(record["argv"])
    assert argv[argv.index("--cc") + 1] == record["cc"]
    assert record["sha256"] == triage_core.draft_sha256(record)
    rendered = triage_core.render_approvals_message(record)
    assert "- Cc: `colleague@example.invalid, cc-one@example.invalid`" in rendered
    assert "- 원문 인용: 포함" in rendered
    assert SEPARATOR not in rendered  # the quote is noted, never dumped


def test_reply_all_without_other_recipients_degrades_to_plain_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    markdown = _markdown(tmp_path, cc="").replace(f'to: "{ORIGINAL_TO}"', f'to: "{OWNER}"')
    _setup_env(tmp_path, monkeypatch, _detail(tmp_path, body=markdown))
    monkeypatch.setenv("MAILON_ID", OWNER)
    assert _run_cli(
        monkeypatch, "draft", "--uid", "u-1", "--instruction", "x", "--no-post", "--reply-all",
    ) == 0
    _path, record = _only_draft()
    assert record["cc"] == ""
    assert "--cc" not in record["argv"]


# --- C3: follow-up compose quotes a prior mail and the gate sees it ------------------

def test_compose_in_reply_to_quotes_keyword_text_in_normal_draft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sensitive_body = "특허 출원 일정을 공유드립니다."
    _setup_env(tmp_path, monkeypatch, _detail(tmp_path, body=_markdown(tmp_path, body=sensitive_body)))
    assert _run_cli(
        monkeypatch, "compose", "--to", "x@y.z", "--subject", "Re: 일정", "--body", "후속 안내드립니다.",
        "--in-reply-to", "u-1", "--no-post",
    ) == 0
    path, record = _only_draft()
    # Then: the quoted original uses the same draft storage and send payload.
    assert "sensitive" not in record
    assert path.is_relative_to(triage_gate._public_drafts_dir())
    sent = _sent_body(record)
    assert record["body"] == "후속 안내드립니다."
    assert sent == f"후속 안내드립니다.\n\n{EXPECTED_HEADER}\n{sensitive_body}"
    assert record["quote"] == sent[len("후속 안내드립니다.") + 2:]
    assert record["sha256"] == triage_core.draft_sha256(record)


def test_compose_without_in_reply_to_is_byte_identical_to_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_env(tmp_path, monkeypatch, _detail(tmp_path))
    assert _run_cli(
        monkeypatch, "compose", "--to", "x@y.z", "--subject", "제목", "--body", "본문", "--no-post",
    ) == 0
    _path, record = _only_draft()
    assert "quote" not in record
    assert record["argv"] == list(triage_core.build_send_argv(
        "python3", "x@y.z", "제목", "본문", body_html=mail_quote.html_body("본문", ""),
    ))


# --- C4: approval rendering notes the quote in one line -----------------------------

def test_render_notes_quote_without_dumping_it_for_compose() -> None:
    quote = f"{SEPARATOR}\n보낸 사람: a <a@b.c>\n제목: t\n\n" + "가" * 3000
    record = {
        "argv": list(triage_core.build_send_argv("python3", "x@y.z", "제목", f"본문\n\n{quote}")),
        "body": "본문", "category": "compose", "cc": "", "channel_id": "", "flags": [],
        "id": "abc123", "kind": "compose", "mail_subject": "", "message_id": "", "quote": quote,
        "sender": "", "sender_masked": triage_core.mask_value(""), "sensitive": False,
        "status": "pending", "subject": "제목", "tags": [], "to": "x@y.z", "uid": "compose:abc",
        "uid_opaque": triage_core.mask_value("compose:abc"), "policy_version": None,
    }
    record["sha256"] = triage_core.draft_sha256(record)
    out = triage_core.render_approvals_message(record)
    assert "- 원문 인용: 포함" in out
    assert SEPARATOR not in out
    assert len(out) < 2000


def test_draft_hash_changes_when_the_quote_changes() -> None:
    base = {
        "argv": ["python3"], "body": "b", "sensitive": False, "subject": "s", "to": "t",
        "uid": "u", "cc": "", "quote": "q1",
    }
    assert triage_core.draft_sha256(base) != triage_core.draft_sha256({**base, "quote": "q2"})


# --- C5: edges — the parser never raises, the quote is capped ------------------------

def test_parse_original_without_markdown_falls_back_to_wrapper_metadata() -> None:
    import mail_quote

    original = mail_quote.parse_original(
        {"sender": SENDER, "subject": ORIGINAL_SUBJECT, "date": "2026-08-30T15:12:00", "body": None}
    )
    assert (original.sender, original.subject, original.body) == (SENDER, ORIGINAL_SUBJECT, "")
    assert original.to == "" and original.cc == ""
    quote = mail_quote.render_quote(original)
    assert quote == (
        f"{SEPARATOR}\n보낸 사람: {SENDER}\n보낸 날짜: 2026-08-30 15:12\n제목: {ORIGINAL_SUBJECT}\n"
    )


def test_parse_original_plain_text_without_frontmatter_keeps_the_text() -> None:
    import mail_quote

    original = mail_quote.parse_original({"sender": SENDER, "subject": "s", "date": "", "body": "  hello\nworld  "})
    assert original.body == "hello\nworld"
    assert "보낸 날짜" not in mail_quote.render_quote(original)


def test_parse_original_treats_writer_placeholder_as_empty_body(tmp_path: Path) -> None:
    import mail_quote

    original = mail_quote.parse_original(_detail(tmp_path, body=_markdown(tmp_path, body="")))
    assert original.body == ""
    assert original.subject == ORIGINAL_SUBJECT  # YAML escapes (\") are undone


def test_render_quote_caps_an_oversized_original() -> None:
    import mail_quote

    huge = "가" * (mail_quote.MAX_QUOTE_CHARS + 5000)
    original = mail_quote.Original(sender="a <a@b.c>", to="", cc="", date="", subject="s", body=huge)
    quote = mail_quote.render_quote(original)
    assert len(quote) < mail_quote.MAX_QUOTE_CHARS + 200
    assert quote.endswith(f"\n…(원문 {len(huge)}자 중 {mail_quote.MAX_QUOTE_CHARS}자까지 인용)")


def test_reply_all_cc_dedupes_and_excludes_owner_and_sender() -> None:
    import mail_quote

    original = mail_quote.Original(
        sender=SENDER, to=f"{OWNER}, A <a@x.yz>, PEER@example.invalid", cc="A@x.yz, b@x.yz",
        date="", subject="s", body="",
    )
    assert mail_quote.reply_all_cc(original, to="peer@example.invalid", owner=OWNER) == "a@x.yz, b@x.yz"
