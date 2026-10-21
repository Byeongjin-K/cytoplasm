"""Conformance guard for the ``배포됨 != 실행됨`` mutating-skill boundary."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Final

_REPO: Final = Path(__file__).resolve().parents[2]

# Keep this inventory limited to skills with a mutating CLI. Add a newly adopted
# mutating skill here (and its governed module/CLI mapping) in the same change.
_GOVERNED_SKILLS: Final[tuple[str, ...]] = (
    "mail",
    "calendar",
    "todo",
    "wiki",
    "coordination",
    "meeting",
    "speechtotext",
    "doctype",
    "proposal",
    "report",
    "procurement",
    "patent-prep",
    "prompt",
)
_EXEMPT: Final[dict[str, str]] = {}

_MODULES: Final[dict[str, str]] = {
    "mail": "mail_runtime",
    "calendar": "calendar_governed",
    "todo": "todo_governed",
    "wiki": "wiki_governed",
    "coordination": "coordination_governed",
    "meeting": "meeting_governed",
    "speechtotext": "speechtotext_governed",
    "doctype": "doctype_governed",
    "proposal": "proposal_governed",
    "report": "report_governed",
    "procurement": "procurement_governed",
    "patent-prep": "patent_prep_governed",
    "prompt": "prompt_governed",
}
_CLIS: Final[dict[str, str]] = {
    "mail": "triage_cli.py",
    "calendar": "calendar_cli.py",
    "todo": "todo_cli.py",
    "wiki": "wiki_cli.py",
    "coordination": "coordinate_cli.py",
    "meeting": "meeting_cli.py",
    "speechtotext": "speechtotext_cli.py",
    "doctype": "doctype_cli.py",
    "proposal": "proposal_cli.py",
    "report": "report_cli.py",
    "procurement": "procure_cli.py",
    "patent-prep": "patent_cli.py",
    "prompt": "prompt_cli.py",
}


def _governed_module(skill: str):
    scripts = _REPO / "skills" / skill / "scripts"
    assert scripts.is_dir(), f"{skill}: scripts directory is missing"
    module_name = _MODULES.get(skill)
    assert module_name is not None, f"{skill}: governed module is not inventoried"
    assert (scripts / f"{module_name}.py").is_file(), f"{skill}: governed module is missing"
    sys.path.insert(0, str(scripts))
    try:
        return importlib.import_module(module_name)
    finally:
        sys.path.pop(0)


def test_every_mutating_skill_has_a_standalone_governed_module() -> None:
    importlib.invalidate_caches()
    for skill in _GOVERNED_SKILLS:
        module = _governed_module(skill)
        assert module.GOVERNED_LIVE_ROOT == _live_root()
        assert module.LIVE_ROOT_ENV == _live_root_env()
        assert module.SKILL_NAME == skill
        assert module.STALE_COPY_MARKER == _stale_marker()
        assert callable(module.refusal if skill != "mail" else module.governed_copy_refusal)


def test_mutating_cli_calls_the_governed_refusal() -> None:
    for skill in _GOVERNED_SKILLS:
        cli = _CLIS.get(skill)
        assert cli is not None, f"{skill}: mutating CLI is not inventoried"
        source = (_REPO / "skills" / skill / "scripts" / cli).read_text(encoding="utf-8")
        # A skill may call refusal() directly or its module's guard() helper that wraps it
        # (calendar keeps the CLI under the 250 pure-LOC ceiling that way).
        functions = ("governed_copy_refusal",) if skill == "mail" else (".refusal(", ".guard(")
        assert any(function in source for function in functions), (
            f"{skill}: CLI does not reference its governed refusal function"
        )


def test_governed_judgment_body_is_not_copied() -> None:
    offenders = []
    for root in (_REPO / "skills", _REPO / "automation"):
        for path in root.rglob("*.py"):
            if "def governed_copy_refusal(" in path.read_text(encoding="utf-8", errors="ignore"):
                relative = path.relative_to(_REPO).as_posix()
                if relative not in {"automation/skill_mount.py", "skills/mail/scripts/mail_runtime.py"}:
                    offenders.append(relative)
    assert not offenders, f"governed judgment copied outside canonical/fail-closed mail: {offenders}"


def test_exemptions_are_reasoned_and_not_stale() -> None:
    assert all(reason.strip() for reason in _EXEMPT.values())
    assert set(_EXEMPT) <= set(_GOVERNED_SKILLS)


def _live_root():
    from automation.skill_mount import LIVE_ROOT
    return LIVE_ROOT


def _live_root_env():
    from automation.skill_mount import LIVE_ROOT_ENV
    return LIVE_ROOT_ENV


def _stale_marker():
    from automation.skill_mount import STALE_COPY_MARKER
    return STALE_COPY_MARKER


def test_scenario_env_scrubs_forward_the_live_root() -> None:
    """scenario.sh 가 자기 CLI 를 ``env -i`` 로 돌리면 AUTOPHAGY_SKILL_LIVE_ROOT 도 넘겨야 한다.

    샌드박스는 스테이징 사본을 live root 로 선언해 가드를 통과시키는데(deploy-skill.sh),
    시나리오가 그 변수를 떨어뜨리면 가드는 노드 기본 live 루트로 폴백해 사본을
    STALE-SKILL-COPY-BLOCK 으로 막는다 — 2026-09-03 coordination 의 tokenless 레그가
    exit 3 은 맞췄지만 COORD-REFUSED 대신 가드 메시지를 내 13 스킬 중 홀로 stale 로 남았다.
    """
    offenders: list[str] = []
    for skill in _GOVERNED_SKILLS:
        scenario = _REPO / "skills" / skill / "scripts" / "scenario.sh"
        if not scenario.is_file():
            continue
        joined = scenario.read_text(encoding="utf-8").replace("\\\n", " ")
        for number, line in enumerate(joined.splitlines(), 1):
            if line.lstrip().startswith("#") or "env -i" not in line or _CLIS[skill] not in line:
                continue
            if "AUTOPHAGY_SKILL_LIVE_ROOT" not in line:
                offenders.append(f"{skill}:{number}")
    assert not offenders, f"scenario env -i drops the guard root: {offenders}"

