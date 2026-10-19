"""Drive upload + per-person share + mail under one owner ✅ (owner request 2026-10-14).

The real compose CLI writes the draft; a fake gws stands in for Drive and a fake mailon
send for the webmail, so each test watches what would have left the node.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))

import mail_drive_share  # noqa: E402
import mail_preflight  # noqa: E402
import mail_send_retry  # noqa: E402
import triage_approval  # noqa: E402
import triage_cli  # noqa: E402
import triage_confirm  # noqa: E402
import triage_core  # noqa: E402
import triage_gate  # noqa: E402

from automation import drive_outputs  # noqa: E402
from automation.drive_client import DriveClient, DriveClientError  # noqa: E402

TO = "director@example.invalid"
PERSON = "Viewer.Person@Example.com"
BODY = "소장님, 수정 파일을 보내드립니다.\n\n- 다운로드: <Drive 링크>\n\n감사합니다."
PAYLOAD = b"PK\x03\x04" + bytes(range(256)) * 40
APPROVAL = triage_gate.Approval(ref="reaction:m-1", method="manual_reaction", owner="o")


class FakeDrive:
    """In-memory Drive answering the gws argv the client sends."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.folders: dict[tuple[str, str], str] = {}
        self.files: dict[tuple[str, str], str] = {}
        self.blobs: dict[str, bytes] = {}
        self.permissions: dict[str, list[dict[str, str]]] = {}
        self.fail_upload = False
        self.corrupt_upload = False
        self.extra_permission: dict[str, str] | None = None
        self._n = 0

    def _id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n}"

    def __call__(self, argv: list[str]) -> dict[str, object]:
        self.calls.append(argv)
        op = argv[2]
        if op == "+upload":
            if self.fail_upload:
                raise DriveClientError("drive +upload 실패 rc=1: quota")
            file_id = self._id("file")
            self.files[(argv[argv.index("--name") + 1], argv[argv.index("--parent") + 1])] = file_id
            data = Path(argv[3]).read_bytes()
            self.blobs[file_id] = data + b"x" if self.corrupt_upload else data
            self.permissions[file_id] = [{"type": "user", "role": "owner", "emailAddress": "owner@x.y"}]
            if self.extra_permission:
                self.permissions[file_id].append(self.extra_permission)
            return {"id": file_id}
        method = argv[3]
        params = json.loads(argv[argv.index("--params") + 1]) if "--params" in argv else {}
        if op == "permissions" and method == "list":
            return {"permissions": list(self.permissions[params["fileId"]])}
        if op == "permissions" and method == "create":
            body = json.loads(argv[argv.index("--json") + 1])
            self.permissions[params["fileId"]].append(body)
            return {"id": self._id("perm")}
        if method == "list":
            query = params["q"]
            name = query.split("name = '", 1)[1].split("'", 1)[0]
            parent = query.split("' in parents", 1)[0].rsplit("'", 1)[1]
            registry = self.folders if "folder" in query else self.files
            found = registry.get((name, parent))
            return {"files": [{"id": found, "name": name}] if found else []}
        if method == "create":
            meta = json.loads(argv[argv.index("--json") + 1])
            folder_id = self._id("fold")
            self.folders[(meta["name"], meta["parents"][0])] = folder_id
            return {"id": folder_id}
        if method == "get":
            fields = params.get("fields")
            if fields == "sha256Checksum,size":
                data = self.blobs[params["fileId"]]
                return {"sha256Checksum": hashlib.sha256(data).hexdigest(), "size": str(len(data))}
            if fields == "id,trashed,parents":
                return {"id": params["fileId"], "trashed": False, "parents": ["root"]}
            return {"webViewLink": f"https://drive.google.com/file/d/{params['fileId']}/view"}
        raise AssertionError(f"unexpected argv {argv}")

    def uploads(self) -> int:
        return sum(1 for call in self.calls if call[2] == "+upload")

    def grants(self) -> list[dict[str, object]]:
        return [json.loads(call[call.index("--json") + 1]) for call in self.calls if call[2:4] == ["permissions", "create"]]


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


@pytest.fixture
def drive(env: Path, monkeypatch: pytest.MonkeyPatch) -> FakeDrive:
    fake = FakeDrive()
    client = DriveClient(gws_bin="gws", folder_cache=env / "folders.json", runner=fake)
    monkeypatch.setattr(drive_outputs, "client_from_environment", lambda: client)
    return fake


@pytest.fixture
def sends(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    sent: list[tuple[str, ...]] = []
    reply = json.dumps({"status": "submitted"})
    monkeypatch.setattr(triage_gate, "_run_send", lambda argv: (sent.append(argv), (0, reply, ""))[1])
    return sent


def _package(env: Path) -> Path:
    path = env / "outbox" / "Planner-수정본.zip"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(PAYLOAD)
    return path


def _compose(monkeypatch: pytest.MonkeyPatch, *extra: str, body: str = BODY) -> int:
    monkeypatch.setattr(sys, "argv", ["triage_cli", "compose", "--to", TO, "--subject", "Re: 실행 파일",
                                      "--body", body, "--no-post", *extra])
    return triage_cli.main()


def _record() -> dict:
    [path] = sorted(triage_gate._public_drafts_dir().glob("*.json"))
    return json.loads(path.read_text(encoding="utf-8"))


def _sent_body(argv: tuple[str, ...]) -> str:
    return argv[list(argv).index("--body") + 1]


def test_one_execution_uploads_verifies_shares_view_only_and_sends_the_link(
    env: Path, drive: FakeDrive, sends: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    package = _package(env)
    assert _compose(monkeypatch, "--drive-share", str(package), "--share-with", PERSON) == 0
    record = _record()
    share = record["drive_share"]
    assert f"DRIVE-SHARE draft={record['id']} file={share['drive_name']} people=1 role=reader" in capsys.readouterr().out
    assert share["grantees"] == [PERSON.lower()] and share["role"] == "reader"
    assert share["sha256"] == hashlib.sha256(PAYLOAD).hexdigest() and share["size_bytes"] == len(PAYLOAD)
    assert share["folder"][:2] == ["autophagy", "공유"] and share["drive_name"].endswith("_Planner-수정본.zip")
    assert Path(share["frozen_path"]).read_bytes() == PAYLOAD
    card = triage_core.render_approvals_message(record)
    assert "보기 권한(reader)만: `viewer.person@example.com` — 링크 공개 없음" in card
    assert "하나라도 실패하면 메일은 나가지 않습니다" in card
    assert drive.calls == [] and sends == []  # nothing leaves the node before the ✅

    triage_gate.execute_draft(record, APPROVAL)

    assert drive.uploads() == 1
    assert drive.grants() == [{"type": "user", "role": "reader", "emailAddress": PERSON.lower()}]
    [grant_call] = [call for call in drive.calls if call[2:4] == ["permissions", "create"]]
    assert json.loads(grant_call[grant_call.index("--params") + 1])["sendNotificationEmail"] is False
    [argv] = sends
    link = "https://drive.google.com/file/d/file"
    assert link in _sent_body(argv) and mail_drive_share.PLACEHOLDER not in _sent_body(argv)
    stored = _record()
    assert stored["status"] == "executed" and stored["drive_share_result"]["link"].startswith(link)
    audit = [json.loads(line) for line in (env / "approvals.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [row["action"] for row in audit][:1] == ["drive.share"]


@pytest.mark.parametrize(
    ("field", "value"),
    [("grantees", ["other@example.com"]), ("folder", ["autophagy", "공유", "다른곳"]),
     ("sha256", "0" * 64), ("role", "writer"), ("drive_name", "renamed.zip"), ("size_bytes", 1)],
)
def test_the_approval_hash_binds_every_share_field(
    env: Path, drive: FakeDrive, sends: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch,
    field: str, value: object,
) -> None:
    assert _compose(monkeypatch, "--drive-share", str(_package(env)), "--share-with", PERSON) == 0
    record = _record()
    tampered = {**record, "drive_share": {**record["drive_share"], field: value}}
    assert triage_core.draft_sha256(tampered) != record["sha256"]
    with pytest.raises(triage_gate.GateError, match="해시 불일치"):
        triage_gate.execute_draft(tampered, APPROVAL)
    assert drive.calls == [] and sends == []


@pytest.mark.parametrize(
    "permission",
    [{"type": "anyone", "role": "reader"}, {"type": "domain", "role": "reader", "domain": "x.y"},
     {"type": "user", "role": "writer", "emailAddress": "viewer.person@example.com"},
     {"type": "group", "role": "reader", "emailAddress": "team@example.com"},
     {"type": "user", "role": "reader", "emailAddress": "stranger@example.com"}],
)
def test_a_file_shared_beyond_the_named_readers_never_sends_the_mail(
    env: Path, drive: FakeDrive, sends: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch,
    permission: dict[str, str],
) -> None:
    drive.extra_permission = permission
    assert _compose(monkeypatch, "--drive-share", str(_package(env)), "--share-with", PERSON) == 0
    with pytest.raises(triage_gate.GateError, match="cause=drive_permission_refused") as raised:
        triage_gate.execute_draft(_record(), APPROVAL)
    assert raised.value.exit_code == 6 and sends == []
    stored = _record()
    assert stored["status"] == "blocked" and stored["last_error"]["stage"] == "drive_share"
    assert all(grant.get("type") == "user" and grant.get("role") == "reader" for grant in drive.grants())
    notice = mail_send_retry.failure_text(stored, "x")
    assert "멈춘 단계: drive_share · 원인: drive_permission_refused" in notice
    assert "메일은 발송되지 않았습니다" in notice


def test_a_failed_upload_stops_the_mail_and_is_retried_on_the_backoff(
    env: Path, drive: FakeDrive, sends: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    drive.fail_upload = True
    assert _compose(monkeypatch, "--drive-share", str(_package(env)), "--share-with", PERSON) == 0
    with pytest.raises(triage_gate.GateError, match="cause=drive_upload_failed retryable=true"):
        triage_gate.execute_draft(_record(), APPROVAL)
    stored = _record()
    assert sends == [] and drive.grants() == []
    assert stored["status"] == "pending" and stored["failed_attempts"] == 1 and stored["next_attempt_at"]


def test_a_drive_copy_whose_checksum_differs_is_not_shared_or_mailed(
    env: Path, drive: FakeDrive, sends: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    drive.corrupt_upload = True
    assert _compose(monkeypatch, "--drive-share", str(_package(env)), "--share-with", PERSON) == 0
    with pytest.raises(triage_gate.GateError, match="cause=drive_checksum_mismatch"):
        triage_gate.execute_draft(_record(), APPROVAL)
    assert sends == [] and drive.grants() == [] and _record()["status"] == "blocked"


def test_a_frozen_copy_changed_after_the_card_is_not_uploaded(
    env: Path, drive: FakeDrive, sends: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _compose(monkeypatch, "--drive-share", str(_package(env)), "--share-with", PERSON) == 0
    record = _record()
    Path(record["drive_share"]["frozen_path"]).write_bytes(b"tampered")
    with pytest.raises(triage_gate.GateError, match="cause=local_copy_changed"):
        triage_gate.execute_draft(record, APPROVAL)
    assert drive.calls == [] and sends == []


def test_a_send_that_fails_after_the_share_reuses_the_upload_next_time(
    env: Path, drive: FakeDrive, monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _compose(monkeypatch, "--drive-share", str(_package(env)), "--share-with", PERSON) == 0
    outcomes = iter([(1, json.dumps({"status": "failed", "error_code": "send_failed"}), ""),
                     (0, json.dumps({"status": "submitted"}), "")])
    sent: list[tuple[str, ...]] = []
    monkeypatch.setattr(triage_gate, "_run_send", lambda argv: (sent.append(argv), next(outcomes))[1])
    with pytest.raises(triage_gate.GateError):
        triage_gate.execute_draft(_record(), APPROVAL)
    triage_gate.execute_draft({**_record(), "next_attempt_at": None}, APPROVAL)
    assert drive.uploads() == 1 and len(drive.grants()) == 1
    assert len(sent) == 2 and _sent_body(sent[0]) == _sent_body(sent[1])


def test_a_send_argv_planted_in_a_stored_draft_is_never_what_gets_sent(
    env: Path, sends: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _compose(monkeypatch, body="평범한 본문") == 0
    record = _record()
    triage_gate.execute_draft({**record, "send_argv": ["python3", "-c", "evil"]}, APPROVAL)
    assert sends == [tuple(record["argv"])]


def test_without_the_owner_approval_the_watch_touches_neither_drive_nor_mail(
    env: Path, drive: FakeDrive, sends: list[tuple[str, ...]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _compose(monkeypatch, "--drive-share", str(_package(env)), "--share-with", PERSON) == 0
    record = _record()
    path = triage_gate._draft_path(record["id"])
    triage_gate.write_json(path, {**record, "message_id": "m-1"})
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: "o")
    monkeypatch.setattr(triage_approval, "expire_retired_approval", lambda _draft: False)
    monkeypatch.setattr(triage_cli, "_remind_pending", lambda _draft, _config: None)
    monkeypatch.setattr(mail_preflight, "execute_cli_draft", triage_gate.execute_draft)

    def no_reaction(_draft: dict) -> str:
        raise triage_gate.GateError("승인 없음", 1)

    monkeypatch.setattr(triage_confirm, "resolve_reaction", no_reaction)
    assert triage_cli.cmd_watch(argparse.Namespace()) == 0
    assert drive.calls == [] and sends == [] and _record()["status"] == "pending"

    monkeypatch.setattr(triage_confirm, "resolve_reaction", lambda _draft: triage_confirm.APPROVE_EMOJI)
    monkeypatch.setattr(triage_cli, "_notify_sent", lambda _draft, _method: None)
    assert triage_cli.cmd_watch(argparse.Namespace()) == 0
    assert drive.uploads() == 1 and len(sends) == 1 and _record()["status"] == "executed"


@pytest.mark.parametrize(
    ("extra", "body", "message"),
    [((), "링크 자리가 없는 본문", "정확히 한 번"),
     (("--share-with", "not-an-address"), BODY, "--share-with"),
     ((), BODY, "--share-with")],
)
def test_compose_refuses_an_incomplete_share_request_without_a_draft(
    env: Path, drive: FakeDrive, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    extra: tuple[str, ...], body: str, message: str,
) -> None:
    assert _compose(monkeypatch, "--drive-share", str(_package(env)), *extra, body=body) == 2
    assert message in capsys.readouterr().err
    assert not list(triage_gate._public_drafts_dir().glob("*.json"))
    share_dir = env / "gate" / "drive-share"
    assert not share_dir.exists() or not any(share_dir.iterdir())


def test_share_options_without_a_file_are_refused(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    assert _compose(monkeypatch, "--share-with", PERSON) == 2
    assert "--drive-share" in capsys.readouterr().err


def test_share_target_fixes_the_folder_and_name_at_request_time() -> None:
    parts, name = drive_outputs.share_target("a b.zip", on=date(2026, 10, 14), project="외부")
    assert parts == ("autophagy", "공유", "외부", "2026") and name == "2026-10-14_a b.zip"


def test_share_refuses_a_folder_outside_the_share_tree(tmp_path: Path) -> None:
    local = tmp_path / "f.bin"
    local.write_bytes(PAYLOAD)
    digest = hashlib.sha256(PAYLOAD).hexdigest()
    with pytest.raises(drive_outputs.ShareRefused) as raised:
        drive_outputs.share(local, ("autophagy", "회의록", "2026"), "f.bin", sha256=digest, size=len(PAYLOAD),
                            grantees=(PERSON,), client=DriveClient("gws", tmp_path / "c.json", runner=FakeDrive()))
    assert raised.value.code == "drive_folder_refused"
