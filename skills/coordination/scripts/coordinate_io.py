"""I/O helpers for the W3-3 coordination skill (Discord REST + gws read).

Everything here is side-effectful; the decision logic lives in
``automation.interop.coordination`` (pure, pytest-covered).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Final
from urllib.parse import quote
from urllib.request import Request, urlopen

API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://github.com/orientpine/autophagy-agents, 0)"
POLL_SECONDS = 3.0
GWS_TIMEOUT_S = 120
_LIVE_CALENDAR_SCRIPTS: Final = "/srv/autophagy-skills/live/calendar/scripts"


class CoordinationError(RuntimeError):
    """Driver refusal with a CLI exit code."""

    def __init__(self, message: str, exit_code: int = 3) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def _interop_runtime() -> Path:
    configured = os.environ.get("INTEROP_RUNTIME")
    if configured:
        return Path(configured).expanduser()
    root = Path("~/.hermes/autophagy-import").expanduser()
    if (root / "automation" / "interop").is_dir():
        return root
    return Path("~/.hermes/interop_runtime").expanduser()


def ensure_runtime() -> None:
    """Make ``automation.interop`` importable from the deployed runtime."""
    runtime = _interop_runtime()
    if str(runtime) not in sys.path:
        sys.path.insert(0, str(runtime))
    try:
        import automation.interop  # noqa: F401
    except ImportError:
        raise CoordinationError(f"interop runtime 불가 (INTEROP_RUNTIME={runtime})", 3) from None


def calendar_scripts() -> Path:
    path = Path(os.environ.get("CALENDAR_SCRIPTS", _LIVE_CALENDAR_SCRIPTS)).expanduser()
    if not (path / "calendar_cli.py").exists():
        raise CoordinationError(f"calendar 스킬 스크립트 없음: {path}", 3)
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
    return path


def interop_config() -> dict[str, str]:
    config = Path(os.environ.get("INTEROP_CONFIG", "~/.hermes/interop/config.json")).expanduser()
    try:
        payload = json.loads(config.read_text(encoding="utf-8"))
    except OSError:
        raise CoordinationError(f"interop config 읽기 실패: {config}", 3) from None
    agent_id = payload.get("agent_id")
    owner_id = payload.get("owner_id")
    if not isinstance(agent_id, str) or not isinstance(owner_id, str):
        raise CoordinationError("interop config에 agent_id/owner_id가 없습니다", 3)
    return {"agent_id": agent_id, "owner_id": owner_id}


def discord_bot_token() -> str:
    ensure_runtime()
    from automation.notice_bot import file_token, resolve_token

    token = resolve_token()
    if token:
        return token
    try:
        token = file_token(Path.home() / ".env.secrets", shell=True)
    except OSError:
        token = ""
    if token:
        return token
    raise CoordinationError("DISCORD_BOT_TOKEN 누락", 3)


def api(method: str, path: str, payload: dict | None = None) -> Any:
    token = discord_bot_token()
    request = Request(
        f"{API}{path}",
        data=None if payload is None else json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method=method,
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310
        body = response.read().decode("utf-8")
    return json.loads(body) if body else None


def team_channel_id() -> str:
    override = os.environ.get("COORD_TEAM_CHANNEL_ID", "")
    if override:
        return override
    guild = next(item for item in api("GET", "/users/@me/guilds") if item["name"] == "Autophagy Lab")
    return next(
        item["id"] for item in api("GET", f"/guilds/{guild['id']}/channels") if item["name"] == "team"
    )


def interop_channel_id() -> str:
    override = os.environ.get("COORD_INTEROP_CHANNEL_ID", "")
    if override:
        return override
    guild = next(item for item in api("GET", "/users/@me/guilds") if item["name"] == "Autophagy Lab")
    return next(
        item["id"]
        for item in api("GET", f"/guilds/{guild['id']}/channels")
        if item["name"] == "autophagy-agents"
    )


def owner_approval_channel(owner_id: str) -> str:
    """Resolve the configured owner's DM through the shared approval directory."""
    if owner_id != interop_config()["owner_id"]:
        raise CoordinationError("승인 소유자 id가 현재 interop 설정과 다릅니다", 3)
    import coordination_binding

    return coordination_binding.approval_directory().owner_dm()


def post_message(channel_id: str, content: str) -> str:
    message = api("POST", f"/channels/{channel_id}/messages", {"content": content})
    return str(message["id"])


def add_reaction(channel_id: str, message_id: str, emoji: str) -> None:
    """Pre-add one Discord reaction to a message for tap-to-confirm UX."""
    api("PUT", f"/channels/{channel_id}/messages/{message_id}/reactions/{quote(emoji, safe='')}/@me")


def poll_envelope(
    *,
    channel_id: str,
    correlation_id: str,
    intent: str,
    sender_id: str,
    timeout_s: float,
    payload_slot: str = "",
) -> Any | None:
    """Poll a channel for the matching §2 envelope; None means deadlock timeout.

    ``payload_slot`` disambiguates renegotiation rounds: confirm responses share
    intent+correlation, so the response must echo the exact proposed slot.
    """
    from automation.interop.delegation import parse_envelope

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for message in api("GET", f"/channels/{channel_id}/messages?limit=50"):
            envelope = parse_envelope(str(message.get("content", "")))
            if (
                envelope is not None
                and envelope.correlation_id == correlation_id
                and envelope.intent == intent
                and envelope.sender_id == sender_id
                and (not payload_slot or envelope.payload.get("slot") == payload_slot)
            ):
                return envelope
        time.sleep(min(POLL_SECONDS, max(0.1, deadline - time.monotonic())))
    return None


def busy_items(*, calendar_id: str, range_start: str, range_end: str) -> tuple[dict, ...]:
    """Read my own calendar (gate-free read) between the requested range."""
    import calendar_gate

    params = {
        "calendarId": calendar_id,
        "maxResults": 50,
        "orderBy": "startTime",
        "singleEvents": True,
        "timeMin": range_start,
        "timeMax": range_end,
    }
    result = subprocess.run(  # noqa: S603
        [calendar_gate.gws_bin(), "calendar", "events", "list", "--params",
         json.dumps(params, ensure_ascii=False)],
        capture_output=True, text=True, timeout=GWS_TIMEOUT_S, check=False,
        cwd=str(Path.home()),
    )
    if result.returncode != 0:
        raise CoordinationError(f"gws list 실패: {result.stderr.strip()[:200]}", 3)
    items = json.loads(result.stdout).get("items", [])
    return tuple(item for item in items if isinstance(item, dict))


def run_calendar_cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Invoke the W3-1 gated calendar CLI exactly as the agent would."""
    cli = calendar_scripts() / "calendar_cli.py"
    return subprocess.run(  # noqa: S603
        [sys.executable, str(cli), *args],
        capture_output=True, text=True, timeout=GWS_TIMEOUT_S + 60, check=False,
    )


def kst_label(slot_iso: str, duration_min: int) -> str:
    """Render a terse Korean KST time label (no calendar content)."""
    from datetime import timedelta

    begin = datetime.fromisoformat(slot_iso)
    finish = begin + timedelta(minutes=duration_min)
    weekday = "월화수목금토일"[begin.weekday()]
    return (
        f"{begin.strftime('%Y-%m-%d')} ({weekday}) "
        f"{begin.strftime('%H:%M')}~{finish.strftime('%H:%M')} KST"
    )


def obs(**fields: object) -> None:
    """Emit one machine-readable observation line for E2E judging."""
    print("OBS-JSON: " + json.dumps(fields, ensure_ascii=False, sort_keys=True), flush=True)
