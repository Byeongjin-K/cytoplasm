"""수렴 통지 어댑터: 기존 문자열 큐와 단일 인자 콜백은 그대로 둔다.

pending_notice에는 문자열만 저장되므로 동결된 기존 형식을 한 경계에서 읽는다.
새 통지는 호출자가 계산한 관측 구간을 쓰고, 재전송에는 없는 시각을 만들지 않는다.
회복만 담긴 소유자 통지는 성공으로 소비해 큐·사건 상태를 닫되 실제로 게시하지 않는다.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, TypeAlias

if TYPE_CHECKING:
    from automation.interop.owner_message import OwnerMessage

Window: TypeAlias = tuple[float, float]
_RECOVERY = re.compile(r"prod가 origin/main에 다시 도달했습니다: ([^\n]*)")


def drift_notice(*, origin_sha: str, current_sha: str, failures: int, elapsed: float) -> str:
    """기존 durable 문자열 형식. 네 필드는 이미 공개된 호출 계약이다."""
    return (
        "prod has not converged to origin/main.\n"
        f"  origin/main : {origin_sha}\n"
        f"  runtime     : {current_sha}\n"
        f"  실패 {failures}회 · 미수렴 {int(elapsed // 60)}분\n"
        "자동 재시도는 계속됩니다. 반복되면 노드에서 원인을 확인하세요 "
        "(재시작·포인터 수정은 하지 마세요)."
    )


def recovery_notice(*, current_sha: str) -> str:
    return f"prod가 origin/main에 다시 도달했습니다: {current_sha}"


def build_notice(content: str, window: Window | None = None) -> OwnerMessage | None:
    """저장된 통지를 봉투로 읽는다. 알 수 없는 옛 형식은 원문 배달로 남긴다."""
    try:
        from automation.interop.owner_message import Action, OwnerMessage, Periodic, Ref, Result
    except Exception:  # noqa: BLE001 - optional module initialization must preserve string delivery
        return None

    detail: Periodic | Result = Result("executed")
    if window is not None:
        try:
            detail = Periodic(*(datetime.fromtimestamp(value, UTC) for value in window))
        except (OSError, OverflowError, ValueError):
            # 손상된 옛 상태의 시각 때문에 원문 통지까지 잃지 않는다.
            return None
    drift = re.fullmatch(
        r"prod has not converged to origin/main\.\n  origin/main : ([^\n]*)\n"
        + r"  runtime     : ([^\n]*)\n  (실패 [^\n]*)\n.*", content, re.DOTALL,
    )
    if drift is not None:
        origin, runtime, summary = drift.groups()
        blocked = runtime.startswith("blocked: ")
        key = runtime.removeprefix("blocked: ") if blocked else origin
        location = Ref(scope="none") if blocked or not origin else Ref(
            scope="resource", search=("Git 커밋", origin),
        )
        return OwnerMessage(
            subject_key=key, subject="수렴 점검", fact=f"runtime {runtime}; {summary}",
            location=location,
            owner=Action("open", Ref(scope="resource", search=("운영 점검", key)),
                         "원인 확인; 재시작·포인터 수정 금지"),
            agent_next="자동 재시도 계속", recovery="not_applicable", detail=detail,
        )
    recovery = _RECOVERY.fullmatch(content)
    if recovery is not None:
        sha = recovery.group(1)
        return OwnerMessage(
            subject_key=sha, subject="수렴 회복", fact="runtime이 목표에 도달",
            location=Ref(scope="resource", search=("Git 커밋", sha)), owner=Action("none"),
            agent_next="다음 틱에서 상태 관측", recovery="not_applicable", detail=Result("executed"),
        )
    from automation.deploy_reconcile_backlog import backlog_message

    return backlog_message(content, detail)


def send_notice(callback: Callable[[str], bool], content: str, window: Window | None = None) -> bool:
    """실제 파사드만 봉투를 받는다. 주입된 단일 인자 콜백의 계약은 유지한다."""
    from automation import owner_notice
    from automation.owner_notice import notify_owner

    if callback is not notify_owner:
        return callback(content)
    # 기존 상태머신·주입 콜백 계약은 유지하되, 소유자 채널에는 문제만 보낸다.
    # 성공으로 소비해야 저장된 복구 pending도 비워지고 다음 사건을 다시 알릴 수 있다.
    if _RECOVERY.fullmatch(content) is not None:
        return True
    message = build_notice(content, window)
    if message is not None and getattr(owner_notice, "ACCEPTS_OWNER_MESSAGE", False):
        ok = notify_owner(content, message=message)
    else:
        ok = notify_owner(content)
    return ok
