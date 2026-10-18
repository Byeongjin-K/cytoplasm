"""Meaning checks beside timer results; never read or write the retired failure ledger."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

from automation.doctor.facts import JobCheck, Run
from automation.install.checks import Status

KST: Final = ZoneInfo("Asia/Seoul")


def _state(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def observe(home: Path, repo: Path, run: Run, now: datetime) -> tuple[JobCheck, ...]:
    checks: list[JobCheck] = []
    collection = _state(home / ".hermes/mail-sync/collection.json")
    if now.astimezone(KST).weekday() < 5 and collection.get("zero_weekday_streak", 0) >= 36:  # six workday hours at 10-minute ticks
        checks.append(JobCheck("mail-sync-quiet", Status.FAIL,
                               "평일 기관메일 수집 성공 후 새 메일 0건이 6회 이상 이어짐"))
    runtime = home / ".hermes/mailon-runtime"
    if runtime.exists():
        code, _ = run(
            ("bash", str(repo / "skills/mail/scripts/mailon_runtime_drift.sh")),
            {"HOME": str(home), "AUTOPHAGY_REPO_ROOT": str(repo), "MAILON_RUNTIME_ROOT": str(runtime)},
        )
        if code != 0:
            checks.append(JobCheck("mailon-runtime-drift", Status.FAIL if code == 1 else Status.WARN,
                                   "기관메일 런타임 드리프트" if code == 1 else "기관메일 런타임 판정 불가"))
    supply = _state(home / ".hermes/supply-chain-watch/tick.json")
    failures = supply.get("reminder_failures", {})
    if isinstance(failures, dict) and failures:
        attempts = max((int(row.get("attempts", 0)) for row in failures.values() if isinstance(row, dict)),
                       default=0)
        checks.append(JobCheck("supply-chain-reminder", Status.FAIL if attempts >= 2 else Status.WARN,
                               f"공급망 승인 리마인더 전송 실패({attempts}회)"))
    return tuple(checks)
