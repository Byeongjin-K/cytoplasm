"""SC-1: self→governed 선점을 2분 틱에서 잡는다 — 일 1회 감사는 노출 창이 하루다.

Hermes ``_find_skill`` 의 rglob 은 governed 심링크 팜을 따라가지 못해 자가 스킬의
배포본 이름 선점을 사전에 막지 못한다(2026-08-16 실측: ``recall`` 자가 스킬이 실제로
만들어졌다). 1차 루트가 발견에서 이기므로 그 순간부터 **승인 게이트를 강제하는
배포본이 가려진다** — 탐지 지연이 곧 노출 창이다. 새 워처를 만들지 않고(규약) 기존
``supply_chain_watch`` 2분 틱에 **이름 대조만**(해시·원장·usage 무접촉) 편입한다.
판정 walk 는 ``selfskill_audit.scan`` 이 소유한 것을 재사용한다(사본 0).

통지 규율: 새 그림자 이름이 나타난 틱에 1건(전송 실패 시 상태 미전진 → 다음 틱
재시도, at-least-once), 지속 중에는 호출자의 저널 한 줄이 신호, 해소되면 상태가
비워져 재발이 새 사건으로 다시 알려진다. 일 1회 ``selfskill_audit`` 보고의
SHADOWS-GOVERNED 줄은 그대로다 — 이 틱은 지연을 줄일 뿐 그 감사를 대체하지 않는다.
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from automation import owner_notice
from automation.owner_notice import notify_owner
from automation.selfskill_audit.report import _governed_root, resolve_owner_id
from automation.selfskill_audit.scan import shadowed_skill_names
from automation.supply_chain_shadow_notice import shadow_message

Notify = Callable[[str], bool]

_STATE_DEFAULT: Final = "~/.hermes/supply-chain-watch/shadows.json"


def state_path() -> Path:
    return Path(os.environ.get("SUPPLY_CHAIN_SHADOW_STATE", _STATE_DEFAULT)).expanduser()


@dataclass(frozen=True, slots=True)
class ShadowPlan:
    """One tick's decision: what to say (if anything) and what to remember."""

    notice: str | None
    state: tuple[str, ...]


def plan_shadow_notice(
    current: tuple[str, ...], notified: tuple[str, ...]
) -> ShadowPlan:
    """새 이름에만 1건 — 같은 그림자를 2분마다 다시 알리면 신호가 소음이 된다."""
    if not current:
        return ShadowPlan(None, ())
    if all(name in notified for name in current):
        # 사라진 이름은 상태에서 떨군다 — 재발이 새 사건으로 다시 알려지게.
        return ShadowPlan(None, current)
    notice = (
        f"SHADOWS-GOVERNED {' '.join(current)} — 자가 스킬이 배포본 이름을 가린다"
        " (승인 게이트를 강제하는 구현이 가려짐). `hermes curator archive <name>`"
        " 또는 이름 변경 후 배포본 발견을 확인하세요."
    )
    return ShadowPlan(notice, current)


def _load_notified(path: Path) -> tuple[str, ...]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    if not isinstance(raw, list):
        return ()
    return tuple(sorted(str(name) for name in raw))


def _save_notified(path: Path, names: tuple[str, ...]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _ = path.write_text(json.dumps(sorted(names)) + "\n", encoding="utf-8")
    path.chmod(0o600)


def run_shadow_check(
    *,
    home: Path | None = None,
    governed_root: Path | None = None,
    notify: Notify = notify_owner,
) -> tuple[str, ...]:
    """One tick. Returns the names currently shadowing so the caller can journal them."""
    account_home = Path.home() if home is None else home
    root = _governed_root() if governed_root is None else governed_root
    current = shadowed_skill_names(account_home, root)
    path = state_path()
    plan = plan_shadow_notice(current, _load_notified(path))
    if plan.notice is not None:
        owner_id = resolve_owner_id(account_home)
        if owner_id and not os.environ.get("AUTOPHAGY_OWNER_ID", "").strip():
            os.environ["AUTOPHAGY_OWNER_ID"] = owner_id
        content = plan.notice
        if notify is notify_owner:
            message = shadow_message(content, current, account_home)
            if message is not None and getattr(owner_notice, "ACCEPTS_OWNER_MESSAGE", False):
                ok = notify_owner(content, message=message, fault=True)
            else:
                ok = notify_owner(content, fault=True)
        else:
            ok = notify(content)
        if not ok:
            return current  # 상태 미전진 — 다음 틱이 같은 통지를 재시도한다
    _save_notified(path, plan.state)
    return current
