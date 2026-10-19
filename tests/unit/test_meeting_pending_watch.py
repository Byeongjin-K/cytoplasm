"""야간 배치가 미처리 전사본을 회의록으로 만드는 계약 (no-agent cron).

별도 파일인 이유는 대상이 CLI 가 아니라 **cron 래퍼**이기 때문이다 — 여기서 보는 것은
회의록의 내용이 아니라 워처가 지켜야 할 규약이다: 마운트를 해결하지 못하면 자식을 띄우지
않고(fail-closed), 부모가 갖지 못한 자격증명을 자식 env 에 명시 전달하며(설계규약 (b-2)),
처리할 것이 없는 밤에는 stdout 을 비워 침묵한다(`--no-agent` 의 "Empty stdout = silent").
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "skills" / "meeting"
sys.path.insert(0, str(SKILL / "scripts"))

import meeting_actions  # noqa: E402
import meeting_cli  # noqa: E402
import meeting_pending_transcript_watch as watch  # noqa: E402


class _Pending:
    def __init__(self, name: str, project: str = "해양고신뢰성", year: str = "2026") -> None:
        self.name = name
        self.project = project
        self.year = year


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return {"HOME": str(home), "PATH": "/usr/bin:/bin", **extra}


def _scripts(tmp_path: Path) -> Path:
    scripts = tmp_path / "live" / "meeting" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "meeting_cli.py").write_text("", encoding="utf-8")
    return scripts


def _recorder():
    calls: list[tuple[list[str], dict[str, str]]] = []

    def runner(argv: list[str], env: dict[str, str]) -> tuple[int, str]:
        calls.append((argv, env))
        return 0, ""

    return calls, runner


def test_one_pending_transcript_is_handed_to_the_cli(tmp_path, capsys) -> None:
    calls, runner = _recorder()

    code = watch.run_once(
        env=_env(tmp_path), scripts=_scripts(tmp_path), runner=runner,
        pending=lambda: (_Pending("2026-08-26_20260825_해양고신뢰성.md"),),
    )
    out = capsys.readouterr().out

    assert code == 0
    assert len(calls) == 1
    argv = calls[0][0]
    assert "ingest" in argv
    assert argv[argv.index("--pending-name") + 1] == "2026-08-26_20260825_해양고신뢰성.md"
    assert "8/26" in out and ".md" not in out, "만든 회의록은 날짜로 알리고 파일명은 형 글에 싣지 않는다"


def test_default_backlog_limit_is_three_per_tick(tmp_path, capsys) -> None:
    calls, runner = _recorder()
    backlog = tuple(_Pending(f"t{index}.md") for index in range(6))

    code = watch.run_once(
        env=_env(tmp_path), scripts=_scripts(tmp_path), runner=runner,
        pending=lambda: backlog,
    )

    assert code == 0
    assert len(calls) == 3


def test_a_backlog_is_capped_per_tick(tmp_path, capsys) -> None:
    """상한이 없으면 하루치 밀린 전사본이 한 밤에 전부 LLM 을 태운다."""
    calls, runner = _recorder()
    backlog = tuple(_Pending(f"t{index}.md") for index in range(7))

    code = watch.run_once(
        env=_env(tmp_path, MEETING_PENDING_LIMIT="3"), scripts=_scripts(tmp_path),
        runner=runner, pending=lambda: backlog,
    )
    out = capsys.readouterr().out

    assert code == 0
    assert [call[0][call[0].index("--pending-name") + 1] for call in calls] == [
        "t0.md", "t1.md", "t2.md"
    ]
    assert "4" in out, f"남은 4건을 알려야 다음 밤을 기다릴지 판단한다: {out}"


def test_child_receives_credentials_the_parent_never_had(tmp_path, capsys) -> None:
    """no-agent cron 은 `~/.env.secrets` 를 os.environ 에 넣어주지 않는다 (설계규약 (b-2))."""
    env = _env(tmp_path)
    secrets = Path(env["HOME"]) / ".env.secrets"
    secrets.write_text(
        'DISCORD_BOT_TOKEN="from-secrets"\nDRIVE_PUBLISH_ENABLED=1\n', encoding="utf-8"
    )
    calls, runner = _recorder()

    watch.run_once(
        env=env, scripts=_scripts(tmp_path), runner=runner,
        pending=lambda: (_Pending("t.md"),),
    )

    child_env = calls[0][1]
    assert "DISCORD_BOT_TOKEN" not in env, "부모가 이미 갖고 있으면 이 테스트는 아무것도 증명하지 않는다"
    assert child_env["DISCORD_BOT_TOKEN"] == "from-secrets"
    assert child_env["DRIVE_PUBLISH_ENABLED"] == "1", "옵트인이 없으면 자식은 Drive 를 건드리지 않는다"


def test_an_empty_backlog_says_nothing(tmp_path, capsys) -> None:
    """매일 도는 작업이라, 할 일이 없는 밤의 한 줄도 1년이면 365번이다."""
    calls, runner = _recorder()

    code = watch.run_once(
        env=_env(tmp_path), scripts=_scripts(tmp_path), runner=runner, pending=tuple,
    )

    assert code == 0
    assert calls == []
    assert capsys.readouterr().out == ""


def test_an_unmounted_skill_refuses_to_run(tmp_path, capsys) -> None:
    calls, runner = _recorder()

    code = watch.run_once(
        env=_env(tmp_path), scripts=tmp_path / "absent", runner=runner,
        pending=lambda: (_Pending("t.md"),),
    )

    assert code == 1
    assert calls == [], "마운트를 확인하지 못하면 자식을 띄우지 않는다"
    assert "MEETING-WATCH-BLOCK" in capsys.readouterr().out


def test_replayed_card_that_is_already_blocked_does_not_fail_ingest(monkeypatch, capsys) -> None:
    """Idempotent create returns yesterday's card; blocked is already the required state."""
    card = meeting_actions.PlannedCard(
        title="pending transcript action", body="private meeting detail", idempotency_key="k1"
    )
    calls: list[list[str]] = []

    def fake_run(argv, capture_output=True, timeout=None, cwd=None, check=False):
        calls.append(argv)
        operation = argv[2]
        responses = {
            "create": (0, json.dumps({"id": "t_existing"}).encode(), b""),
            "block": (1, b"", b"cannot block t_existing"),
            "show": (0, b"Task t_existing: action\n  status:    blocked\n", b""),
        }
        returncode, stdout, stderr = responses[operation]
        if check and returncode:
            raise subprocess.CalledProcessError(returncode, argv, stdout, stderr)
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    monkeypatch.setattr(meeting_cli.subprocess, "run", fake_run)

    assert meeting_cli._run_kanban(card) == "t_existing"
    assert [call[2] for call in calls] == ["create", "block", "show"]
    assert "KANBAN-BLOCK-REDUNDANT card=t_existing" in capsys.readouterr().out


def test_a_failing_child_is_reported_without_stopping_the_rest(tmp_path, capsys) -> None:
    """전사본은 서로 독립이다 — 하나가 실패했다고 나머지를 버리지 않는다."""
    calls: list[str] = []

    def runner(argv: list[str], env: dict[str, str]) -> tuple[int, str]:
        name = argv[argv.index("--pending-name") + 1]
        calls.append(name)
        return (6 if name == "b.md" else 0), ""

    code = watch.run_once(
        env=_env(tmp_path), scripts=_scripts(tmp_path), runner=runner,
        pending=lambda: (_Pending("a.md"), _Pending("b.md"), _Pending("c.md")),
    )
    out = capsys.readouterr().out

    assert calls == ["a.md", "b.md", "c.md"]
    assert code == 1, "실패를 rc 0 으로 덮으면 cron 기록이 성공으로 남는다"
    assert "b.md" in out, "실패한 전사본은 이름을 남겨야 페어 채널에서 고칠 수 있다"


def test_the_child_json_never_reaches_the_owner_post(tmp_path, capsys) -> None:
    """형이 가리킨 글(2026-10-20): 자식 결과 JSON 이 맨 위에 붙었다 — 이제는 사람 말과 링크만."""
    from automation.interop.owner_text import findings

    child = json.dumps({
        "exit": 0, "ref": "121921ba", "provider": "openai-codex", "codex_called": True,
        "note": "2026-10-20-meeting-121921ba.md", "project": "stt-56",
        "link": "https://drive.google.com/file/d/minutes/view",
        "speakers": [{"label": "화자1", "name": None, "basis": "자기소개·호명 등 이름 근거 없음"}],
    }, ensure_ascii=False)

    code = watch.run_once(
        env=_env(tmp_path), scripts=_scripts(tmp_path), runner=lambda argv, env: (0, child + "\n"),
        pending=lambda: (_Pending("2026-10-19_stt-56_2.md"),),
    )
    out = capsys.readouterr().out

    assert code == 0
    assert "10/19 stt-56" in out and "https://drive.google.com/file/d/minutes/view" in out
    assert [line for line in out.splitlines() if findings(line)] == [], out
