"""healthcheck 실패를 소유자에게 알리되, 사건당 한 번만 알린다.

2026-08-02 실측: 노드 에이전트가 배포 체크아웃에 커밋해 9시간 동안 모든 ff-pull 이
막혔을 때 healthcheck 는 그것을 **52번 FAIL 로 정확히 탐지하고도** 소유자에게 닿지
못했다. 탐지는 있는데 도달이 없었다.

그런데 도달만 붙이면 반대쪽으로 넘어간다. 5분마다 도는 스윕이 같은 사건으로 52통을
보내면 그 알림은 곧 무시되고, **무시되는 알림은 없는 알림과 같다.** 그래서 이 모듈이
책임지는 것은 "보낸다"가 아니라 **"사건당 한 번만 보낸다"**이다.

집계 단위는 스윕 1회당 메시지 1통이다. 새 실패가 있을 때만 회복한 체크도 한 통에
담고, 회복만 있으면 상태만 갱신한다. SSH 전면 장애로 9개가 한꺼번에
무너져도 9통이 아니라 1통이다.

판정은 순수하다 — 전송·시각·저장은 가장자리에 있다. 그래야 "정확히 한 통"이 희망이
아니라 단위 테스트가 된다(`deploy_reconcile` 과 같은 이유, 같은 모양).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from automation.interop.owner_message import OwnerMessage

DEFAULT_STATE_PATH: Final = Path("/srv/autophagy-private/healthcheck-notify/state.json")


def state_path() -> Path:
    """상태 파일 위치. env 로 옮길 수 있는 이유는 둘이다 — 래퍼를 실제로 돌려보려면
    /srv 밖으로 가리킬 수 있어야 하고, 전송 측정을 할 때 프로덕션 상태를 건드리지
    않아야 하기 때문이다."""
    override = os.environ.get("HEALTHCHECK_NOTIFY_STATE", "")
    return Path(override).expanduser() if override else DEFAULT_STATE_PATH


@dataclass(frozen=True, slots=True)
class NotifyState:
    """스윕 사이를 넘어가는 유일한 기억: 지금 열려 있는 사건의 체크 이름."""

    open_incidents: tuple[str, ...] = ()


def _render(opened: Sequence[str], closed: Sequence[str]) -> str:
    lines: list[str] = []
    if opened:
        lines.append("healthcheck 실패가 새로 발생했습니다:")
        lines.extend(f"  - {name}" for name in opened)
    if closed:
        lines.append("healthcheck 가 회복됐습니다:")
        lines.extend(f"  - {name}" for name in closed)
    if opened:
        lines.append(
            "노드에서 원인을 먼저 확인하세요 — 원인 확인 전 재시작·설정변경·키 재발급은 하지 않습니다."
        )
    return "\n".join(lines)


def plan_notice(
    state: NotifyState, *, failing: Sequence[str]
) -> tuple[NotifyState, str | None]:
    """이번 스윕의 (다음 상태, 보낼 통지). 새 실패가 없으면 통지는 None.

    같은 체크가 계속 실패하는 것은 새 사건이 아니다 — 이미 알렸다. 반대로 사라진 체크는
    회복이므로 통지 없이 닫는다.
    """
    current = tuple(sorted(set(failing)))
    opened = tuple(name for name in current if name not in state.open_incidents)
    closed = tuple(name for name in state.open_incidents if name not in current)
    following = NotifyState(open_incidents=current)
    if not opened:
        return following, None
    return following, _render(opened, closed)


def notice_message(state: NotifyState, following: NotifyState) -> OwnerMessage | None:
    """시각 없는 스윕 전이 결과. 원인 확인과 다음 스윕 관측을 구분한다."""
    try:
        from automation.interop.owner_message import Action, OwnerMessage, Ref, Result
    except Exception:  # noqa: BLE001 - optional module initialization must preserve string delivery
        return None
    opened = tuple(name for name in following.open_incidents if name not in state.open_incidents)
    closed = tuple(name for name in state.open_incidents if name not in following.open_incidents)
    key = ", ".join(sorted((*opened, *closed)))
    location = Ref(scope="resource", search=("헬스체크", key))
    owner = Action("none")
    if opened:
        owner = Action("open", location, "원인 확인; 확인 전 재시작·설정변경·키 재발급 금지")
    return OwnerMessage(
        subject_key=key, subject="헬스체크 관측",
        fact=f"신규 실패: {', '.join(opened) or '없음'}; 회복: {', '.join(closed) or '없음'}",
        location=location, owner=owner, agent_next="다음 스윕에서 상태 관측",
        recovery="not_applicable", detail=Result("executed"), render_version="owner-ko-v2",
    )


def load_state(path: Path = DEFAULT_STATE_PATH) -> NotifyState:
    """읽지 못하면 '아무 사건도 열려 있지 않다'로 degrade 한다.

    깨진 상태로 tick 을 죽이면 알림이 아니라 healthcheck 자체가 멈춘다 — 통지 하나를
    중복 발송하는 편이 낫다.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return NotifyState()
    if not isinstance(payload, dict):
        return NotifyState()
    names = payload.get("open_incidents")
    if not isinstance(names, list):
        return NotifyState()
    return NotifyState(tuple(sorted(str(name) for name in names)))


def save_state(path: Path, state: NotifyState) -> None:
    """원자적 교체 + 0600. 부분 기록된 상태는 다음 스윕을 오판하게 만든다."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    serialized = json.dumps({"open_incidents": list(state.open_incidents)}, sort_keys=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}.", mode="w", encoding="utf-8", delete=False
        ) as handle:
            temporary = Path(handle.name)
            _ = handle.write(serialized)
            handle.flush()
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def check_names(definitions: Sequence[str]) -> tuple[str, ...]:
    """healthcheck 정의 문자열에서 체크 이름만 동린다.

    인자는 healthcheck.sh 가 들고 있는 모양 그대로다:
    `<이름>|<프로브타입>|<노드>|<계정>|<대상>`.

    bash 에서 이름만 뽑으려면 스윈 루프 안에 한 줄이 더 필요한데,
    healthcheck.sh 는 250 pure-LOC 게이트에 이미 닿아 있어 배선이 정확히 한 줄이어야
    한다. 그래서 파싱을 테스트 가능한 이쪽으로 가져왔다."""
    return tuple(
        definition.split("|", maxsplit=1)[0] for definition in definitions if definition
    )


def main(argv: Sequence[str] | None = None) -> int:
    """이번 스윈의 실패 집합을 받아 필요한 만큼만 알린다.

    **매 스윈 호출된다** — 실패한 스윈에서만 부르면 회복을 상태에 반영하지 못하고
    사건이 열린 채로 남는다."""
    from automation import owner_notice
    from automation.owner_notice import notify_owner

    given = list(argv if argv is not None else sys.argv[1:])
    failing = check_names(given)
    destination = state_path()
    state = load_state(destination)
    following, notice = plan_notice(state, failing=failing)
    if notice is None:
        save_state(destination, following)
        return 0
    message = notice_message(state, following)
    if message is not None and getattr(owner_notice, "ACCEPTS_OWNER_MESSAGE", False):
        ok = notify_owner(notice, message=message, fault=True)
    else:
        ok = notify_owner(notice, fault=True)
    if not ok:
        # 전달 실패는 사건을 연 것으로 치지 않는다 — 다음 스윕이 다시 시도해야 한다.
        print("[healthcheck-notify] NOTIFY-FAILED: 다음 스윕에서 재시도", file=sys.stderr)
        return 0
    save_state(destination, following)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI 진입점
    raise SystemExit(main())
