"""Mail forward (FW): `compose --forward` / `--forward-gmail` through the new-mail gate.

Owner request 2026-10-12: an institution mail had to reach the owner's Gmail with its
body and attachments intact, and the approval CLI only knew new mail and replies.
A forward is a new mail that carries the original — same draft, same card, same
pre-send sha256 manifest — so these tests drive the real CLI against a fixture MailOn
runtime (state.db + stored markdown + two downloaded files) and a fake gws.
"""
from __future__ import annotations

import base64
import functools
import hashlib
import json
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))
sys.path.insert(0, str(_REPO / "skills" / "mail" / "vendor"))

import mail_forward  # noqa: E402
import triage_cli  # noqa: E402
import triage_core  # noqa: E402
import triage_gate  # noqa: E402
from mailon.state import SCHEMA  # noqa: E402
from mailon.writer import Attachment, Mail, build_markdown  # noqa: E402

UID = "u-77"
TO = "owner.personal@gmail.example"
SUBJECT = "과제 협약서 송부"
SENDER = "가상 담당자 <manager@example.invalid>"
ORIGINAL_TO = "owner@example.invalid, 동료 <colleague@example.invalid>"
ORIGINAL_CC = "cc-one@example.invalid"
BODY = "협약서와 예산표를 첨부합니다.\n\n확인 부탁드립니다."
FILES = {"협약서.pdf": b"%PDF-1.7\n" + bytes(range(256)) * 9, "예산표.xlsx": b"PK\x03\x04" + b"\x00\xff" * 700}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _runtime(tmp_path: Path, *, statuses: dict[str, str] | None = None, drop: str = "") -> Path:
    root = tmp_path / "mailon-runtime"
    attachments = []
    for name, data in FILES.items():
        path = root / "data" / "attachments" / UID / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if name != drop:
            path.write_bytes(data)
        attachments.append(Attachment(filename=name, local_path=path, size=len(data)))
    md_path = root / "data" / "mails" / "2026" / "10" / f"{UID}.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    mail = Mail(
        uid=UID, folder="inbox", subject=SUBJECT, sender=SENDER, to=ORIGINAL_TO, cc=ORIGINAL_CC,
        date=datetime(2026, 10, 12, 9, 30), body_text=BODY, attachments=attachments,
    )
    md_path.write_text(build_markdown(mail, root, md_path), encoding="utf-8")
    db = sqlite3.connect(root / "data" / "state.db")
    db.executescript(SCHEMA)
    db.execute(
        "INSERT INTO messages VALUES (?, 'inbox', ?, ?, ?, ?, 0)",
        (UID, SUBJECT, SENDER, "2026-10-12T09:30:00", str(md_path.relative_to(root))),
    )
    for name, data in FILES.items():
        status = (statuses or {}).get(name, "ok")
        db.execute(
            "INSERT INTO attachments (uid, filename, href, status, size_bytes, local_path, first_seen) "
            "VALUES (?, ?, 'x', ?, ?, ?, 0)",
            (UID, name, status, len(data), f"data/attachments/{UID}/{name}" if status == "ok" else None),
        )
    db.commit()
    db.close()
    return root


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    mode_file = tmp_path / "runtime" / "mail-mode.json"
    mode_file.parent.mkdir(parents=True)
    mode_file.write_text(json.dumps({"mode": "full-go", "source": "test"}), encoding="utf-8")
    for name, value in {
        "TRIAGE_GATE_DIR": tmp_path / "gate", "TRIAGE_DB": tmp_path / "triage.db",
        "TRIAGE_MAIL_HOME": tmp_path / "mail", "TRIAGE_MAIL_MODE_FILE": mode_file,
        "TRIAGE_MAIL_MODE_REPO": tmp_path / "absent-repo-mode.json",
        "TRIAGE_APPROVAL_LOG": tmp_path / "approvals.jsonl", "TRIAGE_MAILON_PYTHON": "python3",
        "MAIL_WRAPPER_REPO": tmp_path / "mailon-runtime",
    }.items():
        monkeypatch.setenv(name, str(value))
    monkeypatch.delenv("E2E_TEST_MODE", raising=False)
    return tmp_path


def _cli(monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["triage_cli", *argv])
    return triage_cli.main()


def _drafts() -> list[dict]:
    directory = triage_gate._public_drafts_dir()
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(directory.glob("*.json"))] \
        if directory.is_dir() else []


def _option(record: dict, name: str) -> list[str]:
    argv = list(record["argv"])
    return [argv[index + 1] for index, item in enumerate(argv) if item == name]


def _forward_dirs(root: Path) -> list[Path]:
    base = root / "gate" / "forward"
    return [path for path in base.iterdir()] if base.is_dir() else []


def test_institution_forward_carries_quote_and_both_attachments_byte_for_byte_to_a_send(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _runtime(env)
    assert _cli(monkeypatch, "compose", "--forward", UID, "--to", TO, "--no-post") == 0
    out = capsys.readouterr().out
    [record] = _drafts()
    assert f"FORWARD draft={record['id']} source=kimm attachments=2" in out

    assert record["subject"] == f"FW: {SUBJECT}"
    assert record["to"] == TO and record["cc"] == "" and "--cc" not in record["argv"]
    sent = _option(record, "--body")[0]
    assert "-----원본 메시지-----" in sent and f"제목: {SUBJECT}" in sent and BODY in sent
    assert f"참조: {ORIGINAL_CC}" in sent  # the original's headers are quoted, never addressed
    paths = [Path(item) for item in _option(record, "--attachment")]
    assert [path.name for path in paths] == list(FILES)
    assert [path.read_bytes() for path in paths] == list(FILES.values())
    assert [item["sha256"] for item in record["attachments"]] == [_sha(data) for data in FILES.values()]
    assert all(path.is_relative_to(env / "gate" / "forward") for path in paths)
    assert record["forward"] == {"source": "kimm", "ref_opaque": triage_core.mask_value(UID), "attachment_count": 2}
    assert record["sha256"] == triage_core.draft_sha256(record)

    card = triage_core.render_approvals_message(record)
    assert "메일 전달(FW) 발송 승인 요청" in card and f"- To: `{TO}`" in card
    for name, data in FILES.items():
        assert f"`{name}` · {len(data)} bytes" in card
    assert "원 첨부 2개가 바이트 그대로" in card and BODY not in card

    payload = {"status": "submitted", "verified": True, "attachment_count": 2,
               "attachment_manifest_sha256": record["attachment_manifest_sha256"]}
    sends: list[tuple[str, ...]] = []
    monkeypatch.setattr(triage_gate, "_run_send", lambda argv: (sends.append(argv), (0, json.dumps(payload), ""))[1])
    triage_gate.execute_draft(record, triage_gate.Approval(ref="reaction:m-1", method="manual_reaction", owner="o"))
    assert sends == [tuple(record["argv"])]
    assert _drafts()[0]["status"] == "executed"


def test_forward_note_goes_above_the_original_and_owner_subject_wins(
    env: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _runtime(env)
    assert _cli(
        monkeypatch, "compose", "--forward", UID, "--to", TO, "--subject", "참고", "--body", "참고하세요", "--no-post",
    ) == 0
    [record] = _drafts()
    assert record["subject"] == "참고" and record["body"] == "참고하세요"
    assert _option(record, "--body")[0].startswith("참고하세요\n\n-----원본 메시지-----")


@pytest.mark.parametrize(
    ("layout", "reason"),
    [({"statuses": {"예산표.xlsx": "fail"}}, "attachment-incomplete"), ({"drop": "협약서.pdf"}, "attachment-missing")],
)
def test_a_forward_with_an_unavailable_attachment_is_refused_without_a_draft(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], layout: dict, reason: str,
) -> None:
    _runtime(env, **layout)
    assert _cli(monkeypatch, "compose", "--forward", UID, "--to", TO, "--no-post") == 2
    assert f"FORWARD-REFUSED reason={reason}" in capsys.readouterr().err
    assert _drafts() == [] and _forward_dirs(env) == []


def test_an_approved_forward_whose_copy_changed_is_not_sent(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _runtime(env)
    assert _cli(monkeypatch, "compose", "--forward", UID, "--to", TO, "--no-post") == 0
    [record] = _drafts()
    Path(_option(record, "--attachment")[1]).write_bytes(b"tampered")
    monkeypatch.setattr(triage_gate, "_run_send", lambda _argv: pytest.fail("send must not run"))
    with pytest.raises(triage_gate.GateError, match="attachment_invalid"):
        triage_gate.execute_draft(record, triage_gate.Approval(ref="r", method="manual_reaction", owner="o"))


def test_compose_without_forward_still_requires_subject_and_body(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    assert _cli(monkeypatch, "compose", "--to", TO, "--body", "본문", "--no-post") == 2
    assert "--subject" in capsys.readouterr().err and _drafts() == []


def test_forward_and_in_reply_to_are_exclusive() -> None:
    with pytest.raises(SystemExit):
        triage_cli.build_parser().parse_args(["compose", "--to", TO, "--forward", UID, "--in-reply-to", UID])


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _gmail_runner(*, declared_shift: int = 0, rc: int = 0):
    pdf, xlsx = FILES.values()
    message = {"id": "18f1", "payload": {
        "mimeType": "multipart/mixed",
        "headers": [{"name": "From", "value": SENDER}, {"name": "To", "value": ORIGINAL_TO},
                    {"name": "Subject", "value": SUBJECT}, {"name": "Date", "value": "Mon, 12 Oct 2026 09:30:00 +0900"}],
        "parts": [
            {"mimeType": "multipart/alternative", "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64(BODY.encode())}},
                {"mimeType": "text/html", "body": {"data": _b64(b"<p>html</p>")}},
            ]},
            {"mimeType": "application/pdf", "filename": "협약서.pdf",
             "body": {"attachmentId": "att-1", "size": len(pdf) + declared_shift}},
            {"mimeType": "application/vnd.ms-excel", "filename": "예산표.xlsx",
             "body": {"data": _b64(xlsx), "size": len(xlsx)}},
        ],
    }}
    calls: list[list[str]] = []

    def run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        params = json.loads(argv[argv.index("--params") + 1])
        body = {"size": len(pdf), "data": _b64(pdf)} if "attachments" in argv else message
        assert params["userId"] == "me"
        return subprocess.CompletedProcess(argv, rc, json.dumps(body), "")

    return run, calls


def test_gmail_forward_writes_parts_byte_for_byte_into_the_same_draft(
    env: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, calls = _gmail_runner()
    monkeypatch.setattr(mail_forward, "from_gmail", functools.partial(mail_forward.from_gmail, runner=runner))
    assert _cli(monkeypatch, "compose", "--forward-gmail", "18f1", "--to", TO, "--no-post") == 0
    [record] = _drafts()
    paths = [Path(item) for item in _option(record, "--attachment")]
    assert [path.read_bytes() for path in paths] == list(FILES.values())
    assert [path.name for path in paths] == list(FILES)
    assert record["forward"]["source"] == "gmail" and record["subject"] == f"FW: {SUBJECT}"
    assert BODY in _option(record, "--body")[0]
    assert all(argv[:4] == ["gws", "gmail", "users", "messages"] and "get" in argv for argv in calls)
    assert len(calls) == 2  # the inline part needs no second read


@pytest.mark.parametrize(("shift", "rc", "reason"), [(1, 0, "attachment-size-mismatch"), (0, 3, "gmail-read-failed")])
def test_gmail_forward_refuses_a_short_or_failed_read(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], shift: int, rc: int, reason: str,
) -> None:
    runner, _calls = _gmail_runner(declared_shift=shift, rc=rc)
    monkeypatch.setattr(mail_forward, "from_gmail", functools.partial(mail_forward.from_gmail, runner=runner))
    assert _cli(monkeypatch, "compose", "--forward-gmail", "18f1", "--to", TO, "--no-post") == 2
    assert f"FORWARD-REFUSED reason={reason}" in capsys.readouterr().err
    assert _drafts() == [] and _forward_dirs(env) == []


def test_forward_subject_keeps_an_existing_forward_prefix() -> None:
    assert mail_forward.forward_subject("Fwd: 안내") == "Fwd: 안내"
    assert mail_forward.forward_subject("안내") == "FW: 안내"
