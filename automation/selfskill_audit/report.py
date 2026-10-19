from __future__ import annotations

import getpass
import json
import os
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from automation import owner_notice
from automation.owner_notice import notify_owner
from automation.selfskill_audit.claims import ApprovalClaimHit, find_approval_claims
from automation.selfskill_audit.ledger import Action, Delta, audit, mark_reported
from automation.selfskill_audit.local_log import append_run, update_pending_overlaps
from automation.selfskill_audit.notice import audit_message
from automation.selfskill_audit.overlap import OverlapHit, find_overlaps

_ACCOUNT_LABEL: Final = re.compile(r"[a-z0-9_-]{1,32}")
_ACTION_LABELS: Final = {
    Action.CREATED: "생성",
    Action.EDITED: "편집",
    Action.ARCHIVED: "보관",
    Action.RESTORED: "복원",
    Action.REMOVED: "삭제",
}


def render_summary(
    deltas: tuple[Delta, ...],
    *,
    account_label: str,
    shadowed: tuple[str, ...] = (),
    overlaps: tuple["OverlapHit", ...] = (),
    approval_claims: tuple["ApprovalClaimHit", ...] = (),
    pending_overlaps: int = 0,
) -> str:
    label = account_label if _ACCOUNT_LABEL.fullmatch(account_label) else "unknown"
    counts = Counter(delta.action for delta in deltas)
    count_text = " ".join(
        f"{_ACTION_LABELS[action]}={counts[action]}"
        for action in Action
        if counts[action]
    )
    lines = [f"[자체 스킬 감사] 계정={label}", f"변경={len(deltas)} {count_text}"]
    lines.extend(
        f"- {_ACTION_LABELS[delta.action]} {delta.name} sha256={delta.sha256[:12]} 출처={delta.provenance}"
        for delta in deltas
    )
    if shadowed:
        lines.append(f"SHADOWS-GOVERNED {' '.join(shadowed)} - \uc774 \uc774\ub984\uc740 \ubc30\ud3ec\ubcf8\uc744 \uac00\ub9b0\ub2e4; \ud655\uc778 \ud6c4 archive/rename")
    lines.extend(
        f"OVERLAPS-GOVERNED:{hit.governed_name} 자가 스킬 {hit.self_name} 기능 겹침"
        f"(score={hit.score}, 겹친 낱말: {' '.join(hit.shared)}) - "
        "archive 하거나 repo 로 승격(코드화→PR→릴리스)"
        for hit in overlaps
    )
    lines.extend(
        f"CLAIMS-APPROVAL-ROLE:{hit.skill_name} {' '.join(hit.tokens)} - "
        "approval review 절차를 archive 하거나 governed 코드로 승격"
        for hit in approval_claims
    )
    if pending_overlaps > 0:
        lines.append(
            f"미결 겹침 {pending_overlaps}건 — 승격·폐기 결정 대기(pending-overlaps.json)"
        )
    return "\n".join(lines)


def send_report(
    deltas: tuple[Delta, ...],
    *,
    account_label: str,
    shadowed: tuple[str, ...] = (),
    overlaps: tuple["OverlapHit", ...] = (),
    approval_claims: tuple["ApprovalClaimHit", ...] = (),
    pending_overlaps: int = 0,
) -> bool:
    content = render_summary(
        deltas,
        account_label=account_label,
        shadowed=shadowed,
        overlaps=overlaps,
        approval_claims=approval_claims,
        pending_overlaps=pending_overlaps,
    )
    label = account_label if _ACCOUNT_LABEL.fullmatch(account_label) else "unknown"
    message = audit_message(content, label)
    if message is not None and getattr(owner_notice, "ACCEPTS_OWNER_MESSAGE", False):
        ok = notify_owner(content, message=message, fault=True)
    else:
        ok = notify_owner(content, fault=True)
    return ok


def resolve_owner_id(home: Path = Path.home()) -> str:
    """소유자 id — env 가 먼저, 없으면 이 계정의 interop config 에서 읽는다.

    no-agent cron 은 `~/.env.secrets` 만 자가 로드하는데 거기에는 owner id 가 없다
    (2026-08-16 실측: `DISCORD_BOT_TOKEN` 만 존재 → `NOTIFY-UNCONFIGURED` 로 매 틱 침묵).
    같은 계정의 skill_generation 플러그인이 이미 쓰는 표준 출처를 그대로 따른다.
    """
    from_env = os.environ.get("AUTOPHAGY_OWNER_ID", "").strip()
    if from_env:
        return from_env
    config = home / ".hermes" / "interop" / "config.json"
    try:
        document = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return ""
    owner_id = document.get("owner_id", "") if isinstance(document, dict) else ""
    return owner_id.strip() if isinstance(owner_id, str) else ""


def _governed_root() -> Path | None:
    """배포본이 사는 곳 — 자가 스킬이 그 이름을 가리는지 대조하기 위해서만 읽는다."""
    try:
        from automation.node_config import load_node_config

        return load_node_config().skill_store / "live"
    except Exception:  # noqa: BLE001 - 설정을 못 읽으면 가림 탐지만 건너뛴다(감사 자체는 계속)
        return None


def run_once(
    *,
    home: Path = Path.home(),
    account_label: str | None = None,
    now: datetime | None = None,
) -> int:
    run_at = datetime.now(UTC) if now is None else now
    result = audit(home, now=run_at, governed_root=_governed_root())
    # 가림은 델타가 없어도 알린다 — 승인 게이트를 강제하는 배포본이 가려진 상태 자체가 사건이고,
    # 해소될 때까지 매 틱(일 1회) 다시 말하는 편이 조용히 사는 것보다 낫다.
    # 기능 겹침(SC-4)과 승인 역할 주장 advisory도 같은 원칙 — 해소까지 매일 한 줄.
    try:
        overlaps = find_overlaps(home, _governed_root())
    except Exception:  # noqa: BLE001 - advisory 가 감사·보고 본연을 죽이면 안 된다
        overlaps = ()
        overlaps_available = False
    else:
        overlaps_available = True
    try:
        approval_claims = find_approval_claims(home)
    except Exception:  # noqa: BLE001 - advisory 가 감사·보고 본연을 죽이면 안 된다
        approval_claims = ()
    label = getpass.getuser() if account_label is None else account_label
    pending_overlaps = (
        update_pending_overlaps(now=run_at, overlaps=overlaps, home=home)
        if overlaps_available
        else 0
    )
    if not result.pending_deltas and not result.shadowed and not overlaps and not approval_claims:
        append_run(
            now=run_at,
            account=label,
            deltas=result.deltas,
            shadowed=result.shadowed,
            overlaps=overlaps,
            notified=False,
            home=home,
        )
        return 0
    owner_id = resolve_owner_id(home)
    if owner_id and not os.environ.get("AUTOPHAGY_OWNER_ID", "").strip():
        os.environ["AUTOPHAGY_OWNER_ID"] = owner_id
    if not send_report(
        result.pending_deltas,
        account_label=label,
        shadowed=result.shadowed,
        overlaps=overlaps,
        approval_claims=approval_claims,
        pending_overlaps=pending_overlaps,
    ):
        append_run(
            now=run_at,
            account=label,
            deltas=result.deltas,
            shadowed=result.shadowed,
            overlaps=overlaps,
            notified=False,
            home=home,
        )
        return 1
    append_run(
        now=run_at,
        account=label,
        deltas=result.deltas,
        shadowed=result.shadowed,
        overlaps=overlaps,
        notified=True,
        home=home,
    )
    mark_reported(result)
    return 0


def main(argv: tuple[str, ...] | None = None) -> int:
    arguments = tuple(sys.argv[1:]) if argv is None else argv
    if arguments != ("--once",):
        print("usage: python3 -m automation.selfskill_audit.report --once", file=sys.stderr)
        return 2
    return run_once()


if __name__ == "__main__":
    raise SystemExit(main())
