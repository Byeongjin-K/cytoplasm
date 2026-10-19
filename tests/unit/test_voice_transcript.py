"""소유자 음성 메시지 → 로컬 한국어 전사 — 게이트웨이 훅 경계를 가짜 도구로 고정한다.

지키는 것: 소유자(사람, interop owner_id)의 음성만 전사하고, 전사문은 표식을 단 소유자 글이 되며,
전사에 실패하면 에이전트에게 넘기지 않고(skip) 알린다. 다른 사람·봇의 오디오는 그대로 둔다.
"""

from __future__ import annotations

import enum
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from automation.interop import voice_transcript

OWNER = "100000000000000001"
STRANGER = "100000000000000002"
CHANNEL = "100000000000000020"


class MessageType(enum.Enum):
    TEXT = "text"
    AUDIO = "audio"
    VOICE = "voice"


@dataclass
class Source:
    user_id: str = OWNER
    is_bot: bool = False
    chat_id: str = CHANNEL
    thread_id: str | None = None
    chat_type: str = "group"


@dataclass
class Event:
    text: str
    message_type: MessageType
    media_urls: list[str]
    media_types: list[str]
    source: Source = field(default_factory=Source)
    message_id: str = "100000000000000099"


@dataclass
class FakeTools:
    transcript: str = " 내일 오후 3시에 회의 일정을 잡아줘.\n"
    whisper_rc: int = 0
    duration: str = "3.68"
    calls: list[str] = field(default_factory=list)

    def __call__(self, argv: Sequence[str], timeout: float) -> subprocess.CompletedProcess[str]:
        del timeout
        tool = Path(argv[0]).name
        self.calls.append(tool)
        if tool == "ffprobe":
            return subprocess.CompletedProcess(argv, 0, self.duration, "")
        if tool == "ffmpeg":
            Path(argv[-1]).write_bytes(b"RIFF")
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, self.whisper_rc, self.transcript, "")


def _executable(path: Path) -> Path:
    path.write_text("#!/bin/sh\nexit 97\n", encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture
def node(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    tools = tmp_path / "bin"
    tools.mkdir()
    for name in ("ffmpeg", "ffprobe"):
        _executable(tools / name)
    whisper = _executable(tools / "whisper-cli")
    model = tmp_path / "ggml-large-v3-turbo-q5_0.bin"
    model.write_bytes(b"model")
    hermes_home = tmp_path / "hermes"
    cache = hermes_home / "cache" / "audio"
    cache.mkdir(parents=True)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    monkeypatch.setenv("PATH", str(tools))
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("SPEECHTOTEXT_WHISPER_BIN", str(whisper))
    monkeypatch.setenv("SPEECHTOTEXT_WHISPER_MODEL", str(model))
    monkeypatch.delenv(voice_transcript.DISABLE_ENV, raising=False)
    return {"cache": cache, "scratch": scratch, "whisper": whisper, "model": model}


def _voice(node: dict[str, Path], **source: object) -> Event:
    clip = node["cache"] / "audio_0123abcd.ogg"
    clip.write_bytes(b"OggS")
    return Event(
        text=voice_transcript.PLACEHOLDER,
        message_type=MessageType.VOICE,
        media_urls=[str(clip)],
        media_types=["audio/ogg"],
        source=Source(**source),  # type: ignore[arg-type]
    )


def test_owner_voice_becomes_a_marked_owner_message(node: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    tools = FakeTools()
    monkeypatch.setattr(voice_transcript, "_run", tools)
    event = _voice(node)
    urls = event.media_urls

    result = voice_transcript.gateway_voice(event, OWNER)

    assert result == {"action": "rewrite", "text": f"{voice_transcript.MARKER} 내일 오후 3시에 회의 일정을 잡아줘."}
    assert event.text == result["text"], "an earlier plugin's allow must not hide the transcript"
    assert urls == [] and event.media_urls is urls, "busy path reads the same list — strip in place"
    assert event.media_types == []
    assert event.message_type is MessageType.TEXT
    assert tools.calls == ["ffprobe", "ffmpeg", "whisper-cli"]
    assert list(node["cache"].iterdir()) == [], "the cached voice file is deleted"
    assert list(node["scratch"].iterdir()) == [], "the converted wav is deleted"


def test_the_owner_caption_follows_the_transcript(node: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    event = _voice(node)
    event.text = "참고로 장소는 본관"

    result = voice_transcript.gateway_voice(event, OWNER, run=FakeTools(transcript="회의 잡아줘"))

    assert result is not None
    assert result["text"] == f"{voice_transcript.MARKER} 회의 잡아줘\n\n참고로 장소는 본관"


def test_a_spoken_kill_switch_is_only_text(node: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(voice_transcript, "_run", FakeTools(transcript="!pause-agents"))

    result = voice_transcript.gateway_voice(_voice(node), OWNER)

    assert result is not None and result["action"] == "rewrite"
    assert result["text"] == f"{voice_transcript.MARKER} !pause-agents"


@pytest.mark.parametrize(
    ("source", "message_type"),
    [
        ({"user_id": STRANGER}, MessageType.VOICE),
        ({"user_id": OWNER, "is_bot": True}, MessageType.VOICE),
        ({"user_id": OWNER}, MessageType.AUDIO),
    ],
    ids=["another-person", "bot-with-owner-id", "attached-audio-file"],
)
def test_audio_that_is_not_an_owner_voice_message_is_untouched(
    node: dict[str, Path], source: dict[str, object], message_type: MessageType
) -> None:
    tools = FakeTools()
    event = _voice(node, **source)
    event.message_type = message_type
    before = (event.text, list(event.media_urls), list(event.media_types), event.message_type)

    assert voice_transcript.gateway_voice(event, OWNER, run=tools) is None
    assert (event.text, event.media_urls, event.media_types, event.message_type) == before
    assert tools.calls == []
    assert len(list(node["cache"].iterdir())) == 1


def test_an_owner_text_message_is_untouched(node: dict[str, Path]) -> None:
    event = Event(text="회의 잡아줘", message_type=MessageType.TEXT, media_urls=[], media_types=[])

    assert voice_transcript.gateway_voice(event, OWNER, run=FakeTools()) is None
    assert event.text == "회의 잡아줘"


def test_the_switch_returns_voice_to_the_gateway_default(node: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(voice_transcript.DISABLE_ENV, "0")
    event = _voice(node)

    assert voice_transcript.gateway_voice(event, OWNER, run=FakeTools()) is None
    assert event.media_urls != []


@pytest.mark.parametrize(
    ("tools", "reason", "whisper_ran"),
    [
        (FakeTools(whisper_rc=1), "asr-failed", True),
        (FakeTools(transcript=" [BLANK_AUDIO]\n"), "empty", True),
        (FakeTools(duration="901.5"), "too-long", False),
    ],
    ids=["whisper-error", "silence", "too-long"],
)
def test_a_failed_transcription_is_never_dispatched(
    node: dict[str, Path], tools: FakeTools, reason: str, whisper_ran: bool
) -> None:
    notices: list[str] = []
    event = _voice(node)

    result = voice_transcript.gateway_voice(event, OWNER, run=tools, notify=lambda _event, why: notices.append(why))

    assert result == {"action": "skip", "reason": voice_transcript.SKIP_REASON}
    assert notices == [reason]
    assert ("whisper-cli" in tools.calls) is whisper_ran
    assert event.media_urls == [], "no fallback STT may guess from the same audio"
    assert list(node["cache"].iterdir()) == []
    assert list(node["scratch"].iterdir()) == []


def test_a_missing_model_is_reported_not_replaced(node: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    node["model"].unlink()
    notices: list[str] = []

    result = voice_transcript.gateway_voice(
        _voice(node), OWNER, run=FakeTools(), notify=lambda _event, why: notices.append(why),
        secrets_path=node["scratch"] / "absent",
    )

    assert result is not None and result["action"] == "skip"
    assert notices == ["toolchain-missing"]


def test_the_toolchain_falls_back_to_the_account_secrets_file(node: dict[str, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SPEECHTOTEXT_WHISPER_BIN")
    monkeypatch.delenv("SPEECHTOTEXT_WHISPER_MODEL")
    secrets = tmp_path / "env.secrets"
    secrets.write_text(
        f"OTHER=1\nSPEECHTOTEXT_WHISPER_BIN={node['whisper']}\nSPEECHTOTEXT_WHISPER_MODEL={node['model']}\n",
        encoding="utf-8",
    )
    import os

    toolchain = voice_transcript.resolve_toolchain(os.environ, secrets)

    assert toolchain is not None
    assert (toolchain.whisper, toolchain.model, toolchain.language) == (node["whisper"], node["model"], "ko")


def test_only_files_inside_the_hermes_audio_cache_are_deleted(tmp_path: Path) -> None:
    cache = tmp_path / "cache" / "audio"
    cache.mkdir(parents=True)
    inside = cache / "voice.ogg"
    outside = tmp_path / "owner-recording.ogg"
    for path in (inside, outside):
        path.write_bytes(b"OggS")

    assert voice_transcript.discard_cached([str(inside), str(outside)], (cache,)) == 1
    assert not inside.exists() and outside.exists()


def test_the_failure_notice_says_nothing_was_executed() -> None:
    text = voice_transcript.notice_text("asr-failed")

    assert "`asr-failed`" in text
    assert "실행된 것은 없습니다" in text
