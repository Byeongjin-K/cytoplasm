"""One live patent-export approval per ``patent:{slug}``."""
from __future__ import annotations

import dataclasses
import importlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from urllib.error import HTTPError
from urllib.parse import unquote

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "patent-prep"))

patent_export = importlib.import_module("scripts.patent_export")
gate = importlib.import_module("scripts.patent_export_gate")
manifest = importlib.import_module("scripts.patent_export_manifest")
storage = importlib.import_module("scripts.patent_storage")
watch = importlib.import_module("scripts.patent_export_confirm_reaction_watch")

OWNER = "owner-patent-live"
SLUG = "single-live"
APPROVALS_CHANNEL = "1528936606946122421"  # digit-only: bindings refuse a placeholder id
OWNER_DM_CHANNEL = "1526487936065952385"  # the DM this bot opens with the owner
AGENT_CHAT_CHANNEL = "1526487936065952390"
AGENT_CHAT_THREAD = "1526487936065952391"
REQUEST_THREAD = "1526487936065952392"
NOW = 1_800_000_000


class FakeDiscord:
    """Mutable offline Discord surface whose purpose is recording approval I/O order."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.messages: dict[str, str] = {}
        self.reactions: dict[tuple[str, str], list[dict[str, str | bool]]] = {}
        self.threads: list[str] = []
        self.post_channels: list[str] = []
        self.posts = 0

    def request_thread_id(self, index: int) -> str:
        """Every created request thread is a DISTINCT snowflake, as Discord returns."""
        return f"{int(REQUEST_THREAD) + index}"

    def api(
        self, method: str, path: str, payload: dict[str, str] | None = None
    ) -> dict[str, str] | list[dict[str, str | bool]] | None:
        parts = path.strip("/").split("/")
        if method == "POST" and parts == ["users", "@me", "channels"]:
            return {"id": OWNER_DM_CHANNEL}
        if method == "GET" and len(parts) == 2 and parts[0] == "channels":
            if parts[1] == OWNER_DM_CHANNEL:
                return {"id": parts[1], "type": 1, "recipients": [{"id": OWNER}]}
            if parts[1] == AGENT_CHAT_CHANNEL:
                return {"id": parts[1], "type": 0, "name": "agent-chat", "guild_id": "guild"}
            if parts[1] == AGENT_CHAT_THREAD:
                return {
                    "id": parts[1],
                    "type": 11,
                    "name": "승인-patent-export",
                    "parent_id": AGENT_CHAT_CHANNEL,
                }
            for index, name in enumerate(self.threads):
                if parts[1] == self.request_thread_id(index):
                    # A request thread is described by the name it was created with.
                    return {"id": parts[1], "type": 11, "name": name, "parent_id": AGENT_CHAT_CHANNEL}
            return {"id": parts[1], "type": 0, "name": "approvals"}
        if method == "GET" and path == "/guilds/guild/threads/active":
            return {"threads": [{
                "id": AGENT_CHAT_THREAD,
                "type": 11,
                "name": "승인-patent-export",
                "parent_id": AGENT_CHAT_CHANNEL,
            }]}
        if method == "POST" and path.endswith("/threads"):
            self.threads.append(str((payload or {})["name"]))
            return {"id": self.request_thread_id(len(self.threads) - 1)}
        if method == "POST" and parts == ["channels", AGENT_CHAT_CHANNEL, "messages"]:
            return {"id": "announcement-1"}
        if method == "POST" and path.endswith("/messages"):
            self.posts += 1
            self.post_channels.append(parts[1])
            message_id = f"msg-{self.posts}"
            self.messages[message_id] = str((payload or {})["content"])
            self.calls.append(f"POST:{message_id}")
            return {"id": message_id}
        message_id = parts[3] if len(parts) > 3 else ""
        if method == "PUT":
            return None
        if method == "DELETE":
            self.calls.append(f"DELETE:{message_id}")
            self.messages.pop(message_id, None)
            return None
        if method == "GET" and "/reactions/" in path:
            emoji = unquote(path.split("/reactions/", 1)[1].split("?", 1)[0])
            return list(self.reactions.get((message_id, emoji), []))
        if method == "GET" and "/messages/" in path:
            if message_id not in self.messages:
                raise HTTPError("https://discord.invalid", 404, "missing", None, None)
            return {"id": message_id, "content": self.messages[message_id]}
        raise AssertionError(f"unexpected Discord call: {method} {path}")


@dataclass(frozen=True, slots=True)
class PatentEnv:
    paths: storage.PatentPaths
    export_root: Path
    fake: FakeDiscord
    minted: list[str]


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PatentEnv:
    export_root = tmp_path / "export"
    interop = tmp_path / "interop.json"
    interop.write_text(
        json.dumps({
            "owner_id": OWNER,
            "personal_approvals_channel_id": APPROVALS_CHANNEL,
            "agent_chat_channel_id": AGENT_CHAT_CHANNEL,
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(_REPO))
    monkeypatch.setenv("PATENT_EXPORT_ROOT", str(export_root))
    monkeypatch.setenv("PATENT_ARCHIVE_FOLDER_ID", "folder-live")
    monkeypatch.setenv("PATENT_DRAFT_ROOT", str(tmp_path / "drafts"))
    monkeypatch.setenv("PATENT_STATUS_ROOT", str(tmp_path / "status"))
    monkeypatch.setenv("INTEROP_CONFIG", str(interop))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "unit-test-envtoken")
    monkeypatch.setattr(manifest, "now_ts", lambda: NOW)
    minted: list[str] = []

    def mint_nonce() -> str:
        nonce = f"{len(minted) + 1:032x}"
        minted.append(nonce)
        return nonce

    monkeypatch.setattr(manifest, "mint_nonce", mint_nonce)
    fake = FakeDiscord()
    monkeypatch.setattr(gate, "_api", fake.api)
    paths = storage.PatentPaths.from_environment()
    storage.private_directory(paths.workspace_root / SLUG)
    storage.write_private(paths.workspace_root / SLUG / "draft.md", "private draft\n")
    return PatentEnv(paths, export_root, fake, minted)


def test_same_request_posts_nothing_and_reuses_message_and_nonce(env: PatentEnv) -> None:
    # Given
    patent_export.prepare_export(env.paths, SLUG, mode="enc")
    first = manifest.load_manifest(SLUG)
    env.fake.calls.clear()

    # When
    patent_export.prepare_export(env.paths, SLUG, mode="enc")
    second = manifest.load_manifest(SLUG)

    # Then
    assert env.fake.calls == []
    assert second.message_id == first.message_id == "msg-1"
    assert second.nonce == first.nonce
    assert env.minted == [first.nonce]


def test_changed_authorization_deletes_before_supersede_and_leaves_one_live(
    env: PatentEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    patent_export.prepare_export(env.paths, SLUG, mode="enc")
    original_write = manifest.write_manifest

    def recording_write(entry: manifest.Manifest) -> None:
        if entry.state is manifest.State.CANCELLED:
            env.fake.calls.append(f"SUPERSEDE:{entry.message_id}")
        original_write(entry)

    monkeypatch.setattr(manifest, "write_manifest", recording_write)
    env.fake.calls.clear()

    # When
    patent_export.prepare_export(env.paths, SLUG, mode="plaintext")

    # Then
    assert env.fake.calls.index("DELETE:msg-1") < env.fake.calls.index("SUPERSEDE:msg-1")
    assert env.fake.calls.index("SUPERSEDE:msg-1") < env.fake.calls.index("POST:msg-2")
    current = manifest.load_manifest(SLUG)
    assert current.message_id == "msg-2"
    assert set(env.fake.messages) == {"msg-2"}
    # TRANSFERRED from the characterization test that pinned the slug-overwrite defect:
    # replacement is now legal only after the old approval surface was deleted first.
    assert "msg-1" not in manifest.manifest_path(SLUG).read_text(encoding="utf-8")
    assert len(list(env.export_root.glob("*.json"))) == 1


def test_duplicate_and_superseding_requests_reuse_the_first_request_thread(env: PatentEnv) -> None:
    # Given: one export request already posted into its own thread
    patent_export.prepare_export(env.paths, SLUG, mode="enc")

    # When: the same export is requested again (same hash → PENDING) and then superseded
    patent_export.prepare_export(env.paths, SLUG, mode="enc")
    patent_export.prepare_export(env.paths, SLUG, mode="plaintext")

    # Then: one approval key keeps ONE thread — no empty orphan per retry or supersede
    assert env.fake.threads == [f"📜 특허 반출 · {SLUG}"]
    assert env.fake.post_channels == [env.fake.request_thread_id(0)] * 2


def test_approved_manifest_defers_without_transition_or_delete(env: PatentEnv) -> None:
    # Given
    patent_export.prepare_export(env.paths, SLUG, mode="enc")
    with manifest.lock(SLUG):
        manifest.transition(
            SLUG,
            allowed_from=frozenset({manifest.State.PENDING}),
            to=manifest.State.APPROVED,
            approval_ts=NOW,
        )
    before = manifest.manifest_path(SLUG).read_bytes()
    env.fake.calls.clear()

    # When / Then
    with pytest.raises(gate.ExportGateError):
        patent_export.prepare_export(env.paths, SLUG, mode="plaintext")
    assert manifest.manifest_path(SLUG).read_bytes() == before
    assert env.fake.calls == []
    assert manifest.load_manifest(SLUG).state is manifest.State.APPROVED


def test_corrupt_manifest_refuses_without_posting(env: PatentEnv) -> None:
    # Given
    manifest.manifest_path(SLUG).write_text("{not json", encoding="utf-8")

    # When / Then
    with pytest.raises(gate.ExportGateError):
        patent_export.prepare_export(env.paths, SLUG, mode="enc")
    assert env.fake.posts == 0
    assert manifest.manifest_path(SLUG).read_text(encoding="utf-8") == "{not json"


def test_watcher_aborts_when_manifest_changes_after_reaction_read(
    env: PatentEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    patent_export.prepare_export(env.paths, SLUG, mode="enc")
    original = manifest.load_manifest(SLUG)
    replacement = dataclasses.replace(
        original,
        plaintext_sha256="sha256:" + "b" * 64,
        nonce="f" * 32,
        message_id="msg-new",
    )

    def swap_manifest(_entry: manifest.Manifest) -> str:
        manifest.write_manifest(replacement)
        return gate.APPROVE_EMOJI

    monkeypatch.setattr(gate, "reaction_state", swap_manifest)

    # When
    watch._process(original, NOW)

    # Then
    assert manifest.load_manifest(SLUG) == replacement
    assert manifest.load_manifest(SLUG).state is manifest.State.PENDING


def test_lease_reuses_manifest_lock_without_creating_a_second_lock(env: PatentEnv) -> None:
    # Given
    approval: ModuleType = importlib.import_module("scripts.patent_export_approval")

    # When / Then
    with approval.confirm_lease().hold(f"patent:{SLUG}") as owned:
        assert owned is True
        with pytest.raises(manifest.ManifestError):
            with manifest.lock(SLUG):
                raise AssertionError("the same manifest lock must already be held")
    assert sorted(path.name for path in env.export_root.glob("*.lock")) == [f"{SLUG}.lock"]
    assert list(env.export_root.rglob("*.lease")) == []
