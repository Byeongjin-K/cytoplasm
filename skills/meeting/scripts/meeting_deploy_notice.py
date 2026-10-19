"""Streamed by deploy.sh under the node's agent account; no installed helper copy."""
from __future__ import annotations

import sys

from automation.interop.owner_message import Action, OwnerMessage, Ref, Result
from automation.owner_notice import notify_owner


def notify_restart(before: str, after: str) -> bool:
    """Report changed on-disk bytes, never claim or perform a gateway restart."""
    message = OwnerMessage(
        subject_key="meeting-plugin-restart",
        subject="meeting 게이트웨이 플러그인 갱신",
        fact=f"PLUGIN-CHANGED: {before[:12]} -> {after[:12]}. 파일은 갱신됐지만 재시동 전에는 실행 코드가 다릅니다.",
        location=Ref(scope="resource", search=(
            "agent 홈", ".hermes/plugins/00-meeting-gate/__init__.py",
        )),
        owner=Action("open", Ref(scope="resource", search=(
            "운영 절차", "docs/guide/operations.md §2",
        )), "원인 확인 후 agent·peer 게이트웨이를 함께 재시동하세요. 이미 함께 재시동했다면 추가 조치는 없습니다."),
        agent_next="이 배포 스크립트는 재시동하지 않습니다.",
        recovery="not_applicable",
        detail=Result("executed"),
    )
    return notify_owner("", message=message, fault=True)


if __name__ == "__main__":
    raise SystemExit(0 if notify_restart(*sys.argv[1:]) else 1)
