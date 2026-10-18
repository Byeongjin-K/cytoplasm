from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .patent_export_manifest import Manifest

API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://github.com/orientpine/autophagy-agents, 0)"
APPROVE_EMOJI = "\u2705"
CANCEL_EMOJI = "\u26d4"
ENV_SECRETS = Path.home() / ".env.secrets"


class ExportGateError(RuntimeError):
    def __init__(self, message: str, exit_code: int = 3) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def owner_id() -> str:
    config = Path(os.environ.get("INTEROP_CONFIG", "~/.hermes/interop/config.json")).expanduser()
    try:
        owner = json.loads(config.read_text(encoding="utf-8")).get("owner_id")
    except OSError:
        raise ExportGateError(f"Failed to read interop config: {config}", 3) from None
    if not isinstance(owner, str) or not owner:
        raise ExportGateError("owner_id missing in interop config", 3)
    return owner


def bot_token() -> str:
    from . import patent_export_binding

    patent_export_binding.repo_module("approval_directory")
    from automation.notice_bot import file_token, resolve_token

    token = resolve_token()
    if token:
        return token
    try:
        token = file_token(ENV_SECRETS)
    except OSError:
        token = ""
    if token:
        return token
    raise ExportGateError("DISCORD_BOT_TOKEN missing", 3)


def discord_request(method: str, path: str, payload: dict | None = None) -> dict | list:
    """Public Discord seam for collaborators (the shared channel directory) to reuse."""
    return _api(method, path, payload)


def _api(method: str, path: str, payload: dict | None = None) -> dict | list:
    request = Request(
        f"{API}{path}",
        data=None if payload is None else json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bot {bot_token()}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method=method,
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310
        body = response.read().decode("utf-8")
    try:
        return json.loads(body) if body else {}
    except json.JSONDecodeError as error:
        raise ExportGateError("malformed Discord response") from error


def post_approval_request(channel_id: str, content: str) -> str:
    message = _api("POST", f"/channels/{channel_id}/messages", {"content": content})
    return str(message["id"])


def add_reaction(channel_id: str, message_id: str, emoji: str) -> None:
    _api(
        "PUT",
        f"/channels/{channel_id}/messages/{message_id}"
        f"/reactions/{quote(emoji, safe='')}/@me",
    )


def approval_message_content(channel_id: str, message_id: str) -> str | None:
    try:
        message = _api("GET", f"/channels/{channel_id}/messages/{message_id}")
    except HTTPError as error:
        if error.code == 404:
            return None
        raise ExportGateError("approval message read failed") from error
    except (URLError, OSError) as error:
        raise ExportGateError("approval message read failed") from error
    if not isinstance(message, dict):
        raise ExportGateError("Invalid approval message response", 1)
    content = message.get("content")
    return content if isinstance(content, str) and content else None


def approval_binding_matches(manifest: Manifest, content: str) -> bool:
    expected = {
        f"sha256: {manifest.plaintext_sha256}",
        f"dest_folder_id: {manifest.dest_folder_id}",
        f"expiry_ts: {manifest.expiry_ts}",
        f"mode={manifest.mode}",
    }
    return expected.issubset(set(content.splitlines()))


def delete_approval_request(channel_id: str, message_id: str) -> None:
    try:
        _api("DELETE", f"/channels/{channel_id}/messages/{message_id}")
    except HTTPError as error:
        if error.code != 404:
            raise ExportGateError("approval message delete failed") from error
    except (URLError, OSError) as error:
        raise ExportGateError("approval message delete failed") from error


def dm_owner(channel_id: str, content: str) -> str:
    """기존 DM·메시지 영수증 계약을 유지한다. 새 봉투에는 반출 위치를 싣지 않는다."""
    body = content
    try:
        from automation.interop.owner_message import Action, OwnerMessage, OwnerMessageError, Ref, Result, render
    except Exception:  # noqa: BLE001 - optional module initialization must preserve string delivery
        message = None
    else:
        message = OwnerMessage(
            subject_key="patent-export", subject="특허 반출", fact="반출 완료",
            location=Ref(scope="none"), owner=Action("none"),
            agent_next="추가 반출 없음", recovery="not_applicable", detail=Result("executed"),
        )
        try:
            body = render(message, destination=Ref(scope="channel", space="dm", channel_id=channel_id))
        except OwnerMessageError:
            body = content
    posted = _api("POST", f"/channels/{channel_id}/messages", {"content": body})
    return str(posted["id"])


def _owner_reacted(users: list[dict], owner: str) -> bool:
    return any(
        str(user.get("id", "")) == owner and not bool(user.get("bot", False))
        for user in users
    )


def _reaction_users(channel_id: str, message_id: str, emoji: str) -> list[dict]:
    try:
        users = _api(
            "GET",
            f"/channels/{channel_id}/messages/{message_id}"
            f"/reactions/{quote(emoji, safe='')}?limit=100",
        )
    except HTTPError as error:
        if error.code == 404:
            return []
        raise ExportGateError("reaction query failed") from error
    except (URLError, OSError) as error:
        raise ExportGateError("reaction query failed") from error
    if not isinstance(users, list) or not all(isinstance(user, dict) for user in users):
        raise ExportGateError("Invalid reaction response", 1)
    return users


def _binding_channel_id(manifest: Manifest) -> str:
    """The channel the manifest itself says its approval message lives in."""
    from . import patent_export_binding  # local: the bridge imports this module at load

    return patent_export_binding.stored_binding(manifest).channel_id


def reaction_state(manifest: Manifest) -> str | None:
    """Return the owner's bound reaction (⛔ precedence) or None if none yet.

    Fails CLOSED: raises ExportGateError when the approval message cannot be read
    or its sha256 binding cannot be confirmed, so callers never proceed on an
    unverified reaction (there is NO env shortcut that bypasses this check).
    """
    if not manifest.message_id:
        raise ExportGateError("manifest has no approval message", 1)
    channel_id = _binding_channel_id(manifest)
    content = approval_message_content(channel_id, manifest.message_id)
    if content is None:
        raise ExportGateError("approval message read failed")
    if not approval_binding_matches(manifest, content):
        raise ExportGateError("approval binding failed (sha/mode/dest/expiry)", 1)
    owner = owner_id()
    if _owner_reacted(_reaction_users(channel_id, manifest.message_id, CANCEL_EMOJI), owner):
        return CANCEL_EMOJI
    if _owner_reacted(_reaction_users(channel_id, manifest.message_id, APPROVE_EMOJI), owner):
        return APPROVE_EMOJI
    return None
