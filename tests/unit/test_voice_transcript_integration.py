"""실제 ffmpeg·whisper.cpp 로 짧은 한국어 음성을 전사한다 — 노드 도구가 있을 때만 돈다.

`tests/fixtures/voice/ko-meeting-request.ogg` 는 합성 음성(소유자·타인의 목소리 아님)이다.
도구 경로는 노드와 같은 `SPEECHTOTEXT_WHISPER_BIN`·`SPEECHTOTEXT_WHISPER_MODEL` 로 준다
(단위 격리가 HOME 을 바꾸므로 `~/.env.secrets` 는 읽지 않는다). CI 처럼 도구가 없으면 건너뛴다.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from automation.interop import voice_transcript

SAMPLE = Path(__file__).resolve().parents[1] / "fixtures" / "voice" / "ko-meeting-request.ogg"
TOOLCHAIN = voice_transcript.resolve_toolchain(os.environ, Path("/nonexistent"))


@pytest.mark.skipif(TOOLCHAIN is None, reason="local whisper.cpp toolchain not configured")
def test_a_korean_voice_message_is_transcribed_locally(tmp_path: Path) -> None:
    assert TOOLCHAIN is not None
    clip = tmp_path / SAMPLE.name
    shutil.copyfile(SAMPLE, clip)

    result = voice_transcript.transcribe(clip, TOOLCHAIN)

    assert isinstance(result, voice_transcript.Transcribed), result
    assert "회의" in result.text and "일정" in result.text, result.text
