"""`hermes cron list --all` 출력의 판독 — 잘렸거나 기형인 목록은 빈 목록이 아니라 모름이다.

`deploy_all_observe` 의 cron 관측이 쓴다. 문법은 벤더 `hermes_cli/cron.py::cron_list` 의 출력이고,
배포기의 수렴기(`automation/deploy_cron.sh::_converge_cron_jobs`, awk)와 **같은 계약**을 지킨다 —
두 해석기는 `tests/unit/test_cron_listing_parser_parity.py` 가 같은 입력 묶음으로 묶는다(계약 전문은
그 파일 docstring). 확신할 수 없으면 None 을 돌려준다.
"""
from __future__ import annotations

import re
from typing import Final

_JOB_HEADER: Final = re.compile(r"^  ([0-9a-f]+) \[(active|paused|completed|disabled)\][ \t]*$")
_JOB_FIELD: Final = re.compile(r"^    ([A-Z][A-Za-z ]*):(.*)$")
_TWO_SPACE: Final = re.compile(r"^  [^ ]")
_GATEWAY_WARNING: Final = "  ⚠  Gateway is not running"
_SCHEDULER_WARNING: Final = "  ⚠  Scheduler is not ready"
_NO_JOBS: Final = "No scheduled jobs."


def _norm(value: str) -> str:
    return " ".join(value.split())


def parse_cron_listing(text: str) -> dict[str, list[dict[str, str]]] | None:
    """`hermes cron list --all` → 이름별 블록. 잘렸거나 기형이면 None(빈 목록이 아니다)."""
    if not text.endswith("\n"):
        return None
    jobs: list[dict[str, str]] = []
    titled = empty = False
    ended = True
    for line in text[:-1].split("\n"):
        if header := _JOB_HEADER.match(line):
            if not titled or not ended:
                return None
            jobs.append({"id": header[1], "state": header[2]})
            ended = False
        elif line.startswith((_GATEWAY_WARNING, _SCHEDULER_WARNING)):
            continue
        elif _TWO_SPACE.match(line):
            return None
        elif field := _JOB_FIELD.match(line):
            key = _norm(field[1])
            if not jobs or key in jobs[-1]:
                return None
            jobs[-1][key] = _norm(field[2])
            ended = False
        elif not line:
            ended = True
        elif not jobs:
            titled = titled or "Scheduled Jobs" in line
            empty = empty or line == _NO_JOBS
    if not jobs:
        return {} if empty and not titled else None
    by_name: dict[str, list[dict[str, str]]] = {}
    for job in jobs:
        if not all(job.get(key) for key in ("Name", "Schedule", "Deliver")) or (job.get("Mode") and not job.get("Script")):
            return None
        by_name.setdefault(job["Name"], []).append(job)
    return by_name if ended else None
