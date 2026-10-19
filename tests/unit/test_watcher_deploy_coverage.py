"""배포 스크립트를 가진 스킬은 자기 cron 워처를 **전부** 실어야 한다 — 회귀 고정.

2026-08-18 실측 배경: `skills/mail/scripts/mail_triage_watch.py` 는 SS-1 스킬 루트
반전에 맞춰 governed live store(`/srv/autophagy-skills/live/mail/scripts`)를 읽도록
이미 고쳐져 있었다. 그런데 `skills/mail/deploy.sh` 는 `mail_digest_watch.py` 하나만
올리고 있었다 — 즉 그 수정은 **어떤 배포 경로에도 실리지 않아** 노드에는 반전 *이전*
경로(`~/.hermes/skills/mail/scripts`)를 보는 옛 사본이 그대로 남았다.

반전 후 그 경로는 에이전트 **자기 소유** 스킬 루트라 배포본이 거기 없다. 그래서
워처는 매 틱 `mail skill is not mounted` 로 exit 1 했고, 실측 시점에 **108회 연속**
실패였다. 이 워처가 승인 ✅ 를 해석해 발송하는 유일한 주체이므로, 그동안 소유자가
✅ 를 눌러도 아무 일도 일어나지 않았다 — 게이트는 정상이었고 실행기가 죽어 있었다.

`--deliver local` 이라 그 실패는 소유자에게 닿지도 않았다.

「커밋됨 ≠ 배포됨」은 스킬 본체만의 규칙이 아니다. **어떤 deploy 스크립트도 싣지
않는 워처 수정은 배포되지 않은 것**이고, 그 사실은 산문이 아니라 이 검사로 고정한다.

판정 기준은 `__main__` 가드다 — 그것이 있으면 cron 엔트리포인트이고, 없으면
워처가 live store 에서 import 하는 헬퍼 모듈이다(예: calendar 의
`calendar_watch_commands.py`·`calendar_watch_diagnostics.py`).
"""

from __future__ import annotations

import re
import stat
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_AUTOMATION = _REPO_ROOT / "automation"
_SKILLS = _REPO_ROOT / "skills"

_MAIN_GUARD = re.compile(r"^if __name__ == ['\"]__main__['\"]:", re.M)
_PUSH_TO_SCRIPTS = re.compile(
    r"^[ \t]*push_file\s+[\"']?\$repo_root/(?P<source>[^\"'\s]+)[\"']?\s*(?:\\\s*)?"
    r"[\"']\.hermes/scripts/",
    re.M,
)
_KNOWN_UNDEPLOYED: dict[str, str] = {
    # wiki was here until 2026-08-20. It never belonged: `wiki-confirm-watch` is a
    # cron that actually exists on the node, so "no deploy path" meant every committed
    # wiki fix stopped at the repo. patent-prep genuinely has no cron entry yet.
    "patent-prep": "the export confirmation watcher has no node cron entry yet",
}


def _is_cron_entrypoint(path: Path) -> bool:
    """cron 이 직접 실행하는 워처인가 — 헬퍼 모듈에는 `__main__` 가드가 없다."""
    return bool(_MAIN_GUARD.search(path.read_text(encoding="utf-8")))


def _watcher_entrypoints(scripts_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in scripts_dir.glob("*watch*.py")
        if path.is_file() and _is_cron_entrypoint(path)
    )


def _deploy_scripts() -> list[Path]:
    return sorted(
        (
            *_REPO_ROOT.glob("skills/*/deploy.sh"),
            *_REPO_ROOT.glob("automation/**/deploy.sh"),
        ),
    )


def _pushed_script_sources(deploy_text: str) -> set[str]:
    return {match.group("source") for match in _PUSH_TO_SCRIPTS.finditer(deploy_text)}


def _deployed_script_sources() -> list[Path]:
    sources: set[Path] = set()
    for deploy in _deploy_scripts():
        text = deploy.read_text(encoding="utf-8")
        sources.update(_REPO_ROOT / source for source in _pushed_script_sources(text))
    return sorted(sources)


def _guarded_skill_watchers() -> list[Path]:
    return sorted(
        watcher
        for scripts_dir in _SKILLS.glob("*/scripts")
        for watcher in _watcher_entrypoints(scripts_dir)
    )


def test_provenance_or_comment_mentions_do_not_count_as_watcher_delivery() -> None:
    deploy_text = """\
# fake_watch.py still needs deployment
deploy_provenance_check "$repo_root" \\
  "$repo_root/skills/fake/scripts/fake_watch.py" || exit 4
"""

    assert "skills/fake/scripts/fake_watch.py" not in _pushed_script_sources(deploy_text)


def test_every_skill_watcher_is_carried_by_its_deploy_script() -> None:
    skills = sorted(path for path in _SKILLS.iterdir() if path.is_dir())
    assert skills, "no skills found — glob or layout changed"

    uncarried: list[str] = []
    unused_exemptions = set(_KNOWN_UNDEPLOYED)
    checked = 0
    for skill in skills:
        for watcher in _watcher_entrypoints(skill / "scripts"):
            checked += 1
            deploy = skill / "deploy.sh"
            if not deploy.is_file():
                reason = _KNOWN_UNDEPLOYED.get(skill.name)
                if reason is None:
                    uncarried.append(f"{skill.name}/deploy.sh is missing for {watcher.name}")
                else:
                    unused_exemptions.discard(skill.name)
                continue
            deploy_text = deploy.read_text(encoding="utf-8")
            watcher_source = str(watcher.relative_to(_REPO_ROOT))
            if watcher_source not in _pushed_script_sources(deploy_text):
                uncarried.append(f"{skill.name}/deploy.sh is missing {watcher.name}")

    assert checked, "no watcher entrypoints discovered — the __main__ probe is wrong"
    assert not uncarried, (
        "these watchers exist in the repo but no deploy script carries them, so a "
        "fix to them never reaches the node (mail_triage_watch.py sat 108 ticks "
        f"dead this way): {uncarried}"
    )
    assert not unused_exemptions, f"stale _KNOWN_UNDEPLOYED entries: {sorted(unused_exemptions)}"

    # budget-watch is the skill-side adopter in this delivery cycle. Keep this in the
    # existing replay-gated node so historical FS3 RED evidence remains reproducible.
    budget_deploy = (_SKILLS / "budget" / "deploy.sh").read_text(encoding="utf-8")
    budget_lines = [
        line
        for line in budget_deploy.splitlines()
        if "budget-watch" in line and "hermes cron" in line
    ]
    assert budget_lines and all("--deliver discord" in line for line in budget_lines)
    assert all("--deliver local" not in line for line in budget_lines)
    assert 'hermes cron edit "$job_id"' in budget_deploy


def test_mail_approval_loop_watcher_is_deployed() -> None:
    """메일 승인/발송 루프는 특히 못박는다 — 죽으면 소유자 ✅ 가 무효가 된다."""
    deploy_text = (_SKILLS / "mail" / "deploy.sh").read_text(encoding="utf-8")

    assert "skills/mail/scripts/mail_triage_watch.py" in _pushed_script_sources(deploy_text), (
        "skills/mail/deploy.sh must push mail_triage_watch.py — it is the only "
        "thing that resolves an owner ✅ into an actual send"
    )
    # provenance 가드에도 올라야 한다 — 그러지 않으면 origin/main 대조를 건너뛴다.
    guard_block = deploy_text.split("deploy_provenance_check", 1)
    assert len(guard_block) == 2, "deploy_provenance_check call not found"
    assert "mail_triage_watch.py" in guard_block[1].split("|| exit", 1)[0], (
        "mail_triage_watch.py must be inside deploy_provenance_check so it is "
        "compared against origin/main like every other deployed file"
    )


@pytest.mark.parametrize(
    ("watch_name", "deploy", "weekday_schedule"),
    (
        (
            "research-trends",
            _AUTOMATION / "research_trends" / "deploy.sh",
            "0 9 * * 1-5",
        ),
        (
            "notes-weekly-organize",
            _AUTOMATION / "notes_organize" / "deploy.sh",
            "0 8 * * 1-5",
        ),
    ),
)
def test_weekly_helper_adopters_ship_the_helper_and_reconcile_weekday_schedule(
    watch_name: str, deploy: Path, weekday_schedule: str
) -> None:
    """Weekly wrappers must ship their shared helper and converge catch-up schedules."""
    deploy_text = deploy.read_text(encoding="utf-8")
    helper = "skills/mail/scripts/watch_failure_streak.py"
    guard_block = deploy_text.split("deploy_provenance_check", 1)

    assert helper in _pushed_script_sources(deploy_text), (
        f"{watch_name} deploy must push the shared failure-streak helper"
    )
    assert len(guard_block) == 2, "deploy_provenance_check call not found"
    assert helper in guard_block[1].split("|| exit", 1)[0], (
        f"{watch_name} helper must be covered by deploy_provenance_check"
    )
    if watch_name == "research-trends":
        # This adopter is retired: runtime delivery remains, schedule creation must not.
        from automation.deploy_declarations import all_declarations

        rows = all_declarations(_REPO_ROOT)
        assert not any(row.kind == "cron" and row.destination == watch_name for row in rows)
        assert any(row.owner == "automation/retired_jobs" and row.policy == "required"
                   and row.attr("algorithm") == "retired-job-v1"
                   and row.destination == f".config/systemd/user/autophagy-cron-{watch_name}.timer"
                   for row in rows)
        assert not re.search(r"hermes cron (create|edit)|^converge_(cron|timer)\s",
                             deploy_text, re.M)
        return
    assert weekday_schedule in deploy_text, (
        f"{watch_name} deploy must set its weekday catch-up schedule"
    )
    assert "hermes cron edit" in deploy_text, (
        f"{watch_name} deploy must reconcile an existing cron schedule"
    )
    registrations = [
        line for line in deploy_text.splitlines() if watch_name in line and "hermes cron" in line
    ]
    assert registrations, f"{watch_name} cron registration is missing"
    assert all("--deliver discord" in line for line in registrations)
    assert all("--deliver local" not in line for line in registrations)
    assert 'hermes cron edit "$job_id"' in deploy_text


def test_watchers_do_not_probe_the_self_skill_root() -> None:
    """워처는 governed live store 를 봐야 한다 — `~/.hermes/skills` 는 자가 스킬 루트다.

    반전(SS-1) 이후 `~/.hermes/skills` 는 그 계정이 **직접 만든** 스킬이 사는 곳이고,
    관리자 배포본은 `/srv/autophagy-skills/live` 에 있다. 배포된 워처가 전자를 보면
    영구히 "not mounted" 다. 루트 AGENTS.md 가 산문으로 금지한 바로 그 혼동이다.
    """
    offenders: list[str] = []
    watched = {*_guarded_skill_watchers(), *_deployed_script_sources()}
    for watcher in sorted(watched):
        text = watcher.read_text(encoding="utf-8")
        # 주석/독스트링의 설명은 제외하고 실제 경로 구성만 본다.
        code = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )
        if re.search(r'"\.hermes"\s*,?\s*/?\s*"skills"', code) or (
            ".hermes/skills/" in code
        ):
            offenders.append(str(watcher.relative_to(_REPO_ROOT)))

    assert not offenders, (
        "these deployed watchers resolve the governed skill through the agent's own "
        f"self-skill root, which cannot contain deployed skills: {offenders}"
    )


def test_every_deploy_script_is_executable() -> None:
    non_executable = [
        str(path.relative_to(_REPO_ROOT))
        for path in _deploy_scripts()
        if not path.stat().st_mode & stat.S_IXUSR
    ]

    assert not non_executable, f"owner-execute bit is missing from deploy scripts: {non_executable}"



def test_mail_crons_deliver_where_the_owner_can_see_them() -> None:
    """`--deliver local` 은 전달 대상이 0이다 — 메일 두 cron 은 discord 로 간다.

    2026-07-31 사건은 다이제스트 실패가 `--deliver local` 아래에서 증발해 사라졌고,
    2026-08-18 에는 `mail-triage-watch` 가 111회 연속 실패하고도 아무에게도 닿지
    않았다. 고빈도 워처의 DM 홍수는 연속 실패 임계치(watch_failure_streak)가 막으므로,
    이젠 둘 다 소유자에게 닿는 곳으로 보낸다.
    """
    deploy_text = (_SKILLS / "mail" / "deploy.sh").read_text(encoding="utf-8")

    for job in ("mail-daily-digest", "mail-triage-watch"):
        registrations = [line for line in deploy_text.splitlines() if job in line and "hermes cron" in line]
        assert registrations, f"{job} 의 cron 등록이 deploy.sh 에 없다"
        for line in registrations:
            assert "--deliver discord" in line, f"{job} 가 소유자에게 닿지 않는 곳으로 간다: {line}"
            assert "--deliver local" not in line, f"{job} 가 여전히 --deliver local 이다: {line}"
