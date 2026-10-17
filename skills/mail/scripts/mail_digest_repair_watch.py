#!/usr/bin/env python3
"""아침 다이제스트 뒤 실패를 수리 티켓으로 올리는 no-agent 워처(N1, 매일 09:10 KST).

2026-10-02 노드에 손으로 넣은 Hermes **에이전트 모드** 잡 `mail-triage-repair-daily` 를 대신한다. 그 잡이
모델에게 시키던 판정은 전부 결정적이라(플래그·표식·상태 파일 대조) 모델이 필요 없다. 같은 재료를 읽는다:

1. 다이제스트 DB(`TRIAGE_DB`, 기본 `~/state/mail-triage.db`)의 지난번 이후 새 실행 — 항목 플래그
   `classification_failed` 와 요약 `(요약 실패)` 개수.
2. 다이제스트 워처 출력의 `DIGEST-FAIL stage=… code=…` 표식 — Hermes 가 남긴 잡 출력 파일
   (`~/.hermes/cron/output/<잡 id>/`)에서 지난번 이후 것만.
3. 트리아지 워처 실패 상태 `~/.hermes/watch-failure/mail-triage-watch.json` — 지난번 이후 실패.

실패 종류마다 `repair_cli.py manual "!repair …"` 를 한 번 부른다. 문구에는 종류와 안전한 코드만 넣고 날짜·
개수·메일 내용·주소는 넣지 않는다 — 같은 종류가 다시 나면 수리 쪽 서명이 같은 카드의 재발로 센다.
자동 수리는 하지 않는다. 지난번 위치(`~/.hermes/mail-triage/repair-daily.json`)는 모든 티켓 등록이 성공한
뒤에만 전진한다(상태 마킹은 성공 이후). 출력은 `등록 N건 / occurrence 갱신 M건` 또는 `없음` 한 줄.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

SUMMARY_FALLBACK: Final = "(요약 실패)"
CLASSIFY_FLAG: Final = "classification_failed"
DIGEST_JOB: Final = "mail-daily-digest"
TRIAGE_WATCH: Final = "mail-triage-watch"
_MARKER: Final = re.compile(r"DIGEST-FAIL stage=([a-z_-]+) retry_safe=\w+ code=([A-Za-z0-9_.-]+)")
_DEFAULT_CLI: Final = "/srv/autophagy-agent-current/automation/repair/repair_cli.py"

FileTicket = Callable[[str], dict[str, object]]


def _load_secrets(home: Path) -> None:
    try:
        lines = (home / ".env.secrets").read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        key, sep, value = line.strip().removeprefix("export ").partition("=")
        if sep and key.strip() and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _parse_time(text: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def digest_failures(db: Path, after_run: int) -> tuple[dict[str, int], int]:
    """새 실행들의 (종류별 개수, 가장 최근 run_id). DB 가 없으면 빈 결과."""
    if not db.exists():
        return {}, after_run
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as connection:
        rows = connection.execute(
            "SELECT run_id, flags, summary FROM digest_items WHERE run_id > ?", (after_run,)
        ).fetchall()
        latest = connection.execute("SELECT MAX(run_id) FROM digest_runs").fetchone()[0]
    found: dict[str, int] = {}
    for _run, flags, summary in rows:
        if CLASSIFY_FLAG in str(flags).split(","):
            found["classification_failed"] = found.get("classification_failed", 0) + 1
        if str(summary) == SUMMARY_FALLBACK:
            found["summary_fallback"] = found.get("summary_fallback", 0) + 1
    return found, max(after_run, int(latest or 0))


def digest_markers(home: Path, since: datetime) -> dict[str, int]:
    """`DIGEST-FAIL` 표식 — `digest_fail:<stage>:<code>` 별 개수. 출력이 없으면 빈 결과."""
    try:
        raw = json.loads((home / ".hermes" / "cron" / "jobs.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    jobs = raw.get("jobs") if isinstance(raw, dict) else raw
    ids = [str(j.get("id")) for j in jobs or () if isinstance(j, dict) and j.get("name") == DIGEST_JOB]
    folders = [home / ".hermes" / "cron" / "output" / job_id for job_id in ids]
    # 다이제스트가 사용자 타이머로 돌면 러너가 출력을 여기 남긴다(`automation/cron_timer_run.py`).
    folders.append(home / ".hermes" / "cron-timers" / "output" / DIGEST_JOB)
    found: dict[str, int] = {}
    for folder in folders:
        for path in sorted(folder.glob("*")) if folder.is_dir() else ():
            if datetime.fromtimestamp(path.stat().st_mtime, UTC) <= since:
                continue
            for stage, code in _MARKER.findall(path.read_text(encoding="utf-8", errors="replace")):
                key = f"digest_fail:{stage}:{code}"
                found[key] = found.get(key, 0) + 1
    return found


def triage_watch_failure(home: Path, since: datetime) -> dict[str, int]:
    root = Path(os.environ.get("WATCH_FAILURE_ROOT") or home / ".hermes" / "watch-failure")
    try:
        raw = json.loads((root / f"{TRIAGE_WATCH}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    last = raw.get("last_failure") if isinstance(raw, dict) else None
    when = _parse_time(str(last.get("last_failed_at", ""))) if isinstance(last, dict) else None
    if isinstance(raw, dict) and (raw.get("incident_open") or (when is not None and when > since)):
        return {"triage_watch_failure": int(raw.get("consecutive_failures") or (last or {}).get("count") or 1)}
    return {}


def symptom(kind: str) -> str:
    if kind.startswith("digest_fail:"):
        _, stage, code = kind.split(":", 2)
        return f"!repair 메일 다이제스트 점검: 다이제스트 실패 DIGEST-FAIL stage={stage} code={code}"
    label = {"classification_failed": "분류 실패(classification_failed)",
             "summary_fallback": "요약 실패((요약 실패) 대체 문구)",
             "triage_watch_failure": "트리아지 워처 실패(mail-triage-watch)"}[kind]
    return f"!repair 메일 다이제스트 점검: {label}"


def repair_cli_ticket(message: str) -> dict[str, object]:
    cli = os.environ.get("REPAIR_CLI") or _DEFAULT_CLI
    env = {**os.environ, "PATH": f"{Path.home() / '.local' / 'bin'}:{os.environ.get('PATH', '/usr/bin:/bin')}"}
    done = subprocess.run([sys.executable, "-I", cli, "manual", message], capture_output=True,
                          text=True, env=env, timeout=300, check=False)
    if done.returncode != 0:
        raise RuntimeError(f"repair_cli manual exit {done.returncode}")
    return json.loads(done.stdout.strip().splitlines()[-1])


def run(home: Path, now: datetime, file_ticket: FileTicket) -> tuple[int, str]:
    state_path = home / ".hermes" / "mail-triage" / "repair-daily.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    since = _parse_time(str(state.get("since", ""))) or now - timedelta(hours=24)
    db = Path(os.environ.get("TRIAGE_DB", "~/state/mail-triage.db")).expanduser()
    first = "last_run_id" not in state
    after = int(state.get("last_run_id", 0))
    if first and db.exists():
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as connection:
            rows = connection.execute("SELECT run_id, sent_at FROM digest_runs").fetchall()
        after = max((int(r) for r, sent in rows if (_parse_time(str(sent)) or now) <= since), default=0)
    failures, latest = digest_failures(db, after)
    failures |= digest_markers(home, since)
    failures |= triage_watch_failure(home, since)
    created = updated = 0
    for kind in sorted(failures):
        result = file_ticket(symptom(kind))
        if result.get("created") is True:
            created += 1
        else:
            updated += 1
    state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = state_path.with_name(f".{state_path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"last_run_id": latest, "since": now.isoformat(timespec="seconds")}) + "\n",
                   encoding="utf-8")
    tmp.chmod(0o600)
    os.replace(tmp, state_path)
    return 0, (f"등록 {created}건 / occurrence 갱신 {updated}건" if failures else "없음")


def main() -> int:
    home = Path.home()
    _load_secrets(home)
    try:
        rc, line = run(home, datetime.now(UTC), repair_cli_ticket)
    except (OSError, RuntimeError, ValueError, sqlite3.Error, subprocess.TimeoutExpired) as error:
        print(f"MAIL-DIGEST-REPAIR-FAIL {type(error).__name__}", file=sys.stderr)
        return 1
    print(line)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
