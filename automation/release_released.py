"""이미 서명 태그로 나간 릴리스의 승인 레코드를 알아보고, 잘못 회수된 것에 정정 답글을 단다.

retire 는 지난 릴리스 레코드를 다음 요청 전에 Discord 반응을 다시 읽어 판정한다. 실행 뒤 카드의
소유자 ✅ 가 사라지면 그 판정은 ``bound_pending`` 이 되어, 이미 배포된 릴리스가 history 대신
"낡은 요청"으로 회수되고 카드에 "(취소됨)" 답글이 붙었다(2026-10-17 v1.46.1). 서명 태그는
✅ 뒤에만 잘리고 사라지지 않으므로, 태그가 그 레코드의 version·head 를 가리키면 반응과 무관하게
실행 완료다. 태그 목록은 release.sh 가 ``--released v=sha,…`` 로 넘긴다.
"""
from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable, Mapping
from http.client import HTTPException
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from automation.interop.owner_message import OwnerMessage

ABANDONED_DIRNAME: Final = "release-abandoned"
CORRECTED_SUFFIX: Final = ".corrected"
_ENTRY: Final = re.compile(r"v\d+\.\d+\.\d+=[0-9a-f]{40}")


class ReleasedTagsError(ValueError):
    """``--released`` 값이 ``v<semver>=<40hex>`` 목록이 아니다."""


def parse_released(text: str) -> dict[str, str]:
    entries = [part for part in text.split(",") if part]
    for entry in entries:
        if _ENTRY.fullmatch(entry) is None:
            raise ReleasedTagsError(f"RELEASE-RETIRE-BLOCK: --released entry is malformed: {entry[:60]}")
    return dict(entry.split("=", 1) for entry in entries)


def is_released(record: Mapping[str, str], released: Mapping[str, str]) -> bool:
    head = record.get("head_sha", "")
    return bool(head) and released.get(record.get("version", "")) == head


def correction_message(record: Mapping[str, str]) -> OwnerMessage | None:
    try:
        from automation.interop.owner_message import Action, OwnerMessage, Result
        from automation.release_retire import _approval_location as approval_location
    except Exception:  # noqa: BLE001 - optional module initialization must preserve string delivery
        return None
    return OwnerMessage(
        subject_key=record["version"], subject="릴리스 승인 회수 정정",
        fact=(f"{record['version']} 은 이미 배포됨({record['head_sha'][:12]}) · 위 회수는 기록 정리이며 "
              "배포는 취소되지 않음 · 이 카드에 지금 소유자 ✅ 반응 없음"),
        location=approval_location(record), owner=Action(verb="none"),
        agent_next="추가 실행 없음", recovery="not_applicable", detail=Result(outcome="executed"),
    )


Poster = Callable[[Mapping[str, str]], None]


def _post(record: Mapping[str, str]) -> None:
    from automation import skill_gate

    content = (f"✅ 정정 — {record['version']} 은 이미 배포됨({record['head_sha'][:12]}). "
               "위 회수는 기록 정리이며 배포는 취소되지 않았다. 이 카드에 지금 소유자 ✅ 반응 없음.")
    message = correction_message(record)
    if message is not None:
        from automation.interop.owner_message import Ref, render

        content = render(message, destination=Ref(scope="channel", channel_id=record["channel_id"]))
    skill_gate._api("POST", f"/channels/{record['channel_id']}/messages", {
        "content": content,
        "message_reference": {"message_id": record["message_id"], "fail_if_not_exists": True},
        "allowed_mentions": {"parse": [], "replied_user": False},
    })


def correct_abandoned(gate_dir: Path, released: Mapping[str, str], post: Poster = _post) -> int:
    """이미 태그된 릴리스의 회수 기록마다 정정 답글을 한 번만 단다. 반환값은 단 답글 수."""
    corrected = 0
    for path in sorted((gate_dir / ABANDONED_DIRNAME).glob("*.json")):
        marker = path.with_name(path.name + CORRECTED_SUFFIX)
        if marker.exists():
            continue
        try:
            decoded = json.loads(path.read_bytes())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(decoded, dict):
            continue
        record = {str(name): str(value) for name, value in decoded.items()}
        if not is_released(record, released) or not record.get("channel_id") or not record.get("message_id"):
            continue
        try:
            post(record)
        except (OSError, HTTPException, ValueError, KeyError, SystemExit) as error:
            print(f"RELEASE-ABANDON-CORRECT-FAIL version={record['version']} {type(error).__name__}", file=sys.stderr)
            continue
        marker.touch(mode=0o600)
        corrected += 1
        print(f"RELEASE-ABANDON-CORRECTED version={record['version']} head={record['head_sha'][:12]}", file=sys.stderr)
    return corrected
