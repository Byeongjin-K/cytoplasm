"""AS-1.6 — the manifest decides WHERE a patent-export approval lives.

A new request resolves the surface ONCE, persists it in the manifest, and every
later read/react/delete replays that stored answer. Since v7 a new request lands
in its ``#agent-chat`` thread, so these cases also lock the direction that matters
most: a manifest bound to the guild ``#approvals`` before the flip is still acted
on in the channel its message actually lives in, never retargeted to the new
surface.
"""
from __future__ import annotations

import importlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final
from urllib.parse import unquote

import pytest

_REPO: Final = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills" / "patent-prep"))

gate = importlib.import_module("scripts.patent_export_gate")
manifest = importlib.import_module("scripts.patent_export_manifest")
patent_export = importlib.import_module("scripts.patent_export")
storage = importlib.import_module("scripts.patent_storage")

OWNER: Final = "280680578404010625"
GUILD_APPROVALS: Final = "1528936606946122421"
RESOLVER_CHANNEL: Final = "1999999999999999999"
OWNER_DM: Final = "1526487936065952385"
AGENT_CHAT_CHANNEL: Final = "1526487936065952390"
AGENT_CHAT_THREAD: Final = "1526487936065952391"
REQUEST_THREAD: Final = "1526487936065952392"
SLUG: Final = "binding-slug"
DOC_TITLE: Final = "발명의 명칭 초음파 기반 3차원 스캐너"
DOC_BODY: Final = "청구항 1 본문 비밀"
NOW: Final = 1_800_000_000
SHA: Final = "sha256:" + "a" * 64
FOLDER: Final = "folder-bound"
MESSAGE_ID: Final = "msg-1"

APPROVAL_CONTENT: Final = (
    "PATENT EXPORT APPROVAL REQUEST\n"
    f"slug: {SLUG}\n"
    f"sha256: {SHA}\n"
    f"dest_folder_id: {FOLDER}\n"
    f"expiry_ts: {NOW + 3600}\n"
    "mode=enc\n"
)


@dataclass(slots=True)
class FakeDiscord:
    """Offline Discord surface that records which channel every call touched."""

    messages: dict[str, tuple[str, str]] = field(default_factory=dict)
    reactions: dict[tuple[str, str], list[dict[str, object]]] = field(default_factory=dict)
    touched: list[tuple[str, str]] = field(default_factory=list)
    threads: list[str] = field(default_factory=list)
    posted: int = 0

    def api(self, method: str, path: str, payload: dict | None = None) -> object:
        parts = path.strip("/").split("/")
        if method == "GET" and path == "/guilds/guild/threads/active":
            return {"threads": [{
                "id": AGENT_CHAT_THREAD,
                "type": 11,
                "name": "승인-patent-export",
                "parent_id": AGENT_CHAT_CHANNEL,
            }]}
        if parts[:2] == ["users", "@me"] and parts[2:] == ["channels"]:
            return {"id": OWNER_DM}
        if parts[0] != "channels":
            raise AssertionError(f"unexpected Discord call: {method} {path}")
        channel = parts[1]
        self.touched.append((method, channel))
        if len(parts) == 2:
            return self._describe(channel)
        if method == "POST" and parts[-1] == "threads":
            self.threads.append(str((payload or {})["name"]))
            return {"id": REQUEST_THREAD}
        if method == "POST" and parts[-1] == "messages":
            self.posted += 1
            message_id = f"msg-{self.posted}"
            self.messages[message_id] = (channel, str((payload or {})["content"]))
            return {"id": message_id}
        message_id = parts[3]
        if method == "PUT":
            return None
        if method == "DELETE":
            self.messages.pop(message_id, None)
            return None
        if "/reactions/" in path:
            emoji = unquote(path.split("/reactions/", 1)[1].split("?", 1)[0])
            return list(self.reactions.get((message_id, emoji), []))
        return {"id": message_id, "content": self.messages[message_id][1]}

    def _describe(self, channel: str) -> dict[str, object]:
        if channel == OWNER_DM:
            return {"id": channel, "type": 1, "recipients": [{"id": OWNER}]}
        if channel == AGENT_CHAT_CHANNEL:
            return {"id": channel, "type": 0, "name": "agent-chat", "guild_id": "guild"}
        if channel == AGENT_CHAT_THREAD:
            return {
                "id": channel,
                "type": 11,
                "name": "승인-patent-export",
                "parent_id": AGENT_CHAT_CHANNEL,
            }
        if channel == REQUEST_THREAD:
            return {
                "id": channel,
                "type": 11,
                "name": self.threads[-1] if self.threads else "",
                "parent_id": AGENT_CHAT_CHANNEL,
            }
        return {"id": channel, "type": 0, "name": "approvals"}

    def seed(self, channel: str, content: str) -> None:
        self.messages[MESSAGE_ID] = (channel, content)

    def approve(self, message_id: str = MESSAGE_ID) -> None:
        self.reactions = {(message_id, gate.APPROVE_EMOJI): [{"id": OWNER, "bot": False}]}

    def channels(self) -> set[str]:
        return {channel for _method, channel in self.touched}


@dataclass(frozen=True, slots=True)
class BindingEnv:
    export_root: Path
    fake: FakeDiscord
    paths: object
    interop: Path


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BindingEnv:
    export_root = tmp_path / "export"
    interop = tmp_path / "interop.json"
    interop.write_text(
        json.dumps({
            "owner_id": OWNER,
            "personal_approvals_channel_id": GUILD_APPROVALS,
            "agent_chat_channel_id": AGENT_CHAT_CHANNEL,
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(_REPO))
    monkeypatch.setenv("INTEROP_CONFIG", str(interop))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "unit-test-envtoken")
    monkeypatch.setenv("PATENT_EXPORT_ROOT", str(export_root))
    monkeypatch.setenv("PATENT_ARCHIVE_FOLDER_ID", FOLDER)
    monkeypatch.setenv("PATENT_DRAFT_ROOT", str(tmp_path / "drafts"))
    monkeypatch.setenv("PATENT_STATUS_ROOT", str(tmp_path / "status"))
    monkeypatch.setattr(manifest, "now_ts", lambda: NOW)
    fake = FakeDiscord()
    monkeypatch.setattr(gate, "_api", fake.api)
    paths = storage.PatentPaths.from_environment()
    storage.private_directory(paths.workspace_root / SLUG)
    storage.write_private(paths.workspace_root / SLUG / "draft.md", "private draft\n")
    return BindingEnv(export_root, fake, paths, interop)


def _persist(env: BindingEnv, **binding: object) -> None:
    """Write a manifest row directly so pre- and post-schema shapes are both expressible."""
    payload: dict[str, object] = {
        "slug": SLUG,
        "plaintext_sha256": SHA,
        "dest_folder_id": FOLDER,
        "mode": "enc",
        "expiry_ts": NOW + 3600,
        "nonce": "a" * 32,
        "state": "PENDING",
        "message_id": MESSAGE_ID,
        "created_ts": NOW,
        "approval_ts": None,
    }
    payload.update(binding)
    env.export_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    (env.export_root / f"{SLUG}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_reaction_state_uses_the_manifest_binding(env: BindingEnv) -> None:
    # Given: the manifest is bound to the guild #approvals id, where its approval
    # message really lives, while a fresh resolution would pick a DIFFERENT channel
    # on EITHER surface — the owner DM by current policy, and, if the stored
    # skill-approvals surface were re-resolved instead of replayed, the configured
    # channel below. AS-3.2 retired the env override that used to seed this gap, so
    # the gap now comes from the config key the shared directory actually reads.
    env.interop.write_text(
        json.dumps({"owner_id": OWNER, "personal_approvals_channel_id": RESOLVER_CHANNEL}),
        encoding="utf-8",
    )
    _persist(
        env,
        kind="patent-export",
        surface="skill-approvals",
        channel_id=GUILD_APPROVALS,
        policy_version=1,
    )
    env.fake.seed(GUILD_APPROVALS, APPROVAL_CONTENT)
    env.fake.approve()

    # When: the owner's reaction is read.
    state = gate.reaction_state(manifest.load_manifest(SLUG))

    # Then: every Discord call named the manifest's channel, never the resolver's.
    assert env.fake.channels() == {GUILD_APPROVALS}
    assert state == gate.APPROVE_EMOJI


def test_v1_manifest_still_resolves_to_the_guild_channel(env: BindingEnv) -> None:
    # Given: a pre-schema manifest — no kind/surface/channel_id/policy_version —
    # whose one live approval message was posted to #approvals before AS-1.6.
    _persist(env)
    env.fake.seed(GUILD_APPROVALS, APPROVAL_CONTENT)
    env.fake.approve()

    # When: the owner's reaction is read.
    state = gate.reaction_state(manifest.load_manifest(SLUG))

    # Then: the legacy row stays consumable against the guild surface.
    assert env.fake.channels() == {GUILD_APPROVALS}
    assert state == gate.APPROVE_EMOJI


def test_a_new_request_persists_the_binding_it_posted_under(env: BindingEnv) -> None:
    # Given / When: a brand-new export approval is requested.
    patent_export.prepare_export(env.paths, SLUG, mode="enc")

    # Then: the manifest carries the binding it was posted under, not a blank to re-resolve,
    # and the recorded channel is where the message really is.
    stored = manifest.load_manifest(SLUG)
    policy = importlib.import_module("automation.interop.approval_surface")
    assert stored.kind == "patent-export"
    assert stored.policy_version == policy.POLICY_VERSION
    assert env.fake.messages[str(stored.message_id)][0] == stored.channel_id


def test_new_export_request_posts_to_the_agent_chat_thread(env: BindingEnv) -> None:
    # Given / When: a brand-new export approval is requested under current policy.
    patent_export.prepare_export(env.paths, SLUG, mode="enc")

    # Then: it lives in the thread opened for THIS request, while the shared
    # guild #approvals — which the peer attestation bot can also read — gains nothing,
    # and the per-kind thread that used to collect every export is not reused.
    stored = manifest.load_manifest(SLUG)
    assert (stored.surface, stored.channel_id) == ("agent-chat-thread", REQUEST_THREAD)
    assert env.fake.messages[str(stored.message_id)][0] == REQUEST_THREAD
    assert GUILD_APPROVALS not in env.fake.channels()
    assert AGENT_CHAT_THREAD not in env.fake.channels()


def test_new_export_request_names_its_thread_with_the_export_id_only(env: BindingEnv) -> None:
    # Given: a draft whose document title and body are the masked content.
    storage.write_private(
        env.paths.workspace_root / SLUG / "draft.md",  # type: ignore[attr-defined]
        f"{DOC_TITLE}\n{DOC_BODY}\n",
    )

    # When: a brand-new export approval is requested.
    patent_export.prepare_export(env.paths, SLUG, mode="enc")

    # Then: exactly one thread was opened, named by the export id and nothing else.
    assert env.fake.threads == [f"📜 특허 반출 · {SLUG}"]
    for secret in (DOC_TITLE, DOC_BODY, "draft.md", SHA):
        assert secret not in env.fake.threads[0]


def test_a_new_request_persists_the_approval_thread_it_was_posted_in(env: BindingEnv) -> None:
    # Given / When: a brand-new export approval is requested.
    patent_export.prepare_export(env.paths, SLUG, mode="enc")

    # Then: the manifest names the approval thread, and the content the gate binds
    # against (sha/dest/expiry/mode) is unchanged by the added field.
    stored = manifest.load_manifest(SLUG)
    content = env.fake.messages[str(stored.message_id)][1]
    assert stored.approval_thread_id == stored.channel_id == REQUEST_THREAD
    assert gate.approval_binding_matches(stored, content)
    assert "approval_thread_id" not in content


def test_a_legacy_manifest_without_an_approval_thread_id_still_loads(env: BindingEnv) -> None:
    # Given: a manifest written before the request-thread schema.
    _persist(env, kind="patent-export", surface="skill-approvals", channel_id=GUILD_APPROVALS, policy_version=1)

    # When / Then: it loads with no thread id instead of refusing.
    assert manifest.load_manifest(SLUG).approval_thread_id is None


def test_the_approval_message_carries_the_policy_reaction_line(env: BindingEnv) -> None:
    # Given / When: a brand-new export approval is posted.
    patent_export.prepare_export(env.paths, SLUG, mode="enc")

    # Then: both machine-consumed reactions survive the new envelope instruction,
    # while the sha256 binding the gate verifies is unaffected.
    stored = manifest.load_manifest(SLUG)
    content = env.fake.messages[str(stored.message_id)][1]
    assert gate.APPROVE_EMOJI in content
    assert gate.CANCEL_EMOJI in content
    assert "#approvals" not in content
    assert gate.approval_binding_matches(stored, content)


def test_supersede_deletes_from_the_manifest_channel_not_the_current_policy(
    env: BindingEnv,
) -> None:
    # Given: a live v1 request whose message really sits in the guild #approvals,
    # while current policy would post a brand-new request to its #agent-chat thread.
    _persist(
        env,
        kind="patent-export",
        surface="skill-approvals",
        channel_id=GUILD_APPROVALS,
        policy_version=1,
    )
    env.fake.seed(GUILD_APPROVALS, APPROVAL_CONTENT)
    env.fake.posted = 1  # so the replacement message takes a fresh id
    env.fake.touched.clear()

    # When: changed authorization supersedes it.
    patent_export.prepare_export(env.paths, SLUG, mode="plaintext")

    # Then: the DELETE hit the channel the superseded message actually lives in,
    # and only the replacement moved to the current surface.
    assert ("DELETE", GUILD_APPROVALS) in env.fake.touched
    assert not [channel for method, channel in env.fake.touched if method == "DELETE" and channel != GUILD_APPROVALS]
    replacement = manifest.load_manifest(SLUG)
    assert env.fake.messages[str(replacement.message_id)][0] == REQUEST_THREAD


def test_the_manifests_default_kind_still_names_the_policy_enum() -> None:
    # Given: a deployed skill cannot import the policy enum, so the manifest module
    # mirrors the kind as a literal for pre-schema rows.
    surface = importlib.import_module("automation.interop.approval_surface")

    # When / Then: the mirror must not drift from the enum it stands in for.
    assert manifest._KIND == surface.ApprovalKind.PATENT_EXPORT.value
