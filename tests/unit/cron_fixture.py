"""배포기 시험 리그가 공유하는 cron 선언 조회와 가짜 `hermes`(RCB todo 27).

배포기는 `converge_cron`(`automation/deploy_cron.sh`)으로 등록을 맞추므로, 배포기를 실제로 돌리는
리그는 그 헬퍼와 노드 실측 형식으로 목록을 내는 `hermes` 가 있어야 한다. 기대값은 리그마다 적지 않고
`deploy-manifest.txt` 의 `v2:cron` 선언에서 끌어온다.
"""
from __future__ import annotations

import shlex
from pathlib import Path
from typing import Final

from automation.deploy_declarations import Declaration, all_declarations

REPO: Final = Path(__file__).resolve().parents[2]
HELPER: Final = "automation/deploy_cron.sh"
JOB_ID: Final = "0123456789ab"
HELPER_SOURCE: Final = 'source "$repo_root/automation/deploy_cron.sh"'


def declared_cron(owner: str) -> Declaration:
    [row] = [row for row in all_declarations(REPO) if row.owner == owner and row.kind == "cron"]
    return row


def converge_call(row: Declaration) -> str:
    return (f'converge_cron {row.destination} "{row.attr("schedule")}" '
            f'{row.attr("script")} {row.attr("deliver")}')


def create_args(row: Declaration) -> list[str]:
    return ["cron", "create", row.attr("schedule"), "--name", row.destination, "--no-agent",
            "--script", row.attr("script"), "--deliver", row.attr("deliver")]


def listing(row: Declaration) -> str:
    return ("\n┌─────────────────────────────────────────────────────────────────────────┐\n"
            "│                         Scheduled Jobs                                  │\n"
            "└─────────────────────────────────────────────────────────────────────────┘\n\n"
            f"  {JOB_ID} [active]\n    Name:      {row.destination}\n"
            f"    Schedule:  {row.attr('schedule')}\n    Repeat:    ∞\n"
            f"    Next run:  2026-10-02T09:00:00+09:00\n    Deliver:   {row.attr('deliver')}\n"
            f"    Script:    {row.attr('script')}\n"
            "    Mode:      no-agent (script stdout delivered directly)\n\n")


def listing_only_hermes(row: Declaration, prelude: str = "") -> str:
    """Lists exactly the declared job; any other call exits 9, so a create or edit fails the deploy."""
    return ("#!/usr/bin/env bash\n" + prelude + '[[ "$*" == "cron list --all" ]] || exit 9\n'
            f"printf %s {shlex.quote(listing(row))}\n")


_FAKE_SYSTEMCTL: Final = r'''#!/usr/bin/env python3
import json, os, sys
path = os.path.join(os.environ["HOME"], "fake-systemctl.json")
units = json.load(open(path)) if os.path.exists(path) else {}
args = [a for a in sys.argv[1:] if a != "--user"]
if args[0] in ("enable", "disable"):
    units[args[-1]] = "on" if args[0] == "enable" else "off"
elif args[0] == "is-enabled":
    print("enabled" if units.get(args[1]) == "on" else "disabled")
elif args[0] == "is-active":
    print("active" if units.get(args[1]) == "on" else "inactive")
json.dump(units, open(path, "w"))
'''


def timer_env(root: Path) -> dict[str, str]:
    """`converge_timer` 를 부르는 배포기를 리그에서 돌릴 때 — 노드 릴리스 트리 대신 이 체크아웃의 수렴기,
    가짜 systemctl(상태는 노드 HOME 의 json), 잡이 없는 Hermes 목록. 일시정지 순서는 `test_cron_timer.py` 가 본다."""
    root.mkdir(parents=True, exist_ok=True)
    systemctl, hermes = root / "fake-systemctl", root / "fake-timer-hermes"
    _ = systemctl.write_text(_FAKE_SYSTEMCTL, encoding="utf-8")
    _ = hermes.write_text("#!/bin/bash\n[[ \"$*\" == \"cron list --all\" ]] || exit 9\nprintf 'No scheduled jobs.\\n'\n",
                          encoding="utf-8")
    for path in (systemctl, hermes):
        path.chmod(0o755)
    return {"DEPLOY_TIMER_RUNTIME": str(REPO), "CRON_TIMER_SYSTEMCTL": str(systemctl), "CRON_TIMER_HERMES": str(hermes)}
