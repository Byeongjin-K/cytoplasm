"""cron 등록은 선언과 같아야 한다(RCB todo 27, GAP-9).

배포기의 등록 줄은 대부분 "이름이 있으면 exit 0, 없으면 create" 였다 — 저장소에서 주기를
고쳐도 노드에 닿지 않았고, 몇몇은 `--all` 없이 목록을 읽어 일시정지된 잡을 못 보고 하나 더
만들 수 있었다. 이제 잡마다 `deploy-manifest.txt` 에 `v2:cron` 행이 있고, 배포기는
`converge_cron <이름> <주기> <스크립트> <전달>`(`automation/deploy_cron.sh`)로 그 값에 맞춘다.

예외는 FS3 고정 테스트(`tests/unit/test_watcher_deploy_coverage.py`)가 문구를 못박은 다섯 줄뿐이다.
그 줄은 날것 형태로 남지만 담은 값(이름·주기·스크립트·전달)이 선언과 같아야 하고, 목록은
`hermes cron list --all` 로만 읽는다.

검사는 행 단위 어휘 검사다: 배포기 줄에서 `converge_cron` 호출과 날것 `hermes cron create|edit`
줄을 뽑고, `for account in …; do` 루프 안의 호출은 그 루프가 도는 계정마다 등록으로 센다.
새 파일인 이유: 등록 문구를 보던 `test_watcher_deploy_coverage.py` 는 FS3 고정 파일이라 케이스를 더할 수 없다.
"""
from __future__ import annotations

import re
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest

from automation.deploy_declarations import all_declarations

_REPO: Final = Path(__file__).resolve().parents[2]
HELPER_SOURCE: Final = 'source "$repo_root/automation/deploy_cron.sh"'
TIMER_SOURCE: Final = 'source "$repo_root/automation/deploy_timer.sh"'
PINNED: Final = frozenset({
    ("automation/notes_organize", "notes-weekly-organize"),
    ("automation/research_trends", "research-trends"),
    ("skills/budget", "budget-watch"),
    ("skills/mail", "mail-daily-digest"),
    ("skills/mail", "mail-triage-watch"),
})
_CONVERGE: Final = re.compile(r"^\s*converge_(cron|timer)\s+(.+)$")
_SWITCH: Final = re.compile(r'^\s*run_agent\(\)\s*\{\s*run_account\s+"\$NODE_(AGENT|PEER)_ACCOUNT"')
_LOOP: Final = re.compile(r"^\s*for\s+account\s+in\s+(.+?);\s*do\s*$")
_DONE: Final = re.compile(r"^\s*done\b")
_LOOP_ACCOUNTS: Final = {"$NODE_AGENT_ACCOUNT": "agent", "$NODE_PEER_ACCOUNT": "peer"}
_RAW: Final = re.compile(r"(?<![\w-])hermes\s+cron\s+(?:create|edit)(?![\w-])")
_RAW_CREATE: Final = re.compile(
    r'hermes cron create "([^"]+)" --name ([\w.-]+) --no-agent --script ([\w.-]+) --deliver (\w+)'
)
_RAW_EDIT: Final = re.compile(
    r'hermes cron edit "\$job_id" --schedule "([^"]+)" --deliver (\w+) --no-agent --script ([\w.-]+)'
)
_RAW_LIST: Final = re.compile(r"hermes cron list(?P<rest>[^|;)]*)")
_OPERATORS: Final = frozenset({"||", "&&", ";", "|", "&"})
_NAME: Final = re.compile(r"[a-z0-9][a-z0-9-]*")
_SCRIPT: Final = re.compile(r"[A-Za-z0-9_]+\.py")
_DELIVERIES: Final = frozenset({"local", "discord"})
_EVERY: Final = re.compile(r"every [1-9][0-9]*[smh]")
_CRON_FIELDS: Final = re.compile(r"[0-9*/,-]+( [0-9*/,-]+){4}")


@dataclass(frozen=True, slots=True)
class Job:
    package: str
    account: str
    name: str
    schedule: str
    script: str
    deliver: str
    runner: str = "hermes"
    env: str = ""

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.package, self.account, self.name)


@dataclass(frozen=True, slots=True)
class Registration:
    job: Job
    via: str


@dataclass(frozen=True, slots=True)
class Problem:
    rule: str
    package: str
    detail: str


def declared_jobs(root: Path) -> tuple[tuple[Job, ...], tuple[Problem, ...]]:
    jobs: list[Job] = []
    problems: list[Problem] = []
    for row in all_declarations(root):
        if row.kind != "cron" or row.policy in ("held", "retired"):
            continue
        if row.attr("mode") != "no-agent":
            problems.append(Problem("mode", row.owner, row.destination))
        if not (_NAME.fullmatch(row.destination) and _SCRIPT.fullmatch(row.attr("script"))
                and row.attr("deliver") in _DELIVERIES
                and (_EVERY.fullmatch(row.attr("schedule")) or _CRON_FIELDS.fullmatch(row.attr("schedule")))):
            problems.append(Problem("malformed", row.owner, row.destination[:80]))
        runner = row.attr("runner")
        jobs.append(Job(row.owner, row.account, row.destination, row.attr("schedule"),
                        row.attr("script"), row.attr("deliver"), runner,
                        row.attr("env") if runner == "systemd" else ""))
    return tuple(jobs), tuple(problems)


def _raw_registration(package: str, accounts: tuple[str, ...], line: str) -> tuple[list[Job], list[Problem]]:
    create, edit = _RAW_CREATE.search(line), _RAW_EDIT.search(line)
    if create is None or edit is None:
        return [], [Problem("raw-unparsed", package, line.strip()[:120])]
    schedule, name, script, deliver = create.groups()
    problems: list[Problem] = []
    if (package, name) not in PINNED:
        problems.append(Problem("raw-unpinned", package, name))
    if edit.groups() != (schedule, deliver, script):
        problems.append(Problem("raw-branches-disagree", package, name))
    listings = list(_RAW_LIST.finditer(line))
    if not listings or any("--all" not in found.group("rest").split() for found in listings):
        problems.append(Problem("listing-without-all", package, name))
    return [Job(package, account, name, schedule, script, deliver) for account in accounts], problems


def registrations(root: Path) -> tuple[tuple[Registration, ...], tuple[Problem, ...]]:
    found: list[Registration] = []
    problems: list[Problem] = []
    scripts = sorted(root.glob("automation/*/deploy*.sh")) + sorted(root.glob("skills/*/deploy*.sh"))
    for script in scripts:
        package = script.parent.relative_to(root).as_posix()
        text = script.read_text(encoding="utf-8")
        loop: tuple[str, ...] = ()
        default: tuple[str, ...] = ("agent",)
        uses_helper = uses_timer = False
        for line in text.splitlines():
            if line.lstrip().startswith("#"):
                continue
            if (match := _LOOP.match(line)) is not None:
                words = (word.strip('"') for word in shlex.split(match.group(1), posix=False))
                loop = tuple(_LOOP_ACCOUNTS[word] for word in words if word in _LOOP_ACCOUNTS)
                continue
            if _DONE.match(line):
                loop = ()
                continue
            if not loop and (match := _SWITCH.match(line)) is not None:
                default = (match.group(1).lower(),)
            accounts = loop or default
            if (match := _CONVERGE.match(line)) is not None:
                timer = match.group(1) == "timer"
                uses_helper = uses_helper or not timer
                args: list[str] = []
                for token in shlex.split(match.group(2)):
                    if token in _OPERATORS:
                        break
                    args.append(token)
                if len(args) not in ((4, 5) if timer else (4,)):
                    problems.append(Problem("converge-arity", package, line.strip()))
                    continue
                name, schedule, cron_script, deliver, *env = args
                runner, via = ("systemd", "timer") if timer else ("hermes", "converge")
                uses_timer = uses_timer or timer
                found.extend(Registration(Job(package, account, name, schedule, cron_script, deliver,
                                              runner, "".join(env)), via) for account in accounts)
            elif _RAW.search(line):
                jobs, raw_problems = _raw_registration(package, accounts, line)
                found.extend(Registration(job, "raw") for job in jobs)
                problems.extend(raw_problems)
        if uses_helper and HELPER_SOURCE not in text:
            problems.append(Problem("helper-not-sourced", package, script.name))
        if uses_timer and TIMER_SOURCE not in text:
            problems.append(Problem("timer-helper-not-sourced", package, script.name))
    return tuple(found), tuple(problems)


def check(root: Path) -> tuple[Problem, ...]:
    declared, problems = declared_jobs(root)
    registered, registration_problems = registrations(root)
    found = [*problems, *registration_problems]
    by_key = {job.key: job for job in declared}
    registered_keys: dict[tuple[str, str, str], int] = {}
    raw_kept: set[tuple[str, str, str]] = set()
    for registration in registered:
        job = registration.job
        want = by_key.get(job.key)
        if registration.via == "raw" and want is not None and want.runner == "systemd":
            # P2: FS3 고정 날것 줄은 Hermes 잡 정의를 계속 맞춘다(되돌리기용) — 값만 대조하고 타이머가 실행 주체다.
            raw_kept.add(job.key)
            if (job.schedule, job.script, job.deliver) != (want.schedule, want.script, want.deliver):
                found.append(Problem("mismatch", job.package, f"{job.account}:{job.name}"))
            continue
        registered_keys[job.key] = registered_keys.get(job.key, 0) + 1
        if want is None:
            found.append(Problem("undeclared", job.package, f"{job.account}:{job.name}"))
        elif want != job:
            found.append(Problem("mismatch", job.package, f"{job.account}:{job.name}"))
    for key in raw_kept - set(registered_keys):
        found.append(Problem("raw-without-timer", key[0], f"{key[1]}:{key[2]}"))
    for key, count in registered_keys.items():
        if count > 1:
            found.append(Problem("registered-twice", key[0], f"{key[1]}:{key[2]}"))
    for job in declared:
        if job.key not in registered_keys:
            found.append(Problem("unregistered", job.package, f"{job.account}:{job.name}"))
    return tuple(found)


def _rules(problems: tuple[Problem, ...]) -> set[tuple[str, str]]:
    return {(problem.rule, problem.package) for problem in problems}


def _package(tmp_path: Path, manifest: str, deploy: str) -> Path:
    package = tmp_path / "automation" / "pkg"
    package.mkdir(parents=True)
    (package / "deploy-manifest.txt").write_text(
        "agent|automation/pkg/w.py|.hermes/scripts/w.py|required\n" + manifest, encoding="utf-8"
    )
    (package / "deploy.sh").write_text(f"#!/usr/bin/env bash\n{HELPER_SOURCE}\n{deploy}", encoding="utf-8")
    return tmp_path


def _copy_budget(tmp_path: Path, old: str, new: str) -> Path:
    package = tmp_path / "skills" / "budget"
    package.mkdir(parents=True)
    source = _REPO / "skills" / "budget"
    shutil.copy(source / "deploy-manifest.txt", package / "deploy-manifest.txt")
    text = (source / "deploy.sh").read_text(encoding="utf-8")
    assert old in text, f"fixture anchor missing from skills/budget/deploy.sh: {old}"
    (package / "deploy.sh").write_text(text.replace(old, new), encoding="utf-8")
    return tmp_path


def test_every_registration_matches_its_declaration() -> None:
    declared, _ = declared_jobs(_REPO)
    registered, _ = registrations(_REPO)

    assert check(_REPO) == ()
    names = {account: {job.name for job in declared if job.account == account} for account in ("agent", "peer")}
    assert len(names["agent"]) == 33
    assert names["peer"] == {"doctor-watch", "selfskill-audit-watch"}
    timers = {(r.job.account, r.job.name) for r in registered if r.via == "timer"}
    assert timers == {("agent", name) for name in names["agent"]}  # 바로 전환: agent 잡 전부가 타이머
    raw = {(registration.job.package, registration.job.name) for registration in registered
           if registration.via == "raw"}
    assert raw == PINNED


def test_the_three_formerly_hand_registered_jobs_are_registered_by_their_deployers() -> None:
    declared, _ = declared_jobs(_REPO)
    registered, _ = registrations(_REPO)
    by_key = {job.key: job for job in declared}

    for package, name in (
        ("automation/cost-report", "daily-cost-report"),
        ("automation/reminder_poller", "reminder-poller"),
        ("automation/repair", "repair-report-consumer"),
    ):
        want = by_key[(package, "agent", name)]
        assert Registration(want, "timer" if want.runner == "systemd" else "converge") in registered
        assert HELPER_SOURCE in (_REPO / package / "deploy.sh").read_text(encoding="utf-8")


def test_a_registration_without_a_declaration_is_rejected(tmp_path: Path) -> None:
    root = _package(tmp_path, "", 'converge_cron ghost-watch "*/5 * * * *" w.py local\n')

    assert ("undeclared", "automation/pkg") in _rules(check(root))


def test_a_declaration_without_a_registration_is_rejected(tmp_path: Path) -> None:
    root = _package(
        tmp_path,
        "agent|automation/pkg/w.py|w-watch|v2:cron;schedule=*/5 * * * *;script=w.py;deliver=local;mode=no-agent\n",
        "true\n",
    )

    assert ("unregistered", "automation/pkg") in _rules(check(root))


@pytest.mark.parametrize("old", ['"*/30 * * * *"', '--schedule "*/30 * * * *"'])
def test_a_pinned_raw_line_that_disagrees_with_its_declaration_is_rejected(tmp_path: Path, old: str) -> None:
    root = _copy_budget(tmp_path, old, old.replace("*/30", "*/15"))

    rules = _rules(check(root))

    assert rules & {("mismatch", "skills/budget"), ("raw-branches-disagree", "skills/budget")}


def test_a_raw_listing_without_all_is_rejected(tmp_path: Path) -> None:
    root = _copy_budget(tmp_path, "hermes cron list --all | awk", "hermes cron list | awk")

    assert ("listing-without-all", "skills/budget") in _rules(check(root))


def test_pinned_mail_hermes_lines_skip_a_retired_or_hermes_free_account() -> None:
    """The two FS3-pinned mail lines must not re-create paused Hermes jobs after P5
    (2026-10-18: every deploy re-created them and the retirement step refused)."""
    deploy = (_REPO / "skills/mail/deploy.sh").read_text(encoding="utf-8")
    lines = [line for line in deploy.splitlines()
             if "hermes cron list --all" in line and ("mail-triage-watch" in line or "mail-daily-digest" in line)]
    assert len(lines) == 2
    guard = '{ [ -e "$HOME/.hermes/peer-retired" ] || ! command -v hermes >/dev/null; } && exit 0; job_id='
    assert all(guard in line and line.index(guard) < line.index("hermes cron list") for line in lines)
