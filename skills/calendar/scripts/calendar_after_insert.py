"""What runs right after an approved insert, outside the frozen command and its hash.

1. 경합 정리(소유자 규칙 Q62, 2026-10-21): 실행 직전 검사를 지났는데도 같은 일정이 먼저
   생겨 있었다면(두 승인이 동시에 실행) 먼저 있던 것을 남기고 **방금 이 승인이 만든 것**을
   지운다. 방금 만든 일정에 참석자가 있으면 지우지 않고 삭제 승인 카드를 올려 소유자에게 묻는다.
2. 기본색 채우기: 동결 본문에 ``colorId`` 가 없고 노드 표식 ``calendar-default-color.mode``
   가 색을 가리키면, 방금 만든 일정에 ``colorId`` 하나만 patch 한다(``sendUpdates=none``).
   동결 argv·``draft_sha256``·카드 바이트는 그대로다 — 승인은 그 일정을 만드는 것이었고,
   색은 릴리스 승인으로 켠 노드의 표시 정책이다. 실패해도 생성은 되돌리지 않는다.

둘 다 이 승인이 만든 그 이벤트 하나에만 닿는다. 다른 일정은 읽기만 한다.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path

import calendar_core
import calendar_dedupe
import calendar_gate


def _gws(argv: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 -- fixed argv built here, one event id
        [calendar_gate.gws_bin(), *argv[1:]], capture_output=True, text=True,
        timeout=calendar_gate.GWS_TIMEOUT_S, check=False, cwd=str(calendar_gate._gate_dir()),
    )


def _audit(draft: Mapping[str, object], event_id: str, action: str, status: str) -> None:
    sealed = json.loads(calendar_gate._draft_path(str(draft["id"])).read_text(encoding="utf-8"))
    calendar_gate._append_record({
        "action": f"calendar.{action}",
        "approval": {"ref": str(sealed.get("approval_ref") or "")},
        "result": {"status": status},
        "target_id": f"event:{draft['calendar_id']}:{event_id}",
        "timestamp": calendar_gate.utc_now(),
    })


def _update(draft_id: str, fields: Mapping[str, object]) -> None:
    path = calendar_gate._draft_path(draft_id)
    record = json.loads(path.read_text(encoding="utf-8"))
    calendar_gate.write_json(path, {**record, **fields})


def _cjson(value: Mapping[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def settle_race(draft: Mapping[str, object], event_id: str) -> tuple[str, calendar_dedupe.Existing | None]:
    """Keep the earlier event; remove ours (``removed``), or ask by card when ours has attendees (``asked``)."""
    if not draft.get("start") or not draft.get("end"):
        return "", None
    events = calendar_dedupe.list_day_events(str(draft["calendar_id"]), str(draft["start"]))
    ours = next((event for event in events if event.get("id") == event_id), None)
    if ours is None:
        return "", None
    mine = (str(ours.get("created") or ""), event_id)
    earlier = sorted(
        (item for item in calendar_dedupe.duplicates(draft, events, exclude_id=event_id)
         if (item.created, item.event_id) < mine),
        key=lambda item: (item.created, item.event_id),
    )
    if not earlier:
        return "", None
    keep = earlier[0]
    calendar_id = str(draft["calendar_id"])
    if isinstance(ours.get("attendees"), list) and ours["attendees"]:
        card = _ask_delete(draft, event_id)
        _update(str(draft["id"]), {"duplicate_of": keep.record(), "cleanup_draft": card})
        print(f"DUPLICATE-CREATED-CARD draft={draft['id']} cleanup={card}")
        return "asked", keep
    result = _gws(("gws", "calendar", "events", "delete", "--params",
                   _cjson({"calendarId": calendar_id, "eventId": event_id, "sendUpdates": "none"})))
    if result.returncode != 0:
        _audit(draft, event_id, "dedupe_delete", "failed")
        raise calendar_gate.GateError("중복으로 생긴 새 일정을 지우지 못했습니다 — 다음 틱에 다시 봅니다", 6)
    _audit(draft, event_id, "dedupe_delete", "executed")
    _update(str(draft["id"]), {"status": "duplicate", "duplicate_of": keep.record(), "removed_event": event_id})
    print(f"DUPLICATE-REMOVED draft={draft['id']} event={event_id} kept={keep.event_id}")
    return "removed", keep


def _ask_delete(draft: Mapping[str, object], event_id: str) -> str:
    """An event with attendees is never removed silently: post the existing delete card."""
    import calendar_card
    import calendar_output

    record = calendar_gate.create_draft(
        action="delete", argv=calendar_core.build_delete_argv(str(draft["calendar_id"]), event_id),
        calendar_id=str(draft["calendar_id"]), event_id=event_id, summary=str(draft["summary"]),
        start="", end="", channel_id=str(draft.get("channel_id") or "dm"),
        origin_channel_id=str(draft.get("origin_channel_id") or ""),
        origin_message_id=str(draft.get("origin_message_id") or ""),
    )
    captured = calendar_card.capture_context(record)
    calendar_output.cmd_post_confirm(argparse.Namespace(draft=captured["id"]))
    return str(captured["id"])


def fill_color(draft: Mapping[str, object], event_id: str) -> str:
    """Patch only ``colorId`` on the event this approval created when the node asks for one."""
    from automation.calendar_default_color import node_color

    argv = draft["argv"]
    if draft.get("action") != "create" or not isinstance(argv, list) or "--json" not in argv:
        return ""
    if "colorId" in json.loads(str(argv[argv.index("--json") + 1])):
        return ""
    try:
        color = node_color(Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser())
    except (OSError, ValueError):
        print(f"COLOR-FILL-SKIPPED draft={draft['id']} reason=invalid-marker")
        return ""
    if color is None:
        return ""
    result = _gws(("gws", "calendar", "events", "patch", "--params",
                   _cjson({"calendarId": str(draft["calendar_id"]), "eventId": event_id, "sendUpdates": "none"}),
                   "--json", _cjson({"colorId": color})))
    status = "executed" if result.returncode == 0 else "failed"
    _audit(draft, event_id, "color_fill", status)
    _update(str(draft["id"]), {"color_fill": color if status == "executed" else "failed"})
    marker = "COLOR-FILLED" if status == "executed" else "COLOR-FILL-FAILED"
    print(f"{marker} draft={draft['id']} event={event_id} color={color}")
    return color if status == "executed" else ""
