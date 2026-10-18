from __future__ import annotations

import ast

import os
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_WRAPPER = _REPO / "skills" / "budget" / "scripts" / "budget_watch.py"


def _run_wrapper(
    home: Path, scripts: Path | None = None, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "HOME": str(home), **(extra_env or {})}
    if scripts is not None:
        env["BUDGET_SCRIPTS"] = str(scripts)
    return subprocess.run(
        [sys.executable, str(_WRAPPER)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env=env,
    )


def _plant_cli(scripts: Path) -> None:
    scripts.mkdir(parents=True)
    (scripts / "budget_cli.py").write_text("raise SystemExit(0)\n", encoding="utf-8")


def test_wrapper_ignores_the_self_skill_root(tmp_path: Path) -> None:
    # Given: an executable lookalike exists only in the account-owned self-skill root.
    _plant_cli(tmp_path / ".hermes" / "skills" / "budget" / "scripts")

    # When: the wrapper resolves its governed budget CLI.
    result = _run_wrapper(tmp_path)

    # Then: the self-authored skill is ignored; the miss is RECORDED in the streak
    # and the tick exits 0 — with --deliver discord any non-zero exit makes the
    # scheduler post its own failure banner every tick (2026-08-24 measured).
    assert result.returncode == 1
    assert "not mounted" in result.stdout
    assert not (tmp_path / ".hermes/watch-failure/budget-watch.json").exists()


def test_pre_main_crash_prints_one_masked_line_on_the_first_tick(tmp_path: Path) -> None:
    # Given: secret loading crashes before main reaches the expected child-rc path.
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / ".env.secrets").write_bytes(b"\xffowner@example.com-152648282079")

    # When: the first cron tick runs with no prior streak state.
    result = _run_wrapper(tmp_path)

    # Then: an exceptional wrapper crash is immediate, singular, masked, and exit 1.
    assert result.returncode == 1
    lines = result.stdout.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("budget-watch error: ")
    assert "owner@example.com" not in lines[0]
    assert "152648282079" not in lines[0]
    assert len(lines[0]) <= 300


def test_wrapper_honors_the_scripts_env_override(tmp_path: Path) -> None:
    # Given: the governed CLI is supplied through the test/runtime override.
    scripts = tmp_path / "mounted-budget" / "scripts"
    _plant_cli(scripts)

    # When: the wrapper runs with that scripts directory.
    result = _run_wrapper(tmp_path, scripts)

    # Then: the wrapper executes the mounted CLI successfully and remains silent.
    assert result.returncode == 0
    assert result.stdout == ""


def test_wrapper_expires_pending_requests_before_running_the_governed_child(tmp_path: Path) -> None:
    """만료가 먼저 끝나야 같은 tick의 watch가 초안을 재게시하지 않는다."""
    scripts = tmp_path / "mounted-budget" / "scripts"
    marker = tmp_path / "expired"
    scripts.mkdir(parents=True)
    (scripts / "budget_confirm.py").write_text(
        "from pathlib import Path\n"
        "import os\n"
        "def expire_pending_drafts(_now):\n"
        "    Path(os.environ['EXPIRY_MARKER']).write_text('expired', encoding='utf-8')\n",
        encoding="utf-8",
    )
    (scripts / "budget_cli.py").write_text(
        "from pathlib import Path\n"
        "import os\n"
        "raise SystemExit(0 if Path(os.environ['EXPIRY_MARKER']).exists() else 1)\n",
        encoding="utf-8",
    )

    result = _run_wrapper(tmp_path, scripts, {"EXPIRY_MARKER": str(marker)})

    assert result.returncode == 0
    assert marker.read_text(encoding="utf-8") == "expired"


def test_wrapper_self_loads_secrets_and_propagates_them_to_the_child() -> None:
    """no-agent cron 규약: 시크릿을 자체 로드하고 자식 env 에 명시 전파한다.

    2026-08-18 실측: `BUDGET_SHEET_ID` 를 `~/.env.secrets` 에 넣었는데도 워처는 계속
    `GATE-REFUSED BUDGET_SHEET_ID가 없습니다 (fail-closed)` 로 죽었다. Hermes no-agent
    cron 은 시크릿을 os.environ 에 넣지 않으므로, 자체 로드가 없으면 설정이 있어도
    자식에게 도달하지 않는다 — 셸에서 `set -a; . ~/.env.secrets` 를 한 뒤 같은 워처를
    돌리면 rc=0 이었다. 정답 형태는 `skills/todo/scripts/todo_confirm_reaction_watch.py`
    의 `_load_env_secrets()` 다.
    """
    source = (
        Path(__file__).resolve().parents[2]
        / "skills" / "budget" / "scripts" / "budget_watch.py"
    ).read_text(encoding="utf-8")

    assert ".env.secrets" in source, (
        "워처가 ~/.env.secrets 를 자체 로드하지 않는다 — no-agent cron 에서는 "
        "os.environ 에 시크릿이 없어 설정해도 무효다"
    )

    tree = ast.parse(source)
    spawns = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "subprocess.run"
    ]
    assert spawns, "subprocess.run 호출을 찾지 못했다 — 래퍼 구조가 바뀌었나"
    for call in spawns:
        assert any(kw.arg == "env" for kw in call.keywords), (
            "자식에게 env= 를 명시 전파하지 않는다 — 규약 (b-2) 위반"
        )
