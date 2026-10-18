"""Pure half of the install wizard: answers in, installer argv / config / summaries out.

Nothing here reads a terminal, runs a process or touches the host, which is what lets
every screen be proven with plain function calls. The summaries are counted from the
installer's own output, never hand-written — prose about the plan goes stale the day the
installer changes.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final

from automation.doctor.capabilities import CAPABILITIES
from automation.install.assets import render_node_toml as _render_config
from automation.install.owner_actions import FOLLOW_UP_HEADING
from automation.install.profiles import DEFAULT_PROFILE, HEALTHCHECK_DECLARATION_PATH
from automation.node_config import default_node_config

DEFAULT_ORIGIN_URL: Final = "https://github.com/orientpine/cytoplasm.git"
DOCTOR_ARGV: Final = ("python3", "-m", "automation.doctor")

PROFILE_MENU: Final = (
    ("dori", "도리 — 리드(짱돌)·비서(유돌) 기본 구성, 옥돌·무돌은 선택(root·신뢰키 불필요)"),
    ("core", "레거시 Hermes — 게이트웨이·승인 게이트·자동 업데이트"),
    ("rag", "레거시 Hermes + 개인 RAG(검색·임베딩·Qdrant)"),
    ("report-hub", "레거시 Hermes + Lab report-hub(콜렉터·대시보드)"),
    ("full", "레거시 Hermes 전부(기본 + report-hub + RAG)"),
)

#: 도리 프로필은 Hermes 설치기(`PROFILES`)가 아니라 `automation.install.dori` 로 간다.
DORI_PROFILE: Final = DEFAULT_PROFILE


def dori_argv(roles: tuple[str, ...], owner_call: str | None, *, dry_run: bool) -> tuple[str, ...]:
    argv: list[str] = ["python3", "-m", "automation.install.dori"]
    for role in roles:
        argv += ["--role", role]
    if owner_call:
        argv += ["--owner-call", owner_call]
    if dry_run:
        argv.append("--dry-run")
    return tuple(argv)

_KIND_MEANING: Final = {
    "account": "서비스 계정을 만든다(agent · peer · ops)",
    "group": "서비스 그룹을 만든다",
    "directory": "디렉터리와 소유·권한을 맞춘다",
    "file": "설정·systemd 유닛·sudoers 파일(내용 sha256 로 비교, 같으면 다음 실행에서 사라진다)",
    "deploy-key": "배포 키를 만든다(개인키는 출력되지 않는다)",
    "peer-attest-key": "peer 증명 키를 만든다(개인키는 출력되지 않는다)",
    "gitleaks": "비밀 유출 스캐너를 설치한다",
    "repository": "저장소를 받는다",
    "timer": "systemd 타이머를 켠다 — 자동 업데이트가 시작되는 지점",
    "check": "쓰기가 아니라 판정. 계획 확인(dry-run)에서는 실행되지 않는다",
}

_STOP_HINTS: Final = (
    ("hermes-gateway", "각 서비스 계정에 Hermes 게이트웨이를 설치·기동한다 "
     "(docs/guide/third-party-runtime-prereqs.md §3). 설치기는 Hermes 를 설치하지 않는다."),
    ("discord-readiness", "봇 설정이 부족하다 — 토큰 거부·Message Content 인텐트 OFF·권한·채널 "
     "중 하나다. docs/guide/install.md §5 를 단독으로 돌려 어느 항목인지 본다 "
     "(토큰이 설치기에 닿지 않은 경우는 WARN 이라 설치를 세우지 않는다)."),
    ("KNOWN-HOSTS-MISSING", "ops 계정 known_hosts 에 origin 호스트키를 넣는다 "
     "(docs/guide/install.md §6.2). 지문은 반드시 대역외로 대조한다."),
    ("EnsureRepository", "배포 공개키를 저장소에 read-only 로 등록했는지 확인한다 "
     "(docs/guide/install.md §6.2)."),
    ("update-trust", "공지된 지문과 다르다. 여기서 멈추고 유지보수자에게 확인한다."),
    ("healthcheck", "healthcheck 로그가 지목한 프로브를 본다 (docs/guide/install.md §7)."),
)


@dataclass(frozen=True, slots=True)
class Answers:
    profile: str
    origin_url: str
    node_name: str
    operator_account: str
    config_path: Path
    trust_key: Path
    expected_fingerprint: str | None
    components: tuple[str, ...]


def render_node_toml(answers: Answers) -> str:
    config = replace(
        default_node_config(),
        origin_url=answers.origin_url,
        require_signed_updates=True,
        peer_attest_mode="signed",
        primary_node_name=answers.node_name,
        rag_node_name=answers.node_name,
        deploy_ssh_host="",
        operator_account=answers.operator_account,
    )
    return _render_config(config)


def installer_argv(answers: Answers, *, dry_run: bool, as_root: bool) -> tuple[str, ...]:
    argv: list[str] = [
        "python3", "-m", "automation.install",
        "--config", str(answers.config_path),
        "--update-trust-key", str(answers.trust_key),
        "--profile", answers.profile,
    ]
    if answers.expected_fingerprint:
        argv += ["--expect-update-trust-fingerprint", answers.expected_fingerprint]
    for component in answers.components:
        argv += ["--with-component", component]
    if dry_run:
        argv.append("--dry-run")
    elif not as_root:
        argv.insert(0, "sudo")
    return tuple(argv)


def _plan_lines(output: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for line in output.splitlines():
        head, _, rest = line.partition(". ")
        if head.isdigit() and rest:
            kind, _, detail = rest.partition(" ")
            rows.append((kind, detail))
    return rows


def summarize_plan(output: str) -> str:
    rows = _plan_lines(output)
    counts = Counter(kind for kind, _ in rows)
    lines = ["이 계획이 실제로 하는 일 (계획 확인 출력에서 그대로 집계했다):"]
    for kind, count in counts.most_common():
        lines.append(f"  {kind:<16} {count:>3}  {_KIND_MEANING.get(kind, '')}")
    lines.append(f"  {'(합계)':<16} {len(rows):>3}")
    checks = [detail for kind, detail in rows if kind == "check"]
    if checks:
        lines.append(f"  판정(check): {', '.join(checks)}")
    if any(str(HEALTHCHECK_DECLARATION_PATH) in detail for _, detail in rows):
        lines.append(
            f"  헬스체크 선언: {HEALTHCHECK_DECLARATION_PATH} — 고른 프로필의 묶음만 감시하므로 "
            "설치 뒤 healthcheck.sh --suggest 를 돌릴 필요가 없다"
        )
    return "\n".join(lines)


def _owner_follow_up(output: str) -> tuple[str, ...]:
    """설치기가 소유자에게 넘긴 절차를 그대로 옮긴다 — 요약이 그것을 삼키면 안 된다."""
    rows = output.splitlines()
    for index, line in enumerate(rows):
        if line.startswith(FOLLOW_UP_HEADING):
            return tuple(rows[index:])
    return ()


def summarize_verdict(output: str, *, returncode: int) -> str:
    lines = [
        line for line in output.splitlines()
        if line.startswith(("[PASS] trust-key", "[WARN] trust-key", "[PASS] healthcheck",
                            "[FAIL] healthcheck", "--- INSTALLED", "--- NOT-INSTALLED"))
    ]
    follow_up = _owner_follow_up(output)
    if returncode == 0:
        return "\n".join((
            "설치 완료 — 설치기의 종료 게이트가 전부 통과했다.",
            *(f"  {line}" for line in lines),
            *follow_up,
            "다음:",
            "  systemctl list-timers | grep autophagy   # 자동 업데이트 타이머가 살아 있는지",
            "  docs/guide/manual-member.md               # 설치 이후의 사용법·그룹 가입",
        ))
    stop = next(
        (line for line in output.splitlines()
         if line.startswith(("[FAIL]", "INSTALL-BLOCK", "USAGE-ERROR", "TRUST-KEY-"))),
        f"rc={returncode}",
    )
    hint = next((text for key, text in _STOP_HINTS if key in stop), "docs/guide/install.md §8 에서 그 이름을 찾는다.")
    return "\n".join((
        f"설치 미완 (rc={returncode}) — 설치기는 첫 실패에서 멈춘다. 의도된 동작이다.",
        f"  멈춘 곳: {stop}",
        f"  조치: {hint}",
        "  고친 뒤 같은 명령을 그대로 다시 실행한다(멱등 — 끝난 항목은 건너뛴다).",
        *follow_up,
    ))


def approval_preview() -> str:
    """설치 전에 보여 줄 「사람이 승인할 것」 — doctor 와 같은 레지스트리에서 뽑아 낡지 않는다."""
    lines = ["  설치가 끝나면 사람이 승인·연결해야 하는 것 (설치 마지막에 doctor 가 하나씩 확인·안내한다):"]
    lines += [f"    - {cap.label}: {cap.approve_at}" for cap in CAPABILITIES if cap.approve_at]
    return "\n".join(lines)


def doctor_settled(output: str) -> bool:
    """고장이 없고 모든 계정을 실제로 진단했으면 끝난 것이다 — 남은 확인(WARN)은 선택 기능이다."""
    return not any(line.startswith(("[FAIL]", "[WARN] doctor-run")) for line in output.splitlines())
