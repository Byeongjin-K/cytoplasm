"""소유자 음성 메시지 — 노드의 로컬 whisper.cpp 로 한국어 전사해 소유자가 친 글로 넘긴다.

소유자 지시(2026-10-12): 소유자 대화 채널에 보이스로 보내면 로컬 전사 모델로 한국어로 바꿔
에이전트가 소유자가 쓴 글처럼 보게 한다. Hermes 게이트웨이도 음성을 자동 전사하지만 그 경로는
faster-whisper `base` 로 언어를 추정해 한국어를 영어로 잡고(노드 로그 `lang=en`), 실패하면 같은
약한 모델로 다시 시도한다 — 추측으로 행동하게 된다. 그래서 소유자의 음성만 여기서 가로챈다.

- **누구의 음성인가**: 봇이 아니고 작성자 id 가 interop `owner_id` 인 사람의 음성 메시지뿐이다.
  다른 사람·봇의 오디오는 건드리지 않는다(이 모듈은 None 을 돌려주고 이벤트는 그대로 간다).
- **무엇으로 전사하나**: speechtotext 스킬과 같은 노드 도구 — `SPEECHTOTEXT_WHISPER_BIN`·
  `SPEECHTOTEXT_WHISPER_MODEL`(게이트웨이 env 에 없으면 계정 `~/.env.secrets` 에서 읽는다),
  ffmpeg 로 16 kHz mono wav 로 바꾼 뒤 `-l ko`. 네트워크를 쓰지 않는다 — 원음은 노드 밖으로 나가지 않는다.
- **에이전트가 보는 것**: `[음성 메시지 · 로컬 전사] <전사문>` (감사용 표식). 표식이 앞에 붙으므로
  전사문이 kill-switch 명령이나 프로토콜 봉투로 읽힐 수 없다 — 타이핑한 소유자 글보다 더 많은 것을
  허락하지 않는다. 승인은 여전히 카드의 ✅ 리액션뿐이다.
- **실패하면**: 그 메시지를 에이전트에게 넘기지 않고(skip) 메시지가 온 자리(스레드·DM)에 알린다.
  추측한 내용으로 행동하지 않는다.
- **임시 오디오**: 변환한 wav 는 작업 디렉터리째 지우고, 게이트웨이가 캐시한 원본 음성 파일도
  Hermes 오디오 캐시 안에 있을 때만 지운다. 전사문은 로그에 남기지 않는다(길이·시간만).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

LOGGER: Final = logging.getLogger("autophagy.interop")

MARKER: Final = "[음성 메시지 · 로컬 전사]"
SKIP_REASON: Final = "voice_transcription_failed"
#: Discord 어댑터가 본문 없는 첨부 메시지에 넣는 자리표시 문구 — 전사문이 있으면 버린다.
PLACEHOLDER: Final = "(The user sent a message with no text content)"
#: 이보다 긴 음성은 대화 메시지가 아니라 녹음이다 — speechtotext·meeting 경로로 보낸다.
MAX_SECONDS: Final = 600.0
#: 이 훅은 게이트웨이 이벤트 루프에서 동기로 돈다. 한 단계가 이보다 오래 걸리면 실패로 닫는다.
STEP_TIMEOUT_SECONDS: Final = 120.0
DEFAULT_LANGUAGE: Final = "ko"
DISABLE_ENV: Final = "AUTOPHAGY_VOICE_TRANSCRIBE"
THREAD_NAME: Final = "음성 메시지"
ENV_SECRETS: Final = Path("~/.env.secrets")
_TOOL_KEYS: Final = ("SPEECHTOTEXT_WHISPER_BIN", "SPEECHTOTEXT_WHISPER_MODEL", "SPEECHTOTEXT_LANGUAGE")
#: Hermes `_event_media_is_stt_input` 와 같은 판정 — 첨부 오디오 파일·문서는 전사 대상이 아니다.
_NOT_STT_TYPES: Final = frozenset({"audio", "document"})
#: whisper 가 무음 구간에 내는 표식 — 말이 아니다.
_NON_SPEECH_TOKENS: Final = ("[BLANK_AUDIO]", "[ Silence ]", "(silence)")
_REASONS: Final = {
    "toolchain-missing": "노드에 로컬 전사 도구(whisper.cpp·모델·ffmpeg)를 찾지 못했습니다",
    "too-long": f"음성이 {int(MAX_SECONDS // 60)}분보다 깁니다 — 긴 녹음은 전사본(speechtotext) 경로로 보내 주세요",
    "decode-failed": "음성 파일을 읽지 못했습니다",
    "asr-failed": "전사 모델이 오류로 끝났습니다",
    "timeout": "전사가 제한 시간 안에 끝나지 않았습니다",
    "empty": "알아들을 수 있는 말이 없었습니다",
}

Runner = Callable[[Sequence[str], float], subprocess.CompletedProcess[str]]
Notify = Callable[[object, str], None]


@dataclass(frozen=True, slots=True)
class Toolchain:
    whisper: Path
    model: Path
    ffmpeg: Path
    ffprobe: Path | None
    language: str


@dataclass(frozen=True, slots=True)
class Transcribed:
    text: str


@dataclass(frozen=True, slots=True)
class Failed:
    reason: str


def _run(argv: Sequence[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — argv 는 해석된 로컬 도구 경로와 파일 경로뿐이다
        list(argv), capture_output=True, text=True, timeout=timeout, check=False, stdin=subprocess.DEVNULL,
    )


def _secret_values(path: Path) -> dict[str, str]:
    try:
        lines = path.expanduser().read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    values: dict[str, str] = {}
    for line in lines:
        name, separator, value = line.strip().partition("=")
        if separator and name in _TOOL_KEYS and value.strip():
            values[name] = value.strip().strip("'\"")
    return values


def resolve_toolchain(env: Mapping[str, str], secrets_path: Path = ENV_SECRETS) -> Toolchain | None:
    """도구·모델 중 하나라도 없으면 None — 다른 모델로 대신하지 않는다."""
    values = {key: env.get(key, "").strip() for key in _TOOL_KEYS}
    if not all(values[key] for key in _TOOL_KEYS[:2]):
        stored = _secret_values(secrets_path)
        values = {key: values[key] or stored.get(key, "") for key in _TOOL_KEYS}
    whisper = Path(values["SPEECHTOTEXT_WHISPER_BIN"]).expanduser() if values["SPEECHTOTEXT_WHISPER_BIN"] else None
    model = Path(values["SPEECHTOTEXT_WHISPER_MODEL"]).expanduser() if values["SPEECHTOTEXT_WHISPER_MODEL"] else None
    search_path = env.get("PATH") or os.defpath
    ffmpeg = shutil.which("ffmpeg", path=search_path)
    ffprobe = shutil.which("ffprobe", path=search_path)
    if whisper is None or model is None or ffmpeg is None:
        return None
    if not (whisper.is_file() and os.access(whisper, os.X_OK) and model.is_file()):
        return None
    return Toolchain(
        whisper=whisper,
        model=model,
        ffmpeg=Path(ffmpeg),
        ffprobe=Path(ffprobe) if ffprobe else None,
        language=values["SPEECHTOTEXT_LANGUAGE"] or DEFAULT_LANGUAGE,
    )


def voice_indices(event: object) -> tuple[int, ...]:
    """전사할 첨부의 위치 — 음성 메시지이거나 오디오 MIME 이되 첨부 파일·문서 메시지는 아니다."""
    message_type = str(getattr(getattr(event, "message_type", None), "value", getattr(event, "message_type", "")))
    if message_type in _NOT_STT_TYPES:
        return ()
    urls = list(getattr(event, "media_urls", None) or ())
    types = list(getattr(event, "media_types", None) or ())
    return tuple(
        index
        for index in range(len(urls))
        if message_type == "voice" or (types[index] if index < len(types) else "").startswith("audio/")
    )


def is_owner(event: object, owner_id: str) -> bool:
    source = getattr(event, "source", None)
    return (
        bool(owner_id)
        and source is not None
        and not bool(getattr(source, "is_bot", False))
        and str(getattr(source, "user_id", "") or "") == owner_id
    )


def _duration(path: Path, toolchain: Toolchain, run: Runner) -> float | None:
    if toolchain.ffprobe is None:
        return None
    try:
        probed = run(
            [str(toolchain.ffprobe), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            STEP_TIMEOUT_SECONDS,
        )
        return float(probed.stdout.strip()) if probed.returncode == 0 else None
    except (subprocess.TimeoutExpired, ValueError, OSError):
        return None


def _clean(stdout: str) -> str:
    text = " ".join(line.strip() for line in stdout.splitlines() if line.strip())
    for token in _NON_SPEECH_TOKENS:
        text = text.replace(token, " ")
    return " ".join(text.split())


def transcribe(path: Path, toolchain: Toolchain, run: Runner | None = None) -> Transcribed | Failed:
    """음성 파일 하나를 한국어 텍스트로. 작업 디렉터리는 성공·실패 모두 지운다."""
    run = run or _run
    duration = _duration(path, toolchain, run)
    if duration is not None and duration > MAX_SECONDS:
        return Failed("too-long")
    work = Path(tempfile.mkdtemp(prefix="voice-asr-"))
    try:
        wav = work / "voice.wav"
        try:
            decoded = run(
                [str(toolchain.ffmpeg), "-nostdin", "-loglevel", "error", "-i", str(path),
                 "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)],
                STEP_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return Failed("timeout")
        if decoded.returncode != 0 or not wav.is_file():
            return Failed("decode-failed")
        try:
            heard = run(
                [str(toolchain.whisper), "-m", str(toolchain.model), "-l", toolchain.language,
                 "-nt", "-np", "-f", str(wav)],
                STEP_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return Failed("timeout")
        if heard.returncode != 0:
            return Failed("asr-failed")
        text = _clean(heard.stdout)
        return Transcribed(text) if text else Failed("empty")
    except OSError:
        return Failed("asr-failed")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def compose(transcripts: Sequence[str], caption: str) -> str:
    """에이전트가 볼 글 — 음성마다 표식 한 줄, 그 아래 소유자가 함께 쓴 글(있으면)."""
    parts = [f"{MARKER} {text}" for text in transcripts]
    caption = caption.strip()
    if caption and caption != PLACEHOLDER:
        parts.append(caption)
    return "\n\n".join(parts)


def notice_text(reason: str) -> str:
    why = _REASONS.get(reason, "전사하지 못했습니다")
    return (
        f"⚠️ 음성 메시지를 전사하지 못했습니다 — {why} (사유 코드 `{reason}`).\n"
        "이 음성으로 실행된 것은 없습니다. 다시 보내거나 글로 적어 주세요."
    )


def audio_cache_dirs(env: Mapping[str, str]) -> tuple[Path, ...]:
    home = Path(env.get("HERMES_HOME") or "~/.hermes").expanduser()
    return (home / "cache" / "audio", home / "audio_cache")


def discard_cached(paths: Sequence[str], cache_dirs: Sequence[Path]) -> int:
    """게이트웨이가 받아 둔 원본 음성을 지운다 — Hermes 오디오 캐시 안의 파일만."""
    roots = [root.resolve() for root in cache_dirs]
    removed = 0
    for raw in paths:
        try:
            target = Path(raw).resolve()
            if target.is_file() and any(target.is_relative_to(root) for root in roots):
                target.unlink()
                removed += 1
        except OSError as error:
            LOGGER.warning("interop voice cache cleanup failed error=%s", type(error).__name__)
    return removed


def _strip_voice(event: object, indices: Sequence[int]) -> None:
    """이벤트에서 전사한 음성을 빼고 글 메시지로 만든다 — 같은 리스트 객체를 고쳐 바쁜 경로도 같게 본다."""
    urls = getattr(event, "media_urls", None)
    types = getattr(event, "media_types", None)
    for index in sorted(indices, reverse=True):
        if isinstance(urls, list) and index < len(urls):
            del urls[index]
        if isinstance(types, list) and index < len(types):
            del types[index]
    current = getattr(event, "message_type", None)
    try:
        event.message_type = type(current)("text") if current is not None and not isinstance(current, str) else "text"  # type: ignore[attr-defined]
    except (TypeError, ValueError):
        event.message_type = "text"  # type: ignore[attr-defined]


def gateway_voice(
    event: object,
    owner_id: str,
    *,
    env: Mapping[str, str] | None = None,
    run: Runner | None = None,
    notify: Notify | None = None,
    secrets_path: Path = ENV_SECRETS,
) -> dict[str, str] | None:
    """게이트웨이 훅의 입구 — 소유자 음성이 아니면 None(이벤트는 그대로), 맞으면 rewrite 또는 skip."""
    if not is_owner(event, owner_id):
        return None
    indices = voice_indices(event)
    if not indices:
        return None
    env = os.environ if env is None else env
    if env.get(DISABLE_ENV) == "0":
        return None
    started = time.monotonic()
    paths = [str(getattr(event, "media_urls")[index]) for index in indices]
    toolchain = resolve_toolchain(env, secrets_path)
    outcome: list[str] | Failed
    if toolchain is None:
        outcome = Failed("toolchain-missing")
    else:
        heard: list[str] = []
        outcome = heard
        for path in paths:
            result = transcribe(Path(path), toolchain, run)
            if isinstance(result, Failed):
                outcome = result
                break
            heard.append(result.text)
    discard_cached(paths, audio_cache_dirs(env))
    _strip_voice(event, indices)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if isinstance(outcome, Failed):
        LOGGER.warning("interop voice transcription failed reason=%s clips=%d elapsed_ms=%d",
                       outcome.reason, len(paths), elapsed_ms)
        (notify or notify_failure)(event, outcome.reason)
        return {"action": "skip", "reason": SKIP_REASON}
    text = compose(outcome, str(getattr(event, "text", "") or ""))
    event.text = text  # type: ignore[attr-defined] — 앞선 플러그인의 allow 가 rewrite 를 가려도 같은 글을 본다
    LOGGER.warning("interop voice transcribed clips=%d chars=%d elapsed_ms=%d",
                   len(paths), sum(len(item) for item in outcome), elapsed_ms)
    return {"action": "rewrite", "text": text}


def notify_failure(event: object, reason: str) -> None:
    """메시지가 온 자리(스레드·DM, 채널이면 그 메시지에 단 스레드)에 알린다. 예외를 올리지 않는다."""
    source = getattr(event, "source", None)
    chat_id = str(getattr(source, "chat_id", "") or "")
    thread_id = str(getattr(source, "thread_id", "") or "")
    is_dm = getattr(source, "chat_type", "") == "dm"
    message_id = str(getattr(event, "message_id", None) or getattr(source, "message_id", None) or "")
    if not chat_id:
        return

    def deliver() -> None:
        try:
            from automation.interop import origin_notice, owner_proxy_watch

            runtime = owner_proxy_watch.live_runtime()
            if thread_id or is_dm or not message_id:
                target = thread_id or chat_id
            else:
                target = origin_notice.resolve_thread_id(
                    runtime.api, origin_notice.OriginRef(channel_id=chat_id, message_id=message_id), THREAD_NAME
                )
            runtime.send(target, notice_text(reason))
            LOGGER.warning("interop voice failure notified reason=%s", reason)
        except Exception as error:  # noqa: BLE001 — 통지 실패가 게이트웨이를 멈추지 않는다
            LOGGER.warning("interop voice failure notice failed error=%s", type(error).__name__)

    threading.Thread(target=deliver, daemon=True).start()
