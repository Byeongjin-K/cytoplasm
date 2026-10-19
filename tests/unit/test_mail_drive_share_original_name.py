"""A received attachment is shared under its own name, Korean and spaces kept (live QA 2, 2026-10-19).

The staged copy has an ASCII name; the card and Drive showed "mail-9793361-2.hwpx" instead of
"1. 과업지시서_….hwpx". New file so the recorded share and gate cases keep their shape.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))

import mail_drive_share  # noqa: E402
import triage_cli  # noqa: E402

from automation.yoodori_gate import yoodori_read_gate as gate  # noqa: E402
from automation.yoodori_gate import yoodori_request_runtime as runtime  # noqa: E402

NAME = "1. 과업지시서_실증실험실 로컬 데이터 환경 조성.hwpx"
BODY = "공유합니다.\n\n<Drive 링크>\n"
ORIGIN = ["--origin-channel-id", "123456789", "--origin-message-id", "987654321"]


def _stage(home: Path, staging: Path) -> Path:
    data = home / ".hermes/mailon-runtime/state/data"
    (data / "attachments/42").mkdir(parents=True)
    (data / "attachments/42" / NAME).write_bytes(b"PK\x03\x04 sample")
    db = sqlite3.connect(data / "state.db")
    db.execute("CREATE TABLE attachments (uid TEXT, filename TEXT, status TEXT, local_path TEXT)")
    db.execute("INSERT INTO attachments VALUES ('42', ?, 'ok', NULL)", (NAME,))
    db.commit()
    db.close()
    path, _digest, _size = runtime.stage_mail_attachment(home, staging, "42", NAME)
    return path


def test_the_gate_shares_a_staged_attachment_under_its_own_name(tmp_path: Path, monkeypatch) -> None:
    # Given: a received attachment staged by mail-attachment.
    staging = tmp_path / "staging"
    monkeypatch.setattr(gate, "STAGING", staging)
    staged = _stage(tmp_path / "home", staging)
    # When: a share-link card is built from the staged path without a name.
    _, argv, _ = gate._card("mail-compose", ["--to", "a@example.invalid", "--subject", "공유", "--body", BODY,
                                             "--drive-share", str(staged), "--share-with", "a@example.invalid",
                                             *ORIGIN])
    # Then: the compose call carries the original name.
    assert staged.name == "mail-42-1.hwpx"
    assert argv[argv.index("--drive-name") + 1] == NAME


def test_drive_name_needs_a_drive_share() -> None:
    # Given / When / Then: a name without a file to share is refused.
    with pytest.raises(gate.Refused):
        gate._card("mail-compose", ["--to", "a@example.invalid", "--subject", "s", "--body", "b",
                                    "--drive-name", NAME, *ORIGIN])


def test_prepare_uploads_and_shows_the_given_name(tmp_path: Path, monkeypatch) -> None:
    # Given: a staged file and its original name.
    monkeypatch.setenv("TRIAGE_GATE_DIR", str(tmp_path / "gate"))
    staged = tmp_path / "mail-42-1.hwpx"
    staged.write_bytes(b"PK\x03\x04 sample")
    # When: the share is prepared with that name.
    share = mail_drive_share.prepare(str(staged), ("a@example.invalid",), body=BODY,
                                     today=date(2026, 10, 19), name=NAME)
    # Then: the card, the frozen copy and the Drive file all use it, bytes unchanged.
    assert share["source_name"] == NAME
    assert Path(share["frozen_path"]).name == NAME
    assert "과업지시서" in share["drive_name"] and share["drive_name"].endswith(".hwpx")
    assert Path(share["frozen_path"]).read_bytes() == staged.read_bytes()


@pytest.mark.parametrize(("given", "expected"), [
    ("../../etc/과업.hwpx", "etc 과업.hwpx"),
    ("이름만", "이름만.hwpx"),
    ("", "mail-42-1.hwpx"),
])
def test_path_characters_are_dropped_and_the_suffix_kept(tmp_path: Path, given: str, expected: str) -> None:
    # Given / When / Then: no path can be smuggled in, and the extension survives.
    assert mail_drive_share._shown_name(given, tmp_path / "mail-42-1.hwpx") == expected


def test_the_compose_parser_takes_drive_name() -> None:
    # Given / When: the real compose parser reads the gate's argv.
    args = triage_cli.build_parser().parse_args(
        ["compose", "--to", "a@example.invalid", "--drive-share", "/x", "--drive-name", NAME])
    # Then: the name arrives as given.
    assert args.drive_name == NAME
