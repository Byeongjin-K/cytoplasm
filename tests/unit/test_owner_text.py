"""형 채널 글의 기계 출력 규칙(`automation.interop.owner_text`)과 그것을 쓰는 두 출구.

표본은 2026-10-18~20 #yoodori 에 실제로 올라간 글에서 옮겼다. 기계 출력 표본이 형 몫으로
새거나, 사람 말 표본(메일 알림·릴리스 한 줄·회의록 한 줄)이 형 채널에서 빠지면 실패한다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from automation import cron_timer, cron_timer_run, owner_notice
from automation.interop import owner_text

MACHINE = [
    json.dumps({"exit": 0, "ref": "121921ba", "provider": "openai-codex", "speakers": []}),
    "mail-daily-digest recovered after 1 consecutive failures (2026-10-18T23:01:12Z..2026-10-18T23:01:12Z UTC)"
    " — last failure: DIGEST-FAIL stage=build retry_safe=false code=codex_unavailable",
    "MAIL-ATTACHMENT-DRIVE-FAIL code=download_failed",
    "mail-triage-watch error: rc=6: 메일 서비스 연결에 실패했습니다.",
    "> 릴리스 v2.6.4 가 적용되었습니다. (HEAD 659a79eef5d6)",
    ">   · [healthcheck-wrapper] WRAPPER-DRIFT",
    "위치: 링크 없음 (주소 없음); 검색: 자가 스킬 루트 / /home/agent/.hermes/skills",
    "⚠️ 정기 작업 'mail-sync-watch' 실패(종료코드 2)",
]
HUMAN = [
    "회의록 하나 만들었어 — 10/19 stt-56",
    "https://drive.google.com/file/d/example-file-id/view?usp=sharing",
    "새 메일(기관메일): 홍길동 · [회의] 개념설계 검토 회의 결과 및 최종 자료 공유",
    "새 메일(기관메일): 보안 담당 · [보안] 2026년 하반기 보안교육 이수 요청 (16:12). 기한이 10/23(금)까지야.",
    "✅ v2.6.6 적용됨 — 릴리스 서명 열쇠 교체(새 기계)와 세션 탭·정기 작업 정리",
    "> 주제 6개 · 논문 18편 — 전문은 첨부 `research-trends-20261019.md`",
    "곧 시작: 탁구 레슨 (4/8) 14:20",
    "마감 3일 전·1일 전·당일(D-3·D-1·D-day)에 알려 줄게.",
]


@pytest.mark.parametrize("line", MACHINE)
def test_machine_output_is_caught(line: str) -> None:
    assert owner_text.findings(line)


@pytest.mark.parametrize("line", HUMAN)
def test_human_lines_reach_the_owner(line: str) -> None:
    assert owner_text.findings(line) == ()


def test_split_keeps_json_internal_and_sends_diagnostics_to_pair() -> None:
    view = owner_text.split("\n".join([MACHINE[0], HUMAN[0], MACHINE[2]]))

    assert view.owner == HUMAN[0]
    assert view.data == (MACHINE[0],)
    assert view.diagnostics == (MACHINE[2],)


def _home(tmp_path: Path, script: str) -> tuple[Path, cron_timer.Job]:
    (tmp_path / ".hermes" / "scripts").mkdir(parents=True)
    (tmp_path / ".hermes" / ".env").write_text("DISCORD_HOME_CHANNEL=222\n", encoding="utf-8")
    (tmp_path / ".env.secrets").write_text("DISCORD_BOT_TOKEN=fixture-token\n", encoding="utf-8")
    (tmp_path / ".hermes" / "scripts" / "m.py").write_text(script, encoding="utf-8")
    return tmp_path, cron_timer.Job("m-watch", "* * * * *", "m.py", (), "discord")


def test_timer_runner_posts_only_the_human_line_of_the_minutes_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = f"print({MACHINE[0]!r})\nprint({HUMAN[0]!r})\n"
    home, job = _home(tmp_path, script)
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(cron_timer_run, "_discord_send", lambda *a: sent.append(a))
    monkeypatch.setattr("automation.notice_bot.fault_channel", lambda home=None: None)
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)

    assert cron_timer_run.run(home, job) == 0

    assert sent == [("fixture-token", "222", HUMAN[0])]
    kept = next((home / ".hermes" / "cron-timers" / "output" / "m-watch").glob("*.txt")).read_text()
    assert '"provider"' in kept, "JSON 은 형 글에서만 빠지고 내부 출력 기록에는 남는다"


def test_timer_runner_sends_warning_lines_to_pair_and_the_rest_to_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, job = _home(tmp_path, f"print({HUMAN[2]!r})\nprint({MACHINE[2]!r})\n")
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(cron_timer_run, "_discord_send", lambda *a: sent.append(a))
    monkeypatch.setattr("automation.notice_bot.fault_channel", lambda home=None: "333")

    assert cron_timer_run.run(home, job) == 0

    assert ("fixture-token", "222", HUMAN[2]) in sent
    assert [target for _, target, body in sent if "경고를 남겼어요" in body] == ["333"]
    assert not any(owner_text.findings(line) for *_, body in sent for line in body.splitlines()), "코드 줄은 출력 기록에만"
    kept = next((home / ".hermes" / "cron-timers" / "output" / "m-watch").glob("*.txt")).read_text()
    assert MACHINE[2] in kept


def test_timer_runner_without_pair_channel_keeps_the_legacy_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, job = _home(tmp_path, f"print({MACHINE[0]!r})\nprint({MACHINE[2]!r})\n")
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(cron_timer_run, "_discord_send", lambda *a: sent.append(a))
    monkeypatch.setattr("automation.notice_bot.fault_channel", lambda home=None: None)

    assert cron_timer_run.run(home, job) == 0

    assert sent == [("fixture-token", "222", MACHINE[2])], "경고는 없애지 않고 JSON 만 빠진다"


def test_owner_notice_trims_machine_lines_and_forwards_diagnostics_to_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posted: list[tuple[str, str]] = []
    monkeypatch.setattr(owner_notice, "resolve_token", lambda *a: "fixture-token")
    monkeypatch.setattr(owner_notice, "fault_channel", lambda: "333")
    monkeypatch.setattr(owner_notice, "resolve_notice_target", lambda token: "222")
    monkeypatch.setattr(owner_notice, "owner_notice_channel", lambda: "222")
    monkeypatch.setattr(owner_notice, "send_notice", lambda token, channel, body: posted.append((channel, body)))

    assert owner_notice.notify_owner("\n".join([HUMAN[4], MACHINE[4], MACHINE[5]])) is True

    assert posted == [("333", "\n".join([HUMAN[4], MACHINE[4], MACHINE[5]]))], "경고 코드가 든 통지는 통째 페어로"


def test_owner_notice_drops_json_result_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    posted: list[tuple[str, str]] = []
    monkeypatch.setattr(owner_notice, "resolve_token", lambda *a: "fixture-token")
    monkeypatch.setattr(owner_notice, "fault_channel", lambda: "333")
    monkeypatch.setattr(owner_notice, "resolve_notice_target", lambda token: "222")
    monkeypatch.setattr(owner_notice, "owner_notice_channel", lambda: "222")
    monkeypatch.setattr(owner_notice, "send_notice", lambda token, channel, body: posted.append((channel, body)))

    assert owner_notice.notify_owner("\n".join([MACHINE[0], HUMAN[0]])) is True

    assert posted == [("222", HUMAN[0])]
