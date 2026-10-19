"""Literal new-card receipts and append-only/fallback contracts."""
from __future__ import annotations

import builtins
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from automation.interop import owner_message as om
from automation.plaud_sync import render as p
from automation.memory_relocate import render as m
from tests.unit.test_plaud_sync_owner_cards import (
    PLAUD_RECORD, PLAUD_V4, MEMORY_V1, TODO_V1, PREVIEW, ENTRY, memory_record, todo_gate,
)
if TYPE_CHECKING:
    from skills.todo.scripts.todo_approval_render import prepare_approval_card, render_todo_approval, TodoRenderError
else:
    from todo_approval_render import prepare_approval_card, render_todo_approval, TodoRenderError

PLAUD_V5 = '대상: PLAUD 노트: standup (2026-09-01) (rec-001)\n사실: 2026-09-01T08:00:00Z; 000_PARA/Area/Lifelog/2026/2026-09-01-standup--abcdef123456.md; plaud-sync-render-v5 (승인 요청; 만료: 기한 없음)\n위치: 이 메시지\n- action_hash: `sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb`\n\n> 첫째 줄\n> 둘째 줄\n\n인계: 소유자: 위 위치 · 반응 ✅ 승인 / ⛔ 취소; 다음: Obsidian 저장·recall 인제스트; 수정은 이 스레드에 답글\n되돌리기: 해당 없음; 취소 시: 저장 취소'
MEMORY_V2 = '대상: memory→Obsidian 재배치 (Areas/research-memory.md)\n사실: 선호 도구: uv 원문 기호도 보존: `x → y`; 회수 예상 문자 수: 321; mc-reloc-render-v2 (승인 요청; 만료: 기한 없음)\n위치: 이 메시지\n- action_hash: `sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc`\n인계: 소유자: 위 위치 · 반응 ✅ 승인 / ⛔ 취소; 다음: 저장·인제스트 확인 후 자체 메모리(ambient) 삭제; 이후 recall(검색)로만 조회\n되돌리기: 해당 없음; 취소 시: 원본 유지'
TODO_V2 = '대상: Google Tasks 등록 (합성 과제)\n사실: 기한: 2026-09-12; todo-render-v2 (승인 요청; 만료: 기한 없음)\n위치: 이 메시지\nargv hash: sha256:abc\n인계: 소유자: 위 위치 · 반응 ✅ 승인 / ⛔ 취소; 다음: Google Tasks 등록\n되돌리기: 해당 없음; 취소 시: 등록 취소'
TODO_V2_DEADLINE = '대상: Google Tasks 등록 (합성 과제)\n사실: 기한: 2026-09-12; todo-render-v2 (승인 요청; 만료: 2026-09-11T13:00:00+00:00)\n위치: 이 메시지\nargv hash: sha256:abc\n인계: 소유자: 위 위치 · 반응 ✅ 승인 / ⛔ 취소; 다음: Google Tasks 등록\n되돌리기: 해당 없음; 취소 시: 등록 취소'


@pytest.mark.parametrize("producer", ["plaud", "memory", "todo", "todo-deadline"])
def test_envelope_bytes_when_new_card_is_rendered(producer: str, tmp_path: Path) -> None:
    # Given fixed inputs; when rendered; then compare with independently captured shipped bytes.
    match producer:
        case "plaud":
            actual = p.render_plaud_approval(
                PLAUD_RECORD,
                preview=PREVIEW,
                render_version="plaud-sync-render-v5",
            )
            expected = PLAUD_V5
        case "memory":
            actual = m.render_relocation_approval(
                memory_record(),
                entry_text=ENTRY,
                render_version="mc-reloc-render-v2",
            )
            expected = MEMORY_V2
        case "todo":
            actual = render_todo_approval(
                todo_gate(tmp_path).intent,
                render_version="todo-render-v2",
            )
            expected = TODO_V2
        case "todo-deadline":
            actual = render_todo_approval(
                todo_gate(tmp_path).intent,
                datetime(2026, 9, 11, 13, tzinfo=UTC),
                render_version="todo-render-v2",
            )
            expected = TODO_V2_DEADLINE
        case _:
            raise AssertionError(producer)
    assert actual == expected


@pytest.mark.parametrize("failure", ["import", "capability", "render"])
def test_previous_version_is_selected_when_envelope_is_unavailable(failure: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given a staggered runtime lacking one envelope capability.
    match failure:
        case "import":
            original = builtins.__import__
            def import_module(name, globals=None, locals=None, fromlist=(), level=0):
                if name == "automation.interop" and "owner_message" in fromlist:
                    raise ImportError("synthetic missing leaf")
                return original(name, globals, locals, fromlist, level)
            monkeypatch.setattr(builtins, "__import__", import_module)
        case "capability":
            monkeypatch.delattr(om, "render")
        case "render":
            def refuse(message: om.OwnerMessage, *, destination: om.Ref) -> str:
                raise om.OwnerMessageError(detail="synthetic")
            monkeypatch.setattr(om, "render", refuse)
    # When selecting new cards; then record precisely the previous version and its bytes.
    assert p.prepare_approval_card(PLAUD_RECORD, PREVIEW) == ("plaud-sync-render-v4", PLAUD_V4)
    assert m.prepare_approval_card(memory_record(), ENTRY) == ("mc-reloc-render-v1", MEMORY_V1)
    assert prepare_approval_card(todo_gate(tmp_path).intent) == ("todo-render-v1", TODO_V1)


@pytest.mark.parametrize("producer", ["plaud", "memory", "todo"])
def test_unknown_stored_version_is_refused_when_replayed(producer: str, tmp_path: Path) -> None:
    # Given a persisted unknown version; when replayed; then no fallback guesses its bytes.
    match producer:
        case "plaud":
            with pytest.raises(p.PlaudRenderError):
                p.prepare_approval_card(replace(PLAUD_RECORD, render_version="unknown"), PREVIEW)
        case "memory":
            with pytest.raises(m.RenderError):
                m.prepare_approval_card(replace(memory_record(), render_version="unknown"), ENTRY)
        case "todo":
            with pytest.raises(TodoRenderError):
                render_todo_approval(todo_gate(tmp_path).intent, render_version="unknown")


@pytest.mark.parametrize("producer", ["plaud", "memory", "todo"])
def test_wire_line_is_preserved_when_envelope_is_rendered(producer: str, tmp_path: Path) -> None:
    # Given the original machine line; when rendering a new card; then preserve it exactly once.
    match producer:
        case "plaud":
            card = p.render_plaud_approval(PLAUD_RECORD, preview=PREVIEW)
            wire = "- action_hash: `sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb`"
            binding = "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        case "memory":
            card = m.render_relocation_approval(memory_record(), entry_text=ENTRY)
            wire = "- action_hash: `sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc`"
            binding = "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
        case "todo":
            card = render_todo_approval(todo_gate(tmp_path).intent)
            wire, binding = "argv hash: sha256:abc", "sha256:abc"
        case _:
            raise AssertionError(producer)
    assert card.splitlines().count(wire) == 1
    assert card.count(binding) == 1


def test_v5_keeps_all_seven_full_preview_lines_when_metadata_is_realistic() -> None:
    # Given all seven 190-character preview lines and long synthetic metadata.
    record = replace(PLAUD_RECORD, recording_id="00000000-0000-4000-8000-000000000001",
                     note_title="주간 회의 검토와 다음 일정 조율 및 자료 준비에 관한 논의 (2026-09-08)",
                     note_relpath="000_PARA/Area/Lifelog/2026/2026-09-08-주간-회의-검토와-다음-일정-조율-및-자료-준비에-관한-논의--abcdef123456.md")
    preview = p.summary_preview(
        "## 요약\n" + "\n".join("가" * 300 for _ in range(8)),
        render_version="plaud-sync-render-v5",
    )
    # When rendered through the v5 default.
    card = p.render_plaud_approval(
        record,
        preview=preview,
        render_version="plaud-sync-render-v5",
    )
    # Then envelope fields fit without stealing any preview line or binding character.
    assert p.PREVIEW_LINES == 7 and p.PREVIEW_LINE_CHARS == 190 and p.MAX_MESSAGE_CHARS == 1900
    assert len(card) <= 1900
    assert len(preview.splitlines()) == 7
    assert all(len(line) == 190 for line in preview.splitlines())
    assert [line[2:] for line in card.splitlines() if line.startswith("> ")] == preview.splitlines()
    assert record.action_hash in card
    assert sum(line.startswith(("대상:", "사실:", "위치:", "인계:", "되돌리기:")) for line in card.splitlines()) == 5


@pytest.mark.parametrize("producer", ["plaud"])
def test_selected_version_survives_when_cron_merges_effect_persistence(producer: str) -> None:
    # Given a pure FSM result based on the old snapshot and a mid-tick committed card.
    if producer == "plaud":
        from automation.plaud_sync.cron.plaud_sync_watch import _merge_effect_bindings
        from automation.plaud_sync.model import PlaudSyncState
        from automation.plaud_sync.watch_step import ResolveResult
        record = PLAUD_RECORD
        before = PlaudSyncState(1, None, {"rec-001": record})
        result = ResolveResult(before, (), (), ())
        current = replace(record, message_id="333", channel_id="222", render_version="plaud-sync-render-v5")
        persisted = PlaudSyncState(1, None, {"rec-001": current})
        # When the real cron reconciles the local effect's durable state.
        merged = _merge_effect_bindings(before, result, persisted)
        version = merged.state.records["rec-001"].render_version
        expected = "plaud-sync-render-v5"
    # Then the final cron save keeps the version actually posted.
    assert version == expected
