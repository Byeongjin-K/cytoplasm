r"""`hermes cron list --all` 해석기 두 벌이 같은 입력에 같은 답을 내는지 묶는다(RCB F2 B1).

목록은 두 곳에서 읽힌다 — 릴리스 판정의 관측기(`automation/deploy_all_cron_listing.parse_cron_listing`,
Python)와 배포기의 cron 수렴(`automation/deploy_cron.sh::_converge_cron_jobs`, awk). 둘이 서로 다른
문법을 받으면 형식이 조금만 바뀌어도 수렴기는 잡을 만들고 매 릴리스 `--verify` 는 그 잡을 모름으로
붙잡는데 각자의 시험은 자기 픽스처로 초록이다. 그래서 같은 입력 묶음을 둘 다에 먹이고, 판독
가능 여부와 뽑은 필드가 같은지 — 그리고 그 답이 아래 계약과 같은지를 함께 단언한다.

벤더 형식(노드 `~/.hermes/hermes-agent/hermes_cli/cron.py::cron_list`, 2026-10-01 읽기 전용 확인):
  - 잡이 하나도 없으면 상자 없이 `No scheduled jobs.` 와 안내 한 줄만 찍고 끝난다.
  - 있으면 빈 줄 · `Scheduled Jobs` 제목 상자 3줄 · 빈 줄, 그리고 잡마다 `  <id> [<state>]` 머리 줄,
    4칸 들여쓴 `Name`·`Schedule`·`Repeat`·`Next run`·`Deliver` (항상), `Skills`(있을 때)·`Script`(있을
    때)·`Monitor`/`Changed`·`Mode`(no-agent 일 때만)·`Workdir`, `Last run`(**한 번이라도 돈 뒤에만** —
    `last_status` 가 있어야 찍힌다)·`Execution`·`⚠ Delivery failed`/`⚠ Missed scheduled fire`, 그리고
    잡마다 빈 줄 하나로 끝난다. `Last run` 의 오류 문구는 여러 줄일 수 있다(실측: 0열 `stdout:` 줄).
  - 상태는 `paused`·`completed`·`active`·`disabled` 넷뿐이다.
  - 게이트웨이가 꺼져 있으면 목록 뒤에 `  ⚠  Gateway is not running …` 2칸 줄과 5칸 이상 안내 줄이 붙는다.

계약(두 해석기 공통):
  - 판독 가능: (a) 머리 줄이 하나도 없고 `No scheduled jobs.` 줄이 있는 목록 = 잡 0개, (b) 첫 머리 줄
    앞에 `Scheduled Jobs` 제목이 있고, 잡 블록마다 빈 줄로 끝나며, 출력이 개행으로 끝나는 목록.
  - 판독 불가(모름 — 없음으로 읽지 않는다): 빈 출력, 제목 없는 잡 목록, 잡 없는 제목, 개행 없이 끝나거나
    블록 중간에서 끝난 목록, 넷 밖의 상태, 다른 2칸 들여쓴 줄(게이트웨이 경고 줄 제외), 잡 앞의 필드 줄,
    한 블록 안에서 두 번 나온 키, `Name`·`Schedule`·`Deliver` 가 비었거나 `Mode` 가 있는데 `Script` 가 없는 잡.
  - 뽑는 필드: id · state · Name · Schedule · Deliver · Script · Mode · Skills(없으면 빈 값, 값의 공백은 하나로
    접는다). `Last run` 이 없는 잡(한 번도 돌지 않은 잡)과 `Mode` 가 없는 잡(에이전트 잡)도 판독 가능하다 —
    벤더가 그 줄을 찍지 않는 것이 정상 형식이기 때문이다. 에이전트 잡을 고치거나 깨끗하다고 읽는 일은
    각 소비자(수렴기 rc 7, 관측기 `different-mode`)가 막는다.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Final

import pytest

from automation.deploy_all_cron_listing import parse_cron_listing
from tests.unit.cron_fixture import declared_cron, listing

_REPO: Final = Path(__file__).resolve().parents[2]
_HELPER: Final = _REPO / "automation" / "deploy_cron.sh"
_US: Final = "\037"

Job = tuple[str, str, str, str, str, str, str, str]  # id, Name, Schedule, Deliver, Script, Mode, Skills, state

_BOX: Final = (
    "\n┌─────────────────────────────────────────────────────────────────────────┐\n"
    "│                         Scheduled Jobs                                  │\n"
    "└─────────────────────────────────────────────────────────────────────────┘\n\n"
)
_MODE: Final = "no-agent (script stdout delivered directly)"


def _block(job_id: str, name: str, *, state: str = "active", last_run: bool = True, extra: str = "") -> str:
    ran = "    Last run:  2026-10-01T09:00:40+09:00  ok\n    Execution: completed  0f0f0f0f\n" if last_run else ""
    return (f"  {job_id} [{state}]\n    Name:      {name}\n    Schedule:  */5 * * * *\n    Repeat:    ∞\n"
            f"    Next run:  2026-10-02T09:00:00+09:00\n    Deliver:   local\n    Script:    {name}.py\n"
            f"    Mode:      {_MODE}\n{extra}{ran}\n")


# 노드 실측(task-27 캡처 agent·peer 계정)을 id·오류 문구만 가려 옮겼다 — 여러 줄 `Last run` 오류,
# `Execution` 없는 잡, `Skills` 만 있는 에이전트 잡이 들어 있다.
_REAL_AGENT: Final = _BOX + (
    "  aaaaaaaaaaa1 [active]\n    Name:      daily-cost-report\n    Schedule:  0 9 * * *\n    Repeat:    ∞\n"
    "    Next run:  2026-10-02T09:00:00+09:00\n    Deliver:   local\n    Script:    send_cost_report.py\n"
    f"    Mode:      {_MODE}\n"
    "    Last run:  2026-10-01T09:00:40.708998+09:00  error: Script exited with code 1\nstdout:\n"
    "cost-report error: <masked>  (28 failures in a row)\n\n"
    "  aaaaaaaaaaa2 [active]\n    Name:      notes-weekly-organize\n    Schedule:  0 9 * * 1-5\n    Repeat:    ∞\n"
    "    Next run:  2026-10-02T09:00:00+09:00\n    Deliver:   discord\n    Script:    notes_organize.py\n"
    f"    Mode:      {_MODE}\n    Last run:  2026-10-01T09:00:40.735212+09:00  ok\n\n"
    "  aaaaaaaaaaa3 [active]\n    Name:      mail-triage-repair-daily\n    Schedule:  10 9 * * *\n"
    "    Repeat:    ∞\n    Next run:  2026-10-02T09:10:00+09:00\n    Deliver:   local\n    Skills:    mail, repair\n"
    "    Last run:  2026-10-01T09:10:41.370029+09:00  error: RuntimeError: <masked>\n\n"
)
_REAL_PEER: Final = _BOX + (
    "  bbbbbbbbbbb1 [active]\n    Name:      doctor-watch\n    Schedule:  17 * * * *\n    Repeat:    ∞\n"
    "    Next run:  2026-10-01T14:17:00+09:00\n    Deliver:   local\n    Script:    doctor_watch.py\n"
    f"    Mode:      {_MODE}\n    Last run:  2026-10-01T13:17:32.152286+09:00  ok\n"
    "    Execution: completed  e9bdc98a\n\n"
)
_GATEWAY_WARNING: Final = (
    "  ⚠  Gateway is not running — jobs won't fire automatically.\n"
    "     Start it with: hermes gateway install\n"
    "                    sudo hermes gateway install --system  # Linux servers\n"
    "     Check status:  hermes cron status\n"
)


def _job(job_id: str, name: str, state: str = "active") -> Job:
    return (job_id, name, "*/5 * * * *", "local", f"{name}.py", _MODE, "", state)


_FIXTURE_ROW: Final = declared_cron("automation/effect_request")

#: 사례 이름 → (목록, 계약상 기대: None = 판독 불가, 아니면 뽑힌 잡들).
_CASES: Final[dict[str, tuple[str, list[Job] | None]]] = {
    "real-agent": (_REAL_AGENT, [
        ("aaaaaaaaaaa1", "daily-cost-report", "0 9 * * *", "local", "send_cost_report.py", _MODE, "", "active"),
        ("aaaaaaaaaaa2", "notes-weekly-organize", "0 9 * * 1-5", "discord", "notes_organize.py", _MODE, "", "active"),
        ("aaaaaaaaaaa3", "mail-triage-repair-daily", "10 9 * * *", "local", "", "", "mail, repair", "active"),
    ]),
    "real-peer": (_REAL_PEER, [
        ("bbbbbbbbbbb1", "doctor-watch", "17 * * * *", "local", "doctor_watch.py", _MODE, "", "active"),
    ]),
    "never-run": (_BOX + _block("c00000000001", "fresh-watch", last_run=False), [_job("c00000000001", "fresh-watch")]),
    "every-vendor-state": (
        _BOX + "".join(_block(f"d0000000000{i}", f"s{i}", state=s) for i, s in enumerate(("active", "paused", "completed", "disabled"))),
        [_job(f"d0000000000{i}", f"s{i}", s) for i, s in enumerate(("active", "paused", "completed", "disabled"))],
    ),
    "prompt-only-agent-job": (
        _BOX + "  e00000000001 [active]\n    Name:      ask\n    Schedule:  0 9 * * *\n    Repeat:    ∞\n"
        "    Next run:  2026-10-02T09:00:00+09:00\n    Deliver:   discord\n\n",
        [("e00000000001", "ask", "0 9 * * *", "discord", "", "", "", "active")],
    ),
    "header-trailing-space": (_BOX + _block("f00000000001", "w").replace("[active]\n", "[active]  \n"), [_job("f00000000001", "w")]),
    "gateway-warning-trailer": (_BOX + _block("f00000000002", "w") + _GATEWAY_WARNING, [_job("f00000000002", "w")]),
    "no-jobs": ("No scheduled jobs.\nCreate one with 'hermes cron create ...' or the /cron command in chat.\n", []),
    "fixture-listing": (listing(_FIXTURE_ROW), [(
        "0123456789ab", _FIXTURE_ROW.destination, _FIXTURE_ROW.attr("schedule"), _FIXTURE_ROW.attr("deliver"),
        _FIXTURE_ROW.attr("script"), _MODE, "", "active",
    )]),
    "unknown-state": (_BOX + _block("900000000001", "w", state="frozen"), None),
    "missing-title-box": (_block("900000000002", "w"), None),
    "title-without-jobs": (_BOX, None),
    "truncated-mid-block": (_BOX + _block("900000000003", "w").split("    Deliver:")[0], None),
    "truncated-mid-line": (_BOX + _block("900000000004", "w")[:-25], None),
    "truncated-before-final-blank": (_BOX + _block("900000000005", "w")[:-1], None),
    "empty": ("", None),
    "duplicate-key": (_BOX + _block("900000000006", "w", extra="    Name:      other\n"), None),
    "traceback-in-last-run": (
        _BOX + _block("900000000007", "w", extra='    Last run:  2026-10-01T09:00:00+09:00  error: Traceback:\n'
                      '  File "/x.py", line 3, in <module>\n'),
        None,
    ),
    "mode-without-script": (_BOX + _block("900000000008", "w").replace("    Script:    w.py\n", ""), None),
    "non-hex-id-header": (_BOX + _block("zz0000000001", "w"), None),
    "field-before-any-job": (_BOX + "    Name:      w\n\n" + _block("900000000009", "w"), None),
}


def _python(text: str) -> list[Job] | None:
    parsed = parse_cron_listing(text)
    if parsed is None:
        return None
    return sorted(
        (j["id"], j["Name"], j["Schedule"], j["Deliver"], j.get("Script", ""), j.get("Mode", ""),
         j.get("Skills", ""), j["state"])
        for jobs in parsed.values() for j in jobs
    )


def _awk(text: str, tmp_path: Path) -> list[Job] | None:
    """수렴기가 쓰는 바로 그 함수를 가짜 `hermes`(목록을 그대로 내는) 위에서 돌린다."""
    (tmp_path / "listing.txt").write_text(text, encoding="utf-8")
    hermes = tmp_path / "hermes"
    _ = hermes.write_text('#!/bin/bash\n[[ "$*" == "cron list --all" ]] || exit 9\ncat "$LISTING"\n', encoding="utf-8")
    hermes.chmod(0o755)
    proc = subprocess.run(
        ("bash", "-c", f'source "{_HELPER}"; _converge_cron_jobs 30'),
        env={"PATH": f"{tmp_path}:/usr/bin:/bin", "LANG": "C.UTF-8", "LISTING": str(tmp_path / "listing.txt")},
        capture_output=True, text=True, check=False, timeout=60,
    )
    if proc.returncode != 0:
        return None
    rows: list[Job] = []
    for line in proc.stdout.splitlines():
        fields = line.split(_US)
        assert len(fields) == 8, line
        rows.append((fields[0], fields[1], fields[2], fields[3], fields[4], fields[5], fields[6], fields[7]))
    return sorted(rows)


@pytest.mark.parametrize("case", list(_CASES))
def test_both_cron_listing_parsers_read_the_same_listing_the_same_way(case: str, tmp_path: Path) -> None:
    text, expected = _CASES[case]

    observed = {"python": _python(text), "awk": _awk(text, tmp_path)}

    assert observed == {"python": expected and sorted(expected), "awk": expected and sorted(expected)}
