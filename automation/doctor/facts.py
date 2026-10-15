"""doctor 의 관측 절반 — 이 계정의 연결·승인 상태를 읽기만 한다.

비밀 값은 들고 나오지 않는다: `~/.env.secrets` 는 "비어 있지 않은 키 이름" 집합으로만,
경로 설정은 "그 경로가 있다/없다"로만 옮긴다. Discord 토큰만 온라인 검사 동안 메모리에서
쓰이고 Facts 에는 실리지 않는다. 네트워크를 부르는 것은 `online=True` 일 때의 두 가지
(`gws auth status`, Discord GET 전용 검사)뿐이다.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, Literal, TypeAlias

from automation.doctor.cron_env import Gap
from automation.doctor.cron_env import observe as observe_cron_env
from automation.install.checks import CheckResult
from automation.interop import free_response

Role: TypeAlias = Literal["agent", "peer"]
Run: TypeAlias = Callable[[tuple[str, ...], Mapping[str, str]], tuple[int, str]]
DiscordProbe: TypeAlias = Callable[[str, Path | None], tuple[CheckResult, ...]]

TOKEN_KEY: Final = "DISCORD_BOT_TOKEN"
DRIVE_KEY: Final = "DRIVE_PUBLISH_ENABLED"
PATH_KEYS: Final = ("SPEECHTOTEXT_WHISPER_BIN", "SPEECHTOTEXT_WHISPER_MODEL")
GWS_FILES: Final = ("client_secret.json", "credentials.enc", ".encryption_key")
_REPO: Final = Path(__file__).resolve().parents[2]
_ERROR_LIMIT: Final = 120
#: Hermes 가 자격 하나를 쉬게 하는 가장 긴 기본 쿨다운(429·기타 = 1시간). 공급자가 재개 시각을 주면 그것이 이긴다.
HERMES_MAX_COOLDOWN_SECONDS: Final = 60 * 60
_LONG_TOKEN: Final = re.compile(r"Bearer\s+\S+|[A-Za-z0-9_\-]{24,}")


@dataclass(frozen=True, slots=True)
class CronJob:
    name: str
    script: str
    enabled: bool
    paused: bool
    last_status: str
    failure_streak: int
    last_error: str


@dataclass(frozen=True, slots=True)
class GwsStatus:
    token_valid: bool
    scopes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Facts:
    account: str
    role: Role
    home: Path
    gateway_unit: str
    hermes: bool
    gateway: str
    auth: Mapping[str, tuple[str, ...]] | None
    model_provider: str
    custom_providers: frozenset[str]
    fallback: tuple[str, ...]
    secret_names: frozenset[str] | None
    drive_publish: bool
    paths: Mapping[str, bool]
    interop: frozenset[str] | None
    gws_installed: bool
    gws_files: frozenset[str]
    gws: GwsStatus | str | None
    cron: tuple[CronJob, ...] | None
    discord: tuple[CheckResult, ...] | str | None
    model_default: str = ""
    fallback_models: tuple[str, ...] = ()
    agent_chat_channel: str = ""
    discord_channels: tuple[tuple[str, ...], tuple[str, ...]] | None = ((), ())
    cron_env: tuple[tuple[Gap, ...], int] | str | None = None
    #: 공급자별로 `exhausted`(일시 쿨다운) 자격이 다시 쓰이기까지 남은 초 — 가장 늦은 것. 알 수 없으면 None.
    cooldowns: Mapping[str, float | None] | None = None


def mask(text: str) -> str:
    """한 줄로 접고 토큰처럼 긴 문자열을 가린다 — 오류 문구가 통지로 나가기 때문이다."""
    line = " ".join(text.split())
    return _LONG_TOKEN.sub("<masked>", line)[:_ERROR_LIMIT]


def _run(argv: tuple[str, ...], extra: Mapping[str, str]) -> tuple[int, str]:
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv, read-only status commands
            argv, capture_output=True, text=True, env={**os.environ, **extra}, timeout=60, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return 127, type(error).__name__
    return done.returncode, done.stdout


def _discord_probe(token: str, config: Path | None) -> tuple[CheckResult, ...]:
    from automation.install.discord_check import probe

    return probe(token, config)


def _json(path: Path) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def read_env_file(path: Path) -> dict[str, str] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        key, separator, value = line.partition("=")
        if separator and key and not key.startswith("#"):
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _epoch(value: object) -> float | None:
    """Hermes 가 쓰는 재개 시각 모양(epoch 초·밀리초·ISO-8601)을 초로 읽는다."""
    if isinstance(value, bool) or value is None or value == "":
        return None
    if isinstance(value, int | float):
        number = float(value)
    else:
        text = str(value).strip()
        try:
            number = float(text)
        except ValueError:
            try:
                return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
            except ValueError:
                return None
    if number <= 0:
        return None
    return number / 1000.0 if number > 1_000_000_000_000 else number


def _resume_at(entry: Mapping[str, object]) -> float | None:
    """`exhausted` 자격이 다시 쓰이는 시각의 상한 — 공급자 재개 시각, 없으면 표시 시각 + 1시간."""
    reset = _epoch(entry.get("last_error_reset_at"))
    if reset is not None:
        return reset
    marked = _epoch(entry.get("last_status_at"))
    return None if marked is None else marked + HERMES_MAX_COOLDOWN_SECONDS


def _cooldowns(path: Path, now: float) -> Mapping[str, float | None]:
    """`exhausted` 는 Hermes 의 일시 쿨다운이다(영구 인증 실패는 `dead`) — 언제 풀리는지만 옮긴다."""
    raw = _json(path)
    pool = raw.get("credential_pool") if isinstance(raw, dict) else None
    if not isinstance(pool, dict):
        return {}
    result: dict[str, float | None] = {}
    for provider, entries in pool.items():
        if not isinstance(entries, list):
            continue
        resumes = [_resume_at(entry) for entry in entries
                   if isinstance(entry, dict) and entry.get("last_status") == "exhausted"]
        if resumes:
            known = [at for at in resumes if at is not None]
            result[str(provider)] = max(known) - now if len(known) == len(resumes) else None
    return result


def _auth(path: Path) -> Mapping[str, tuple[str, ...]] | None:
    raw = _json(path)
    if not isinstance(raw, dict):
        return None
    pool = raw.get("credential_pool")
    if not isinstance(pool, dict):
        return {}
    return {
        str(provider): tuple(str(entry.get("last_status") or "") for entry in entries if isinstance(entry, dict))
        for provider, entries in pool.items()
        if isinstance(entries, list)
    }


def _blocks(path: Path) -> dict[str, list[tuple[str, str]]]:
    """최상위 키 → 그 아래 `키: 값` 쌍. 이 파일에서 doctor 가 보는 세 블록은 이 모양이면 충분하다."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    blocks: dict[str, list[tuple[str, str]]] = {}
    current: list[tuple[str, str]] | None = None
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace():
            current = blocks.setdefault(line.partition(":")[0].strip(), [])
            continue
        key, _, value = line.strip().lstrip("- ").partition(":")
        if current is not None and value.strip():
            current.append((key.strip(), value.strip().strip("'\"")))
    return blocks


def _hermes_models(
    path: Path,
) -> tuple[str, frozenset[str], tuple[str, ...], str, tuple[str, ...]]:
    """주 모델·폴백은 이 파일 하나가 정한다 — 코드는 모델을 고르지 않는다(2026-09-29)."""
    blocks = _blocks(path)
    provider = next((value for key, value in blocks.get("model", []) if key == "provider"), "")
    default = next((value for key, value in blocks.get("model", []) if key == "default"), "")
    custom = frozenset(value for key, value in blocks.get("custom_providers", []) if key == "name")
    chain = blocks.get("fallback_providers", [])
    fallback = tuple(value for key, value in chain if key == "provider")
    fallback_models = tuple(value for key, value in chain if key == "model")
    return provider, custom, fallback, default, fallback_models


def _interop(path: Path) -> frozenset[str] | None:
    raw = _json(path)
    if not isinstance(raw, dict):
        return None
    return frozenset(str(key) for key, value in raw.items() if value not in (None, ""))


def _cron(path: Path) -> tuple[CronJob, ...] | None:
    raw = _json(path)
    jobs = raw.get("jobs") if isinstance(raw, dict) else raw
    if not isinstance(jobs, list):
        return None
    return tuple(
        CronJob(
            name=str(job.get("name") or job.get("id") or "?"),
            script=str(job.get("script") or ""),
            enabled=job.get("enabled") is not False,
            paused=bool(job.get("paused_at")),
            last_status=str(job.get("last_status") or ""),
            failure_streak=int(job.get("failure_streak") or 0),
            last_error=mask(str(job.get("last_error") or "")),
        )
        for job in jobs
        if isinstance(job, dict)
    )


def _agent_chat(path: Path) -> str:
    try:
        return free_response.agent_chat_channel(path)
    except (OSError, ValueError):
        return ""


def _discord_channels(path: Path) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return (), ()
    except OSError:
        return None
    return free_response.configured_channels(text)


def _gws_status(binary: str, run: Run) -> GwsStatus | str:
    code, out = run((binary, "auth", "status"), {})
    if code != 0:
        return f"gws auth status rc={code}"
    try:
        raw = json.loads(out)
    except ValueError:
        return "gws auth status 응답을 해석하지 못했다"
    if not isinstance(raw, dict):
        return "gws auth status 응답을 해석하지 못했다"
    scopes = raw.get("scopes")
    return GwsStatus(
        token_valid=raw.get("token_valid") is True,
        scopes=tuple(str(scope) for scope in scopes) if isinstance(scopes, list) else (),
    )


def _gateway(unit: str, run: Run, uid: int) -> str:
    _, out = run(("systemctl", "--user", "is-active", unit), {"XDG_RUNTIME_DIR": f"/run/user/{uid}"})
    lines = out.strip().splitlines()
    state = lines[-1].strip() if lines else ""
    known = {"active", "inactive", "failed", "activating", "deactivating", "reloading"}
    return state if state in known else "unknown"


def gather(
    *, account: str, role: Role, home: Path, gateway_unit: str, online: bool,
    run: Run = _run, discord_probe: DiscordProbe = _discord_probe, uid: int | None = None,
    repo: Path = _REPO, now: float | None = None,
) -> Facts:
    hermes_dir = home / ".hermes"
    secrets = read_env_file(home / ".env.secrets")
    names = None if secrets is None else frozenset(key for key, value in (secrets or {}).items() if value)
    paths = {key: Path(secrets[key]).expanduser().exists() for key in PATH_KEYS if secrets and secrets.get(key)}
    gws_local = home / ".local" / "bin" / "gws"
    gws_binary = str(gws_local) if gws_local.is_file() else shutil.which("gws")
    gws_files = frozenset(name for name in GWS_FILES if (home / ".config" / "gws" / name).is_file())
    interop_path = hermes_dir / "interop" / "config.json"
    token = (secrets or {}).get(TOKEN_KEY, "")
    provider, custom, fallback, default, fallback_models = _hermes_models(hermes_dir / "config.yaml")
    cron = _cron(hermes_dir / "cron" / "jobs.json")
    registered = frozenset(job.name for job in cron or () if job.enabled and not job.paused)
    discord: tuple[CheckResult, ...] | str | None = None
    if online and token:
        try:
            discord = discord_probe(token, interop_path if interop_path.is_file() else None)
        except Exception as error:  # noqa: BLE001 - a probe failure is a verdict, never a crash
            discord = type(error).__name__
    return Facts(
        account=account, role=role, home=home, gateway_unit=gateway_unit,
        hermes=(home / ".local" / "bin" / "hermes").is_file(),
        gateway=_gateway(gateway_unit, run, os.getuid() if uid is None else uid),
        auth=_auth(hermes_dir / "auth.json"),
        cooldowns=_cooldowns(hermes_dir / "auth.json", time.time() if now is None else now),
        model_provider=provider,
        custom_providers=custom,
        fallback=fallback,
        secret_names=names,
        drive_publish=(secrets or {}).get(DRIVE_KEY, "") == "1",
        paths=paths,
        interop=_interop(interop_path),
        gws_installed=gws_binary is not None,
        gws_files=gws_files,
        gws=_gws_status(gws_binary, run) if online and gws_binary and gws_files else None,
        cron=cron,
        discord=discord,
        model_default=default,
        fallback_models=fallback_models,
        agent_chat_channel=_agent_chat(interop_path),
        discord_channels=_discord_channels(hermes_dir / "config.yaml"),
        cron_env=observe_cron_env(repo=repo, role=role, registered=registered,
                                  hermes_env=read_env_file(hermes_dir / ".env"), environ=os.environ),
    )
