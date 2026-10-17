"""BCC compose contracts, separate from frozen historical compose evidence."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills/mail/scripts"))

import mail_bcc  # noqa: E402
import mail_preflight  # noqa: E402
import mail_send_retry  # noqa: E402
import triage_cli  # noqa: E402
import triage_core  # noqa: E402
import triage_gate  # noqa: E402
import triage_mode  # noqa: E402
import triage_pipeline  # noqa: E402
import mail_approval_attachment  # noqa: E402

TO = "owner@example.test"
BCC = ("one@example.test", "two@example.test")


@pytest.fixture
def compose_env(monkeypatch, tmp_path):
    monkeypatch.setenv("TRIAGE_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("TRIAGE_MAILON_PYTHON", "python3")
    monkeypatch.setattr(triage_mode, "effective_mode", lambda: "full-go")
    return tmp_path


def draft(**extra):
    return {"argv": ["send"], "body": "notice", "subject": "seminar", "to": TO,
            "uid": "compose:one", "kind": "compose", "id": "abc123", **extra}


def test_bcc_hash_changes_with_hidden_recipients():
    # Given
    original = draft(bcc=list(BCC))
    # When
    changed = triage_core.draft_sha256({**original, "bcc": ["other@example.test"]})
    # Then
    assert changed != triage_core.draft_sha256(original)


def test_absent_bcc_retains_legacy_hash():
    # Given
    original = draft(cc="", quote="original")
    bound = {key: original[key] for key in ("argv", "body", "subject", "to", "uid", "cc", "quote")}
    expected = hashlib.sha256(json.dumps(
        bound, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    ).encode()).hexdigest()
    # When
    actual = triage_core.draft_sha256(original)
    # Then
    assert actual == expected


def test_bcc_argv_delivers_all_hidden_recipients():
    # Given / When
    argv = triage_core.build_send_argv("python3", TO, "seminar", "notice", bcc=BCC)
    # Then
    assert argv[argv.index("--bcc") + 1] == ",".join(BCC)


def test_no_bcc_argv_is_unchanged():
    # Given / When
    argv = triage_core.build_send_argv("python3", TO, "seminar", "notice")
    # Then
    assert argv == ("python3", "-m", "mailon.main", "send", "--to", TO,
                    "--subject", "seminar", "--body", "notice", "--confirm-send", "--json")


def test_bcc_dedupe_and_visible_overlap():
    # Given / When
    parsed = mail_bcc.collect(
        ("One <one@example.test>, TWO@example.test", "one@EXAMPLE.test", TO),
        mail_bcc.AddressFile(), (TO, "two@example.test"),
    )
    # Then
    assert parsed == ("one@example.test",)


@pytest.mark.parametrize("column,content", [
    ("email", "name,email\nFirst,one@example.test\nSecond,two@example.test\n"),
    ("", "one@example.test\n\n two@example.test\n"),
])
def test_bcc_file_column_and_plain_lines(tmp_path, column, content):
    # Given
    path = tmp_path / "recipients.csv"
    path.write_text(content)
    # When
    parsed = mail_bcc.collect((), mail_bcc.AddressFile(str(path), column), (TO,))
    # Then
    assert parsed == BCC


@pytest.mark.parametrize("value", ["bad-address", "", "private@example.test,", "a@example.test; b@example.test"])
def test_invalid_bcc_reports_line_without_address(value):
    # Given / When
    with pytest.raises(triage_gate.GateError) as raised:
        mail_bcc.collect((BCC[0], value), mail_bcc.AddressFile(), ())
    # Then
    assert raised.value.exit_code == 2
    assert "2행" in str(raised.value)
    assert "@" not in str(raised.value)


@pytest.mark.parametrize("column,content,line", [
    ("", "one@example.test\nbroken\n", 2),
    ("email", "name,email\nA,one@example.test\nB,\n", 3),
    ("email", "name,wrong\nA,one@example.test\n", 1),
    ("", "\n", 1),
    ("", "one@example.test,two@example.test\n", 1),
])
def test_invalid_bcc_file_reports_physical_line(tmp_path, column, content, line):
    # Given
    path = tmp_path / "list.csv"
    path.write_text(content)
    # When
    with pytest.raises(triage_gate.GateError) as raised:
        mail_bcc.collect((), mail_bcc.AddressFile(str(path), column), ())
    # Then
    assert raised.value.exit_code == 2
    assert f"{line}행" in str(raised.value)
    assert "@" not in str(raised.value)


def test_bcc_column_requires_file():
    # Given / When
    with pytest.raises(triage_gate.GateError) as raised:
        mail_bcc.collect((), mail_bcc.AddressFile(column="email"), ())
    # Then
    assert raised.value.exit_code == 2


def test_compose_bcc_persists_and_prints_only_count(compose_env, capsys):
    # Given
    args = triage_cli.build_parser().parse_args([
        "compose", "--to", TO, "--bcc", ",".join(BCC),
        "--subject", "seminar", "--body", "notice", "--no-post",
    ])
    # When
    assert triage_cli.cmd_compose(args) == 0
    # Then
    stored = triage_gate.list_drafts()[0]
    assert stored["bcc"] == list(BCC)
    assert stored["sha256"] == triage_core.draft_sha256(stored)
    output = capsys.readouterr().out
    assert f"BCC draft={stored['id']} count=2" in output
    assert all(address not in output for address in BCC)


def test_no_bcc_record_has_no_extra_hash_field(compose_env):
    # Given / When
    stored = triage_pipeline.compose_and_post(TO, "seminar", "notice", post=False)
    # Then
    assert "bcc" not in stored


def test_bcc_survives_preflight_body_rewrite(compose_env):
    # Given
    stored = triage_pipeline.compose_and_post(TO, "seminar", "notice", post=False, bcc=BCC)
    # When
    rewritten = mail_preflight._draft_with_payload(
        stored, {"to": TO, "cc": "", "subject": "seminar", "body": "revised"},
    )
    # Then
    assert rewritten["bcc"] == list(BCC)
    assert rewritten["argv"][rewritten["argv"].index("--bcc") + 1] == ",".join(BCC)


@pytest.mark.parametrize("attachment", [False, True])
def test_bcc_card_counts_without_private_addresses(attachment):
    # Given
    record = draft(bcc=list(BCC), sha256="hash", render_version="3")
    # When
    content = (mail_approval_attachment.summary(record, "approve") if attachment
               else triage_core.render_approvals_message(record))
    # Then
    assert "총 수신자 3명" in content
    assert "받는 사람 1 · 참조 0 · 숨은참조 2" in content
    assert "example.test 2명" in content
    assert all(address not in content for address in BCC)


def test_bcc_failure_names_whole_mail_and_stage():
    # Given
    record = draft(bcc=list(BCC), last_error={"error_code": "external_service_error",
                                           "step": "recipient_commit"})
    # When
    text = mail_send_retry.failure_text(record, "failed")
    # Then
    assert "숨은참조 2명 전원 미발송" in text
    assert "recipient_commit" in text
    assert all(address not in text for address in BCC)


def test_bcc_post_trigger_failure_does_not_claim_nobody_received_it():
    # Given
    record = draft(bcc=list(BCC), status="blocked",
                   last_error={"error_code": "send_unverified", "step": "send_trigger"})
    # When
    text = mail_send_retry.failure_text(record, "failed")
    # Then
    assert "전원 미발송" not in text
    assert "발송되지 않았습니다" not in text
    assert "결과 미확인" in text


@pytest.mark.parametrize("options", [
    ["--bcc-column", "email"], ["--bcc-file", ""],
])
def test_compose_invalid_file_options_exit_two(compose_env, options):
    # Given
    args = triage_cli.build_parser().parse_args([
        "compose", "--to", TO, "--subject", "seminar", "--body", "notice", *options,
    ])
    # When
    with pytest.raises(triage_gate.GateError) as raised:
        triage_cli.cmd_compose(args)
    # Then
    assert raised.value.exit_code == 2
    assert not triage_gate.list_drafts()


def test_cli_accepts_repeated_bcc_and_csv_column(compose_env, capsys):
    # Given
    path = compose_env / "list.csv"
    path.write_text("name,email\nTwo,two@example.test\n")
    args = triage_cli.build_parser().parse_args([
        "compose", "--to", TO, "--subject", "seminar", "--body", "notice", "--no-post",
        "--bcc", BCC[0], "--bcc", BCC[0].upper(),
        "--bcc-file", str(path), "--bcc-column", "email",
    ])
    # When
    assert triage_cli.cmd_compose(args) == 0
    # Then
    assert triage_gate.list_drafts()[0]["bcc"] == list(BCC)
    output = capsys.readouterr().out
    assert all(address not in output for address in BCC)
