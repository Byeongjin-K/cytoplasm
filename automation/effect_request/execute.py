"""What runs after the owner's ✅: a Drive save or a frozen command, each exactly once.

Both executors re-derive the action hash from what is on disk before anything leaves the
node, so a frozen file or argv that changed after the card was posted runs nothing.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Final

from .model import COMMAND, DRIVE_SAVE, EffectRequest, EffectRequestError, action_hash, file_sha256

Publisher = Callable[..., object]
Runner = Callable[..., subprocess.CompletedProcess[str]]

_EMAIL: Final = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_LONG_DIGITS: Final = re.compile(r"\d{7,}")
_SECRET: Final = re.compile(r"(?i)(token|secret|password|key)\s*[=:]\s*\S+")
TAIL_CHARS: Final = 400


def mask(text: str) -> str:
    return _LONG_DIGITS.sub("[번호]", _EMAIL.sub("[메일주소]", _SECRET.sub(r"\1=[가림]", text)))


def verify_binding(record: EffectRequest) -> None:
    if action_hash(record.effect, record.summary, record.payload) != record.action_hash:
        raise EffectRequestError("요청 내용이 승인 카드의 해시와 다르다 — 실행하지 않았다")


def _frozen_file(record: EffectRequest) -> Path:
    path = Path(str(record.payload["frozen_path"]))
    try:
        digest, size = file_sha256(path)
    except OSError as error:
        raise EffectRequestError("동결한 파일을 읽을 수 없다 — 저장하지 않았다") from error
    if (digest, size) != (record.payload["sha256"], record.payload["size"]):
        raise EffectRequestError("동결한 파일이 승인 뒤 바뀌었다 — 저장하지 않았다")
    return path


def save_to_drive(record: EffectRequest, publisher: Publisher) -> dict[str, object]:
    """Upload the frozen copy owner-only to the bound folder; the facade reads it back by sha256."""
    path = _frozen_file(record)
    payload = record.payload
    try:
        result = publisher(
            str(payload["kind"]), str(payload["title"]), [(path, str(payload["title"]))],
            on=date.fromisoformat(str(payload["on"])),
            project=payload.get("project") or None,
        )
    except Exception as error:  # noqa: BLE001 - every facade failure becomes a retryable receipt
        raise EffectRequestError(f"Drive 저장 실패: {type(error).__name__}: {mask(str(error))[:200]}",
                                 retryable=True) from error
    links = tuple(getattr(result, "links", ()) or ())
    return {"links": list(links), "action": str(getattr(result, "action", "")),
            "sha256": payload["sha256"], "owner_only": True}


def run_command(record: EffectRequest, runner: Runner = subprocess.run) -> dict[str, object]:
    """Run the frozen argv once; a non-zero exit is a final failure, never re-run."""
    payload = record.payload
    argv = [str(item) for item in payload["argv"]]
    try:
        completed = runner(
            argv, cwd=str(payload["cwd"]), capture_output=True, text=True,
            timeout=int(payload["timeout"]), env=dict(os.environ), check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise EffectRequestError(f"제한 시간 {payload['timeout']}초를 넘겨 멈췄다") from error
    except OSError as error:
        raise EffectRequestError(f"명령을 시작하지 못했다: {type(error).__name__}") from error
    tail = mask((completed.stdout or "") + (completed.stderr or ""))[-TAIL_CHARS:]
    receipt: dict[str, object] = {"returncode": completed.returncode, "tail": tail}
    if completed.returncode != 0:
        raise EffectRequestError(f"종료 코드 {completed.returncode}: {tail[-200:]}")
    return receipt


def execute(record: EffectRequest, *, publisher: Publisher, runner: Runner = subprocess.run) -> dict[str, object]:
    verify_binding(record)
    if record.effect == DRIVE_SAVE:
        return save_to_drive(record, publisher)
    if record.effect == COMMAND:
        return run_command(record, runner)
    raise EffectRequestError(f"알 수 없는 작업 종류: {record.effect}")
