"""MailOn 실행 백엔드 — 승인 이후의 발송만 담당한다 (W4-2, 제약 1/6).

``triage_gate`` 에서 분리(G8 LOC): 파사드는 드래프트 저장소와 경로를 소유하고,
이 모듈은 소유자 승인 **이후** 일어나는 전부를 소유한다 — fail-closed 모드
재확인, 첨부 매니페스트 검증, external-effect 승인 레코드, 얼어붙은 argv 실행,
W0-6 감사 레코드, 그리고 연속 실패 2회의 no-go 강등.

파사드의 부수효과 지점(``_run_send``·``write_json``·``_draft_path``·
``_approval_log``·``db_path``·``_send_log``)은 **모듈 객체를 통해** 호출한다.
그래야 그 이름들이 여전히 유일한 이음매로 남는다 — 테스트가 고정하는 지점이
분리 전과 정확히 같아야 하기 때문이다.
"""

from __future__ import annotations

import hashlib
import json
import re

import triage_core
import triage_gate
import triage_mode
import triage_store
from triage_mode import append_record as _append_record

FAILURE_DOWNGRADE_THRESHOLD = 2

# mailon's fixed cause codes (e.g. ``mailbox_list_unavailable``). Anything shaped
# otherwise is free text and is dropped, so the record stays free of raw messages.
_CAUSE_CODE = re.compile(r"[a-z][a-z0-9_]{2,47}")

_NON_SEND_FAILURE_CODES = frozenset({
    "attachment_invalid",
    "attachment_unsupported",
    "attachment_upload_failed",
    "auth_error",
    "confirmation_required",
    "external_service_error",
    "validation_error",
})

_SAFE_FAILURE_MESSAGES = {
    "attachment_invalid": "첨부 파일이 유효하지 않아 발송하지 않았습니다.",
    "attachment_unsupported": "현재 첨부 업로드 기능을 사용할 수 없어 발송하지 않았습니다.",
    "attachment_upload_failed": "첨부 업로드에 실패하여 메일을 발송하지 않았습니다.",
    "auth_error": "메일 서비스 인증에 실패했습니다. 재인증 후 다시 시도하세요.",
    "external_service_error": "메일 서비스 연결에 실패했습니다. 잠시 후 다시 시도하세요.",
    "send_failed": "메일 발송에 실패했습니다. 잠시 후 다시 시도하세요.",
    "send_unverified": "메일 발송 여부를 확인하지 못했습니다. 중복 발송 방지를 위해 보낸메일함을 먼저 확인하세요.",
    "validation_error": "메일 입력값이 유효하지 않습니다. 입력을 확인한 뒤 새 초안을 만드세요.",
}


def _send_payload(stdout: str) -> dict:
    """Parse the mailon JSON contract even when the subprocess exits non-zero."""
    try:
        payload = triage_core.first_json_object(stdout)
    except triage_core.LlmParseError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _error_info(payload: dict) -> dict:
    """Normalize safe error metadata; never carry provider/raw message text."""
    error_code = payload.get("error_code")
    stage = payload.get("stage")
    retryable = payload.get("retryable")
    if not isinstance(error_code, str) or not error_code:
        error_code = ""
    if not isinstance(stage, str) or not stage:
        stage = "unknown"
    if not isinstance(retryable, bool):
        retryable = error_code not in _NON_SEND_FAILURE_CODES
    info = {"error_code": error_code, "stage": stage, "retryable": retryable}
    cause = payload.get("cause")
    if isinstance(cause, str) and _CAUSE_CODE.fullmatch(cause):
        info["cause"] = cause
    return info


def _record_draft_failure(draft: dict, error: dict) -> None:
    """Persist only safe retry metadata for the approval watcher."""
    path = triage_gate._draft_path(draft["id"])
    if path is None:
        return
    status = "pending" if error["retryable"] else "blocked"
    triage_gate.write_json(path, {**draft, "status": status, "last_error": error})


def _safe_failure_message(error: dict) -> str:
    return _SAFE_FAILURE_MESSAGES.get(
        error["error_code"], "메일 발송 처리에 실패했습니다."
    )

def execute_mailon_draft(draft: dict, approval: triage_gate.Approval) -> None:
    """Validate approved attachments, execute frozen argv, and verify the result."""
    mode = triage_mode.effective_mode()
    if mode != "full-go":
        raise triage_gate.GateError(f"mail-mode={mode} — 실발송 비활성(W4-1N 분기)", 3)
    attachments = draft.get("attachments") or []
    if attachments:
        try:
            triage_core.verify_attachment_manifest(
                attachments, draft.get("attachment_manifest_sha256") or ""
            )
        except triage_core.AttachmentPolicyError as error:
            safe_error = {
                "error_code": error.error_code,
                "stage": "validation",
                "retryable": False,
            }
            _record_draft_failure(draft, safe_error)
            raise triage_gate.GateError(
                f"{_safe_failure_message(safe_error)} (code={error.error_code})", 2
            ) from error

    argv = tuple(draft["argv"])
    _append_record(triage_gate._approval_log(), _approval_record(argv, approval))
    rc, stdout, _stderr = triage_gate._run_send(argv)
    payload = _send_payload(stdout)
    status = str(payload.get("status") or ("no-json" if rc == 0 else ""))
    error = _error_info(payload)
    result_matches = True
    if attachments and rc == 0 and status == "submitted":
        expected_manifest = draft.get("attachment_manifest_sha256")
        result_matches = (
            isinstance(expected_manifest, str)
            and payload.get("verified") is True
            and payload.get("attachment_count") == len(attachments)
            and payload.get("attachment_manifest_sha256") == expected_manifest
        )
        if not result_matches:
            error = {
                "error_code": "send_unverified",
                "stage": "verify",
                "retryable": False,
            }

    if rc != 0 or status != "submitted" or not result_matches:
        if not error["error_code"]:
            error = {
                **error,
                "error_code": "send_failed",
                "stage": error["stage"] if error["stage"] != "unknown" else "send",
                "retryable": True,
            }
        _append_record(
            triage_gate._approval_log(),
            _audit_record(draft, approval, status="failed", error=error),
        )
        if error["error_code"] not in _NON_SEND_FAILURE_CODES:
            failures = triage_store.bump_send_failures(triage_gate.db_path())
            if failures >= FAILURE_DOWNGRADE_THRESHOLD:
                triage_mode.downgrade_to_no_go(
                    f"approved mailon send failed {failures} consecutive times (rc={rc})"
                )
        else:
            failures = triage_store.consecutive_send_failures(triage_gate.db_path())
        _record_draft_failure(draft, error)
        exit_code = 2 if error["stage"] == "validation" else 6
        raise triage_gate.GateError(
            f"{_safe_failure_message(error)} "
            f"(error_code={error['error_code']} stage={error['stage']} "
            f"retryable={str(error['retryable']).lower()} consecutive={failures}"
            + (f" cause={error['cause']}" if error.get("cause") else "") + ")",
            exit_code,
        )

    triage_store.reset_send_failures(triage_gate.db_path())
    _append_record(
        triage_gate._approval_log(), _audit_record(draft, approval, status="sent")
    )
    send_record = {
        "draft_id": draft["id"],
        "method": approval.method,
        "ref": approval.ref,
        "sha256": draft["sha256"],
        "status": "sent",
        "timestamp": triage_core.utc_now(),
        "to_masked": triage_core.mask_value(draft["to"]),
        "uid": draft["uid_opaque"],
    }
    if attachments:
        send_record["attachment_count"] = len(attachments)
        send_record["attachment_manifest_sha256"] = draft["attachment_manifest_sha256"]
    _append_record(triage_gate._send_log(), send_record)
    path = triage_gate._draft_path(draft["id"])
    if path is not None:
        triage_gate.write_json(path, {**draft, "status": "executed",
                                      "approval_ref": approval.ref, "method": approval.method})


def _approval_record(argv: tuple[str, ...], approval: triage_gate.Approval) -> dict:
    return {
        "action": "external_effect.approval",
        "approval": {
            "channel": "approvals",
            "message_id": approval.ref,
            "method": approval.method,
            "owner_id": approval.owner,
        },
        "hash": triage_core.external_effect_action_hash(argv),
        "result": {"status": "approved"},
        "target_id": triage_core.EXTERNAL_EFFECT_TARGET_ID,
        "timestamp": triage_core.utc_now(),
    }


def _mark_draft_blocked(draft: dict, error_code: str) -> None:
    """Persist a terminal safe state without recording private diagnostics."""
    path = triage_gate._draft_path(draft["id"])
    if path is not None:
        triage_gate.write_json(path, {**draft, "status": "blocked", "error_code": error_code})


def _audit_record(
    draft: dict, approval: triage_gate.Approval, *, status: str, error: dict | None = None,
) -> dict:
    kind = draft.get("kind", "reply")
    if kind == "compose":
        action = "mail.compose_send"
        target_id = f"mail:compose:{triage_core.mask_value(draft['to'])}"
        channel = "owner-dm" if draft.get("channel_id") else "approvals"
    else:
        action = "mail.reply_send"
        target_id = f"mail:reply:{triage_core.mask_value(draft['to'])}"
        channel = "approvals"
    approval_field = {"channel": channel, "method": approval.method, "ref": approval.ref}
    canonical = json.dumps(
        {
            "action": action,
            "approval": approval_field,
            "payload": {"draft_sha256": draft["sha256"]},
            "target_id": target_id,
        },
        ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    )
    result = {"status": status}
    if error is not None:
        result.update(error)
    return {
        "action": action,
        "approval": approval_field,
        "hash": f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}",
        "result": result,
        "target_id": target_id,
        "timestamp": triage_core.utc_now(),
    }
