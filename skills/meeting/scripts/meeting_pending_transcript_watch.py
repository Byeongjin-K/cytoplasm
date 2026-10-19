#!/usr/bin/env python3
"""no-agent cron: 어젯밤까지 쌓인 미처리 전사본을 회의록으로 만든다 (매일 00:00 KST).

Drive 만 폴링한다 — Discord 메시지·첨부를 수신하지 않으므로 실시간 에이전트와 경쟁하지
않는다(설계규약 (a)). `~/.env.secrets` 는 이 프로세스가 직접 읽고(b), 해석한 자격증명을
자식 env 에 명시 전달하며(b-2), repo 와 마운트 경로는 공용 리졸버가 판정하고(c),
파일명은 스킬 고유이며(e), 처리 여부는 Drive 의 회의록 존재로만 판정한다(f — 별도 상태
파일이 없어 마킹과 실제가 어긋날 수 없다).

**stdout 이 곧 통지다.** `--no-agent` cron 은 빈 stdout 을 침묵으로 다루므로, 만들 회의록이
없는 밤에는 아무 말도 하지 않는다. 매일 도는 작업의 "할 일 없음" 한 줄은 1년이면 365번이다.
그래서 자식 `meeting_cli.py ingest` 의 stdout(결과 JSON)은 잡아서 stderr(내부 로그)로 넘기고, 형에게는
"회의록 하나 만들었어 — 10/19 stt-56" + Drive 링크만 낸다(형 지시 2026-10-20 00:10 KST — 맨 위에 원시 JSON 이
붙은 글을 형이 가리켰다).

`!meeting` 대화형 경로는 미처리가 여러 건이면 고르지 않고 멈춘다 — 소유자가 즉시 보고
고를 수 있기 때문이다. 야간 배치는 그럴 수 없어 상한까지 순차 처리한다. 매일 밤 같은
이유로 서면 기능이 죽는다.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

RELEASE_CURRENT: Final = Path("/srv/autophagy-agent-current")
MIRROR_CHECKOUT: Final = Path("/srv/autophagy-agents")
LIMIT_ENV: Final = "MEETING_PENDING_LIMIT"
DEFAULT_LIMIT: Final = 3
CHILD_TIMEOUT: Final = 1800.0
#: 자식이 필요로 하는 것 전부. `DRIVE_PUBLISH_ENABLED` 가 빠지면 자식은 Drive 를 건드리지
#: 않아 원장도 관리번호도 없는 회의록이 나온다. 모델 호출은 Codex OAuth 하나뿐이고 그
#: 자격증명은 키가 아니라 HOME 아래 저장소에 있으므로, 여기서 넘길 게이트웨이 키는 없다 —
#: 부모의 HOME 이 자식 env 로 그대로 간다(아래 `child_environment`).
SECRET_KEYS: Final = (
    "OPENAI_API_KEY",
    "DISCORD_BOT_TOKEN",
    "DRIVE_PUBLISH_ENABLED",
    "DRIVE_GWS_BIN",
)

#: 자식 하나를 돌려 (종료코드, stdout) 을 돌려준다.
Runner = Callable[[list[str], dict[str, str]], tuple[int, str]]
_DATED: Final = re.compile(r"(\d{4})-(\d{2})-(\d{2})_(.+?)(?:\.md)?")


@dataclass(frozen=True, slots=True)
class Made:
    name: str
    project: str
    link: str

    @property
    def label(self) -> str:
        match = _DATED.fullmatch(self.name)
        if match is None:
            return self.project or Path(self.name).stem
        _, month, day, rest = match.groups()
        return f"{int(month)}/{int(day)} {self.project or rest}"


def made_from(name: str, stdout: str) -> Made:
    """자식의 마지막 JSON 줄에서 과제명·링크만 읽는다. 못 읽으면 전사본 이름으로 쓴다."""
    for line in reversed(stdout.splitlines()):
        if not line.startswith("{"):
            continue
        try:
            result = json.loads(line)
        except ValueError:
            continue
        if isinstance(result, dict):
            return Made(name, str(result.get("project") or ""), str(result.get("link") or ""))
    return Made(name, "", "")


def owner_text(made: Sequence[Made], failed: Sequence[str], remaining: int) -> str:
    """형 채널 글 — 무엇이 됐나 + 링크. 파일명·JSON 은 싣지 않는다."""
    lines: list[str] = []
    if len(made) == 1:
        lines += [f"회의록 하나 만들었어 — {made[0].label}", *([made[0].link] if made[0].link else [])]
    elif made:
        lines.append(f"회의록 {len(made)}개 만들었어")
        lines += [f"- {item.label}" + (f" {item.link}" if item.link else "") for item in made]
    if failed:
        lines.append(f"{len(failed)}건은 못 만들어서 다음 밤에 다시 해 볼게: {', '.join(failed)}")
    if remaining > 0:
        lines.append(f"남은 {remaining}건은 다음 밤에 이어서 할게.")
    return "\n".join(lines)


def read_secrets(env: Mapping[str, str]) -> dict[str, str]:
    home = env.get("HOME")
    if not home:
        return {}
    try:
        raw = (Path(home) / ".env.secrets").read_text(encoding="utf-8")
    except OSError:
        return {}
    found: dict[str, str] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        found[key.strip()] = value.strip().strip('"').strip("'")
    return found


def child_environment(env: Mapping[str, str]) -> dict[str, str]:
    """부모가 os.environ 에 갖지 못한 자격증명까지 자식에게 값으로 넘긴다 (설계규약 (b-2))."""
    resolved = read_secrets(env)
    environment = dict(env)
    for key in SECRET_KEYS:
        value = environment.get(key) or resolved.get(key, "")
        if value:
            environment[key] = value
    return environment


def repo_root(env: Mapping[str, str]) -> Path:
    override = env.get("AUTOPHAGY_RUNTIME_ROOT") or env.get("AUTOPHAGY_REPO_ROOT")
    if override:
        return Path(override).expanduser()
    return RELEASE_CURRENT if (RELEASE_CURRENT / "automation").is_dir() else MIRROR_CHECKOUT


def mounted_scripts(env: Mapping[str, str]) -> Path:
    """마운트 경로는 공용 단일 정의가 판정한다 — 사본을 또 만들지 않는다."""
    root = repo_root(env)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from automation import skill_mount  # noqa: PLC0415 - repo 는 위에서야 해결된다
    except ImportError:
        return Path(env.get("MEETING_SCRIPTS", "/srv/autophagy-skills/live/meeting/scripts"))
    return skill_mount.skill_scripts("meeting", env_var="MEETING_SCRIPTS", env=env)


def load_pending(scripts: Path, env: Mapping[str, str]) -> Sequence[Any]:
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    for key, value in child_environment(env).items():
        os.environ.setdefault(key, value)
    import meeting_project  # noqa: PLC0415 - 마운트가 해결된 뒤에만 import 가능하다

    return meeting_project.pending_transcripts()


def spawn(argv: list[str], environment: dict[str, str]) -> tuple[int, str]:
    completed = subprocess.run(  # noqa: S603
        argv, env=environment, check=False, timeout=CHILD_TIMEOUT,
        stdout=subprocess.PIPE, text=True, errors="replace",
    )
    if completed.stdout:
        sys.stderr.write(completed.stdout)
    return completed.returncode, completed.stdout or ""


def _limit(env: Mapping[str, str]) -> int:
    try:
        return max(1, int(env.get(LIMIT_ENV, "")))
    except ValueError:
        return DEFAULT_LIMIT


def run_once(
    *,
    env: Mapping[str, str] | None = None,
    scripts: Path | None = None,
    pending: Callable[[], Sequence[Any]] | None = None,
    runner: Runner | None = None,
) -> int:
    environment = dict(os.environ if env is None else env)
    resolved_scripts = scripts if scripts is not None else mounted_scripts(environment)
    if not (resolved_scripts / "meeting_cli.py").is_file():
        print(
            "MEETING-WATCH-BLOCK: meeting 스킬이 마운트되지 않아 회의록을 만들지 않았습니다 "
            f"(찾은 경로: {resolved_scripts})."
        )
        return 1

    try:
        candidates = list(
            pending() if pending is not None else load_pending(resolved_scripts, environment)
        )
    except Exception as failure:  # noqa: BLE001 - 조회 실패는 알려야지 숨기면 안 된다
        print(f"MEETING-WATCH-BLOCK: 미처리 전사본을 조회하지 못했습니다 ({type(failure).__name__}).")
        return 1
    if not candidates:
        return 0

    limit = _limit(environment)
    child_env = child_environment(environment)
    call = runner if runner is not None else spawn
    cli = str(resolved_scripts / "meeting_cli.py")
    done: list[Made] = []
    failed: list[str] = []
    for item in candidates[:limit]:
        argv = [sys.executable, cli, "ingest", "--pending-name", item.name]
        try:
            code, stdout = call(argv, child_env)
        except Exception as failure:  # noqa: BLE001 - 전사본은 서로 독립이다
            code, stdout = -1, ""
            print(f"  자식 예외 {item.name}: {type(failure).__name__}", file=sys.stderr)
        if code == 0:
            done.append(made_from(item.name, stdout))
        else:
            failed.append(item.name)

    remaining = len(candidates) - len(done) - len(failed)
    print(owner_text(done, failed, remaining))
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    root = repo_root(os.environ)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from automation import pipeline_lock  # noqa: PLC0415 - repo 는 위에서야 해결된다
    except ImportError:
        print("MEETING-WATCH-BLOCK: 파이프라인 lock 을 불러오지 못해 실행하지 않았습니다.")
        return 1
    # speechtotext 가 전사본을 막 발행하고 회의록을 만드는 중이면 그 전사본은 여기서도
    # "미처리"로 보인다 — 같은 파이프라인이므로 같은 lock 을 잡고, 겹치면 다음 밤에 온다.
    with pipeline_lock.hold(os.environ) as acquired:
        if not acquired:
            return 0
        return run_once()


if __name__ == "__main__":
    sys.exit(main())
