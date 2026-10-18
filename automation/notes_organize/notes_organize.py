"""Run weekly notes organization; keep the delivered-week watermark.

Success is silent and only successful delivery advances the watermark. Failures
remain nonzero for the timer runner; the old watch-failure ledger is retired."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from importlib import import_module
from pathlib import Path
from typing import Final, Protocol, cast


class _StreakHelper(Protocol):
    def record(
        self, name: str, *, ok: bool, detail: str = "", threshold: int = 5
    ) -> str | None: ...


# The helper is deployed flat beside this wrapper. Package fallback keeps imports working
# from the checkout under pytest; a half-deployed node retains the legacy failure line.
def _import_streak_helper() -> _StreakHelper | None:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    for module_name in ("watch_failure_streak", "skills.mail.scripts.watch_failure_streak"):
        try:
            return cast(_StreakHelper, cast(object, import_module(module_name)))
        except ImportError:
            continue
    return None


_packaged_streak = _import_streak_helper()
watch_failure_streak = _packaged_streak

# 마운트 판정은 governed live 정의 하나(automation/skill_mount.py)에서만 온다. 노드에서
# 이 래퍼는 ~/.hermes/scripts/ 에 평평하게 배포되므로 코드 루트를 값으로 되짚는다 —
# 체크아웃 → 릴리스 current → 미러 (test_skill_runtime_root_fallback.py 와 같은 관용구).
for _root in (
    *Path(__file__).resolve().parents,
    Path(os.environ.get("AUTOPHAGY_RUNTIME_ROOT") or "/srv/autophagy-agent-current"),
    Path("/srv/autophagy-agents"),
):
    if (_root / "automation" / "skill_mount.py").is_file():
        sys.path.insert(0, str(_root))
        break
try:
    from automation.skill_mount import skill_scripts
except ImportError:  # pragma: no cover — 릴리스가 아직 이 정의에 수렴하지 않은 노드
    _SCRIPTS = None  # 판정할 수 없으면 마운트 없음으로 fail-closed 한다
else:
    _SCRIPTS = skill_scripts("report", env_var="REPORT_SCRIPTS")
CLI = None if _SCRIPTS is None else _SCRIPTS / "report_cli.py"
_ENV_SECRETS: Final = Path.home() / ".env.secrets"
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LONG_DIGITS = re.compile(r"\d{5,}")
WATCH_NAME: Final = "notes-weekly-organize"
FAILURE_NOTICE_THRESHOLD: Final = 1
WEEK_WATERMARK: Final = "delivered-week"
KST: Final = timezone(timedelta(hours=9), "KST")


def _load_env_secrets(path: Path = _ENV_SECRETS) -> None:
    """no-agent cron hands the wrapper no secrets, so the parent loads them itself.

    Measured 2026-08-18 on `budget-watch`: the value was already in ~/.env.secrets and
    the tick still failed as if it were missing, because nothing put it in the
    environment (규약 (b)). The report CLI reaches Obsidian/RAG credentials the same
    way. Inventory check: tests/unit/test_watcher_secret_propagation.py.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def _redact(text: str) -> str:
    return _LONG_DIGITS.sub("[MASKED-NUM]", _EMAIL.sub("[MASKED-EMAIL]", text))


def _state_dir() -> Path:
    override = os.environ.get("NOTES_ORGANIZE_STATE_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".hermes" / "notes-organize"


def _current_iso_week() -> str:
    calendar = datetime.now(KST).isocalendar()
    return f"{calendar.year}-W{calendar.week:02d}"


def _delivered_week() -> str:
    try:
        return (_state_dir() / WEEK_WATERMARK).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return ""


def _record_delivered_week(week: str) -> None:
    directory = _state_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    _ = os.chmod(directory, 0o700)
    path = directory / WEEK_WATERMARK
    _ = path.write_text(week + "\n", encoding="utf-8")
    _ = os.chmod(path, 0o600)


def _announce(*, ok: bool, detail: str = "", fallback: str = "") -> bool:
    """The timer runner owns failure counting; report the actual failing exit."""
    if not ok:
        print((fallback or f"notes-organize error: {detail}")[:300])
    return False


def main() -> int:
    _load_env_secrets()
    week = _current_iso_week()
    if _delivered_week() == week:
        return 0
    if CLI is None or not CLI.exists():
        detail = "report skill is not mounted"
        recorded = _announce(ok=False, detail=detail, fallback=f"notes-organize error: {detail}")
        return 0 if recorded else 1
    try:
        result = subprocess.run(
            [sys.executable, str(CLI), "organize"],
            cwd=Path.home(),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            # 규약 (b-2): the child gets the environment we just self-loaded, explicitly.
            env={**os.environ},
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        detail = error.__class__.__name__
        recorded = _announce(ok=False, detail=detail, fallback=f"notes-organize error: {detail}")
        return 0 if recorded else 1
    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()
        child_detail = _redact(tail[-1])[:300] if tail else ""
        detail = f"rc={result.returncode}"
        if child_detail:
            detail += f": {child_detail}"
        recorded = _announce(
            ok=False,
            detail=detail,
            fallback=f"notes-organize error {detail}",
        )
        return 0 if recorded else 1
    try:
        _record_delivered_week(week)
    except OSError as error:
        detail = f"watermark {error.__class__.__name__}"
        recorded = _announce(ok=False, detail=detail, fallback=f"notes-organize error: {detail}")
        return 0 if recorded else 1
    _announce(ok=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:  # noqa: BLE001 - cron crash path: immediate masked line
        try:
            print(f"notes-organize error: {_redact(str(error))}"[:300])
        except BrokenPipeError:
            pass
        raise SystemExit(1) from None
