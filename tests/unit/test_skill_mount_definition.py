"""세 no-agent cron 래퍼가 governed live 마운트 정의 **하나**를 쓰는지 고정한다.

배경(`docs/follow-ups.md`, 2026-08-17): report·coordination·calendar·
(그때는 budget·research-trends 도)가 각자 마운트 경로를 들고 있어, 심링크가 멀쩡한데도 `not mounted`
또는 import 오류를 냈다. 판정은 `automation/skill_mount_drift.py`·`skill_mount_probe.sh`
와 같은 `/srv/autophagy-skills/live/<skill>` 정의 하나여야 한다.

경로 여러 벌이 우연히 같은 문자열이어도 정의가 하나인 것은 아니다 — 그래서 이 파일은
문자열을 여러 번 확인하는 대신 **주입된 live 루트를 모든 래퍼가 모두 따르는지**를 본다.
주입(`AUTOPHAGY_SKILL_LIVE_ROOT`)은 `skill_mount_probe.sh` 의 `HEALTHCHECK_SKILL_LIVE_ROOT`
와 같은 테스트/운영용 구멍이며, 없으면 governed 기본값으로 fail-closed 한다.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

_REPO: Final = Path(__file__).resolve().parents[2]
_GOVERNED_LIVE: Final = "/srv/autophagy-skills/live"
_LIVE_ROOT_ENV: Final = "AUTOPHAGY_SKILL_LIVE_ROOT"

#: 래퍼가 읽는 환경 변수는 전부 걷어내고 테스트가 명시한 것만 남긴다 — 바깥 환경이
#: 판정을 바꾸면 이 테스트가 증명하는 것이 없어진다.
_STRIPPED_ENV: Final = (
    "REPORT_SCRIPTS",
    "CALENDAR_SCRIPTS",
    "COORDINATION_SCRIPTS",
    "NOTES_ORGANIZE_STATE_DIR",
    "WATCH_FAILURE_ROOT",
    _LIVE_ROOT_ENV,
)

#: (래퍼 경로, governed 스킬 이름, 해결된 스크립트 경로를 담은 모듈 심볼).
_WRAPPERS: Final = (
    ("automation/notes_organize/notes_organize.py", "report", "_SCRIPTS"),
    ("skills/coordination/scripts/confirm_reaction_watch.py", "coordination", "_SCRIPTS"),
    ("skills/calendar/scripts/confirm_reaction_watch.py", "calendar", "_SCRIPTS"),
)


def _environment(home: Path, live_root: Path) -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items() if key not in _STRIPPED_ENV}
    environment["HOME"] = str(home)
    environment[_LIVE_ROOT_ENV] = str(live_root)
    # 배포된 래퍼는 `~/.hermes/scripts/` 에서 평평하게 돌지만, 체크아웃에서는 리포가
    # 코드 루트다.
    environment["PYTHONPATH"] = str(_REPO)
    return environment


def _run_module(relative: str, symbol: str, home: Path, live_root: Path) -> subprocess.CompletedProcess[str]:
    """래퍼를 import 만 하고(엔트리포인트 미실행) 해결된 스크립트 경로를 찍는다."""
    script = (
        "import runpy\n"
        f"namespace = runpy.run_path({str(_REPO / relative)!r})\n"
        f"print(namespace[{symbol!r}])\n"
    )
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
        env=_environment(home, live_root),
        cwd=str(_REPO),
    )


def _run_wrapper(relative: str, home: Path, live_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_REPO / relative)],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
        env=_environment(home, live_root),
        cwd=str(_REPO),
    )


def _plant_mounted_cli(live_root: Path, skill: str, cli: str, marker: Path) -> None:
    scripts = live_root / skill / "scripts"
    scripts.mkdir(parents=True)
    _ = (scripts / cli).write_text(
        "from pathlib import Path\n"
        f"_ = Path({str(marker)!r}).write_text('ran', encoding='utf-8')\n",
        encoding="utf-8",
    )


def _streak_state(home: Path, watch_name: str) -> dict[str, object]:
    raw = (home / ".hermes" / "watch-failure" / f"{watch_name}.json").read_text(encoding="utf-8")
    parsed: object = json.loads(raw)
    assert isinstance(parsed, dict)
    return {
        "consecutive_failures": parsed["consecutive_failures"],
        "incident_open": parsed["incident_open"],
    }


def test_shared_definition_when_no_override_then_governed_live_scripts() -> None:
    # Given: 아무 덮어쓰기도 없는 환경.
    from automation.skill_mount import skill_scripts

    # When/Then: 다섯 스킬 모두 governed live 아래로 해결된다.
    for _, skill, _symbol in _WRAPPERS:
        assert skill_scripts(skill, env={}) == Path(f"{_GOVERNED_LIVE}/{skill}/scripts")


def test_shared_definition_when_env_override_set_then_override_wins() -> None:
    # Given: 스킬별 덮어쓰기(기존 운영 구멍)가 설정된 환경.
    from automation.skill_mount import skill_scripts

    # When
    resolved = skill_scripts("report", env_var="REPORT_SCRIPTS", env={"REPORT_SCRIPTS": "~/mounted"})

    # Then: 덮어쓰기가 이기고 `~` 는 펼쳐진다.
    assert resolved == Path.home() / "mounted"


def test_shared_definition_when_compared_with_drift_probe_then_same_live_root() -> None:
    # Given: 마운트 드리프트 판정기의 live 루트 기본값.
    from automation.skill_mount import LIVE_ROOT

    drift_source = (_REPO / "automation" / "skill_mount_drift.py").read_text(encoding="utf-8")

    # Then: 공유 정의가 그것과 같은 문자열이어야 한다(정의가 갈리면 오진이 돌아온다).
    assert str(LIVE_ROOT) == _GOVERNED_LIVE
    assert f'default=Path("{_GOVERNED_LIVE}")' in drift_source


@pytest.mark.parametrize(("relative", "skill", "symbol"), _WRAPPERS)
def test_wrapper_when_only_governed_live_exists_then_resolves_there(
    tmp_path: Path, relative: str, skill: str, symbol: str
) -> None:
    # Given: governed live 루트만 존재하고(레거시 홈 경로는 없다), 스킬별 덮어쓰기도 없다.
    home = tmp_path / "home"
    home.mkdir()

    # When: 래퍼를 import 한다.
    result = _run_module(relative, symbol, home, _REPO / "skills")

    # Then: 마운트된 스킬을 governed live 아래에서 찾는다 = mounted.
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(_REPO / "skills" / skill / "scripts")


@pytest.mark.parametrize(
    ("relative", "watch_name", "cli"),
    (
        ("automation/notes_organize/notes_organize.py", "notes-weekly-organize", "report_cli.py"),
    ),
)
def test_watcher_when_only_governed_live_exists_then_runs_the_mounted_cli(
    tmp_path: Path, relative: str, watch_name: str, cli: str
) -> None:
    # Given: governed live 아래에만 CLI 가 있다.
    home = tmp_path / "home"
    home.mkdir()
    live_root = tmp_path / "srv" / "autophagy-skills" / "live"
    marker = tmp_path / "child-ran"
    skill = "report"
    _plant_mounted_cli(live_root, skill, cli, marker)

    # When: cron 틱이 돈다.
    result = _run_wrapper(relative, home, live_root)

    # Then: 마운트된 CLI 가 실제로 실행되고, 실패 스트릭은 열리지 않는다.
    assert result.returncode == 0, result.stderr
    assert marker.read_text(encoding="utf-8") == "ran"
    assert not (home / ".hermes" / "watch-failure" / f"{watch_name}.json").exists()


@pytest.mark.parametrize(
    ("relative", "watch_name", "incident_open"),
    (
        ("automation/notes_organize/notes_organize.py", "notes-weekly-organize", True),
    ),
)
def test_watcher_when_neither_path_exists_then_reports_not_mounted(
    tmp_path: Path, relative: str, watch_name: str, incident_open: bool
) -> None:
    # Given: governed live 루트도, 레거시 홈 경로도 없다.
    home = tmp_path / "home"
    home.mkdir()

    # When: cron 틱이 돈다.
    result = _run_wrapper(relative, home, tmp_path / "absent")

    # Then: the timer sees the failure; the retired ledger remains absent.
    assert result.returncode == 1, result.stderr
    assert "not mounted" in result.stdout
    assert not (home / ".hermes/watch-failure" / f"{watch_name}.json").exists()
@pytest.mark.parametrize(
    ("relative", "symbol", "missing_module"),
    (
        ("skills/coordination/scripts/confirm_reaction_watch.py", "_SCRIPTS", "coordinate_io"),
        ("skills/calendar/scripts/confirm_reaction_watch.py", "_SCRIPTS", "calendar_confirm"),
    ),
)
def test_import_watcher_when_neither_path_exists_then_fails_closed(
    tmp_path: Path, relative: str, symbol: str, missing_module: str
) -> None:
    # Given: governed live 루트도, 레거시 홈 경로도 없다.
    home = tmp_path / "home"
    home.mkdir()

    # When: 래퍼를 import 한다.
    result = _run_module(relative, symbol, home, tmp_path / "absent")

    # Then: 마운트 없이 조용히 진행하지 않는다 — 미마운트는 실패다.
    assert result.returncode != 0
    assert missing_module in result.stderr
