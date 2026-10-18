#!/usr/bin/env python3
"""Daily LiteLLM cost report DM (agent-side half of W1-7).

Registered as a Hermes cron job (no_agent script mode, 09:00 KST) under the
`agent` account; the deployed copy lives at ~/.hermes/scripts/ (Hermes cron
sandbox rule). It can also be force-run directly:

    sudo -u agent -H python3 ~/.hermes/scripts/send_cost_report.py

Data path (account isolation): the agent NEVER touches ops secrets. It fetches
a MASKED spend aggregate over a command-restricted SSH forced command
(~agent/.ssh/autophagy-spend-ro -> ops@127.0.0.1, forced command =
automation/cost-report/spend_snapshot.py). Full spend detail stays in
/srv/autophagy-private/runtime-logs/ (ops-only).

Delivery: Discord DM to the owner, sent by the Autophagy-Agent bot
(DISCORD_BOT_TOKEN from ~agent/.env.secrets; owner_id from
~/.hermes/interop/config.json — the exact pattern proven in
automation/interop/gate_driver.py).

Soft cap: when month-to-date spend exceeds the required
`COST_REPORT_SOFT_CAP`, the report includes an alert line.

no_agent semantics: empty stdout + exit 0 on success (silent tick); on failure
prints one masked line and exits 1 so the scheduler records an error alert.

Test hooks (never set on the production cron path):
  COST_REPORT_SNAPSHOT_FILE  read snapshot JSON from a file instead of SSH
  COST_REPORT_DRY_RUN=1      print the composed body instead of sending the DM
  COST_REPORT_SOFT_CAP       required alert threshold in USD
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from automation.interop.owner_message import OwnerMessage

SSH_IDENTITY = Path.home() / ".ssh" / "autophagy-spend-ro"
ENV_SECRETS = Path.home() / ".env.secrets"
SNAPSHOT_MAX_AGE_SECONDS = 600

REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"sk-[A-Za-z0-9_-]{6,}"), "[MASKED_KEY]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "[MASKED_TOKEN]"),
    (re.compile(r"\b(?:Bearer|Bot)\s+\S+", re.IGNORECASE), "[MASKED_AUTH]"),
    (re.compile(r"\b[A-Za-z0-9_-]{23,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{20,}\b"), "[MASKED_TOKEN]"),
    (re.compile(r"\b\d{17,19}\b"), "[MASKED_ID]"),
)


def redact(text: str) -> str:
    for pattern, replacement in REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def bot_token() -> str:
    sys.path.insert(0, str(_release_runtime_root()))
    from automation.notice_bot import file_token, resolve_token

    token = resolve_token()
    if token:
        return token
    token = file_token(ENV_SECRETS)
    if token:
        return token
    raise RuntimeError("DISCORD_BOT_TOKEN not available")


def parse_json_object(raw: str, what: str) -> dict[str, object]:
    parsed: object = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{what} is not a JSON object")
    return parsed


def fetch_snapshot() -> dict[str, object]:
    override = os.environ.get("COST_REPORT_SNAPSHOT_FILE", "")
    if override:
        return parse_json_object(Path(override).read_text(), "snapshot file")
    result = subprocess.run(
        [
            "ssh",
            "-i", str(SSH_IDENTITY),
            "-o", "BatchMode=yes",
            "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", "ConnectTimeout=15",
            "ops@127.0.0.1",
            "cost-report",
        ],
        capture_output=True,
        text=True,
        timeout=90,
        env=dict(os.environ),
    )
    if result.returncode != 0:
        raise RuntimeError(f"spend fetch failed rc={result.returncode}: {redact(result.stderr.strip())[:200]}")
    return parse_json_object(result.stdout, "spend snapshot")


def _num(value: object, what: str) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    raise RuntimeError(f"snapshot field {what} is not numeric")


def _stats(snapshot: dict[str, object], name: str) -> tuple[float, int]:
    section = snapshot.get(name)
    if not isinstance(section, dict):
        raise RuntimeError(f"snapshot field {name} malformed")
    return (
        _num(section.get("spend_usd"), f"{name}.spend_usd"),
        int(_num(section.get("requests"), f"{name}.requests")),
    )


def usd(value: float) -> str:
    return f"${value:.6f}"


def compose(snapshot: dict[str, object], soft_cap: float) -> str:
    kst_now = str(snapshot.get("kst_now", "?"))
    today_spend, today_requests = _stats(snapshot, "today_kst")
    month_spend, month_requests = _stats(snapshot, "month_to_date_kst")
    all_spend, all_requests = _stats(snapshot, "all_time")

    key_parts: list[str] = []
    entries = snapshot.get("per_key_month_kst")
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            alias = str(entry.get("alias", "?"))
            spend = _num(entry.get("spend_usd"), "per_key.spend_usd")
            requests = int(_num(entry.get("requests"), "per_key.requests"))
            key_parts.append(f"{alias} {usd(spend)} ({requests}건)")
    lines = [
        f"📊 LiteLLM 일일 비용 리포트 — {kst_now} KST",
        f"오늘(KST) 지출: {usd(today_spend)} ({today_requests}건)",
        f"이번 달 누적: {usd(month_spend)} ({month_requests}건)",
        f"전체 누적: {usd(all_spend)} ({all_requests}건)",
        "키별(월): " + (", ".join(key_parts) if key_parts else "이번 달 사용 없음"),
    ]
    if month_spend > soft_cap:
        lines.append(
            f"⚠️ 소프트캡 경보: 월 누적 {usd(month_spend)} — ${soft_cap:g} 소프트캡 초과"
        )
    lines.append(f"(source: LiteLLM_SpendLogs · generated {snapshot.get('generated_at_utc', '?')})")
    return redact("\n".join(lines))


def _release_runtime_root() -> Path:
    """owner_notice 파사드가 사는 릴리스 런타임 — 배포 사본이 늦게 해석한다(ON-2)."""
    override = os.environ.get("AUTOPHAGY_REPO_ROOT", "").strip()
    if override:
        return Path(override)
    release = Path("/srv/autophagy-agent-current")
    return release if release.is_dir() else Path("/srv/autophagy-agents")


def periodic_message(body: str, kst_now: str) -> OwnerMessage | None:
    """오늘 집계 구간만 복원한다. 누락된 시각을 현재 시각으로 대신하지 않는다."""
    try:
        from automation.interop.owner_message import Action, OwnerMessage, Periodic, Ref, Result
    except Exception:  # noqa: BLE001 - optional module initialization must preserve string delivery
        return None
    detail: Periodic | Result
    try:
        end = datetime.strptime(kst_now, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone(timedelta(hours=9)),
        )
    except ValueError:
        detail = Result(outcome="executed")
        fact = f"{body} · 관측 구간 없음: KST 집계 시각 누락 또는 형식 오류"
    else:
        detail = Periodic(start=end.replace(hour=0, minute=0, second=0), end=end)
        fact = f"{body} · 관측은 오늘 지출 기준; 월·전체 누적은 별도"
    return OwnerMessage(
        subject_key="cost-report", subject="일일 지출", fact=fact,
        location=Ref(scope="resource", search=("소유자용 원장 조회 화면 없음", "Discord 에이전트 DM에 '일일 지출 근거 확인' 요청")),
        owner=Action(verb="none"), agent_next="다음 정기 보고", recovery="not_applicable",
        detail=detail,
    )


def send_dm(body: str, *, kst_now: str = "") -> None:
    """목적지(지정 채널/DM)는 owner_notice 파사드가 정한다(ON-2) — 마스킹은 여기 그대로."""
    os.environ.setdefault("DISCORD_BOT_TOKEN", bot_token())
    root = str(_release_runtime_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    from automation import owner_notice
    from automation.owner_notice import notify_owner

    message = periodic_message(body, kst_now)
    if message is not None and getattr(owner_notice, "ACCEPTS_OWNER_MESSAGE", False):
        ok = notify_owner(body, message=message)
    else:
        ok = notify_owner(body)
    if not ok:
        raise RuntimeError("owner notice delivery failed")


def main() -> int:
    soft_cap = float(os.environ["COST_REPORT_SOFT_CAP"])
    snapshot = fetch_snapshot()
    body = compose(snapshot, soft_cap)
    if os.environ.get("COST_REPORT_DRY_RUN", "") == "1":
        print(body)
        return 0
    send_dm(body, kst_now=str(snapshot.get("kst_now", "")))
    # Success: stay silent (no_agent silent tick).
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001 — cron alert path: one masked line, nonzero exit
        print(f"cost-report error: {redact(str(error))[:300]}")
        sys.exit(1)
