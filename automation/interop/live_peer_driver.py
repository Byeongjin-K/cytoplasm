"""Drive masked live bot-message sequences for W1-6 QA."""

from __future__ import annotations

import json
import os
import time
from urllib.request import Request, urlopen


API = "https://discord.com/api/v10"


def main() -> None:
    """Create two peer-authored threads and post rate-limit and duplicate probes."""
    from automation.notice_bot import resolve_token

    token = resolve_token(required=True)
    agent_id = os.environ["INTEROP_AGENT_BOT_ID"]
    guild = next(item for item in _get(token, "/users/@me/guilds") if item["name"] == "Autophagy Lab")
    team = next(item for item in _get(token, f"/guilds/{guild['id']}/channels") if item["name"] == "team")
    rate_thread = _thread(token, team["id"], "w1-6-rate")
    duplicate_thread = _thread(token, team["id"], "w1-6-duplicate")
    mention = f"<@{agent_id}>"
    for index in range(6):
        _post(token, f"/channels/{rate_thread}/messages", {"content": f"W1-6-rate-{index} {mention}"})
        time.sleep(2)
    _post(token, f"/channels/{duplicate_thread}/messages", {"content": f"W1-6-duplicate {mention}"})
    _post(token, f"/channels/{duplicate_thread}/messages", {"content": f"W1-6-duplicate {mention}"})
    print(json.dumps({"rate_messages": 6, "duplicate_messages": 2}))


def _thread(token: str, parent_channel_id: str, name: str) -> str:
    root = _post(token, f"/channels/{parent_channel_id}/messages", {"content": f"{name} root"})
    thread = _post(
        token,
        f"/channels/{parent_channel_id}/messages/{root['id']}/threads",
        {"name": name, "auto_archive_duration": 60},
    )
    return thread["id"]


def _get(token: str, path: str) -> list[dict[str, str]]:
    payload = _request(token, path, None)
    if not isinstance(payload, list):
        raise ValueError("Discord GET response is not a list")
    return payload


def _post(token: str, path: str, payload: dict[str, str]) -> dict[str, str]:
    response = _request(token, path, payload)
    if not isinstance(response, dict):
        raise ValueError("Discord POST response is not an object")
    return response


def _request(token: str, path: str, payload: dict[str, str] | None) -> dict[str, str] | list[dict[str, str]]:
    request = Request(
        f"{API}{path}",
        data=None if payload is None else json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            "User-Agent": "DiscordBot (https://github.com/orientpine/autophagy-agents, 0)",
        },
        method="GET" if payload is None else "POST",
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310
        decoded = json.loads(response.read().decode("utf-8"))
    if isinstance(decoded, dict) or isinstance(decoded, list):
        return decoded
    raise ValueError("Discord response is not JSON object or array")


if __name__ == "__main__":
    main()
