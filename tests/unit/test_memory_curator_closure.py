from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from automation.memory_curator.binding import (
    DeletionMarker,
    MARKER_VERSION,
    promotion_key,
    promoted_slug,
    render_marker,
)
from automation.memory_curator.closure import (
    ClosureRequest,
    SETTLED_SUFFIX_PREFIX,
    close_terminal_promotions,
)
from automation.memory_curator.state import CuratorState, PromotionRecord, empty_state

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "wiki" / "scripts"))
import wiki_approval  # noqa: E402
import wiki_gate  # noqa: E402


@dataclass
class FakeSurface:
    contents: dict[str, str]
    edits: int = 0
    missing_on_edit: bool = False
    crash_after_patch: bool = False

    def probe(self, channel_id: str, message_id: str, action_hash: str) -> str:
        content = self.contents.get(message_id)
        if content is None:
            return "missing"
        return "bound" if action_hash in content else "binding-mismatch"

    def edit_settled(
        self,
        channel_id: str,
        message_id: str,
        action_hash: str,
        suffix: str,
    ) -> str:
        if self.missing_on_edit:
            return "missing"
        content = self.contents[message_id]
        if action_hash not in content:
            return "binding-mismatch"
        if content.endswith(suffix):
            return "already-settled"
        self.contents[message_id] = content + suffix
        self.edits += 1
        if self.crash_after_patch:
            self.crash_after_patch = False
            raise RuntimeError("crash-after-patch")
        return "edited"


def _fixture(tmp_path: Path) -> tuple[CuratorState, Path, FakeSurface, str, dict[str, object]]:
    digest = "a" * 64
    key = promotion_key("memory", digest)
    slug = promoted_slug("memory", digest)
    marker = render_marker(DeletionMarker(MARKER_VERSION, key, "memory", digest, True))
    note_text = f"note\n{marker}"
    note_hash = hashlib.sha256(note_text.encode()).hexdigest()
    saved: dict[str, object] = {
        "action": "create",
        "channel_id": "channel-1",
        "confirm_message_id": "message-1",
        "created": "2026-08-05T00:00:00Z",
        "id": "draft-1",
        "note_text": note_text,
        "sha256": note_hash,
        "slug": slug,
        "status": "saved",
    }
    drafts = tmp_path / "gate" / "drafts"
    drafts.mkdir(parents=True)
    (drafts / "draft-1.json").write_text(
        json.dumps(saved, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )
    record = PromotionRecord(
        "memory", digest, slug, "2026-08-05T00:00:00Z", note_hash,
        "draft-1", "message-1", "reconciled", "2026-08-05T00:00:01Z",
        "2026-08-05T00:00:02Z", None, None,
    )
    base = empty_state()
    state = CuratorState(base.version, {key: record}, base.alert, {})
    surface = FakeSurface({"message-1": f"저장 draft-1 sha256:{note_hash}"})
    return state, tmp_path / "gate", surface, key, saved


def _run(
    state: CuratorState,
    gate: Path,
    surface: FakeSurface,
    *,
    after_step=lambda _step: None,
):
    return close_terminal_promotions(
        ClosureRequest(state, gate, surface, False, after_step)
    )


def test_terminal_record_is_edited_archived_dropped_and_journaled(tmp_path: Path) -> None:
    state, gate, surface, key, _saved = _fixture(tmp_path)

    result = _run(state, gate, surface)

    assert result.closable == 1 and result.unbound == 0
    assert surface.edits == 1
    assert surface.contents["message-1"].endswith(f"{SETTLED_SUFFIX_PREFIX}{key}")
    assert (gate / "archive" / "draft-1.json").is_file()
    assert not (gate / "drafts" / "draft-1.json").exists()
    assert len((gate / "curator-closure.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_wiki_edit_settled_preserves_original_content_and_hash(monkeypatch) -> None:
    original = "승인 본문 sha256:abc"
    calls: list[tuple[str, str, dict[str, str] | None]] = []

    def api(method: str, path: str, payload: dict[str, str] | None = None):
        calls.append((method, path, payload))
        if method == "GET":
            return {"channel_id": "channel-1", "content": original}
        return {}

    monkeypatch.setattr(wiki_gate, "_api", api)
    gate = wiki_approval.WikiApprovalGate({})

    outcome = gate.edit_settled("channel-1", "message-1", "abc", "\nSETTLED")

    assert outcome == "edited"
    assert calls[-1][0] == "PATCH"
    assert calls[-1][2] == {"content": original + "\nSETTLED"}


@pytest.mark.parametrize(
    "case",
    [
        "draft-id",
        "message-id",
        "action-hash",
        "promotion-key",
        "slug",
        "state-note-hash",
        "saved-note-hash",
    ],
)
def test_each_cross_binding_mismatch_is_unbound_without_side_effects(
    tmp_path: Path, case: str
) -> None:
    state, gate, surface, key, saved = _fixture(tmp_path)
    record = state.promotions[key]
    if case == "draft-id":
        saved["id"] = "other-draft"
    elif case == "message-id":
        saved["confirm_message_id"] = "other-message"
    elif case == "action-hash":
        surface.contents["message-1"] = "no digest"
    elif case == "promotion-key":
        wrong = render_marker(DeletionMarker(MARKER_VERSION, promotion_key("memory", "b" * 64), "memory", "b" * 64, True))
        saved["note_text"] = f"note\n{wrong}"
        saved["sha256"] = hashlib.sha256(str(saved["note_text"]).encode()).hexdigest()
        record = replace(record, note_sha256=str(saved["sha256"]))
        surface.contents["message-1"] = f"sha256:{saved['sha256']}"
    elif case == "slug":
        saved["slug"] = "other-slug"
    elif case == "state-note-hash":
        record = replace(record, note_sha256="c" * 64)
    elif case == "saved-note-hash":
        saved["note_text"] = str(saved["note_text"]) + "tampered"
    (gate / "drafts" / "draft-1.json").write_text(json.dumps(saved) + "\n", encoding="utf-8")
    state = CuratorState(state.version, {key: record}, state.alert, {})

    result = _run(state, gate, surface)

    assert result.closable == 0 and result.unbound == 1, case
    assert surface.edits == 0, case
    assert not (gate / "archive").exists(), case
    assert (gate / "drafts" / "draft-1.json").is_file(), case


def test_repeated_tick_edits_only_once_using_remote_suffix(tmp_path: Path) -> None:
    state, gate, surface, _key, _saved = _fixture(tmp_path)
    _ = _run(state, gate, surface)
    _ = _run(state, gate, surface)
    assert surface.edits == 1


def test_crash_after_patch_resumes_without_duplicate_edit(tmp_path: Path) -> None:
    state, gate, surface, _key, _saved = _fixture(tmp_path)
    surface.crash_after_patch = True
    with pytest.raises(RuntimeError, match="crash-after-patch"):
        _ = _run(state, gate, surface)
    _ = _run(state, gate, surface)
    assert surface.edits == 1
    assert not (gate / "drafts" / "draft-1.json").exists()


@pytest.mark.parametrize("crash_step", ["after-archive", "before-drop"])
def test_archive_receipt_resumes_at_drop(tmp_path: Path, crash_step: str) -> None:
    state, gate, surface, _key, _saved = _fixture(tmp_path)

    def crash(step: str) -> None:
        if step == crash_step:
            raise RuntimeError(step)

    with pytest.raises(RuntimeError, match=crash_step):
        _ = _run(state, gate, surface, after_step=crash)
    assert (gate / "archive" / "draft-1.json").is_file()
    _ = _run(state, gate, surface)
    assert surface.edits == 1
    assert not (gate / "drafts" / "draft-1.json").exists()


def test_abandoned_record_without_saved_binding_is_unbound(tmp_path: Path) -> None:
    state, gate, surface, key, _saved = _fixture(tmp_path)
    (gate / "drafts" / "draft-1.json").unlink()
    abandoned = replace(state.promotions[key], status="abandoned")
    state = CuratorState(state.version, {key: abandoned}, state.alert, {})
    result = _run(state, gate, surface)
    assert result.unbound == 1 and surface.edits == 0


def test_message_disappearing_between_probe_and_patch_is_terminal(tmp_path: Path) -> None:
    state, gate, surface, _key, _saved = _fixture(tmp_path)
    surface.missing_on_edit = True
    result = _run(state, gate, surface)
    assert result.closable == 1 and surface.edits == 0
    assert (gate / "archive" / "draft-1.json").is_file()


def test_reaction_after_settlement_is_noop_with_journal_only(tmp_path: Path) -> None:
    state, gate, surface, _key, _saved = _fixture(tmp_path)
    _ = _run(state, gate, surface)
    journal = gate / "curator-closure.jsonl"
    before = len(journal.read_text(encoding="utf-8").splitlines())
    _ = _run(state, gate, surface)
    assert surface.edits == 1
    assert len(journal.read_text(encoding="utf-8").splitlines()) == before + 1


def test_v3_state_remains_loadable_after_closure_feature() -> None:
    state = empty_state()
    assert state.version == 3
