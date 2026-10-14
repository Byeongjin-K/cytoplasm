"""Append-only exact-fact presentation checks, separate from frozen renderer tests."""
from __future__ import annotations

from dataclasses import replace

from automation.interop import owner_message as om


def envelope() -> om.OwnerMessage:
    here = om.Ref("self")
    return om.OwnerMessage(
        subject_key="sha256:synthetic", subject="메모리 정리",
        fact="exact line  \nsecond\t\n→ MEMORY.md",
        location=here, owner=om.Action("react", here, "➡️ 이동 · 📌 유지 · 🗑️ 삭제"),
        agent_next="선택한 줄 처리", recovery="not_applicable", detail=om.Approval(None, "유지"),
        render_version="owner-ko-v4",
    )


def test_v4_when_fact_has_whitespace_preserves_exact_bytes() -> None:
    # Given: message text carries a source-byte binding.
    message = envelope()
    # When: the public contract renders its new presentation.
    rendered = om.render(message, destination=om.Ref("self"))
    # Then: no rstrip, whitespace folding or truncation changes its fact/key/choices.
    assert f"\n{message.fact}\n" in rendered
    assert message.subject_key in rendered
    assert message.owner.argument in rendered


def test_v4_when_location_is_remote_retains_the_resolved_reference() -> None:
    # Given: rendering moves away from the referenced private thread.
    reference = om.Ref("message", space="guild", guild_id="901", channel_id="902", message_id="903")
    message = replace(envelope(), location=reference, owner=om.Action("open", reference))
    # When: references resolve against another destination.
    rendered = om.render(message, destination=om.Ref("channel", channel_id="904"))
    # Then: location and handoff retain the full, validated coordinates.
    link = om.discord_link(space="guild", guild_id="901", channel_id="902", message_id="903")
    assert link.url is not None and link.url in rendered


def test_v4_when_local_result_needs_no_handoff_fits_one_line() -> None:
    # Given: one short migration result, with no action or recovery.
    message = replace(envelope(), fact="기존 묶음 대신 개별 메시지로 게시했습니다.",
                      detail=om.Result("executed"), owner=om.Action("none"), agent_next=None)
    # When: the envelope renders on its own thread.
    rendered = om.render(message, destination=om.Ref("self"))
    # Then: it is a single result line, preserving the exact fact.
    assert len(rendered.splitlines()) == 1
    assert message.fact in rendered
