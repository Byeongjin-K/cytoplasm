"""Read-only Discord readiness check for a third-party installation.

Answers one question before an install starts: *is this bot token usable by the
agent runtime as it is configured right now?* Four checks, in the order a
failure actually blocks progress:

1. token   — `GET /users/@me`            (is the token valid at all)
2. intent  — `GET /applications/@me`     (Message Content intent enabled)
3. perms   — `GET /users/@me/guilds`     (required bot permissions, no admin)
4. channel — `GET /channels/{id}` + `GET /channels/{id}/messages?limit=1`
             (the three channels the runtime needs are visible and readable)

Every request is a GET. Nothing is posted, edited or deleted — running this
against a live server is safe. The bot token is read from the environment, is
never passed on the command line, is never written to stdout/stderr, and the
rendered report is redacted as a last line of defence.

HTTP conventions (auth header shape, DiscordBot User-Agent, 429 `Retry-After`)
mirror `automation/interop/discord_transport.py`; the UA is not optional —
Discord answers 403 to a default urllib UA on some endpoints.

Usage::

    DISCORD_BOT_TOKEN=... python3 automation/install/discord_check.py \\
        --config ~/.hermes/interop/config.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

REPO_ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):  # direct `python3 automation/install/discord_check.py`
    sys.path.insert(0, str(REPO_ROOT))

from automation.install.checks import CheckResult, Status, exit_code, redact, render  # noqa: E402

DISCORD_API: Final = "https://discord.com/api/v10"
USER_AGENT: Final = "DiscordBot (https://github.com/orientpine/autophagy-agents, 0)"
TOKEN_ENV: Final = "DISCORD_BOT_TOKEN"
_REQUEST_TIMEOUT_SECONDS: Final = 30.0
_MAX_RATE_LIMIT_RETRIES: Final = 3

# Discord application flags — either bit means Message Content is switched on.
# The `_LIMITED` variant is what an unverified app (<100 guilds) gets, and it is
# just as good for our purposes, so both are accepted.
_GATEWAY_MESSAGE_CONTENT: Final = 1 << 18
_GATEWAY_MESSAGE_CONTENT_LIMITED: Final = 1 << 19

# Discord permission bits. The required set is the onboarding invite URL's set
# (docs/guide/onboarding-kit.md §3.2); the forbidden set is the same document's
# explicit denylist — a bot that holds them is over-privileged for this system.
_PERMISSIONS: Final[Mapping[str, int]] = {
    "ADD_REACTIONS": 1 << 6,
    "VIEW_CHANNEL": 1 << 10,
    "SEND_MESSAGES": 1 << 11,
    "ATTACH_FILES": 1 << 15,
    "READ_MESSAGE_HISTORY": 1 << 16,
    "MANAGE_ROLES": 1 << 28,
    "CREATE_PUBLIC_THREADS": 1 << 35,
    "SEND_MESSAGES_IN_THREADS": 1 << 38,
    "ADMINISTRATOR": 1 << 3,
    "MANAGE_GUILD": 1 << 5,
}
REQUIRED_PERMISSIONS: Final = (
    "VIEW_CHANNEL",
    "SEND_MESSAGES",
    "READ_MESSAGE_HISTORY",
    "ADD_REACTIONS",
    "ATTACH_FILES",
    "CREATE_PUBLIC_THREADS",
    "SEND_MESSAGES_IN_THREADS",
)
FORBIDDEN_PERMISSIONS: Final = ("ADMINISTRATOR", "MANAGE_GUILD", "MANAGE_ROLES")

# Guild text (0) and announcement (5) channels are both fine; anything else
# (voice, forum, category, thread) cannot host the runtime's message flows.
_TEXT_CHANNEL_TYPES: Final = frozenset({0, 5})


@dataclass(frozen=True, slots=True)
class ChannelRequirement:
    """One channel the runtime needs, and where its id comes from."""

    role: str
    expected_name: str
    config_key: str
    purpose: str


# The three channels a runtime needs. `#team` is a human discussion channel and
# is deliberately absent — no code path reads it.
# Source: docs/guide/discord-server-architecture.md §1.2.
REQUIRED_CHANNELS: Final = (
    ChannelRequirement(
        role="agents-log",
        expected_name="agents-log",
        config_key="agents_log_channel_id",
        purpose="봇의 구조화된 규약 보고(v0) 게시 대상",
    ),
    ChannelRequirement(
        role="interop",
        expected_name="autophagy-agents",
        config_key="interop_channel_id",
        purpose="봇간 조율(coord-) 봉투 트래픽",
    ),
    ChannelRequirement(
        role="approvals",
        expected_name="approvals",
        config_key="personal_approvals_channel_id",
        purpose="개인 서버의 스킬 공급망 승인 게이트(skill-deploy / peer attest / publish)",
    ),
)

# The owner-DM approval surface is NOT in REQUIRED_CHANNELS and cannot be: a DM
# channel has no id in the interop config, Discord creates it on first contact,
# and looking one up (`POST /users/@me/channels`) is a write. The owner id is
# not known at install time either. Rather than let a green report imply the
# approval path was verified — it carries mail, calendar, coordination, todo and
# repair confirmations — the gap is declared as a WARN, which cannot change the
# exit code. Routing lives in automation/interop/approval_surface.py.
OWNER_DM_SURFACE: Final = "owner-dm"


def owner_dm_coverage() -> CheckResult:
    """Declare the approval surface this read-only check cannot reach."""
    return CheckResult(
        f"surface[{OWNER_DM_SURFACE}]",
        Status.WARN,
        "UNVERIFIED-SURFACE: 소유자 DM 승인 표면은 이 검사가 확인하지 못한다 — "
        "DM 채널은 config에 id가 없고 조회 자체가 쓰기 호출이라 읽기 전용 계약을 깨뜨린다. "
        "위 채널 검사가 전부 PASS여도 메일·캘린더·조율·할일·수리 승인이 도달한다는 "
        "뜻은 아니다. 설치 후 소유자가 봇과의 DM을 열어 첫 승인 카드가 도착하는지로 "
        "확인한다.",
    )


@dataclass(frozen=True, slots=True)
class ApiResponse:
    """A Discord REST answer reduced to what the pure evaluators need.

    `transport_error` carries a category name only (never a URL with query
    parameters and never a header), so no evaluator can leak credentials.
    """

    status: int
    payload: Any = None
    transport_error: str | None = None


Fetch = Callable[[str], ApiResponse]


def _http_detail(response: ApiResponse) -> str:
    if response.transport_error is not None:
        return f"네트워크 오류({response.transport_error}) — discord.com HTTPS 아웃바운드를 확인한다"
    if response.status == 401:
        return "401 — 토큰이 거부됐다. Developer Portal에서 Reset Token 후 ~/.env.secrets를 갱신한다"
    if response.status == 403:
        return "403 — 권한 부족이거나 DiscordBot User-Agent 누락(문서 §0.1의 검증된 함정)"
    if response.status == 429:
        return "429 — 레이트리밋이 반복됐다. 잠시 후 다시 실행한다"
    return f"HTTP {response.status} — 예상치 못한 응답"


def evaluate_token(response: ApiResponse) -> CheckResult:
    """Decide whether the token authenticates as a bot user."""
    if response.status != 200 or not isinstance(response.payload, Mapping):
        return CheckResult("token", Status.FAIL, f"INVALID-TOKEN: {_http_detail(response)}")
    bot_id = response.payload.get("id")
    username = response.payload.get("username")
    if not isinstance(bot_id, str) or not isinstance(username, str):
        return CheckResult("token", Status.FAIL, "INVALID-TOKEN: 응답에 봇 id/username이 없다")
    if response.payload.get("bot") is not True:
        return CheckResult(
            "token", Status.FAIL, "NOT-A-BOT: 사용자 토큰이다. Bot 탭의 봇 토큰을 사용한다"
        )
    return CheckResult("token", Status.PASS, f"봇 인증 성공 — {username} (id={bot_id})")


def evaluate_intents(response: ApiResponse) -> CheckResult:
    """Decide whether the Message Content privileged intent is switched on."""
    if response.status != 200 or not isinstance(response.payload, Mapping):
        return CheckResult("intent", Status.FAIL, f"INTENT-UNKNOWN: {_http_detail(response)}")
    flags = response.payload.get("flags")
    if not isinstance(flags, int):
        return CheckResult("intent", Status.FAIL, "INTENT-UNKNOWN: 애플리케이션 flags가 없다")
    if flags & _GATEWAY_MESSAGE_CONTENT:
        return CheckResult("intent", Status.PASS, "Message Content Intent ON (verified 앱)")
    if flags & _GATEWAY_MESSAGE_CONTENT_LIMITED:
        return CheckResult(
            "intent", Status.PASS, "Message Content Intent ON (100 길드 미만 앱의 limited 형태 — 정상)"
        )
    return CheckResult(
        "intent",
        Status.FAIL,
        "MESSAGE-CONTENT-INTENT-OFF: Developer Portal → Bot → Privileged Gateway Intents → "
        "Message Content Intent를 ON으로 저장하고 게이트웨이를 재시작한다. "
        "꺼져 있으면 다른 봇 메시지의 content가 빈 문자열로 도착해 규약 파싱이 전부 실패한다",
    )


def _decode_permissions(raw: object) -> int | None:
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            return None
    return None


def evaluate_permissions(response: ApiResponse) -> tuple[CheckResult, ...]:
    """Decide, per guild, whether the bot holds exactly the permissions it needs."""
    if response.status != 200 or not isinstance(response.payload, Sequence) or isinstance(response.payload, str):
        return (CheckResult("permissions", Status.FAIL, f"GUILDS-UNKNOWN: {_http_detail(response)}"),)
    guilds = [guild for guild in response.payload if isinstance(guild, Mapping)]
    if not guilds:
        return (
            CheckResult(
                "permissions",
                Status.FAIL,
                "NO-GUILD: 봇이 어느 서버에도 초대되지 않았다. 초대 URL로 공유 서버와 개인 서버에 초대한다",
            ),
        )
    return tuple(_evaluate_guild(guild) for guild in guilds)


def _evaluate_guild(guild: Mapping[str, Any]) -> CheckResult:
    guild_id = guild.get("id")
    label = f"permissions[{guild_id if isinstance(guild_id, str) else '?'}]"
    granted = _decode_permissions(guild.get("permissions"))
    if granted is None:
        return CheckResult(label, Status.FAIL, "PERMISSIONS-UNKNOWN: 길드 응답에 permissions가 없다")
    missing = [name for name in REQUIRED_PERMISSIONS if not granted & _PERMISSIONS[name]]
    held_forbidden = [name for name in FORBIDDEN_PERMISSIONS if granted & _PERMISSIONS[name]]
    if held_forbidden:
        return CheckResult(
            label,
            Status.FAIL,
            f"OVER-PRIVILEGED: {', '.join(held_forbidden)} 보유. 초대를 취소하고 최소 권한 URL로 다시 초대한다",
        )
    if missing:
        return CheckResult(
            label,
            Status.FAIL,
            f"MISSING-PERMISSION: {', '.join(missing)}. 초대 URL의 permissions 값을 고쳐 다시 초대한다",
        )
    return CheckResult(label, Status.PASS, "필수 권한 7종 보유, 금지 권한 없음")


def evaluate_channel(
    requirement: ChannelRequirement,
    channel_id: str | None,
    channel: ApiResponse | None,
    history: ApiResponse | None,
) -> CheckResult:
    """Decide whether one required channel is visible and readable."""
    name = f"channel[{requirement.role}]"
    if channel_id is None:
        return CheckResult(
            name,
            Status.FAIL,
            f"MISSING-CHANNEL-ID: #{requirement.expected_name} ({requirement.purpose})의 id가 없다. "
            f"채널을 만들고 id를 config의 {requirement.config_key} 또는 "
            f"--{requirement.role}-channel-id로 넘긴다",
        )
    if channel is None:
        return CheckResult(name, Status.FAIL, "CHANNEL-UNKNOWN: 채널 조회가 수행되지 않았다")
    if channel.status == 404:
        return CheckResult(
            name,
            Status.FAIL,
            f"MISSING-CHANNEL: id={channel_id}를 찾을 수 없다. 채널이 삭제됐거나 봇이 그 서버에 없다",
        )
    if channel.status == 403:
        return CheckResult(
            name,
            Status.FAIL,
            f"NO-ACCESS: id={channel_id}에 View Channel 권한이 없다. 채널 권한 오버라이드를 확인한다",
        )
    if channel.status != 200 or not isinstance(channel.payload, Mapping):
        return CheckResult(name, Status.FAIL, f"CHANNEL-UNKNOWN: {_http_detail(channel)}")
    channel_type = channel.payload.get("type")
    if channel_type not in _TEXT_CHANNEL_TYPES:
        return CheckResult(
            name,
            Status.FAIL,
            f"WRONG-CHANNEL-TYPE: type={channel_type}. 길드 텍스트 채널(type 0)이어야 한다",
        )
    if history is None or history.status == 403:
        return CheckResult(
            name,
            Status.FAIL,
            f"NO-HISTORY: id={channel_id}에서 Read Message History가 거부됐다. "
            "승인 리액션 판독과 봉투 수신이 불가능하다",
        )
    if history.status != 200:
        return CheckResult(name, Status.FAIL, f"HISTORY-UNKNOWN: {_http_detail(history)}")
    actual_name = channel.payload.get("name")
    guild_id = channel.payload.get("guild_id")
    detail = f"#{actual_name} (guild={guild_id}) 조회·이력 읽기 가능"
    if requirement.role not in {"approvals", "yoodori"} and actual_name != requirement.expected_name:
        return CheckResult(
            name,
            Status.WARN,
            f"{detail} — 다만 이름이 관례값 #{requirement.expected_name}과 다르다. "
            "id 기반 흐름은 동작하지만 이름 검색 폴백은 실패한다",
        )
    return CheckResult(name, Status.PASS, detail)


def run_checks(fetch: Fetch, channel_ids: Mapping[str, str]) -> tuple[CheckResult, ...]:
    """Run every check against an injected fetcher, stopping early on a dead token."""
    token_result = evaluate_token(fetch("/users/@me"))
    if token_result.status is Status.FAIL:
        return (token_result,)
    results: list[CheckResult] = [token_result, evaluate_intents(fetch("/applications/@me"))]
    results.extend(evaluate_permissions(fetch("/users/@me/guilds")))
    for requirement in REQUIRED_CHANNELS:
        channel_id = channel_ids.get(requirement.role)
        if channel_id is None:
            results.append(evaluate_channel(requirement, None, None, None))
            continue
        channel = fetch(f"/channels/{channel_id}")
        history = fetch(f"/channels/{channel_id}/messages?limit=1") if channel.status == 200 else None
        results.append(evaluate_channel(requirement, channel_id, channel, history))
    results.append(owner_dm_coverage())
    return tuple(results)


def _urllib_fetch(token: str) -> Fetch:
    """Build the real read-only fetcher (GET only, DiscordBot UA, 429 backoff)."""

    def fetch(path: str) -> ApiResponse:
        request = urllib.request.Request(
            f"{DISCORD_API}{path}",
            headers={"Authorization": f"Bot {token}", "User-Agent": USER_AGENT},
            method="GET",
        )
        for attempt in range(_MAX_RATE_LIMIT_RETRIES):
            try:
                with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:  # noqa: S310
                    return ApiResponse(response.status, json.loads(response.read().decode("utf-8")))
            except urllib.error.HTTPError as error:
                if error.code != 429 or attempt == _MAX_RATE_LIMIT_RETRIES - 1:
                    return ApiResponse(error.code)
                time.sleep(_retry_after(error.headers.get("Retry-After")))
            except (urllib.error.URLError, TimeoutError):
                return ApiResponse(0, transport_error="connection")
            except (ValueError, UnicodeDecodeError):
                return ApiResponse(0, transport_error="malformed-response")
        return ApiResponse(429)

    return fetch


def probe(token: str, config_path: Path | None) -> tuple[CheckResult, ...]:
    """`main` 과 같은 GET 전용 검사를 환경변수 대신 인자로 받은 토큰으로 돌린다(doctor 용)."""
    return run_checks(_urllib_fetch(token), load_channel_ids(config_path, {}))


def _retry_after(value: str | None) -> float:
    if value is None:
        return 1.0
    try:
        return min(float(value), 30.0)
    except ValueError:
        return 1.0


def load_channel_ids(config_path: Path | None, overrides: Mapping[str, str | None]) -> dict[str, str]:
    """Resolve channel ids from explicit flags first, then the interop config."""
    resolved = {role: value for role, value in overrides.items() if value}
    if config_path is None:
        return resolved
    try:
        raw = json.loads(config_path.expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return resolved
    if not isinstance(raw, Mapping):
        return resolved
    for requirement in REQUIRED_CHANNELS:
        if requirement.role in resolved:
            continue
        value = raw.get(requirement.config_key)
        if isinstance(value, str) and value:
            resolved[requirement.role] = value
    return resolved


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="discord_check",
        description=(
            "설치 전 Discord 전제조건을 읽기 전용으로 확인한다 "
            "(토큰 유효성 · Message Content 인텐트 · 봇 권한 · 필수 채널 3종 접근). "
            "쓰기 호출은 하지 않는다."
        ),
        epilog=f"토큰은 환경변수 {TOKEN_ENV}에서만 읽으며 출력에 절대 포함되지 않는다.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="채널 id를 읽어올 interop config (보통 ~/.hermes/interop/config.json)",
    )
    parser.add_argument("--token-env", default=TOKEN_ENV, help=f"봇 토큰 환경변수 이름 (기본 {TOKEN_ENV})")
    for requirement in REQUIRED_CHANNELS:
        parser.add_argument(
            f"--{requirement.role}-channel-id",
            dest=f"{requirement.role}_channel_id".replace("-", "_"),
            default=None,
            help=f"#{requirement.expected_name} 채널 id — {requirement.purpose}",
        )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: resolve inputs, run read-only checks, print a redacted report."""
    args = _parser().parse_args(argv)
    token = os.environ.get(args.token_env, "")
    if not token:
        print(
            f"USAGE-ERROR: 환경변수 {args.token_env}가 비어 있다. "
            "`set -a; . ~/.env.secrets; set +a` 후 다시 실행한다.",
            file=sys.stderr,
        )
        return 2
    overrides = {
        requirement.role: getattr(args, f"{requirement.role}_channel_id".replace("-", "_"))
        for requirement in REQUIRED_CHANNELS
    }
    channel_ids = load_channel_ids(args.config, overrides)
    results = run_checks(_urllib_fetch(token), channel_ids)
    print(redact(render(results), token))
    return exit_code(results)


if __name__ == "__main__":
    raise SystemExit(main())
