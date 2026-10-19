"""RED-first contract for the owner-DM-approved mail compose path.

The production surface (triage_pipeline.compose_and_post, triage_cli.cmd_compose,
DM-channel-bound reaction resolution, compose audit records) DOES NOT exist yet —
every test here must FAIL until it is implemented.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Never

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "mail" / "scripts"))

import mail_quote  # noqa: E402
import triage_cli  # noqa: E402
import triage_approval  # noqa: E402
import triage_binding  # noqa: E402
import triage_confirm  # noqa: E402
import triage_core  # noqa: E402
import triage_gate  # noqa: E402
import triage_mode  # noqa: E402
import triage_pipeline  # noqa: E402
import triage_store  # noqa: E402

from automation.interop.approval_surface import ChannelFacts, POLICY_VERSION  # noqa: E402


OWNER_ID = "owner-1"
APPROVALS_CHANNEL = "approvals-1"
DM_CHANNEL = "100000000000000002"
AGENT_CHAT_CHANNEL = "100000000000000003"
AGENT_CHAT_THREAD = "100000000000000004"
NOTICE_MESSAGE_ID = "100000000000000005"
MESSAGE_ID = "message-1"

COMPOSE_TO = "x@y.z"
COMPOSE_CC = "cc1@example.test, cc2@example.test"
COMPOSE_SUBJECT = "테스트 제목"
COMPOSE_BODY = "테스트 본문"


def _compose_draft(
    *, channel_id: str = DM_CHANNEL, message_id: str = MESSAGE_ID, body: str = COMPOSE_BODY
) -> dict:
    record = {
        "argv": list(triage_core.build_send_argv("python3", COMPOSE_TO, COMPOSE_SUBJECT, body)),
        "body": body,
        "category": "compose",
        "channel_id": channel_id,
        "flags": [],
        "id": "abc123",
        "kind": "compose",
        "mail_subject": "",
        "message_id": message_id,
        "sender": "",
        "sender_masked": triage_core.mask_value(""),
        "sensitive": False,
        "surface": "owner-dm",
        "status": "pending",
        "subject": COMPOSE_SUBJECT,
        "tags": [],
        "to": COMPOSE_TO,
        "uid": "compose:abc",
        "uid_opaque": triage_core.mask_value("compose:abc"),
        "policy_version": 1,
    }
    record["sha256"] = triage_core.draft_sha256(record)
    return record


def _compose_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRIAGE_MAILON_PYTHON", "python3")
    monkeypatch.setenv("TRIAGE_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("TRIAGE_MAIL_HOME", str(tmp_path / "mail"))
    monkeypatch.setenv("TRIAGE_DB", str(tmp_path / "triage.db"))
    interop = tmp_path / "interop-config.json"
    interop.write_text(
        json.dumps({"owner_id": OWNER_ID, "agent_chat_channel_id": AGENT_CHAT_CHANNEL}),
        encoding="utf-8",
    )
    monkeypatch.setenv("INTEROP_CONFIG", str(interop))


def _dm_post_api(requests: list[tuple[str, str]]):
    def request(method: str, path: str, payload: dict | None = None):
        del payload
        requests.append((method, path))
        if method == "POST" and path == "/users/@me/channels":
            return {"id": DM_CHANNEL}
        if method == "GET" and path == f"/channels/{DM_CHANNEL}":
            return {"type": 1, "name": "", "recipients": [{"id": OWNER_ID}]}
        if method == "GET" and path == f"/channels/{AGENT_CHAT_CHANNEL}":
            return {"type": 0, "name": "agent-chat", "guild_id": "guild-1"}
        if method == "POST" and path == f"/channels/{AGENT_CHAT_CHANNEL}/messages":
            return {"id": NOTICE_MESSAGE_ID}
        if method == "POST" and path == (
            f"/channels/{AGENT_CHAT_CHANNEL}/messages/{NOTICE_MESSAGE_ID}/threads"
        ):
            return {"id": AGENT_CHAT_THREAD}
        if method == "GET" and path == f"/channels/{AGENT_CHAT_THREAD}":
            return {
                "type": 11, "name": f"메일 발신 · {COMPOSE_SUBJECT}",
                "parent_id": AGENT_CHAT_CHANNEL,
            }
        if method == "POST":
            return {"id": MESSAGE_ID}
        if method == "PUT":
            return None
        raise AssertionError(f"unexpected Discord call: {method} {path}")

    return request


def _dm_reaction_api(draft: dict, users_by_emoji: dict[str, list[dict]]):
    def request(method: str, path: str, payload: dict | None = None):
        del payload
        if method == "GET" and path == f"/channels/{DM_CHANNEL}":
            return {"type": 1, "name": "", "recipients": [{"id": OWNER_ID}]}
        if method == "GET" and path == f"/channels/{DM_CHANNEL}/messages/{draft['message_id']}":
            return {"content": f"draft sha256:{draft['sha256']}"}
        if method == "GET" and path.startswith(f"/channels/{DM_CHANNEL}/") and "/reactions/" in path:
            for emoji, users in users_by_emoji.items():
                quoted = triage_confirm.quote(emoji, safe="")
                if path.endswith(f"/reactions/{quoted}?limit=100"):
                    return users
            return []
        # Neutral legacy #approvals answers: no owner decision exists there.
        if method == "GET" and "/messages/" in path and "/reactions/" not in path:
            return {"content": f"draft sha256:{draft['sha256']}"}
        if method == "GET" and "/reactions/" in path:
            return []
        if method == "POST":
            return {"id": MESSAGE_ID}
        return None

    return request


def _run_compose_watch(
    monkeypatch: pytest.MonkeyPatch, draft: dict, users_by_emoji: dict[str, list[dict]]
):
    sent: list[triage_gate.Approval] = []
    discarded: list[str] = []
    notices: list[str] = []
    monkeypatch.setattr(triage_mode, "effective_mode", lambda: "full-go")
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER_ID)
    monkeypatch.setattr(triage_confirm, "_api", _dm_reaction_api(draft, users_by_emoji))
    monkeypatch.setattr(triage_gate, "list_drafts", lambda: [draft])
    monkeypatch.setattr(triage_gate, "execute_draft", lambda _draft, approval: sent.append(approval))
    monkeypatch.setattr(triage_gate, "discard_draft", lambda draft_id: discarded.append(draft_id))
    monkeypatch.setattr(triage_confirm, "dm_owner", lambda content: notices.append(content))
    monkeypatch.setattr(triage_cli, "cmd_process", lambda _args: 0)

    assert triage_cli.cmd_watch(argparse.Namespace()) == 0
    return sent, discarded, notices


def test_compose_draft_record_binds_kind_channel_and_frozen_argv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: a compose environment with a fake Discord API and a bound owner
    _compose_env(monkeypatch, tmp_path)
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER_ID)
    monkeypatch.setattr(triage_confirm, "_api", _dm_post_api([]))
    # When: the compose pipeline drafts and posts a new outbound mail
    record = triage_pipeline.compose_and_post(COMPOSE_TO, COMPOSE_SUBJECT, COMPOSE_BODY, post=True)
    # Then: the record binds compose kind, its agent-chat thread, and the frozen argv
    assert record["kind"] == "compose"
    assert record["channel_id"] == AGENT_CHAT_THREAD
    assert record["surface"] == "agent-chat-thread"
    assert record["policy_version"] == POLICY_VERSION
    assert record["sender"] == ""
    assert record["mail_subject"] == ""
    assert record["category"] == "compose"
    assert record["uid"].startswith("compose:")
    assert record["argv"] == list(
        triage_core.build_send_argv(
            "python3", COMPOSE_TO, COMPOSE_SUBJECT, COMPOSE_BODY,
            body_html=mail_quote.html_body(COMPOSE_BODY, ""),
        )
    )
    assert record["sha256"] == triage_core.draft_sha256(record)


def test_compose_cc_is_frozen_rendered_and_bound_to_hash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _compose_env(monkeypatch, tmp_path)
    record = triage_pipeline.compose_and_post(
        COMPOSE_TO, COMPOSE_SUBJECT, COMPOSE_BODY, post=False, cc=COMPOSE_CC,
    )

    assert record["cc"] == COMPOSE_CC
    assert record["argv"][record["argv"].index("--cc") + 1] == COMPOSE_CC
    assert f"- Cc: `{COMPOSE_CC}`" in triage_core.render_approvals_message(record)
    changed = {**record, "cc": "other@example.test"}
    assert triage_core.draft_sha256(changed) != record["sha256"]


def test_compose_cli_accepts_repeated_cc() -> None:
    args = triage_cli.build_parser().parse_args([
        "compose", "--to", COMPOSE_TO,
        "--cc", "cc1@example.test", "--cc", "cc2@example.test",
        "--subject", COMPOSE_SUBJECT, "--body", COMPOSE_BODY,
    ])

    assert args.cc == ["cc1@example.test", "cc2@example.test"]


def test_compose_post_targets_dm_channel_with_reaction_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: a recording fake Discord API behind the compose environment
    requests: list[tuple[str, str]] = []
    _compose_env(monkeypatch, tmp_path)
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER_ID)
    monkeypatch.setattr(triage_confirm, "_api", _dm_post_api(requests))
    # When: the compose pipeline posts the confirmation request
    triage_pipeline.compose_and_post(COMPOSE_TO, COMPOSE_SUBJECT, COMPOSE_BODY, post=True)
    # Then: an agent-chat announcement anchors the thread before its approval card
    # is posted, then ✅ and ⛔ are pre-added in that order.
    expected = [
        ("POST", f"/channels/{AGENT_CHAT_CHANNEL}/messages"),
        ("POST", f"/channels/{AGENT_CHAT_CHANNEL}/messages/{NOTICE_MESSAGE_ID}/threads"),
        ("GET", f"/channels/{AGENT_CHAT_THREAD}"),
        ("POST", f"/channels/{AGENT_CHAT_THREAD}/messages"),
        ("PUT", f"/channels/{AGENT_CHAT_THREAD}/messages/{MESSAGE_ID}/reactions/%E2%9C%85/@me"),
        ("PUT", f"/channels/{AGENT_CHAT_THREAD}/messages/{MESSAGE_ID}/reactions/%E2%9B%94/@me"),
    ]
    assert requests == expected


def test_compose_no_post_creates_draft_without_network(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: a Discord API that refuses every network call
    _compose_env(monkeypatch, tmp_path)
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER_ID)

    def deny_network(method: str, path: str, payload: dict | None = None):
        raise AssertionError(f"network call: {method} {path}")

    monkeypatch.setattr(triage_confirm, "_api", deny_network)
    # When: the compose pipeline drafts without posting
    record = triage_pipeline.compose_and_post(COMPOSE_TO, COMPOSE_SUBJECT, COMPOSE_BODY, post=False)
    # Then: the draft exists with no channel or message binding
    assert record["channel_id"] == ""
    assert record["message_id"] == ""
    assert record["surface"] is None
    assert record["policy_version"] is None


def test_compose_no_post_renders_without_the_approval_facade(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Given: a console-only draft path with the approval façade unavailable.
    _compose_env(monkeypatch, tmp_path)
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER_ID)

    def unavailable(_name: str) -> Never:
        raise ImportError

    monkeypatch.setattr(triage_binding, "_repo_module", unavailable)

    # When: the compose pipeline renders its no-post output.
    record = triage_pipeline.compose_and_post(COMPOSE_TO, COMPOSE_SUBJECT, COMPOSE_BODY, post=False)

    # Then: pure console output does not depend on the approval lifecycle façade.
    assert record["message_id"] == ""


def test_resolve_reaction_uses_draft_bound_dm_channel(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Given: a DM-bound compose draft and an API that only answers DM-channel paths
    draft = _compose_draft()
    monkeypatch.setenv("TRIAGE_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER_ID)
    approve_quoted = triage_confirm.quote(triage_confirm.APPROVE_EMOJI, safe="")

    def dm_only(method: str, path: str, payload: dict | None = None):
        del payload
        if path != f"/channels/{DM_CHANNEL}" and not path.startswith(f"/channels/{DM_CHANNEL}/"):
            raise AssertionError(f"non-DM Discord call: {method} {path}")
        if method == "GET" and path == f"/channels/{DM_CHANNEL}":
            return {"type": 1, "name": "", "recipients": [{"id": OWNER_ID}]}
        if method == "GET" and path == f"/channels/{DM_CHANNEL}/messages/{MESSAGE_ID}":
            return {"content": f"draft sha256:{draft['sha256']}"}
        if method == "GET" and path.endswith(f"/reactions/{approve_quoted}?limit=100"):
            return [{"id": OWNER_ID, "bot": False}]
        if method == "GET" and "/reactions/" in path:
            return []
        raise AssertionError(f"unexpected Discord call: {method} {path}")

    monkeypatch.setattr(triage_confirm, "_api", dm_only)
    # When: the resolver reads the owner decision for the compose draft
    action = triage_confirm.resolve_reaction(draft)
    # Then: the ✅ approval is found on the draft-bound DM channel
    assert action == triage_confirm.APPROVE_EMOJI


def test_resolve_reaction_fails_closed_for_compose_without_channel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: a compose draft with no bound channel and a network that must stay silent
    draft = _compose_draft(channel_id="")
    monkeypatch.setenv("TRIAGE_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER_ID)

    def deny_network(method: str, path: str, payload: dict | None = None):
        raise AssertionError(f"network call: {method} {path}")

    monkeypatch.setattr(triage_confirm, "_api", deny_network)
    # When / Then: the resolver fails closed without any #approvals fallback
    with pytest.raises(triage_gate.GateError):
        triage_confirm.resolve_reaction(draft)


def test_watch_sends_dm_bound_compose_once(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a pending DM-bound compose draft with an owner-only ✅ on the DM message
    draft = _compose_draft()
    # When: the production watch tick resolves the DM-bound approval
    sent, discarded, notices = _run_compose_watch(
        monkeypatch, draft, {triage_confirm.APPROVE_EMOJI: [{"id": OWNER_ID, "bot": False}]}
    )
    # Then: the compose draft is sent exactly once, never discarded, and the
    # owner receives a send-success DM (2026-07-20)
    assert len(sent) == 1
    assert discarded == []
    assert len(notices) == 1 and "발송 완료" in notices[0]


def test_watch_prefers_cancel_on_dm_compose(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: the owner reacted with BOTH ✅ and ⛔ on the DM compose message
    draft = _compose_draft()
    # When: the production watch tick resolves the DM-bound reactions
    sent, discarded, notices = _run_compose_watch(
        monkeypatch,
        draft,
        {
            triage_confirm.APPROVE_EMOJI: [{"id": OWNER_ID, "bot": False}],
            triage_confirm.CANCEL_EMOJI: [{"id": OWNER_ID, "bot": False}],
        },
    )
    # Then: cancellation wins — zero sends and the draft is discarded
    assert sent == []
    assert discarded == [draft["id"]]


def test_render_compose_full_text_with_hash_footer() -> None:
    # Given: a compose draft whose body exceeds the reply-preview truncation length
    long_body = "가" * 700
    draft = _compose_draft(body=long_body)
    # When: the confirmation message is rendered
    out = triage_core.render_approvals_message(draft)
    # Then: compose header, recipient, UNTRUNCATED body, and the shared hash footer
    assert "[mail-triage] 새 메일 발송 승인 요청" in out
    assert COMPOSE_TO in out
    assert long_body in out
    assert "…" not in out
    assert f"draft: `{draft['id']}` sha256: `{draft['sha256']}`" in out


def test_execute_draft_compose_audit_record(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Given: an approved compose draft, tmp audit surfaces, and a fake successful send
    approval_log = tmp_path / "approvals.jsonl"
    monkeypatch.setenv("TRIAGE_APPROVAL_LOG", str(approval_log))
    _compose_env(monkeypatch, tmp_path)
    monkeypatch.setattr(triage_mode, "effective_mode", lambda: "full-go")
    monkeypatch.setattr(triage_gate, "_run_send", lambda argv: (0, '{"status": "submitted"}', ""))
    draft = _compose_draft()
    # When: the gate executes the compose draft under a DM reaction approval
    triage_gate.execute_draft(
        draft,
        triage_gate.Approval(ref=f"reaction:{MESSAGE_ID}", method="manual_reaction", owner=OWNER_ID),
    )
    # Then: the approval log carries a compose-specific audit record bound to owner-dm
    records = [
        json.loads(line)
        for line in approval_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    compose_records = [item for item in records if item.get("action") == "mail.compose_send"]
    assert len(compose_records) == 1
    audit = compose_records[0]
    assert audit["target_id"] == f"mail:compose:{triage_core.mask_value(COMPOSE_TO)}"
    assert audit["approval"]["channel"] == "owner-dm"


@pytest.mark.parametrize(
    ("error_code", "stage", "retryable", "expected_text"),
    [
        ("attachment_invalid", "validation", False, "첨부 파일이 유효하지"),
        ("attachment_unsupported", "attachment_upload", False, "첨부 업로드 기능"),
        ("attachment_upload_failed", "attachment_upload", True, "첨부 업로드에 실패"),
    ],
)
def test_attachment_errors_keep_stable_classification_without_send_failure_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error_code: str, stage: str,
    retryable: bool, expected_text: str,
) -> None:
    approval_log = tmp_path / "approvals.jsonl"
    monkeypatch.setenv("TRIAGE_APPROVAL_LOG", str(approval_log))
    _compose_env(monkeypatch, tmp_path)
    monkeypatch.setattr(triage_mode, "effective_mode", lambda: "full-go")
    payload = json.dumps({
        "status": "error", "error_code": error_code,
        "stage": stage, "retryable": retryable,
        "message": "must not be exposed by the gate",
    })
    monkeypatch.setattr(triage_gate, "_run_send", lambda argv: (3, payload, ""))
    draft = _compose_draft()
    draft_path = tmp_path / "gate" / "drafts" / f"{draft['id']}.json"
    triage_mode.write_json(draft_path, draft)

    with pytest.raises(triage_gate.GateError, match=expected_text) as caught:
        triage_gate.execute_draft(
            draft,
            triage_gate.Approval(
                ref=f"reaction:{MESSAGE_ID}", method="manual_reaction", owner=OWNER_ID,
            ),
        )

    assert f"code={error_code}" in str(caught.value)
    assert "must not be exposed" not in str(caught.value)
    assert triage_store.consecutive_send_failures(tmp_path / "triage.db") == 0
    stored = json.loads(draft_path.read_text(encoding="utf-8"))
    assert stored["status"] == ("pending" if retryable else "blocked")
    assert stored["last_error"] == {
        "error_code": error_code, "stage": stage, "retryable": retryable,
    }
    records = [json.loads(line) for line in approval_log.read_text(encoding="utf-8").splitlines()]
    audit = next(item for item in records if item.get("action") == "mail.compose_send")
    assert audit["result"] == {
        "status": "failed", "error_code": error_code,
        "stage": stage, "retryable": retryable,
    }


def test_real_send_failure_still_increments_failure_counter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("TRIAGE_APPROVAL_LOG", str(tmp_path / "approvals.jsonl"))
    _compose_env(monkeypatch, tmp_path)
    monkeypatch.setattr(triage_mode, "effective_mode", lambda: "full-go")
    payload = json.dumps({
        "status": "error", "error_code": "send_failed",
        "stage": "send", "retryable": True,
        "message": "safe provider-neutral message",
    })
    monkeypatch.setattr(triage_gate, "_run_send", lambda argv: (4, payload, ""))

    with pytest.raises(triage_gate.GateError, match="메일 발송에 실패"):
        triage_gate.execute_draft(
            _compose_draft(),
            triage_gate.Approval(
                ref=f"reaction:{MESSAGE_ID}", method="manual_reaction", owner=OWNER_ID,
            ),
        )

    assert triage_store.consecutive_send_failures(tmp_path / "triage.db") == 1


@pytest.mark.parametrize(
    ("cause", "kept"),
    [("mailbox_list_unavailable", True), ("could not resolve folder uid", False)],
)
def test_send_failure_keeps_the_mailon_cause_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cause: str, kept: bool,
) -> None:
    """2026-10-12: the failed draft said only send_failed, so nobody could tell a
    login failure from a webmail page that never rendered."""
    approval_log = tmp_path / "approvals.jsonl"
    monkeypatch.setenv("TRIAGE_APPROVAL_LOG", str(approval_log))
    _compose_env(monkeypatch, tmp_path)
    monkeypatch.setattr(triage_mode, "effective_mode", lambda: "full-go")
    payload = json.dumps({
        "status": "error", "error_code": "send_failed", "stage": "send",
        "retryable": True, "cause": cause, "message": "safe provider-neutral message",
    })
    monkeypatch.setattr(triage_gate, "_run_send", lambda argv: (2, payload, ""))
    draft = _compose_draft()
    draft_path = tmp_path / "gate" / "drafts" / f"{draft['id']}.json"
    triage_mode.write_json(draft_path, draft)

    with pytest.raises(triage_gate.GateError) as caught:
        triage_gate.execute_draft(
            draft,
            triage_gate.Approval(
                ref=f"reaction:{MESSAGE_ID}", method="manual_reaction", owner=OWNER_ID,
            ),
        )

    stored = json.loads(draft_path.read_text(encoding="utf-8"))
    records = [json.loads(line) for line in approval_log.read_text(encoding="utf-8").splitlines()]
    audit = next(item for item in records if item.get("action") == "mail.compose_send")
    if kept:
        assert stored["last_error"]["cause"] == cause
        assert audit["result"]["cause"] == cause
        assert f"cause={cause}" in str(caught.value)
    else:  # free text is not a cause code and must not reach the records
        assert "cause" not in stored["last_error"]
        assert "cause" not in audit["result"]
        assert cause not in str(caught.value)


def test_cli_compose_blocked_in_no_go(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: the effective mail mode forbids any outbound compose
    monkeypatch.setattr(triage_mode, "effective_mode", lambda: "no-go")
    # When / Then: the compose CLI refuses before creating anything
    with pytest.raises(triage_gate.GateError):
        triage_cli.cmd_compose(
            argparse.Namespace(to=COMPOSE_TO, subject="s", body="b", no_post=True)
        )


def _dm_payload_api(payloads: list[dict]):
    def request(method: str, path: str, payload: dict | None = None):
        if payload is not None:
            payloads.append({"method": method, "path": path, **payload})
        if method == "POST" and path == "/users/@me/channels":
            return {"id": DM_CHANNEL}
        if method == "GET" and path == f"/channels/{DM_CHANNEL}":
            return {"type": 1, "name": "", "recipients": [{"id": OWNER_ID}]}
        if method == "GET" and path == f"/channels/{AGENT_CHAT_CHANNEL}":
            return {"type": 0, "name": "agent-chat", "guild_id": "guild-1"}
        if method == "POST" and path == f"/channels/{AGENT_CHAT_CHANNEL}/messages":
            return {"id": NOTICE_MESSAGE_ID}
        if method == "POST" and path == (
            f"/channels/{AGENT_CHAT_CHANNEL}/messages/{NOTICE_MESSAGE_ID}/threads"
        ):
            return {"id": AGENT_CHAT_THREAD}
        if method == "GET" and path == f"/channels/{AGENT_CHAT_THREAD}":
            return {
                "type": 11, "name": f"메일 발신 · {COMPOSE_SUBJECT}",
                "parent_id": AGENT_CHAT_CHANNEL,
            }
        if method == "POST":
            return {"id": MESSAGE_ID}
        return None

    return request


def _executed_related_draft() -> dict:
    return {"kind": "compose", "status": "executed",
            "created": triage_core.utc_now(),
            "to": "x@y.z, missing@y.z", "subject": "테스트 제목 회신 요청"}


def test_compose_warns_on_recipient_gap_from_recent_related_send(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: a recent executed compose to a superset of recipients
    payloads: list[dict] = []
    _compose_env(monkeypatch, tmp_path)
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER_ID)
    monkeypatch.setattr(triage_confirm, "_api", _dm_payload_api(payloads))
    monkeypatch.setattr(triage_gate, "list_drafts", lambda: [_executed_related_draft()])
    # When: composing a follow-up that drops one of them
    record = triage_pipeline.compose_and_post(COMPOSE_TO, COMPOSE_SUBJECT, COMPOSE_BODY, post=True)
    # Then: the approval DM carries the omission warning, record hash untouched
    contents = [p["content"] for p in payloads if "content" in p]
    assert any("⚠️" in c and "missing@y.z" in c for c in contents)
    assert record["sha256"] == triage_core.draft_sha256(record)


def test_compose_no_warning_without_gap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payloads: list[dict] = []
    _compose_env(monkeypatch, tmp_path)
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER_ID)
    monkeypatch.setattr(triage_confirm, "_api", _dm_payload_api(payloads))
    monkeypatch.setattr(triage_gate, "list_drafts", lambda: [])
    triage_pipeline.compose_and_post(COMPOSE_TO, COMPOSE_SUBJECT, COMPOSE_BODY, post=True)
    contents = [p["content"] for p in payloads if "content" in p]
    assert contents and all("⚠️" not in c for c in contents)


def test_compose_persists_origin_binding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given: a channel-initiated compose instruction carrying its origin refs
    _compose_env(monkeypatch, tmp_path)
    monkeypatch.setattr(triage_mode, "effective_mode", lambda: "full-go")
    args = triage_cli.build_parser().parse_args([
        "compose", "--to", COMPOSE_TO, "--subject", COMPOSE_SUBJECT,
        "--body", COMPOSE_BODY, "--no-post",
        "--origin-channel-id", "200000000000000001",
        "--origin-message-id", "origin-message-1",
    ])
    # When: the compose draft is created without posting
    assert triage_cli.cmd_compose(args) == 0
    # Then: the stored record carries the origin binding for result routing
    [draft] = triage_gate.list_drafts()
    assert draft["origin_channel_id"] == "200000000000000001"
    assert draft["origin_message_id"] == "origin-message-1"


_RETIRED_APPROVALS = "100000000000000013"
_RETIRED_MESSAGE = "100000000000000016"


class _RetiredDirectory:
    def owner_dm(self) -> str:
        return DM_CHANNEL

    def skill_approvals(self) -> str:
        return _RETIRED_APPROVALS

    def agent_chat(self) -> str:
        return AGENT_CHAT_CHANNEL

    def agent_chat_thread(self, _kind: object) -> str:
        return AGENT_CHAT_THREAD

    def agent_chat_request_thread(self, _kind: object, _request: object) -> str:
        return AGENT_CHAT_THREAD

    def describe(self, channel_id: str) -> ChannelFacts:
        if channel_id == DM_CHANNEL:
            return ChannelFacts(1, "", (OWNER_ID,))
        if channel_id == _RETIRED_APPROVALS:
            return ChannelFacts(0, "approvals", ())
        if channel_id == AGENT_CHAT_THREAD:
            return ChannelFacts(11, "approval-thread", (), AGENT_CHAT_CHANNEL)
        raise AssertionError(f"unexpected channel: {channel_id}")


def _retired_draft(*, legacy: bool) -> dict[str, object]:
    record: dict[str, object] = {
        "argv": ["python3", "send", "--masked"],
        "body": "synthetic body",
        "category": "compose",
        "channel_id": None if legacy else DM_CHANNEL,
        "created": "2026-07-17T00:00:00Z",
        "flags": [],
        "id": "legacy1" if legacy else "compose1",
        "kind": None if legacy else "compose",
        "mail_subject": "",
        "message_id": _RETIRED_MESSAGE,
        "sender": "",
        "sender_masked": triage_core.mask_value(""),
        "sensitive": False,
        "status": "pending",
        "subject": "synthetic subject",
        "surface": None if legacy else "owner-dm",
        "tags": [],
        "to": "recipient@example.test",
        "uid": "legacy-uid" if legacy else "compose:retired",
        "uid_opaque": "masked",
        "policy_version": None if legacy else 6,
    }
    record["sha256"] = triage_core.draft_sha256(record)
    return record


def _run_retired_watch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    draft: dict[str, object],
) -> tuple[dict[str, object], list[tuple[str, str]]]:
    monkeypatch.setenv("TRIAGE_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("TRIAGE_MAIL_HOME", str(tmp_path / "mail"))
    monkeypatch.setattr(triage_mode, "effective_mode", lambda: "full-go")
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: OWNER_ID)
    monkeypatch.setattr(triage_binding, "approval_directory", lambda: _RetiredDirectory())
    monkeypatch.setattr(triage_approval, "approval_directory", lambda: _RetiredDirectory())
    monkeypatch.setattr(triage_cli, "_remind_pending", lambda _draft, _config: None)
    requests: list[tuple[str, str]] = []

    def api(method: str, path: str, payload: dict | None = None):
        del payload
        requests.append((method, path))
        channel = draft["channel_id"] or _RETIRED_APPROVALS
        if method == "GET" and path == f"/channels/{channel}/messages/{_RETIRED_MESSAGE}":
            return {"content": f"draft sha256:{draft['sha256']}"}
        if method == "GET" and "/reactions/" in path:
            return []
        if method == "DELETE":
            return None
        raise AssertionError(f"unexpected Discord call: {method} {path}")

    monkeypatch.setattr(triage_confirm, "_api", api)
    path = tmp_path / "gate" / "drafts" / f"{draft['id']}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(draft), encoding="utf-8")
    assert triage_cli.cmd_watch(argparse.Namespace()) == 0
    return json.loads(path.read_text(encoding="utf-8")), requests


def test_watch_expires_owner_dm_compose_from_retired_policy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stored, requests = _run_retired_watch(monkeypatch, tmp_path, _retired_draft(legacy=False))

    assert stored["status"] == "expired"
    assert stored["expired_reason"] == "approval-surface-retired"
    assert ("DELETE", f"/channels/{DM_CHANNEL}/messages/{_RETIRED_MESSAGE}") in requests
    assert not any(method == "POST" for method, _path in requests)


def test_watch_expires_legacy_pending_without_fabricating_approval(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stored, requests = _run_retired_watch(monkeypatch, tmp_path, _retired_draft(legacy=True))

    assert stored["status"] == "expired"
    assert "approval_ref" not in stored
    assert ("DELETE", f"/channels/{_RETIRED_APPROVALS}/messages/{_RETIRED_MESSAGE}") in requests
    assert not any(method in {"POST", "PUT"} for method, _path in requests)
