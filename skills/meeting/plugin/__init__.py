"""Hermes user plugin: W2-3 meeting ingest pre-dispatch gate.

Veto-then-handle: when the OWNER explicitly requests meeting ingest with a
bounded ``!meeting`` command or trusted structured intent, this hook spawns the
deterministic meeting CLI and returns ``skip`` so the content NEVER enters the
main-agent context. File name, suffix, MIME type, and DM placement
only select a supported payload; none of them establish user intent. After a
trigger is detected the handler is fail-CLOSED: any internal error still
returns ``skip`` (constraint 6 outranks availability).

Non-triggering messages return ``None`` (not "allow") so later plugin hooks
(interop-protocol loop guard / kill switch) keep full authority.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypedDict
from urllib.error import HTTPError
from urllib.request import Request, urlopen

LOGGER: Final = logging.getLogger("autophagy.meeting_gate")
_DOC_SUFFIXES: Final = frozenset({".md", ".markdown", ".txt", ".pdf"})
_DOC_MIMES: Final = frozenset({"text/markdown", "text/plain", "application/pdf"})
_CACHE_PREFIX: Final = re.compile(r"^doc_[0-9a-f]{12}_")
_MEETING_COMMAND: Final = re.compile(r"^\s*!meeting(?=\s|$)", re.IGNORECASE)
MEETING_INTENT_METADATA_KEY: Final = "meeting_intent"
_CONFIG_PATH: Final = Path("~/.hermes/meeting/config.json").expanduser()
_LIVE_CLI: Final = "/srv/autophagy-skills/live/meeting/scripts/meeting_cli.py"
_CLI_PATH: Final = Path(os.environ.get("MEETING_CLI", _LIVE_CLI)).expanduser()
_INBOX: Final = Path("~/.hermes/meeting/inbox").expanduser()
_SPAWN_LOG_DIR: Final = Path("~/.hermes/meeting/logs").expanduser()
ENV_SECRETS: Final = Path("~/.env.secrets").expanduser()
#: 자식이 비밀 파일에서 물려받는 값 전부. 모델 호출은 Codex OAuth 하나뿐이고 그 자격증명은
#: API 키가 아니라 HOME 아래 저장소에 있으므로, 여기에 넣을 게이트웨이 키는 없다.
_CHILD_CREDENTIALS: Final = frozenset({"DISCORD_BOT_TOKEN"})

ACK_MESSAGE: Final = "회의록 접수 — 처리 중입니다 (수 분 내 결과 통지)."
ERROR_MESSAGE: Final = (
    "회의록 처리 시작에 실패했습니다. 안전을 위해 본문은 에이전트로 전달하지 않았습니다. "
    "잠시 후 다시 시도해 주세요."
)


class UnsupportedPythonError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Trigger:
    """One detected meeting ingest request."""

    chat_id: str
    doc_paths: tuple[str, ...]
    body: str | None
    message_id: str = ""


class MeetingConfig(TypedDict, total=False):
    owner_id: str
    python: str


def register(ctx) -> None:
    """Register the pre-dispatch hook."""
    ctx.register_hook("pre_gateway_dispatch", pre_gateway_dispatch)
    LOGGER.warning("meeting gate plugin registered")


def _config() -> MeetingConfig | None:
    try:
        raw = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    config = MeetingConfig()
    owner_id = raw.get("owner_id")
    python = raw.get("python")
    if isinstance(owner_id, str):
        config["owner_id"] = owner_id
    if isinstance(python, str):
        config["python"] = python
    return config


def _intent_text(event) -> str:
    """Return the original user caption when the adapter preserved it.

    Discord may prepend an inlined text attachment to ``event.text``.  A raw
    message with an empty caption is still authoritative: falling back in that
    case could promote a command copied inside the attachment to user intent.
    """

    raw_message = getattr(event, "raw_message", None)
    if raw_message is not None:
        if isinstance(raw_message, dict) and "content" in raw_message:
            return str(raw_message.get("content") or "")
        if hasattr(raw_message, "content"):
            return str(getattr(raw_message, "content") or "")
    return str(getattr(event, "text", "") or "")


def _normalized_mime(value: object) -> str:
    return str(value or "").partition(";")[0].strip().lower()


def _origin_message_id(event) -> str:
    """Discord id of the instruction message — anchors the result thread; "" when unexposed."""
    direct = getattr(event, "message_id", None)
    if direct:
        return str(direct)
    raw_message = getattr(event, "raw_message", None)
    if isinstance(raw_message, dict):
        return str(raw_message.get("id") or "")
    if raw_message is not None:
        return str(getattr(raw_message, "id", "") or "")
    return ""


def _decide(event, owner_id: str) -> Trigger | None:
    source = event.source
    if bool(getattr(source, "is_bot", False)):
        return None
    if str(getattr(source, "user_id", "") or "") != owner_id:
        return None
    text = _intent_text(event)
    command = _MEETING_COMMAND.match(text)
    metadata = getattr(event, "metadata", None)
    trusted_intent = (
        isinstance(metadata, dict)
        and metadata.get(MEETING_INTENT_METADATA_KEY) is True
    )
    if command is None and not trusted_intent:
        return None
    media_urls = list(getattr(event, "media_urls", None) or ())
    media_types = list(getattr(event, "media_types", None) or ())
    media_types += [""] * (len(media_urls) - len(media_types))
    docs = tuple(
        url
        for url, mime in zip(media_urls, media_types)
        if Path(url).suffix.lower() in _DOC_SUFFIXES
        or _normalized_mime(mime) in _DOC_MIMES
    )
    body = None
    if not docs:
        body = text[command.end() :].strip() if command is not None else text.strip()
    return Trigger(
        chat_id=str(getattr(source, "chat_id", "")),
        doc_paths=docs,
        body=body,
        message_id=_origin_message_id(event),
    )


def _label_for(path: str) -> str:
    return _CACHE_PREFIX.sub("", Path(path).name)


def child_environment() -> dict[str, str]:
    environment = dict(os.environ)
    try:
        lines = ENV_SECRETS.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        name, separator, value = line.partition("=")
        if separator and name in _CHILD_CREDENTIALS and name not in environment and value.strip():
            environment[name] = value.strip()
    environment["PATH"] = f"{Path('~/.local/bin').expanduser()}:{environment.get('PATH', '/usr/bin:/bin')}"
    # Codex OAuth 자격증명은 HOME 아래에서만 찾는다 — 자식에게 반드시 함께 간다.
    environment.setdefault("HOME", str(Path("~").expanduser()))
    return environment


def _spawn(argv: list[str]) -> None:
    _SPAWN_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = _SPAWN_LOG_DIR / f"spawn-{time.strftime('%Y%m%d')}.log"
    with log_path.open("ab") as log_handle:
        subprocess.Popen(  # noqa: S603
            argv,
            stdout=log_handle,
            stderr=log_handle,
            stdin=subprocess.DEVNULL,
            cwd=Path("~").expanduser(),
            env=child_environment(),
            start_new_session=True,
        )
    log_path.chmod(0o600)


def _launch(trigger: Trigger, python_bin: str) -> None:
    base = [python_bin, str(_CLI_PATH), "ingest", "--notify-channel", trigger.chat_id]
    if trigger.message_id:
        base += ["--notify-message-id", trigger.message_id]
    if trigger.doc_paths:
        for doc in trigger.doc_paths:
            _spawn([*base, "--file", doc, "--label", _label_for(doc)])
        return
    if trigger.body and trigger.body.strip():
        _INBOX.mkdir(parents=True, exist_ok=True)
        body_file = _INBOX / f"body-{time.strftime('%Y%m%dT%H%M%S')}-{os.getpid()}.txt"
        body_file.write_text(trigger.body, encoding="utf-8")
        body_file.chmod(0o600)
        _spawn([*base, "--body-file", str(body_file), "--label", "!meeting 본문"])
        return
    # `!meeting` 만 온 것은 오류가 아니다 — 아직 회의록이 없는 전사본을 처리하라는 요청이다.
    _spawn([*base, "--from-pending-transcript"])


def _discord_post(path: str, payload: dict) -> object:
    try:
        from automation.notice_bot import resolve_token
    except ImportError:
        # The standalone legacy plugin has no automation package. It may retain
        # its old sender only before cutover; an override must never be bypassed.
        try:
            (Path.home() / ".hermes/notice-bot.json").lstat()
        except FileNotFoundError:
            token = os.environ.get("DISCORD_BOT_TOKEN", "")
        else:
            print("NOTICE-BOT-UNAVAILABLE", file=sys.stderr)
            return None
    else:
        token = resolve_token()
    if not token:
        return None
    request = Request(
        f"https://discord.com/api/v10{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            "User-Agent": "DiscordBot (https://github.com/orientpine/autophagy-agents, 0)",
        },
        method="POST",
    )
    with urlopen(request, timeout=15) as response:  # noqa: S310
        body = response.read().decode("utf-8")
    return json.loads(body) if body else None


def _post(chat_id: str, content: str) -> None:
    if not chat_id:
        return
    _discord_post(f"/channels/{chat_id}/messages", {"content": content})


_THREAD_ICON = "🎙️"
_THREAD_NAME = "회의록 처리"


def _thread_for(chat_id: str, message_id: str) -> str:
    """Thread anchored on the instruction message; 400 means it already exists (id == message id).

    게이트웨이 플러그인 프로세스는 INTEROP_RUNTIME 경로를 보장받지 못하므로
    `automation.interop.origin_notice`의 앵커 규칙(메시지 스레드 우선, 400=재사용)을
    여기서 같은 의미로 적용한다 — 이름은 내용을 읽기 전이므로 내용 없는 고정값.
    """
    try:
        thread = _discord_post(
            f"/channels/{chat_id}/messages/{message_id}/threads", {"name": f"{_THREAD_ICON} {_THREAD_NAME}"}
        )
    except HTTPError as error:
        if error.code != 400:
            raise
        return message_id
    if not isinstance(thread, dict) or not thread.get("id"):
        return message_id
    thread_id = str(thread["id"])
    try:  # 새 스레드의 첫 메시지는 큰 제목(2026-10-16 소유자 지시) — 실패해도 ACK 는 간다
        _discord_post(f"/channels/{thread_id}/messages", {"content": f"## {_THREAD_ICON} {_THREAD_NAME}"})
    except Exception as error:  # noqa: BLE001
        print(f"THREAD-TITLE-FAIL thread={thread_id} err={type(error).__name__}", file=sys.stderr)
    return thread_id


def _ack_target(trigger: Trigger) -> str:
    """Where the ACK goes: the anchored thread when resolvable, else the channel (best-effort)."""
    if not trigger.message_id:
        return trigger.chat_id
    try:
        return _thread_for(trigger.chat_id, trigger.message_id)
    except Exception:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK — ACK routing must never block the intercept
        LOGGER.warning("meeting gate thread anchor failed; acking in channel")
        return trigger.chat_id


def pre_gateway_dispatch(event, gateway, session_store, **kwargs):
    """Detect meeting inputs from the owner and veto agent dispatch for them."""
    del gateway, session_store, kwargs
    config = _config()
    if config is None:
        return None
    owner_id = config.get("owner_id")
    if not owner_id:
        return None
    try:
        trigger = _decide(event, owner_id)
    except Exception:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK — detection must never break dispatch
        LOGGER.exception("meeting gate trigger detection failed")
        return None
    if trigger is None:
        return None
    # Triggered: fail CLOSED from here — content must not reach the main agent.
    try:
        _launch(trigger, str(config.get("python", "/usr/bin/python3")))
        _post(_ack_target(trigger), ACK_MESSAGE)
        LOGGER.warning(
            "meeting gate intercepted docs=%d body=%s",
            len(trigger.doc_paths),
            trigger.body is not None,
        )
    except Exception:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK — triggered content must remain fail-closed
        LOGGER.exception("meeting gate launch failed (still skipping dispatch)")
        try:
            _post(_ack_target(trigger), ERROR_MESSAGE)
        except Exception:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK — error notification is best-effort
            LOGGER.exception("meeting gate error notify failed")
    return {"action": "skip", "reason": "meeting_ingest"}


if sys.version_info < (3, 10):  # pragma: no cover
    raise UnsupportedPythonError("meeting gate plugin requires Python 3.10+")
