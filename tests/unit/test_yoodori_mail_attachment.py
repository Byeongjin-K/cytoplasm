"""YooDori stages an attachment of the owner's own received mail (live QA 2, 2026-10-19).

The owner asked YooDori to share a mail attachment through Drive; only staged paths were
accepted and nothing could move a received attachment there. New file so the original gate
cases keep their byte-for-byte argv pins.
"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from automation.yoodori_gate import yoodori_read_gate as gate
from automation.yoodori_gate import yoodori_request_runtime as runtime

jobs = gate.yoodori_jobs
ORIGIN = ["--origin-channel-id", "123456789", "--origin-message-id", "987654321"]
NAME = "과업지시서(안).hwpx"
PAYLOAD = b"PK\x03\x04 hwpx sample"


def _mailbox(home: Path, rows: list[tuple[str, str, str, str | None]]) -> Path:
    data = home / ".hermes/mailon-runtime/state/data"
    data.mkdir(parents=True)
    db = sqlite3.connect(data / "state.db")
    db.execute("CREATE TABLE attachments (uid TEXT, filename TEXT, href TEXT, status TEXT, size_bytes INTEGER,"
               " error_msg TEXT, attempts INTEGER, local_path TEXT, first_seen INTEGER, last_attempt INTEGER)")
    for uid, filename, status, local in rows:
        db.execute("INSERT INTO attachments (uid, filename, status, local_path) VALUES (?, ?, ?, ?)",
                   (uid, filename, status, local))
    db.commit()
    db.close()
    return data


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    data = _mailbox(home, [("9793361", "목록.hwpx", "ok", None), ("9793361", NAME, "ok", None),
                           ("9793361", "missing.pdf", "ok", None), ("555", "남의.pdf", "fail", None)])
    folder = data / "attachments/9793361"
    folder.mkdir(parents=True)
    (folder / NAME).write_bytes(PAYLOAD)
    monkeypatch.setattr(gate, "HOME", home)
    monkeypatch.setattr(gate, "STAGING", home / ".local/share/yoodori-staging")
    notice_bot = SimpleNamespace(resolve_token=lambda environ=None: "token")
    monkeypatch.setattr(gate, "_release_modules", lambda: (None, notice_bot))
    monkeypatch.setattr(gate, "_owner_id", lambda: "111111111111111111")
    monkeypatch.setattr(gate, "_log", lambda *a: None)
    return home


def _run(monkeypatch: pytest.MonkeyPatch, *words: str) -> int:
    import shlex

    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", shlex.join(["mail-attachment", *words, *ORIGIN]))
    return gate.main()


def test_an_owner_mail_attachment_is_staged_and_only_its_path_is_printed(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: the owner's own message asks for the attachment.
    monkeypatch.setattr(jobs, "verify_owner_origin", lambda *a, **k: SimpleNamespace(link="l"))
    # When: YooDori asks for it by the name mail-get shows.
    rc = _run(monkeypatch, "9793361", NAME)
    # Then: a private staged copy with the same bytes, and only path, digest and size go out.
    staged = gate.STAGING / "mail-9793361-2.hwpx"
    out = capsys.readouterr().out
    assert rc == 0 and staged.read_bytes() == PAYLOAD
    assert staged.stat().st_mode & 0o777 == 0o600
    assert (gate.STAGING / "mail-9793361-2.hwpx.name").read_text(encoding="utf-8") == NAME
    assert out.strip() == (f"STAGED mail-9793361-2.hwpx sha256={hashlib.sha256(PAYLOAD).hexdigest()} "
                           f"bytes={len(PAYLOAD)} path={staged} original={NAME}")
    assert gate.checked_value(str(staged), "file", gate.STAGING) == str(staged)


@pytest.mark.parametrize(("uid", "name"), [
    ("555", "남의.pdf"),        # not a saved attachment of the owner's mailbox
    ("9793361", "없음.pdf"),     # no such attachment in that mail
    ("9793361", "missing.pdf"),  # recorded but not on the node
])
def test_other_or_missing_attachments_are_refused_and_nothing_is_staged(
    home: Path, monkeypatch: pytest.MonkeyPatch, uid: str, name: str,
) -> None:
    # Given: a verified owner message.
    monkeypatch.setattr(jobs, "verify_owner_origin", lambda *a, **k: SimpleNamespace(link="l"))
    # When / Then: refused, and the staging folder holds no file.
    assert _run(monkeypatch, uid, name) == 2
    assert not gate.STAGING.exists() or not any(gate.STAGING.iterdir())


def test_without_a_verified_owner_message_nothing_is_copied(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: the origin is not the owner's recent message.
    def refuse(*_a, **_k):
        raise jobs.JobRefused("origin message is not the owner's own message")

    monkeypatch.setattr(jobs, "verify_owner_origin", refuse)
    # When / Then: refused before any read of the mailbox.
    assert _run(monkeypatch, "9793361", NAME) == 2
    assert not gate.STAGING.exists()


def test_the_origin_is_required_and_a_link_outside_the_mail_runtime_is_refused(
    home: Path, tmp_path: Path,
) -> None:
    # Given / When / Then: no origin is a parse refusal.
    with pytest.raises(gate.Refused):
        gate.build(["mail-attachment", "9793361", NAME])
    # Given: a recorded path that points outside the node's mail runtime.
    outside = tmp_path / "secret.txt"
    outside.write_bytes(b"x")
    _mailbox(tmp_path / "other", [("1", "secret.txt", "ok", str(outside))])
    # When / Then: refused, never copied.
    with pytest.raises(runtime.Refused):
        runtime.stage_mail_attachment(tmp_path / "other", tmp_path / "stage", "1", "secret.txt")
    assert not (tmp_path / "stage").exists()
