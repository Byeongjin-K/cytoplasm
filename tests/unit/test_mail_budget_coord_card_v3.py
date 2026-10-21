"""New card-version coverage, separate from FS3-frozen characterization files."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from automation.entity_preflight.contracts import JsonValue
from automation.interop import owner_message
from automation.interop.approval_card import CardRenderError, prepare
from tests.unit.mail_approval_card_golden import BYTES_V3
from tests.unit.test_mail_approval_cards import (
    LEGACY,
    record,
    render_case,
    test_gmail_probe_when_action_hash_wire_changes as gmail_wire_probe,
)
from skills.coordination.scripts.coordination_lifecycle import render_owner_card
from skills.coordination.scripts.coordination_pending import (
    PendingConfirm,
    PendingConfirmError,
    PendingConfirmStore,
)

CASES = ("original", "compose", "gmail", "coordination")


@pytest.mark.parametrize("name", CASES)
def test_v3_selects_multiline_owner_v2_when_explicit(
    name: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given the same fixed records used for the frozen v1/v2 captures.
    seen: list[owner_message.OwnerMessage] = []

    def capture(message: owner_message.OwnerMessage, *, destination: owner_message.Ref) -> str:
        seen.append(message)
        assert destination == owner_message.Ref(scope="self")
        return "rendered"

    monkeypatch.setattr(owner_message, "render", capture)
    # When the public producer renders v3.
    content = render_case(name, {**record(), "render_version": "3"}, monkeypatch)
    # Then the shared renderer receives the new version and separate fact lines.
    assert len(seen) == 1
    assert seen[0].render_version == "owner-ko-v2"
    assert len(seen[0].fact.splitlines()) > 1
    assert " · - " not in seen[0].fact
    binding = ("sha256:digest-1" if name == "coordination"
               else "- draft: `abc123` sha256: `digest-1`")
    assert binding in content.splitlines()


@pytest.mark.parametrize("name", CASES)
def test_new_selection_records_v3_when_owner_runtime_available(
    name: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a renderer for a new unposted record.
    def renderer(version: str) -> str:
        return render_case(name, {**record(), "render_version": version}, monkeypatch)

    # When the shared selector prepares the new card.
    card = prepare(renderer)
    # Then v3 is selected, rather than silently falling back to old wording.
    assert card.render_version == "3"


@pytest.mark.parametrize("name", CASES)
def test_v3_matches_posted_capture_when_parent_renderer_runs(
    name: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given independent v3 captures; when the real renderer runs; then posted bytes match.
    assert render_case(name, {**record(), "render_version": "3"}, monkeypatch) == BYTES_V3[name]


@pytest.mark.parametrize("name", CASES)
def test_new_card_falls_back_when_runtime_only_supports_owner_v1(
    name: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a callable older runtime that only understands owner-ko-v1.
    real = owner_message.render

    def older(message: owner_message.OwnerMessage, *, destination: owner_message.Ref) -> str:
        if message.render_version != "owner-ko-v1":
            raise owner_message.OwnerMessageError(render_version=message.render_version)
        return real(message, destination=destination)

    monkeypatch.setattr(owner_message, "render", older)

    def renderer(version: str) -> str:
        draft = {**record(), "render_version": version}
        if name == "coordination":
            return render_owner_card({
                "id": str(draft["id"]), "sha256": str(draft["sha256"]),
                "created": str(draft["created"]), "start": str(draft["start"]),
                "render_version": version,
            }, LEGACY[name])
        return render_case(name, draft, monkeypatch)

    # When selection handles an unsupported new version; then it records exact v1 bytes.
    card = prepare(renderer)
    assert card.render_version == "1"
    assert card.content == LEGACY[name]
    # A stored v3 is not silently rewritten through that same optional runtime.
    with pytest.raises(CardRenderError):
        _ = prepare(renderer, "3")


def test_pending_store_loads_v3_when_owner_card_was_posted(tmp_path: Path) -> None:
    # Given a persisted v3 record with the same identity and timestamp fields.
    entry = PendingConfirm(
        draft_id="abc123", sha256="digest-1", dm_channel_id="222", dm_message_id="333",
        slot="2026-09-01T09:00:00+09:00", summary="일정 제목", correlation="coord-1",
        duration_min=30, created=datetime(2026, 9, 1, tzinfo=UTC), render_version="3",
    )
    store = PendingConfirmStore(tmp_path / "pending.jsonl")
    store.append(entry)
    # When the watcher's actual loader parses that record.
    loaded = store.load()
    # Then the binding and the presentation version survive together.
    assert loaded == (entry,)
    assert loaded[0].render_version == "3"


@pytest.mark.parametrize("binding", ["intact", "digest", "prefix"])
def test_gmail_v3_probe_rejects_changed_wire(
    binding: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Exercise the same real consumer with the new card, not a second probe implementation.
    gmail_wire_probe("3", binding, monkeypatch)


def test_mail_v3_shows_keyword_body_on_both_surfaces(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given distinct confidential subject/body/attachment data.
    draft: dict[str, JsonValue] = {
        **record(), "render_version": "3", "body": "PRIVATE-BODY",
        "subject": "PRIVATE-SUBJECT", "attachments": [{
            "display_name": "PRIVATE-FILE.txt", "size_bytes": 23, "mime_type": "text/plain",
        }],
    }
    # When both supported destinations render the same sensitive reply.
    console = render_case("original", draft, monkeypatch)
    owner = render_case("original", draft, monkeypatch)
    # Then the destination's existing disclosure policy is retained.
    assert "PRIVATE-BODY" in console and "PRIVATE-SUBJECT" in console
    assert "PRIVATE-BODY" in owner and "PRIVATE-SUBJECT" in owner
    assert "PRIVATE-FILE" in console and "PRIVATE-FILE" in owner


def test_coordination_v3_unknown_pending_version_is_refused(tmp_path: Path) -> None:
    # Given an unknown version, even with otherwise valid persisted data.
    store = PendingConfirmStore(tmp_path / "pending.jsonl")
    store.append(PendingConfirm(
        draft_id="abc123", sha256="digest-1", dm_channel_id="222", dm_message_id="333",
        slot="2026-09-01T09:00:00+09:00", summary="일정 제목", correlation="coord-1",
        duration_min=30, created=datetime(2026, 9, 1, tzinfo=UTC), render_version="future",
    ))
    # When loading; then extending the known versions did not remove validation.
    with pytest.raises(PendingConfirmError):
        _ = store.load()
