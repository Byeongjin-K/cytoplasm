"""YooDori gate GAP 1-6 (2026-10-18): mail forward/share cards and owner-command jobs.

New file rather than more cases in test_yoodori_gate.py: those cases pin the original
read/card argv byte for byte, and these cover the owner-command surface added later.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from automation import auto_write
from automation.yoodori_gate import yoodori_jobs as jobs
from automation.yoodori_gate import yoodori_read_gate as gate

REPO = Path(__file__).resolve().parents[2]
PY = "/usr/bin/python3"
LIVE = "/srv/autophagy-skills/live"
ORIGIN = ["--origin-channel-id", "123456789", "--origin-message-id", "987654321"]
OWNER = "111111111111111111"


def b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


@pytest.fixture
def staged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(gate, "STAGING", tmp_path / "staging")
    monkeypatch.setattr(gate, "HOME", tmp_path / "home")
    gate.STAGING.mkdir()
    path = gate.STAGING / "report.pdf"
    path.write_bytes(b"%PDF-1.4 sample")
    return path


def _parses(folder: Path, module: str, argv: list[str]) -> None:
    script = ("import sys, importlib; sys.path.insert(0, sys.argv[1]); "
              "m=importlib.import_module(sys.argv[2]); m.build_parser().parse_args(sys.argv[3:])")
    parsed = subprocess.run([sys.executable, "-c", script, str(folder), module, *argv],
                            cwd=REPO, capture_output=True, text=True, check=False)
    assert parsed.returncode == 0, parsed.stderr


def test_forward_and_drive_share_reach_the_real_compose_parser(staged: Path) -> None:
    # Given: an institutional forward, a Gmail forward and a Drive share link mail.
    mail = REPO / "skills/mail/scripts"
    cases = [
        ["--to", "a@example.invalid", "--forward", "4321", "--body-b64", b64("참고 부탁드립니다")],
        ["--to", "a@example.invalid", "--forward-gmail", "18f0a1b2c3d4e5f6"],
        ["--to", "a@example.invalid", "--subject", "자료 공유", "--body-b64", b64("링크: <Drive 링크>"),
         "--drive-share", str(staged), "--share-with", "a@example.invalid",
         "--share-with", "b@example.invalid", "--drive-folder", "세미나"],
        ["--to", "a@example.invalid", "--subject", "첨부", "--body", "본문",
         "--attachment", str(staged), "--cc", "c@example.invalid", "--cc", "d@example.invalid"],
    ]
    for args in cases:
        # When
        name, argv, cwd = gate.build(["mail-compose", *args, *ORIGIN])
        # Then: every value arrives, repeats included, and compose's own parser accepts it.
        assert (name, cwd, argv[:3]) == ("mail-compose", gate.RELEASE,
                                         [PY, f"{LIVE}/mail/scripts/triage_cli.py", "compose"])
        assert argv.count("--share-with") == args.count("--share-with")
        assert argv.count("--cc") == args.count("--cc")
        _parses(mail, "triage_cli", argv[2:])


@pytest.mark.parametrize("args", [
    ["--to", "a@example.invalid", "--forward", "1", "--in-reply-to", "2"],
    ["--to", "a@example.invalid", "--forward-gmail", "not-hex!"],
    ["--to", "a@example.invalid", "--drive-share", "/etc/passwd", "--share-with", "a@example.invalid"],
    ["--to", "a@example.invalid", "--forward", "1", "--forward", "2"],
])
def test_forward_and_share_misuse_is_refused(staged: Path, args: list[str]) -> None:
    with pytest.raises(gate.Refused):
        gate.build(["mail-compose", *args, *ORIGIN])


def test_doc_job_routes_outputs_and_marks_writes(staged: Path) -> None:
    # Given / When
    report = gate.build_job(["doc", "report", "report", "--query", "실험", "--title", "주간", *ORIGIN])
    draft = gate.build_job(["doc", "doctype", "draft", "--name", "출장보고", "--inputs-json",
                            str(staged), "--out-name", "draft.md", *ORIGIN])
    listing = gate.build_job(["doc", "topics", "list", *ORIGIN])
    # Then: outputs land in the job directory, reads are not ledger writes.
    assert report.argv == [PY, f"{LIVE}/report/scripts/report_cli.py", "report", "--query", "실험",
                           "--title", "주간", "--outputs-root", "{OUT}"]
    assert report.write and report.timeout == 1800 and report.label == "report report 주간"
    assert draft.argv[-2:] == ["--out", "{OUT}/draft.md"] and str(staged) in draft.argv
    assert not listing.write and (listing.channel_id, listing.message_id) == ("123456789", "987654321")


def test_patent_stt_meeting_and_wiki_jobs(staged: Path) -> None:
    export = gate.build_job(["patent", "export", "--slug", "valve-seal", *ORIGIN])
    stt = gate.build_job(["stt", "ingest", "--file", str(staged), "--project", "과제", *ORIGIN])
    wiki = gate.build_job(["wiki-write", "--title", "실험 규칙", "--body-b64", b64("본문\n둘째 줄"), *ORIGIN])
    edit = gate.build_job(["wiki-write", "--edit", "lab-rule", "--body-b64", b64("고침"), *ORIGIN])
    assert export.argv == [PY, f"{LIVE}/patent-prep/scripts/patent_cli.py", "export-now",
                           "--slug", "valve-seal", *ORIGIN] and export.write
    assert stt.argv[-4:] == ["--notify-channel", "123456789", "--notify-message-id", "987654321"]
    assert stt.timeout == 4 * 3600
    assert wiki.argv == [PY, f"{LIVE}/wiki/scripts/wiki_cli.py", "write", "--title", "실험 규칙",
                         "--body", "본문\n둘째 줄", *ORIGIN]
    assert wiki.label == "위키 새 노트" and edit.label == "위키 lab-rule"
    _parses(REPO / "skills/wiki/scripts", "wiki_cli", wiki.argv[2:])


@pytest.mark.parametrize("words", [
    ["doc", "report", "report", "--query", "x"],
    ["doc", "report", "report", "--response-file", "/tmp/x", *ORIGIN],
    ["doc", "report", "report", "--outputs-root", "/tmp", *ORIGIN],
    ["doc", "doctype", "draft", "--inputs-json", "/etc/passwd", *ORIGIN],
    ["doc", "doctype", "draft", "--inputs-json", "~/.env.secrets", *ORIGIN],
    ["doc", "doctype", "draft", "--inputs-json", "../../.env.secrets", *ORIGIN],
    ["doc", "doctype", "draft", "--out-name", "../x", *ORIGIN],
    ["doc", "mail", "compose", *ORIGIN],
    ["doc", "patent", "create", "--slug", "x", *ORIGIN],
    ["doc", "report", "organize", *ORIGIN],
    ["patent", "export-execute", "--slug", "x", *ORIGIN],
    ["patent", "export", "--slug", "x", "--allow-plaintext", *ORIGIN],
    ["stt", "ingest", "--file", str(Path.home() / "x.m4a"), *ORIGIN],
    ["stt", "ingest", "--notify-channel", "1", *ORIGIN],
    ["wiki-write", "--title", "t", "--body", "plain", *ORIGIN],
    ["wiki-write", "--edit", "../x", "--body-b64", b64("b"), *ORIGIN],
    ["wiki-write", "--body-b64", b64("b"), *ORIGIN],
])
def test_job_misuse_is_refused(staged: Path, words: list[str]) -> None:
    with pytest.raises(gate.Refused):
        gate.build_job(words)


def _fetch(author: dict, *, guild: str | None = "222", kind: int = 0):
    def fetch(path: str, _token: str) -> dict:
        if path.endswith("/messages/987654321"):
            return {"id": "987654321", "author": author}
        return {"type": kind, **({"guild_id": guild} if guild else {})}
    return fetch


def _now() -> float:
    return ((987654321 >> 22) + jobs.DISCORD_EPOCH_MS) / 1000 + 60


def test_origin_must_be_the_owners_recent_message() -> None:
    owner = {"id": OWNER}
    origin = jobs.verify_owner_origin("123456789", "987654321", OWNER, "t", now=_now(), fetch=_fetch(owner))
    assert origin.link == "https://discord.com/channels/222/123456789/987654321"
    dm = jobs.verify_owner_origin("123456789", "987654321", OWNER, "t", now=_now(),
                                  fetch=_fetch(owner, guild=None, kind=1))
    assert dm.link.startswith("https://discord.com/channels/@me/")
    for author in ({"id": OWNER, "bot": True}, {"id": "333"}):
        with pytest.raises(jobs.JobRefused):
            jobs.verify_owner_origin("123456789", "987654321", OWNER, "t", now=_now(), fetch=_fetch(author))
    with pytest.raises(jobs.JobRefused, match="24 hours"):
        jobs.verify_owner_origin("123456789", "987654321", OWNER, "t", now=_now() + 2 * 86400,
                                 fetch=_fetch(owner))

    def broken(_path: str, _token: str) -> dict:
        raise OSError("down")
    with pytest.raises(jobs.JobRefused):
        jobs.verify_owner_origin("123456789", "987654321", OWNER, "t", now=_now(), fetch=broken)
    with pytest.raises(jobs.JobRefused):
        jobs.verify_owner_origin("123456789", "987654321", OWNER, "", now=_now(), fetch=_fetch(owner))


def test_write_job_needs_the_release_switch(staged: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: the node never received auto-write-yoodori.
    monkeypatch.setattr(auto_write, "state_root", lambda home=None: Path(gate.HOME) / ".hermes/auto-write")
    job = gate.build_job(["doc", "topics", "add", "로봇", *ORIGIN])
    # When / Then: nothing is created or run.
    with pytest.raises(gate.Refused, match="auto-write-yoodori"):
        gate.run_job(job)
    assert not jobs.jobs_root(gate.HOME).exists()


def test_job_runs_detached_collects_outputs_and_records_the_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a write job whose command prints a file it created under the account home.
    home = tmp_path / "home"
    (home / "outputs").mkdir(parents=True)
    produced = home / "outputs" / "report-1.md"
    code = f"open({str(produced)!r}, 'w').write('# 보고서'); print('REPORT path={produced}')"
    path = jobs.new_job(home, {
        "name": "doc", "argv": [sys.executable, "-c", code], "write": True, "label": "report report 주간",
        "timeout": 60, "origin": {"channel_id": "1", "message_id": "2", "link": "https://discord.com/channels/9/1/2"},
    })
    ledger = tmp_path / "ledger"
    monkeypatch.setattr(auto_write, "state_root", lambda home=None: ledger)
    # When
    rc = jobs.run(path, home=home, release=REPO)
    # Then: the output is fetchable, the receipt carries the owner message link.
    assert rc == 0
    rc, text = jobs.report(path)
    assert "JOB-DONE" in text and "ledger=yes" in text and "report-1.md" in text
    assert jobs.output_file(path, "report-1.md").read_text() == "# 보고서"
    row = json.loads((ledger / "ledger.jsonl").read_text())
    assert (row["kind"], row["source_id"]) == ("yoodori", "discord:1/2")
    assert row["path"] == "report report 주간 · https://discord.com/channels/9/1/2"
    assert "https://discord.com/channels/9/1/2" in auto_write.summary_text([row])
    with pytest.raises(jobs.JobRefused):
        jobs.output_file(path, "../spec.json")


def test_launch_detaches_and_finishes_without_the_caller(tmp_path: Path) -> None:
    home = tmp_path / "home"
    path = jobs.new_job(home, {"name": "doc", "argv": [sys.executable, "-c", "print('done')"],
                               "write": False, "label": "x", "timeout": 30,
                               "origin": {"channel_id": "1", "message_id": "2", "link": ""}})
    jobs.launch(path, {"PATH": os.environ["PATH"], "HOME": str(home)}, python=sys.executable)
    assert jobs.wait(path, 30, step=0.1)
    assert jobs.report(path)[0] == 0 and "done" in (path / "stdout").read_text()


def test_wiki_write_creates_and_edits_without_a_card(tmp_path: Path) -> None:
    env = {**os.environ, "HOME": str(tmp_path), "WIKI_ROOT": str(tmp_path / "wiki"),
           "WIKI_GATE_DIR": str(tmp_path / "gate"), "AUTOPHAGY_SKILL_LIVE_ROOT": str(tmp_path / "live")}
    cli = [sys.executable, str(REPO / "skills/wiki/scripts/wiki_cli.py")]

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([*cli, *args], env=env, capture_output=True, text=True, check=False)

    first = run("write", "--title", "Lab Rule", "--slug", "lab-rule", "--body", "첫 본문", *ORIGIN)
    assert first.returncode == 0, first.stderr
    assert "method=owner_command ref=discord:123456789/987654321" in first.stdout
    assert run("write", "--title", "Lab Rule", "--slug", "lab-rule", "--body", "x", *ORIGIN).returncode == 2
    assert run("write", "--edit", "lab-rule", "--body", "고친 본문", *ORIGIN).returncode == 0
    shown = run("show", "lab-rule")
    assert shown.returncode == 0 and "고친 본문" in shown.stdout
    audit = [json.loads(line) for line in (tmp_path / "gate/audit.jsonl").read_text().splitlines()]
    assert [row["approval"]["method"] for row in audit] == ["owner_command", "owner_command"]
    assert not (tmp_path / "gate/drafts").exists() or not list((tmp_path / "gate/drafts").iterdir())


def test_patent_export_now_uploads_without_a_card(tmp_path: Path) -> None:
    # Given: a draft, a single-owner archive folder and a fake Drive client.
    gws = tmp_path / "gws"
    gws.write_text("#!/usr/bin/env bash\n"
                   "if [[ \"$2\" == permissions ]]; then echo '{\"permissions\":[{\"type\":\"user\",\"role\":\"owner\"}]}';\n"
                   "else echo '{\"id\":\"file-1\",\"webViewLink\":\"https://drive.example/file-1\"}'; fi\n")
    gws.chmod(0o755)
    (tmp_path / "interop.json").write_text(json.dumps({"owner_id": OWNER}))
    env = {**os.environ, "HOME": str(tmp_path), "PATENT_GWS_BIN": str(gws),
           "PATENT_ARCHIVE_FOLDER_ID": "folder-1", "INTEROP_CONFIG": str(tmp_path / "interop.json"),
           "PATENT_DRAFT_ROOT": str(tmp_path / "drafts"), "PATENT_STATUS_ROOT": str(tmp_path / "status"),
           "AUTOPHAGY_SKILL_LIVE_ROOT": str(tmp_path / "live")}
    cli = [sys.executable, str(REPO / "skills/patent-prep/scripts/patent_cli.py")]
    created = subprocess.run([*cli, "create", "--slug", "valve-seal"], env=env,
                             capture_output=True, text=True, check=False)
    assert created.returncode == 0, created.stderr
    draft = next((tmp_path / "drafts").rglob("valve-seal")) / "draft.md"
    draft.write_text("# 발명\n내용")
    # When
    done = subprocess.run([*cli, "export-now", "--slug", "valve-seal", "--mode", "plaintext", *ORIGIN],
                          env=env, capture_output=True, text=True, check=False)
    # Then: uploaded, and the audit names the owner message instead of a card.
    assert done.returncode == 0, done.stderr
    assert "PATENT-EXPORTED slug=valve-seal file=file-1" in done.stdout
    audit = next(tmp_path.rglob("audit.jsonl")).read_text().splitlines()
    record = json.loads(audit[-1])
    assert record["approval"] == {"channel": "123456789", "message_id": "987654321",
                                  "method": "owner_command", "owner_id": OWNER}
