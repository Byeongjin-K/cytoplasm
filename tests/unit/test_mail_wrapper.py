"""W4-1 mail wrapper — mocked mailon stdout schemas, exit-code mapping, masking."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "mail" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import mail_wrapper  # noqa: E402

STUB_MAILON = """\
import json, pathlib, sys, os
if "DISCORD_BOT_TOKEN" in os.environ:
    print("ENV-LEAK", file=sys.stderr); sys.exit(99)
mode = pathlib.Path("stub_mode.txt").read_text().strip() \
    if pathlib.Path("stub_mode.txt").is_file() else "ok"
cmd = sys.argv[1] if len(sys.argv) > 1 else ""
if cmd == "sync":
    if mode == "ok":
        print("2026-07-16 09:00:00 [INFO] mailon: skipping 0 uids")
        print("OK: 2 new mail(s) (retries: 1 recovered, 0 still failing)")
        sys.exit(0)
    if mode == "auth_fail":
        print("FAIL: LoginError: login flow did not reach the mailbox", file=sys.stderr)
        sys.exit(2)
    if mode == "config_fail":
        print("ERROR: Missing required env var MAILON_ID.", file=sys.stderr)
        sys.exit(1)
    if mode == "crash3":
        print("FAIL: RuntimeError: inbox folderUid selector not found", file=sys.stderr)
        sys.exit(3)
    if mode == "hang":
        import signal, time
        def _term(*_):
            pathlib.Path("terminated.txt").write_text("sigterm")
            sys.exit(3)
        signal.signal(signal.SIGTERM, _term)
        time.sleep(30)
        sys.exit(0)
if cmd == "status":
    if mode == "no_db":
        print("No database yet. Run a sync first."); sys.exit(0)
    print("Saved mails: 692")
    print("Last run #6: status=running new=0 started=2026-07-15T12:19:41 ")
    sys.exit(0)
if cmd == "resolve":
    name = sys.argv[sys.argv.index("--name") + 1] if "--name" in sys.argv else ""
    if mode == "auth_fail":
        print("FAIL: LoginError: login flow did not reach the mailbox", file=sys.stderr)
        sys.exit(2)
    if mode == "resolve_malformed":
        print("NOT-JSON garbage")
        sys.exit(0)
    candidates = [] if mode == "resolve_empty" else [
        {"group": "organization", "name": "김샘플",
         "email": "ksample@example.invalid", "org": "AX융합연구센터"},
        {"group": "contacts", "name": "김샘플",
         "email": "ksample@example.invalid", "org": ""},
    ]
    print(json.dumps({"status": "ok", "query": name,
                      "candidates": candidates, "post_count": 1}, ensure_ascii=False))
    sys.exit(0)
sys.exit(64)
"""

ROWS = [
    ("u-103", "inbox", "W4-1 픽스처 제목 gamma-303", "fixture-c@example.invalid",
     "2026-07-16T08:30:00", "data/mails/2026/07/f3.md", 3),
    ("u-102", "inbox", "[광고] 픽스처 스팸 beta-202", "ads@example.invalid",
     "2026-07-16T08:20:00", "data/mails/2026/07/f2.md", 2),
    ("u-101", "inbox", "특허 출원 검토 요청 alpha-101", "fixture-a@example.invalid",
     "2026-07-16T08:10:00", "data/mails/2026/07/f1.md", 1),
]


@pytest.fixture()
def stub_repo(tmp_path: Path, monkeypatch) -> Path:
    repo = tmp_path / "repo"
    (repo / "mailon").mkdir(parents=True)
    (repo / "data").mkdir()
    (repo / "mailon" / "__init__.py").write_text("")
    (repo / "mailon" / "main.py").write_text(STUB_MAILON)
    conn = sqlite3.connect(repo / "data" / "state.db")
    conn.execute(
        """CREATE TABLE messages (
        uid TEXT PRIMARY KEY, folder TEXT NOT NULL DEFAULT 'inbox', subject TEXT,
        sender TEXT, recv_date TEXT, markdown_path TEXT, saved_at INTEGER NOT NULL)"""
    )
    conn.executemany("INSERT INTO messages VALUES (?,?,?,?,?,?,?)", ROWS)
    conn.commit()
    conn.close()
    env_file = tmp_path / "env.secrets"
    env_file.write_text(
        "MAILON_ID=DUMMY-id\nMAILON_PW=DUMMY-pw\nMAILON_TOTP_SECRET=DUMMYBASE32\n"
        "DISCORD_BOT_TOKEN=DUMMY-must-not-pass\n"
    )
    monkeypatch.setenv("MAIL_WRAPPER_REPO", str(repo))
    monkeypatch.setenv("MAIL_WRAPPER_PYTHON", sys.executable)
    monkeypatch.setenv("MAIL_WRAPPER_ENV_FILE", str(env_file))
    monkeypatch.delenv("MAIL_WRAPPER_MASK_SALT", raising=False)
    return repo


def run_cli(*argv: str) -> tuple[int, dict]:
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / "mail_wrapper.py"), *argv],
        capture_output=True, text=True,
    )
    return proc.returncode, json.loads(proc.stdout)


def set_mode(repo: Path, mode: str) -> None:
    (repo / "stub_mode.txt").write_text(mode)


# --- interface cache (exit codes / collection contract) -----------------------

def test_cached_exit_codes_match_interface_doc() -> None:
    assert mail_wrapper.MAILON_INTERFACE["exit_codes"] == {
        0: "ok", 1: "config_error", 2: "auth_or_browser_error", 3: "sync_crash",
        10: "win_launcher_no_venv", 11: "win_launcher_no_agent_browser",
    }


def test_twenty_is_page_size_not_an_exit_code() -> None:
    assert 20 not in mail_wrapper.MAILON_INTERFACE["exit_codes"]
    assert mail_wrapper.MAILON_INTERFACE["collection"] == {
        "page_size": 20, "max_pages": 500,
    }


def test_send_json_schema_is_cached_but_send_is_refused(stub_repo: Path) -> None:
    assert mail_wrapper.MAILON_INTERFACE["send_json_keys"] == (
        "attachment_count", "attachment_manifest_sha256", "csrf_present",
        "network_post_count", "status", "verified",
    )
    assert mail_wrapper.MAILON_INTERFACE["send_error_json_keys"] == (
        "error_code", "message", "retryable", "stage", "status",
    )
    assert mail_wrapper.MAILON_INTERFACE["send_error_json_keys"] == (
        "error_code", "message", "retryable", "stage", "status",
    )
    assert "attachment_invalid" in mail_wrapper.MAILON_INTERFACE["send_error_codes"]
    assert "attachment_upload_failed" in mail_wrapper.MAILON_INTERFACE["send_error_codes"]
    with pytest.raises(ValueError, match="READ-ONLY"):
        mail_wrapper.run_mailon(mail_wrapper._cfg(), ["send", "--to", "x"])


# --- list ---------------------------------------------------------------------

def test_list_local_shape_and_order(stub_repo: Path) -> None:
    rc, out = run_cli("list", "--limit", "5")
    assert rc == 0
    assert out["wrapper"] == "mail-wrapper-v1" and out["status"] == "ok"
    assert out["synced"] is False and out["sync"] is None and out["count"] == 3
    assert [m["uid"] for m in out["mails"]] == ["u-103", "u-102", "u-101"]
    assert set(out["mails"][0]) == {
        "uid", "folder", "date", "subject", "sender", "markdown_path",
    }


def test_list_limit_applies(stub_repo: Path) -> None:
    rc, out = run_cli("list", "--limit", "2")
    assert rc == 0 and out["count"] == 2
    assert [m["uid"] for m in out["mails"]] == ["u-103", "u-102"]


def test_list_all_folders_is_explicit_and_includes_user_folder(stub_repo: Path) -> None:
    # Given: a message collected from a user-defined folder beside the inbox rows.
    with sqlite3.connect(stub_repo / "data" / "state.db") as connection:
        connection.execute(
            "INSERT INTO messages VALUES (?,?,?,?,?,?,?)",
            (
                "u-custom",
                "folder:62001",
                "사용자 폴더 검색 대상",
                "sender@example.invalid",
                "2026-07-16T08:40:00",
                "data/mails/2026/07/custom.md",
                4,
            ),
        )

    # When: the caller explicitly requests an all-folder read.
    rc, out = run_cli("list", "--limit", "10", "--all-folders")

    # Then: the custom-folder message is visible without changing default inbox behavior.
    assert rc == 0
    assert out["scope"] == "all-folders"
    assert [mail["uid"] for mail in out["mails"]][0] == "u-custom"
    default_rc, default_out = run_cli("list", "--limit", "10")
    assert default_rc == 0
    assert "u-custom" not in {mail["uid"] for mail in default_out["mails"]}


def test_list_all_folders_sync_requests_vendor_all_scope(
    stub_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a successful read-only sync adapter.
    calls: list[list[str]] = []

    def run_mailon(_cfg: dict, argv: list[str]) -> tuple[int, str, str]:
        calls.append(argv)
        return 0, "OK: 0 new mail(s) (retries: 0 recovered, 0 still failing)", ""

    monkeypatch.setattr(mail_wrapper, "run_mailon", run_mailon)
    args = mail_wrapper.build_parser().parse_args(
        ["list", "--limit", "5", "--sync", "--all-folders"]
    )

    # When: the all-folder list performs its optional live refresh.
    rc = mail_wrapper.cmd_list(args)

    # Then: the vendor sync receives an explicit all-folder scope.
    assert rc == 0
    assert calls == [["sync", "--limit", "5", "--folders", "all"]]


def test_list_sync_ok_parses_new_mails(stub_repo: Path) -> None:
    set_mode(stub_repo, "ok")
    rc, out = run_cli("list", "--limit", "5", "--sync")
    assert rc == 0 and out["synced"] is True
    assert out["sync"] == {"exit_code": 0, "meaning": "ok", "new_mails": 2}


def test_list_all_folders_opts_into_allfolder_sync_and_unfiltered_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an explicit all-folders list request.
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(mail_wrapper, "_cfg", lambda: {"mask_salt": ""})
    monkeypatch.setattr(
        mail_wrapper,
        "run_mailon",
        lambda _cfg, argv: calls.append(("sync", argv)) or (0, "OK: 1 new mail(s)", ""),
    )
    monkeypatch.setattr(
        mail_wrapper,
        "_db_rows",
        lambda _cfg, where, params, limit: calls.append(("query", (where, params, limit)))
        or [{
            "uid": "u-custom", "folder": "folder:60003", "subject": "Synthetic",
            "sender": "sender@example.invalid", "recv_date": "2026-08-16T00:00:00",
            "markdown_path": "data/mails/custom.md",
        }],
    )
    monkeypatch.setattr(mail_wrapper, "_emit", lambda _payload, exit_code: exit_code)
    args = Namespace(sync=True, limit=5, masked=False, all_folders=True)

    # When: the wrapper performs the read-only list operation.
    rc = mail_wrapper.cmd_list(args)

    # Then: sync explicitly requests all folders and the DB read has no inbox filter.
    assert rc == 0
    assert ("sync", ["sync", "--limit", "5", "--folders", "all"]) in calls
    assert ("query", ("1 = 1", (), 5)) in calls


def test_list_sync_auth_fail_surfaces_reauth_guidance(stub_repo: Path) -> None:
    set_mode(stub_repo, "auth_fail")
    rc, out = run_cli("list", "--sync")
    assert rc == 2
    assert out["status"] == "error" and out["error_code"] == "auth_error"
    assert out["mailon_exit_code"] == 2
    assert out["mailon_exit_meaning"] == "auth_or_browser_error"
    assert "재인증" in out["guidance"] and "mailon.main login" in out["guidance"]
    assert out["failure_signature"] == "login_error"
    # raw stderr is never echoed — only counts + signature
    assert out["stderr_lines"] == 1 and out["stderr_bytes"] > 0
    assert "did not reach the mailbox" not in json.dumps(out)


def test_list_sync_config_fail_maps_to_exit_1(stub_repo: Path) -> None:
    set_mode(stub_repo, "config_fail")
    rc, out = run_cli("list", "--sync")
    assert rc == 1 and out["error_code"] == "config_error"
    assert out["mailon_exit_code"] == 1
    assert "MAILON_ID" in out["guidance"]


def test_list_sync_crash3_falls_back_to_local_db(stub_repo: Path) -> None:
    set_mode(stub_repo, "crash3")
    rc, out = run_cli("list", "--limit", "5", "--sync")
    assert rc == 0 and out["status"] == "ok" and out["count"] == 3
    assert out["sync"]["exit_code"] == 3
    assert out["sync"]["fallback"] == "local-state-db"
    assert out["sync"]["failure_signature"] == "inbox_folder_uid_selector"


def test_list_sync_crash3_with_empty_db_exits_3(stub_repo: Path) -> None:
    set_mode(stub_repo, "crash3")
    (stub_repo / "data" / "state.db").unlink()
    rc, out = run_cli("list", "--sync")
    assert rc == 3 and out["error_code"] == "read_path_error"


def test_list_empty_db_without_sync_is_not_found(stub_repo: Path) -> None:
    (stub_repo / "data" / "state.db").unlink()
    rc, out = run_cli("list")
    assert rc == 5 and out["error_code"] == "not_found"


# --- masking --------------------------------------------------------------------

def test_masked_list_contains_no_plaintext(stub_repo: Path) -> None:
    rc, out = run_cli("list", "--masked")
    assert rc == 0 and out["masked"] is True
    raw = json.dumps(out, ensure_ascii=False)
    assert "픽스처" not in raw and "특허" not in raw and "example.invalid" not in raw
    for mail in out["mails"]:
        assert re.fullmatch(r"sha256:[0-9a-f]{16}", mail["subject"])
        assert re.fullmatch(r"sha256:[0-9a-f]{16}", mail["sender"])


def test_mask_is_deterministic_sha256_prefix() -> None:
    value = "W4-1 픽스처 제목 gamma-303"
    expected = "sha256:" + hashlib.sha256(value.encode()).hexdigest()[:16]
    assert mail_wrapper.mask_value(value) == expected
    assert mail_wrapper.mask_value(value, salt="s") != expected


# --- get / classify -------------------------------------------------------------

def test_get_found_and_not_found(stub_repo: Path) -> None:
    rc, out = run_cli("get", "u-101")
    assert rc == 0 and out["mail"]["uid"] == "u-101"
    rc, out = run_cli("get", "u-999")
    assert rc == 5 and out["error_code"] == "not_found"


def test_get_masked_body_is_hash_only(stub_repo: Path) -> None:
    md = stub_repo / "data" / "mails" / "2026" / "07" / "f1.md"
    md.parent.mkdir(parents=True)
    md.write_text("민감한 본문 내용")
    rc, out = run_cli("get", "u-101", "--body", "--masked")
    assert rc == 0
    assert "body" not in out["mail"] and "민감한" not in json.dumps(out, ensure_ascii=False)
    assert out["mail"]["body_sha256"] == hashlib.sha256("민감한 본문 내용".encode()).hexdigest()
    assert out["mail"]["body_bytes"] > 0


@pytest.mark.parametrize(
    ("subject", "sender", "category"),
    [
        ("[광고] 여름 세일", "ads@example.invalid", "spam"),
        ("특허 출원 검토 요청", "tlo@example.invalid", "important"),
        ("과제비 정산 안내", "admin@example.invalid", "important"),
        ("다음주 세미나 초청", "prof@example.invalid", "important"),
        ("서류 제출 기한 안내", "office@example.invalid", "important"),
        ("소식지 7월호", "noreply@example.invalid", "notice"),
        ("안부 인사", "friend@example.invalid", "general"),
    ],
)
def test_classify_metadata_rules(subject, sender, category) -> None:
    result = mail_wrapper.classify_metadata(subject, sender)
    assert result["category"] == category
    assert "route" not in result
    assert result["basis"] == "metadata-only"


def test_classify_by_uid_masked(stub_repo: Path) -> None:
    rc, out = run_cli("classify", "--uid", "u-101", "--masked")
    assert rc == 0
    assert "patent_sensitive" not in out["classification"]["flags"]
    assert "route" not in out["classification"]
    assert re.fullmatch(r"sha256:[0-9a-f]{16}", out["subject"])
    assert "특허" not in json.dumps(out, ensure_ascii=False)


def test_classify_requires_uid_or_subject(stub_repo: Path) -> None:
    rc, out = run_cli("classify")
    assert rc == 4 and out["error_code"] == "usage_error"


# --- status / env allowlist ------------------------------------------------------

def test_status_parses_human_stdout(stub_repo: Path) -> None:
    rc, out = run_cli("status")
    assert rc == 0
    assert out["saved_mails"] == 692 and out["no_db"] is False
    assert out["last_run"] == {
        "run_id": 6, "status": "running", "new_mails": 0,
        "started": "2026-07-15T12:19:41",
    }


def test_status_no_db_variant(stub_repo: Path) -> None:
    set_mode(stub_repo, "no_db")
    rc, out = run_cli("status")
    assert rc == 0 and out["no_db"] is True and out["saved_mails"] == 0


def test_sync_timeout_lets_mailon_finish_its_run_before_kill(
    stub_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A timed-out sync gets SIGTERM first (2026-10-13 15:44 KST incident).

    SIGKILL left the run row ``running`` forever and the browser alive for 15 hours;
    a terminated mailon closes its browser and records the run as failed.
    """
    set_mode(stub_repo, "hang")
    monkeypatch.setenv("MAIL_WRAPPER_TIMEOUT", "1")
    rc, _out, err = mail_wrapper.run_mailon(mail_wrapper._cfg(), ["sync", "--limit", "1"])
    assert rc == -7 and "timeout" in err
    assert (stub_repo / "terminated.txt").read_text() == "sigterm"


def test_env_allowlist_blocks_discord_token(stub_repo: Path, monkeypatch) -> None:
    # env file carries DISCORD_BOT_TOKEN; the stub exits 99 if it sees it.
    # A passing sync proves the allowlist stripped it.
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "DUMMY-in-process-env-too")
    set_mode(stub_repo, "ok")
    rc, out, err = mail_wrapper.run_mailon(mail_wrapper._cfg(), ["sync", "--limit", "1"])
    assert rc == 0, err
    env = mail_wrapper.build_subprocess_env(mail_wrapper._cfg())
    assert "DISCORD_BOT_TOKEN" not in env
    assert env["MAILON_ID"] == "DUMMY-id" and env["HEADLESS"] == "true"


def test_process_env_overrides_env_file(stub_repo: Path, monkeypatch) -> None:
    monkeypatch.setenv("MAILON_LOGIN_URL", "https://127.0.0.1:9/drill")
    env = mail_wrapper.build_subprocess_env(mail_wrapper._cfg())
    assert env["MAILON_LOGIN_URL"] == "https://127.0.0.1:9/drill"



# --- resolve (RED: T7 미구현) ----------------------------------------------------

def test_resolve_ok_shape(stub_repo: Path) -> None:
    rc, out = run_cli("resolve", "--name", "김샘플")
    assert rc == 0
    assert out["wrapper"] == "mail-wrapper-v1" and out["command"] == "resolve"
    assert out["status"] == "ok" and out["masked"] is False
    assert out["query"] == "김샘플"
    assert out["candidate_count"] == 2
    assert out["candidates"][0] == {
        "group": "organization", "name": "김샘플",
        "email": "ksample@example.invalid", "org": "AX융합연구센터",
    }


def test_resolve_masked_no_plaintext(stub_repo: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / "mail_wrapper.py"),
         "resolve", "--name", "김샘플", "--masked"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0
    raw = proc.stdout
    assert "김샘플" not in raw and "example.invalid" not in raw
    assert "AX융합연구센터" not in raw
    out = json.loads(raw)
    assert out["masked"] is True
    assert re.fullmatch(r"sha256:[0-9a-f]{16}", out["query"])
    for cand in out["candidates"]:
        assert cand["group"] in {"organization", "contacts"}
        for field in ("name", "email", "org"):
            assert re.fullmatch(r"sha256:[0-9a-f]{16}", cand[field])


def test_resolve_empty_is_ok(stub_repo: Path) -> None:
    set_mode(stub_repo, "resolve_empty")
    rc, out = run_cli("resolve", "--name", "김샘플")
    assert rc == 0 and out["status"] == "ok"
    assert out["candidate_count"] == 0 and out["candidates"] == []


def test_resolve_auth_fail_maps_exit_2(stub_repo: Path) -> None:
    set_mode(stub_repo, "auth_fail")
    rc, out = run_cli("resolve", "--name", "김샘플")
    assert rc == 2 and out["error_code"] == "auth_error"
    assert "재인증" in out["guidance"]


def test_resolve_malformed_stdout_is_read_path_error(stub_repo: Path) -> None:
    set_mode(stub_repo, "resolve_malformed")
    rc, out = run_cli("resolve", "--name", "김샘플")
    assert rc == 3 and out["error_code"] == "read_path_error"


def test_resolve_is_read_only_and_send_still_refused(stub_repo: Path) -> None:
    assert "resolve" in mail_wrapper.MAILON_INTERFACE["read_only_commands"]
    with pytest.raises(ValueError, match="READ-ONLY"):
        mail_wrapper.run_mailon(mail_wrapper._cfg(), ["send", "--to", "x"])
