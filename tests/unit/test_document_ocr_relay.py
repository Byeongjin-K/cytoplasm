"""Document OCR on the node (owner Q335 2026-10-21): the released marker points the extractor at the
file-relay client, and YooDori reads a saved mail attachment as text through the same extractor."""
from __future__ import annotations

import re
import shlex
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import automation.document_text as document_text
from automation import document_ocr
from automation.yoodori_gate import yoodori_read_gate as gate
from automation.yoodori_gate import yoodori_request_runtime as runtime

sys.path.insert(0, str(Path(__file__).resolve().parent))
import unit_node  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
ORIGIN = ["--origin-channel-id", "123456789", "--origin-message-id", "987654321"]
NAME_RULE = re.compile(r"^[0-9]+-[0-9]+-[0-9]+\.(pdf|hwp|png|jpg|jpeg)$")


def _fake_relay(home: Path, answer: bytes | None, *, seen: list[str]) -> threading.Thread:
    """The operator relay's file contract: read `<id>.<ext>` from outbox, answer `<id>.md|.err`."""
    outbox, inbox = home / "outbox/ocr", home / "inbox/ocr"

    def serve() -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            requests = [p for p in outbox.glob("*") if not p.name.startswith(".")] if outbox.is_dir() else []
            if requests:
                request = requests[0]
                seen.append(request.name)
                stem = request.name.rsplit(".", 1)[0]
                if answer is not None:
                    (inbox / f"{stem}.md").write_bytes(answer)
                else:
                    (inbox / f"{stem}.err").write_bytes(b"dori-ocr: OCR failed\n")
                return
            time.sleep(0.02)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return thread


def test_relay_hands_the_file_over_and_prints_the_answer(tmp_path, capsys):
    # Given: a scanned page and a relay that answers in markdown.
    source = tmp_path / "스캔.PDF"
    source.write_bytes(b"%PDF-1.4 scan")
    home = tmp_path / "agent"
    seen: list[str] = []
    relay = _fake_relay(home, "# 표\n| Q2 | 5,400 |".encode(), seen=seen)
    # When
    rc = document_ocr.relay(source, home=home, timeout=5, poll=0.02)
    relay.join()
    # Then: the relay saw a name it accepts, the answer reached stdout, and nothing is left behind.
    assert rc == 0
    assert NAME_RULE.match(seen[0])
    assert capsys.readouterr().out == "# 표\n| Q2 | 5,400 |"
    assert not any((home / "outbox/ocr").iterdir()) and not any((home / "inbox/ocr").iterdir())


def test_relay_failure_and_silence_are_exit_codes_and_leave_no_files(tmp_path):
    # Given: one relay that refuses, then no relay at all.
    source = tmp_path / "옛문서.hwp"
    source.write_bytes(b"HWP")
    home = tmp_path / "agent"
    relay = _fake_relay(home, None, seen=[])
    # When / Then: OCR failure is 4, no answer in time is 5, bad input is 2; the request is withdrawn.
    assert document_ocr.relay(source, home=home, timeout=5, poll=0.02) == 4
    relay.join()
    assert document_ocr.relay(source, home=home, timeout=0.1, poll=0.02) == 5
    assert not any((home / "outbox/ocr").iterdir()) and not any((home / "inbox/ocr").iterdir())
    (tmp_path / "a.txt").write_text("x")
    assert document_ocr.relay(tmp_path / "a.txt", home=home) == 2


@pytest.mark.parametrize("marker,expected", [(None, False), (b"off", False), (b"on\n", False), (b"on", True)])
def test_extractor_uses_the_relay_only_for_an_exact_released_marker(tmp_path, monkeypatch, marker, expected):
    # Given: no OCR environment variable, only the deployed marker.
    monkeypatch.delenv(document_text.OCR_COMMAND_ENV, raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    if marker is not None:
        (tmp_path / "document-ocr.mode").write_bytes(marker)
    # When
    command = document_text.ocr_command()
    # Then: exact `on` selects this repository's relay client; anything else keeps OCR off.
    assert bool(command) is expected
    if expected:
        assert command[-2:] == (str(REPO / "automation/document_ocr.py"), "relay")
        assert ".hwp" in document_text.readable_suffixes()


def test_explicit_command_still_wins_over_the_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "document-ocr.mode").write_bytes(b"on")
    monkeypatch.setenv(document_text.OCR_COMMAND_ENV, "dori-ocr --fast")
    assert document_text.ocr_command() == ("dori-ocr", "--fast")


def test_relay_client_runs_as_a_plain_file_with_isolated_python(tmp_path):
    # Given / When: the exact argv the extractor uses, with nothing on PYTHONPATH.
    result = subprocess.run([sys.executable, "-I", str(REPO / "automation/document_ocr.py"), "relay",
                             str(tmp_path / "missing.pdf")], capture_output=True, text=True, check=False)
    # Then: it starts without the repository on sys.path and refuses the missing input itself.
    assert result.returncode == 2 and "document-ocr:" in result.stderr


def _mailbox(home: Path, name: str, payload: bytes) -> None:
    data = home / ".hermes/mailon-runtime/state/data"
    data.mkdir(parents=True)
    db = sqlite3.connect(data / "state.db")
    db.execute("CREATE TABLE attachments (uid TEXT, filename TEXT, status TEXT, local_path TEXT)")
    db.execute("INSERT INTO attachments VALUES ('9793361', '목록.txt', 'ok', NULL)")
    db.execute("INSERT INTO attachments VALUES ('9793361', ?, 'ok', NULL)", (name,))
    db.commit()
    db.close()
    folder = data / "attachments/9793361"
    folder.mkdir(parents=True)
    (folder / name).write_bytes(payload)


def test_attachment_text_job_is_a_read_that_needs_the_owner_message():
    job = gate.build_job(["mail-attachment-text", "9793361", "공고문 2026.pdf", *ORIGIN])
    assert job.write is False and job.timeout > document_text.OCR_TIMEOUT_SECONDS
    assert job.argv[2:] == ["attachment-text", "9793361", "공고문 2026.pdf", "{OUT}"]
    assert Path(job.argv[1]).name == "yoodori_request_runtime.py"
    with pytest.raises(gate.Refused):
        gate.build_job(["mail-attachment-text", "9793361", "공고문 2026.pdf"])
    with pytest.raises(gate.Refused):
        gate.build_job(["mail-attachment-text", "abc", "x.pdf", *ORIGIN])


def test_attachment_text_starts_only_for_a_verified_owner_message(tmp_path, monkeypatch):
    # Given: the origin is not the owner's message.
    monkeypatch.setattr(gate, "HOME", tmp_path / "home")
    monkeypatch.setattr(gate, "_release_modules",
                        lambda: (None, SimpleNamespace(resolve_token=lambda environ=None: "t")))
    monkeypatch.setattr(gate, "_owner_id", lambda: "111111111111111111")

    def refuse(*_a, **_k):
        raise gate.yoodori_jobs.JobRefused("origin is not the owner's message")

    monkeypatch.setattr(gate.yoodori_jobs, "verify_owner_origin", refuse)
    # When / Then: no job directory, nothing read.
    with pytest.raises(gate.yoodori_jobs.JobRefused):
        gate.run_job(gate.build_job(["mail-attachment-text", "9793361", "x.pdf", *ORIGIN]))
    assert not gate.yoodori_jobs.jobs_root(gate.HOME).exists()


def test_attachment_text_writes_the_full_text_and_prints_a_preview(tmp_path, monkeypatch, capsys):
    # Given: a saved text attachment of the owner's mail; OCR is off.
    monkeypatch.delenv(document_text.OCR_COMMAND_ENV, raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    home, out = tmp_path / "home", tmp_path / "out"
    out.mkdir()
    _mailbox(home, "회의 메모.txt", "첫 줄\n둘째 줄".encode())
    # When
    rc = runtime.attachment_text(home, "9793361", "회의 메모.txt", out)
    # Then
    assert rc == 0
    assert (out / "mail-9793361-2.md").read_text(encoding="utf-8") == "첫 줄\n둘째 줄"
    first, *rest = capsys.readouterr().out.splitlines()
    assert first == "ATTACHMENT-TEXT status=ok name=mail-9793361-2.txt sections=1 chars=8 file=mail-9793361-2.md"
    assert rest == ["첫 줄", "둘째 줄"]


def test_attachment_text_reads_a_scan_through_ocr_and_refuses_without_it(tmp_path, monkeypatch, capsys):
    # Given: a legacy hwp attachment.
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    home, out = tmp_path / "home", tmp_path / "out"
    out.mkdir()
    _mailbox(home, "공고.hwp", b"HWP Document File")
    # When OCR is off: a reason, not a crash, and no file.
    monkeypatch.delenv(document_text.OCR_COMMAND_ENV, raising=False)
    assert runtime.attachment_text(home, "9793361", "공고.hwp", out) == 3
    assert "status=refused" in capsys.readouterr().out and not any(out.iterdir())
    # When OCR answers (a stand-in command printing markdown)
    script = tmp_path / "ocr.py"
    script.write_text("import sys\nprint('# 공고\\n| 마감 | 11-30 |')\n")
    monkeypatch.setenv(document_text.OCR_COMMAND_ENV, f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}")
    assert runtime.attachment_text(home, "9793361", "공고.hwp", out) == 0
    assert (out / "mail-9793361-2.md").read_text(encoding="utf-8") == "# 공고\n| 마감 | 11-30 |"


def test_attachment_text_cli_refuses_an_unknown_attachment(tmp_path):
    home = tmp_path / "home"
    _mailbox(home, "a.txt", b"x")
    result = subprocess.run(
        [sys.executable, str(REPO / "automation/yoodori_gate/yoodori_request_runtime.py"), "attachment-text",
         "9793361", "없음.pdf", str(tmp_path)],
        env={**unit_node.env(), "HOME": str(home), "PYTHONPATH": str(REPO)}, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 2 and result.stderr.startswith("ATTACHMENT-TEXT-REFUSED ")
