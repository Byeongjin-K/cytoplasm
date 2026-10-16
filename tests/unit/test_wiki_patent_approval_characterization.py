"""Characterization tests for the two owner-approval records (wiki draft / patent slug).

Both gates persist a Discord approval message id in a record keyed by a stable subject
and rewrite that record on every new request. These tests pin the CURRENT serialization,
round-trip, resolver precedence and state-machine contract so the upcoming "exactly one
live approval message per logical key" refactor is provably behavior-preserving. Two
tests below characterize the overwrite defect itself: CHARACTERIZED, NOT ENDORSED.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import unquote

import pytest

from automation.interop.approval_surface import ApprovalKind, RequestThread, request_thread_notice

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "wiki" / "scripts"))
sys.path.insert(0, str(_REPO / "skills" / "patent-prep"))

import wiki_gate  # noqa: E402
from scripts import patent_export_manifest as pm  # noqa: E402

OWNER_ID = "owner-char-1"
CHANNEL_ID = "1526487936065952385"
AGENT_CHAT_CHANNEL_ID = "1526487936065952390"
AGENT_CHAT_THREAD_ID = "1526487936065952391"
REQUEST_THREAD_ID = "1526487936065952392"
PATENT_CHANNEL_ID = "1528936606946122421"  # AS-1.6: a binding refuses a placeholder id
SLUG = "char-slug"
NOTE_TEXT = (
    '---\ntitle: "Characterization"\ntags: [test]\ncreated: 2026-07-25T00:00:00Z\n'
    "updated: 2026-07-25T00:00:00Z\nlinks: []\n---\n본문\n"
)
NOTE_SHA256 = hashlib.sha256(NOTE_TEXT.encode("utf-8")).hexdigest()
DRAFT_FIELDS = [
    "action", "channel_id", "created", "id", "note_text", "sha256", "slug", "status",
]
POSTED_FIELDS = sorted([*DRAFT_FIELDS, "confirm_message_id"])
STORED_POSTED_FIELDS = sorted([
    # 요청별 승인 스레드: 레코드가 자기 승인이 사는 스레드를 함께 기록한다.
    *POSTED_FIELDS, "approval_thread_id", "kind", "policy_version", "surface", "render_version",
])
MANIFEST_FIELDS = sorted([
    "approval_ts", "created_ts", "dest_folder_id", "expiry_ts", "message_id",
    "mode", "nonce", "plaintext_sha256", "slug", "state", "render_version",
    # AS-1.6: the manifest is now the durable record of WHERE its approval lives.
    "kind", "surface", "channel_id", "policy_version", "approval_thread_id", "approval_guild_id",
])


class FakeDiscordRest:
    """Offline stand-in for ``wiki_gate._api`` (the seam the existing gate tests use)."""

    def __init__(self, approve_users: list[dict]) -> None:
        self.approve_users = approve_users
        self.contents: dict[str, str] = {}
        self.channels: dict[str, str] = {}
        self.notices: list[str] = []
        self.request_thread_calls: list[tuple[str, dict]] = []
        self.threads: list[str] = []
        self._posted = 0

    def __call__(self, method: str, path: str, payload: dict | None = None) -> object:
        channel = path.split("/")[2]
        if method == "POST" and path == "/users/@me/channels":
            return {"id": CHANNEL_ID}
        if method == "POST" and path == f"/channels/{AGENT_CHAT_CHANNEL_ID}/messages":
            assert payload is not None
            self.notices.append(str(payload["content"]))
            return {"id": f"wiki-notice-{len(self.notices)}"}
        if (
            method == "POST"
            and path.startswith(f"/channels/{AGENT_CHAT_CHANNEL_ID}/messages/")
            and path.endswith("/threads")
        ):
            assert payload is not None
            self.request_thread_calls.append((path, payload))
            self.threads.append(str(payload["name"]))
            return {"id": CHANNEL_ID, "type": 11, "parent_id": AGENT_CHAT_CHANNEL_ID}
        if method == "GET" and path == f"/channels/{CHANNEL_ID}":
            if self.threads:
                return {"id": CHANNEL_ID, "name": self.threads[-1], "type": 11, "parent_id": AGENT_CHAT_CHANNEL_ID}
            return {"id": CHANNEL_ID, "name": "", "recipients": [{"id": OWNER_ID}], "type": 1}
        if method == "POST" and path.endswith("/messages"):
            self._posted += 1
            mid = f"wiki-msg-{self._posted}"
            self.contents[mid] = str((payload or {})["content"])
            self.channels[mid] = channel
            return {"id": mid, "channel_id": channel, "content": self.contents[mid]}
        if method == "PUT" and "/reactions/" in path:
            return None
        if method == "GET" and "/reactions/" in path:
            emoji = unquote(path.split("/reactions/", 1)[1].split("?", 1)[0])
            return list(self.approve_users) if emoji == wiki_gate.APPROVE_EMOJI else []
        if method == "GET" and "/messages/" in path:
            mid = path.rsplit("/", 1)[-1]
            channel = self.channels.get(mid, channel)
            return {"id": mid, "channel_id": channel, "content": self.contents.get(mid, "")}
        raise AssertionError(f"unexpected Discord call: {method} {path}")


@pytest.fixture
def wiki_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeDiscordRest:
    interop = tmp_path / "interop.json"
    interop.write_text(
        json.dumps({"owner_id": OWNER_ID, "agent_chat_channel_id": AGENT_CHAT_CHANNEL_ID}),
        encoding="utf-8",
    )
    monkeypatch.setenv("INTEROP_CONFIG", str(interop))
    monkeypatch.setenv("WIKI_ROOT", str(tmp_path / "wiki"))
    monkeypatch.setenv("WIKI_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(_REPO))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "unit-test-token")
    monkeypatch.setattr(wiki_gate, "GATE_DIR", tmp_path / "gate")
    monkeypatch.setattr(wiki_gate, "INTEROP_CONFIG", interop)
    fake = FakeDiscordRest([{"id": OWNER_ID, "bot": False}])
    monkeypatch.setattr(wiki_gate, "_api", fake)
    return fake


def _new_draft() -> dict:
    return wiki_gate.create_draft("create", "char-note", NOTE_TEXT, CHANNEL_ID)


def _stored_draft(tmp_path: Path, draft_id: str) -> dict:
    path = tmp_path / "gate" / "drafts" / f"{draft_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_create_draft_persists_an_exact_field_set(wiki_env: FakeDiscordRest, tmp_path: Path) -> None:
    # Given / When
    draft = _new_draft()

    # Then
    assert sorted(draft.keys()) == DRAFT_FIELDS
    assert sorted(_stored_draft(tmp_path, draft["id"]).keys()) == DRAFT_FIELDS
    assert draft["action"] == "create"
    assert draft["channel_id"] == CHANNEL_ID
    assert draft["slug"] == "char-note"
    assert draft["status"] == "pending"
    assert draft["sha256"] == NOTE_SHA256


def test_confirm_text_is_byte_exact_and_embeds_the_draft_digest(wiki_env: FakeDiscordRest) -> None:
    # Given
    frozen = {"id": "abc123", "sha256": "b" * 64}

    # When
    draft = _new_draft()

    # Then
    assert wiki_gate.confirm_text(frozen) == "저장 abc123 sha256:" + "b" * 64
    assert wiki_gate.confirm_text(draft) == f"저장 {draft['id']} sha256:{NOTE_SHA256}"


def test_post_confirm_message_adds_confirm_message_id(wiki_env: FakeDiscordRest, tmp_path: Path) -> None:
    # Given
    draft = _new_draft()

    # When
    posted = wiki_gate.post_confirm_message(draft)

    # Then
    stored = _stored_draft(tmp_path, draft["id"])
    assert sorted(posted.keys()) == POSTED_FIELDS
    assert sorted(stored.keys()) == STORED_POSTED_FIELDS
    assert posted["confirm_message_id"] == "wiki-msg-1"
    assert stored["confirm_message_id"] == "wiki-msg-1"
    assert f"sha256:{NOTE_SHA256}" in wiki_env.contents["wiki-msg-1"]
    assert stored["render_version"] == 2
    assert wiki_env.notices == [
        request_thread_notice(ApprovalKind.WIKI, RequestThread(draft['id']))
    ]
    assert wiki_env.request_thread_calls == [(
        f"/channels/{AGENT_CHAT_CHANNEL_ID}/messages/wiki-notice-1/threads",
        {"name": f"📚 위키 · {draft['id']}", "auto_archive_duration": 10080},
    )]


def test_second_confirm_message_never_replaces_the_stored_id(wiki_env: FakeDiscordRest, tmp_path: Path) -> None:
    # TRANSFERRED (approval-lifecycle migration): this test used to pin the overwrite defect —
    # a second post replaced confirm_message_id and "wiki-msg-1" vanished from the record, so
    # the owner's approval on the earlier message was never polled again. Its intent-preserving
    # successor is the invariant that closed the defect: a stored id is only ever replaced
    # AFTER the old message was deleted. This fixture's owner has ALREADY reacted ✅ on
    # wiki-msg-1, so that message is never deleted and the stored id survives untouched.
    # The delete-BEFORE-replace ordering itself is pinned by
    # tests/unit/test_wiki_single_live_request.py::
    # test_changed_content_deletes_the_old_message_before_dropping_the_record.
    # Given
    draft = _new_draft()
    first = wiki_gate.post_confirm_message(draft)

    # When
    with pytest.raises(wiki_gate.GateError):
        wiki_gate.post_confirm_message(wiki_gate.load_draft(draft["id"]))

    # Then
    stored = _stored_draft(tmp_path, draft["id"])
    assert first["confirm_message_id"] == "wiki-msg-1"
    assert sorted(stored.keys()) == STORED_POSTED_FIELDS
    assert stored["confirm_message_id"] == "wiki-msg-1"
    assert "wiki-msg-1" in json.dumps(stored)
    assert "wiki-msg-2" not in wiki_env.contents


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        ({"confirm_message_id": "c1", "message_id": "m1"}, "c1"),
        ({"confirm_message_id": None, "message_id": "m1"}, "m1"),
        ({"message_id": "m1"}, "m1"),
        ({}, ""),
    ],
)
def test_confirm_message_id_precedence(record: dict, expected: str) -> None:
    # Given / When / Then
    assert wiki_gate._confirm_message_id(record) == expected


@pytest.mark.parametrize("legacy_record", [False, True])
def test_resolve_reaction_reads_the_precedence_selected_id(
    wiki_env: FakeDiscordRest, tmp_path: Path, legacy_record: bool
) -> None:
    # Given: the live message is reachable only through the field precedence rule — the
    # losing field points at an id the transport serves with no bound digest.
    draft = _new_draft()
    posted = wiki_gate.post_confirm_message(draft)
    bound_id = posted["confirm_message_id"]
    if legacy_record:
        record = {key: value for key, value in posted.items() if key != "confirm_message_id"}
        record["message_id"] = bound_id
    else:
        record = {**posted, "message_id": "stale-msg-0"}

    # When
    saved = wiki_gate.resolve_reaction(record)

    # Then
    assert saved == tmp_path / "wiki" / "char-note.md"
    assert saved.read_text(encoding="utf-8") == NOTE_TEXT
    assert _stored_draft(tmp_path, draft["id"])["status"] == "saved"


@pytest.fixture
def export_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "patent-export"
    monkeypatch.setenv("PATENT_EXPORT_ROOT", str(root))
    return root


def _manifest(slug: str) -> pm.Manifest:
    return pm.Manifest(
        slug=slug, plaintext_sha256="sha256:" + "a" * 64, dest_folder_id="folder-char",
        mode="enc", expiry_ts=1_800_000_000, nonce="0123456789abcdef0123456789abcdef",
        state=pm.State.PENDING, message_id=None, created_ts=1_799_999_000, approval_ts=None,
        approval_thread_id=None, kind="patent-export", surface="skill-approvals",
        channel_id=PATENT_CHANNEL_ID, policy_version=1,
    )


def test_write_manifest_lands_at_the_slug_path_with_an_exact_field_set(export_root: Path) -> None:
    # Given / When
    pm.write_manifest(_manifest(SLUG))

    # Then
    path = export_root / f"{SLUG}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert pm.manifest_path(SLUG) == path
    assert sorted(payload.keys()) == MANIFEST_FIELDS
    assert payload["slug"] == SLUG
    assert payload["state"] == "PENDING"
    assert payload["message_id"] is None
    assert path.stat().st_mode & 0o777 == 0o600
    assert not path.with_suffix(".tmp").exists()


def test_manifest_round_trip_preserves_every_field(export_root: Path) -> None:
    # Given
    written = dataclasses.replace(
        _manifest(SLUG), state=pm.State.APPROVED, message_id="msg-1", approval_ts=1_799_999_500
    )

    # When
    pm.write_manifest(written)
    loaded = pm.load_manifest(SLUG)

    # Then
    assert loaded == written
    assert loaded.message_id == "msg-1"
    assert loaded.state is pm.State.APPROVED
    assert loaded.approval_ts == 1_799_999_500


def test_second_prepare_for_the_same_slug_never_replaces_the_message_id(
    export_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # TRANSFERRED (approval-lifecycle migration): this test used to pin the overwrite defect —
    # a second slug-keyed prepare replaced message_id, so the owner's approval on the earlier
    # message was never polled again. The shared lifecycle now reuses an identical PENDING
    # request, while delete-BEFORE-replace ordering for changed authorization is pinned by
    # tests/unit/test_patent_single_live_request.py::
    # test_changed_authorization_deletes_before_supersede_and_leaves_one_live.
    # Given
    from scripts import patent_export
    from scripts import patent_export_gate
    from scripts import patent_export_manifest
    from scripts import patent_storage

    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(_REPO))
    monkeypatch.setenv("PATENT_ARCHIVE_FOLDER_ID", "folder-char")
    interop = export_root.parent / "interop.json"
    interop.write_text(
        json.dumps({
            "owner_id": OWNER_ID,
            "personal_approvals_channel_id": PATENT_CHANNEL_ID,
            "agent_chat_channel_id": AGENT_CHAT_CHANNEL_ID,
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("INTEROP_CONFIG", str(interop))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "unit-test-envtoken")
    monkeypatch.setattr(patent_export_manifest, "now_ts", lambda: 1_799_999_000)
    paths = patent_storage.PatentPaths(export_root.parent / "drafts", export_root.parent / "status")
    patent_storage.private_directory(paths.workspace_root / SLUG)
    patent_storage.write_private(paths.workspace_root / SLUG / "draft.md", "private draft\n")
    messages: dict[str, str] = {}
    notices: list[str] = []
    request_thread_calls: list[tuple[str, dict[str, object]]] = []
    posted: list[str] = []
    post_channels: list[str] = []

    def api(
        method: str, path: str, payload: dict[str, object] | None = None
    ) -> dict[str, object] | list[dict[str, str | bool]]:
        if method == "POST" and path == "/users/@me/channels":
            return {"id": CHANNEL_ID}
        if method == "GET" and path == f"/channels/{AGENT_CHAT_CHANNEL_ID}":
            return {"id": AGENT_CHAT_CHANNEL_ID, "guild_id": "guild-char"}
        if method == "GET" and path == "/guilds/guild-char/threads/active":
            return {"threads": [{
                "id": AGENT_CHAT_THREAD_ID,
                "name": "승인-patent-export",
                "parent_id": AGENT_CHAT_CHANNEL_ID,
                "type": 11,
            }]}
        if method == "POST" and path == f"/channels/{AGENT_CHAT_CHANNEL_ID}/messages":
            assert payload is not None
            assert payload == {
                "content": request_thread_notice(ApprovalKind.PATENT_EXPORT, RequestThread(SLUG))
            }
            notices.append(str(payload["content"]))
            return {"id": f"patent-notice-{len(notices)}"}
        if method == "POST" and path == (
            f"/channels/{AGENT_CHAT_CHANNEL_ID}/messages/patent-notice-1/threads"
        ):
            assert payload is not None
            assert payload == {
                "name": f"📜 특허 반출 · {SLUG}",
                "auto_archive_duration": 10080,
            }
            request_thread_calls.append((path, payload))
            return {"id": REQUEST_THREAD_ID}
        if method == "POST" and path.endswith("/messages"):
            message_id = f"msg-{len(posted) + 1}"
            posted.append(message_id)
            post_channels.append(path.split("/")[2])
            messages[message_id] = str((payload or {})["content"])
            return {"id": message_id}
        if method == "POST" and path.endswith("/threads"):
            return {"id": REQUEST_THREAD_ID}
        if method == "GET" and path in (
            f"/channels/{AGENT_CHAT_THREAD_ID}",
            f"/channels/{REQUEST_THREAD_ID}",
        ):
            return {
                "id": path.rsplit("/", 1)[-1],
                "type": 11,
                "name": f"📜 특허 반출 · {SLUG}",
                "parent_id": AGENT_CHAT_CHANNEL_ID,
            }
        if method == "PUT":
            return {}
        if method == "GET" and "/reactions/" in path:
            return []
        if method == "GET" and "/messages/" in path:
            message_id = path.split("/messages/", 1)[1].split("/", 1)[0]
            return {"id": message_id, "content": messages[message_id]}
        if method == "GET" and path.count("/") == 2 and path.startswith("/channels/"):
            channel_id = path.rsplit("/", 1)[-1]
            if channel_id == CHANNEL_ID:
                return {"id": channel_id, "type": 1, "recipients": [{"id": OWNER_ID}]}
            return {"id": channel_id, "type": 0, "name": "approvals"}
        raise AssertionError(f"unexpected Discord call: {method} {path}")

    monkeypatch.setattr(patent_export_gate, "_api", api)
    _ = patent_export.prepare_export(paths, SLUG, mode="enc")
    first = patent_export_manifest.load_manifest(SLUG)

    # When
    _ = patent_export.prepare_export(paths, SLUG, mode="enc")
    second = patent_export_manifest.load_manifest(SLUG)

    # Then
    assert first.message_id == "msg-1"
    assert second.message_id == first.message_id
    assert notices == [
        request_thread_notice(ApprovalKind.PATENT_EXPORT, RequestThread(SLUG))
    ]
    assert request_thread_calls == [(
        f"/channels/{AGENT_CHAT_CHANNEL_ID}/messages/patent-notice-1/threads",
        {"name": f"📜 특허 반출 · {SLUG}", "auto_archive_duration": 10080},
    )]
    assert posted == ["msg-1"]
    assert post_channels == [REQUEST_THREAD_ID]
    assert set(messages) == {"msg-1"}


@pytest.mark.parametrize(
    ("start", "to"),
    [
        (pm.State.PENDING, pm.State.PENDING), (pm.State.PENDING, pm.State.APPROVED),
        (pm.State.PENDING, pm.State.CANCELLED), (pm.State.APPROVED, pm.State.CONSUMED),
        (pm.State.APPROVED, pm.State.CANCELLED),
    ],
)
def test_transition_persists_when_current_state_is_allowed(
    export_root: Path, start: pm.State, to: pm.State
) -> None:
    # Given
    pm.write_manifest(dataclasses.replace(_manifest(SLUG), state=start, message_id="msg-1"))

    # When
    result = pm.transition(SLUG, allowed_from=frozenset({start}), to=to)

    # Then
    assert result.state is to
    assert pm.load_manifest(SLUG).state is to
    assert result.message_id == "msg-1"
    assert result.approval_ts is None  # transition never stamps approval_ts on its own


@pytest.mark.parametrize(
    ("start", "allowed_from"),
    [
        (pm.State.APPROVED, pm.State.PENDING),
        (pm.State.CANCELLED, pm.State.PENDING),
        (pm.State.CONSUMED, pm.State.APPROVED),
    ],
)
def test_transition_rejects_and_leaves_the_record_untouched(
    export_root: Path, start: pm.State, allowed_from: pm.State
) -> None:
    # Given
    pm.write_manifest(dataclasses.replace(_manifest(SLUG), state=start, message_id="msg-1"))

    # When / Then
    with pytest.raises(pm.ManifestError, match=f"Cannot transition from {start.value} to APPROVED"):
        pm.transition(SLUG, allowed_from=frozenset({allowed_from}), to=pm.State.APPROVED)
    reloaded = pm.load_manifest(SLUG)
    assert reloaded.state is start
    assert reloaded.message_id == "msg-1"


def test_transition_overrides_only_the_named_fields(export_root: Path) -> None:
    # Given
    base = _manifest(SLUG)
    pm.write_manifest(base)

    # When
    result = pm.transition(
        SLUG, allowed_from=frozenset({pm.State.PENDING}), to=pm.State.APPROVED,
        message_id="msg-9", approval_ts=1_799_999_900,
    )

    # Then
    assert result == dataclasses.replace(
        base, state=pm.State.APPROVED, message_id="msg-9", approval_ts=1_799_999_900
    )
    assert pm.load_manifest(SLUG) == result
