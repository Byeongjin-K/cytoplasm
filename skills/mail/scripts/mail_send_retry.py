"""Bounded retry and the owner's failure notice for an approved mail send.

WHY (2026-10-13): an owner-approved forward failed every watch tick for over an hour
(``compose did not become callable``) and nobody heard about it. A browser or page
failure (``external_service_error``) never counted as a failure, so the draft stayed
``pending``, the watcher retried it every few minutes without end, and the tick that
failed exited like any other. The owner learned of it only by asking.

An approved draft now carries its own failure count (``failed_attempts``) and the
earliest time it may be tried again (``next_attempt_at``). Retries back off and stop
after ``MAX_ATTEMPTS``; each failed attempt is told to the owner in the draft's
approval thread with the step that stopped it. These fields sit outside the draft
hash (``triage_core.draft_sha256``), so the owner's approval stays valid.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final

#: Tries of one approved draft, the first one included. The waits below add up to
#: about 5 hours, longer than the webmail outages measured so far (about 4 hours,
#: 2026-10-12), so a send that recovers on its own still goes out.
MAX_ATTEMPTS: Final = 6
BACKOFF_MINUTES: Final = (10, 20, 40, 80, 160)
DETAIL_KEYS: Final = ("step", "browser_command", "error_kind")

_STAMP = "%Y-%m-%dT%H:%M:%SZ"
_KST = timedelta(hours=9)


def _parse(stamp: object) -> datetime | None:
    if not isinstance(stamp, str):
        return None
    try:
        return datetime.strptime(stamp, _STAMP).replace(tzinfo=UTC)
    except ValueError:
        return None


def after_failure(draft: dict, error: dict, now: datetime) -> dict:
    previous = draft.get("failed_attempts")
    attempts = (previous if isinstance(previous, int) and previous > 0 else 0) + 1
    updated = {**draft, "last_error": error, "failed_attempts": attempts}
    updated.pop("next_attempt_at", None)
    if not error.get("retryable"):
        updated["status"] = "blocked"
    elif attempts >= MAX_ATTEMPTS:
        updated["status"] = "blocked"
        updated["retry_exhausted"] = True
    else:
        wait = BACKOFF_MINUTES[min(attempts, len(BACKOFF_MINUTES)) - 1]
        updated["status"] = "pending"
        updated["next_attempt_at"] = (now + timedelta(minutes=wait)).strftime(_STAMP)
    return updated


def waiting_until(draft: dict, now: datetime) -> str | None:
    due = _parse(draft.get("next_attempt_at"))
    if due is None or due <= now:
        return None
    return str(draft["next_attempt_at"])


def failure_text(draft: dict, reason: str) -> str:
    """What the owner reads in the approval thread after a failed attempt.

    ``draft`` is the stored record after the failure; ``reason`` is the gate's safe
    one-line message, used when the record carries no structured error.
    """
    lines = [f"⚠️ 발송 실패: {draft['subject']} → {draft['to']} (draft {draft['id']})"]
    error = draft.get("last_error")
    delivery_unverified = False
    if draft.get("bcc"):
        stage = (error.get("step") or error.get("stage") or "unknown") if isinstance(error, dict) else "unknown"
        count = len(draft["bcc"])
        if stage in {
            "drive_share", "validation", "login", "auth", "page_boot", "mailbox_check", "compose_open",
            "compose_fields", "editor_fill", "attachment_upload", "recipient_commit",
        }:
            lines.append(f"숨은참조 {count}명 전원 미발송 — 한 통 전체가 발송 전에 중단됐습니다 (단계: {stage}).")
        else:
            delivery_unverified = True
            lines.append(f"숨은참조 {count}명 대상 한 통의 발송 결과 미확인 (단계: {stage}).")
    if isinstance(error, dict) and error.get("error_code"):
        where = error.get("step") or error.get("stage") or "unknown"
        cause = error.get("cause") or error["error_code"]
        line = f"멈춘 단계: {where} · 원인: {cause}"
        if error.get("browser_command"):
            line += f" · 실패한 명령: {error['browser_command']}"
        if error.get("error_kind"):
            line += f" · 오류: {error['error_kind']}"
        lines.append(line)
    else:
        lines.append(f"사유: {reason}")
    attempts = draft.get("failed_attempts")
    count = f"시도 {attempts}/{MAX_ATTEMPTS}" if isinstance(attempts, int) else "시도"
    due = _parse(draft.get("next_attempt_at"))
    if draft.get("status") == "pending" and due is not None:
        lines.append(f"{count} · 다음 자동 재시도: {(due + _KST).strftime('%m-%d %H:%M')} KST")
    elif draft.get("retry_exhausted"):
        lines.append(f"{count} · 자동 재시도를 멈췄습니다. 메일은 발송되지 않았습니다 — 다시 보내려면 새로 요청해 주세요.")
    else:
        lines.append(f"{count} · 자동으로 다시 시도하지 않습니다. 메일은 발송되지 않았습니다.")
    text = "\n".join(lines)
    if delivery_unverified:
        text = text.replace("메일은 발송되지 않았습니다", "메일 발송 여부는 확인되지 않았습니다")
    return text
