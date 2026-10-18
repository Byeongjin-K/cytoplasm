"""Account-local driver used by the W1-5 shell gate."""

from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

from automation.interop.delegation import (
    InteropEnvelope,
    format_envelope,
    parse_envelope,
)
from automation.interop.discord_transport import DiscordTransport
from automation.interop.report import (
    ReportStatus,
    TaskReport,
    format_report,
    parse_report,
)

API = "https://discord.com/api/v10"


def req(path: str, data: dict[str, str] | None = None):
    from automation.notice_bot import resolve_token

    headers = {
        "Authorization": f"Bot {resolve_token(required=True)}",
        "Content-Type": "application/json",
        "User-Agent": "DiscordBot (https://github.com/orientpine/autophagy-agents, 0)",
    }
    request = Request(
        API + path,
        data=None if data is None else json.dumps(data).encode(),
        headers=headers,
        method="GET" if data is None else "POST",
    )
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode())


def config():
    return json.loads(Path("~/.hermes/interop/config.json").expanduser().read_text())


def channel(name: str) -> str:
    guild = next(x for x in req("/users/@me/guilds") if x["name"] == "Autophagy Lab")
    return next(x["id"] for x in req(f"/guilds/{guild['id']}/channels") if x["name"] == name)


def _result_notice_target(token: str) -> str:
    """Resolve the configured notice channel, falling back through the notice facade (ON-2)."""
    from automation.owner_notice import resolve_notice_target

    target = resolve_notice_target(token)
    if not target:
        raise RuntimeError("owner notice target is unavailable")
    return target


def main() -> None:
    from automation.notice_bot import resolve_token

    role, phase, round_id = sys.argv[1:4]
    c = config()
    token = resolve_token(required=True)

    if phase == "a":
        thread = channel("team")
        corr = f"w1-5-{round_id}-{uuid.uuid4().hex[:8]}"
        q = InteropEnvelope(
            corr,
            "agent-cha",
            "peer-test",
            "query_availability",
            {"duration_min": 1},
        )
        req(f"/channels/{thread}/messages", {"content": format_envelope(q)})
        end = time.monotonic() + 90
        response = False
        while time.monotonic() < end:
            response = any(
                (e := parse_envelope(m.get("content", ""))) is not None
                and e.correlation_id == corr
                and e.intent == "response_availability"
                for m in req(f"/channels/{thread}/messages?limit=50")
            )
            if response:
                break
            time.sleep(2)

        notice_target = _result_notice_target(token)
        delivered = False
        dm_end = time.monotonic() + 30
        while time.monotonic() < dm_end:
            messages = req(f"/channels/{notice_target}/messages?limit=50")
            delivered = any(
                f"Interop delegation result: {corr}" in m.get("content", "")
                for m in messages
            )
            if delivered:
                break
            time.sleep(2)

        print(
            json.dumps(
                {
                    "scenario": "A",
                    "round": round_id,
                    "response": response,
                    "dm": delivered,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
            )
        )
        return

    task = f"w1-5-{role}-{round_id}"
    report = TaskReport(
        c["agent_id"],
        task,
        ReportStatus.DONE,
        "W1-5 gate",
        (),
        __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )
    DiscordTransport(token, c["agents_log_channel_id"]).send(format_report(report))
    time.sleep(2)
    peer = "peer-test" if role == "agent" else "agent-cha"
    other = f"w1-5-{'peer' if role == 'agent' else 'agent'}-{round_id}"
    parsed = any(
        (r := parse_report(m.get("content", ""))) is not None
        and r.agent_id == peer
        and r.task_id == other
        for m in req(f"/channels/{c['agents_log_channel_id']}/messages?limit=100")
    )
    print(
        json.dumps(
            {
                "scenario": "B",
                "round": round_id,
                "parsed_other": parsed,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        )
    )


if __name__ == "__main__":
    sys.exit(main())
