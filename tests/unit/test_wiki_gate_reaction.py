from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "skills" / "wiki" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import wiki_gate  # noqa: E402

OWNER_ID = "owner-1"
CHANNEL_ID = "1526487936065952385"
NOTICE_ID = "wiki-request-notice"
AGENT_CHAT_ID = "1526487936065952400"
MESSAGE_ID = "message-1"
NOTE_TEXT = (
    "---\n"
    'title: "Reaction Gate"\n'
    "tags: [test]\n"
    "created: 2026-07-21T00:00:00Z\n"
    "updated: 2026-07-21T00:00:00Z\n"
    "links: []\n"
    "---\n"
    "본문\n"
)


@dataclass
class FakeDiscordRest:
    users_by_emoji: dict[str, list[dict[str, str | bool]]]
    content: str = ""
    message_channel_id: str = CHANNEL_ID
    missing_message: bool = False
    calls: list[tuple[str, str, dict[str, str] | None]] = field(default_factory=list)
    threads: list[str] = field(default_factory=list)
    #: 요청별 스레드가 매달리는 안내 메시지 본문 — 승인 카드와 구별해 센다.
    notices: list[str] = field(default_factory=list)

    def __call__(
        self,
        method: str,
        path: str,
        payload: dict[str, str] | None = None,
    ) -> dict[str, str] | list[dict[str, str | bool]] | None:
        self.calls.append((method, path, payload))
        if method == "POST" and path == "/users/@me/channels":
            return {"id": CHANNEL_ID}
        if method == "POST" and path == f"/channels/{AGENT_CHAT_ID}/messages":
            # 요청별 스레드가 매달릴 안내 메시지 (2026-09-09).
            self.notices.append(str((payload or {})["content"]))
            return {"id": NOTICE_ID}
        if method == "POST" and path == f"/channels/{AGENT_CHAT_ID}/messages/{NOTICE_ID}/threads":
            self.threads.append(str((payload or {})["name"]))
            return {"id": CHANNEL_ID, "type": 11, "parent_id": AGENT_CHAT_ID}
        if method == "GET" and path == f"/channels/{CHANNEL_ID}":
            if self.threads:
                return {"id": CHANNEL_ID, "name": self.threads[-1], "type": 11, "parent_id": AGENT_CHAT_ID}
            return {"id": CHANNEL_ID, "name": "", "recipients": [{"id": OWNER_ID}], "type": 1}
        if method == "POST" and path == f"/channels/{CHANNEL_ID}/messages":
            assert payload is not None
            self.content = payload["content"]
            return {"id": MESSAGE_ID, "channel_id": CHANNEL_ID, "content": self.content}
        if method == "PUT" and path.startswith(
            f"/channels/{CHANNEL_ID}/messages/{MESSAGE_ID}/reactions/"
        ):
            return None
        if method == "GET" and path == f"/channels/{CHANNEL_ID}/messages/{MESSAGE_ID}":
            if self.missing_message:
                raise HTTPError("https://discord.invalid", 404, "missing", None, None)
            return {"id": MESSAGE_ID, "channel_id": self.message_channel_id, "content": self.content}
        for emoji, users in self.users_by_emoji.items():
            encoded = quote(emoji, safe="")
            if method == "GET" and path == (
                f"/channels/{CHANNEL_ID}/messages/{MESSAGE_ID}/reactions/{encoded}?limit=100"
            ):
                return users
        if method == "GET" and "/reactions/" in path:
            return []
        raise AssertionError(f"unexpected Discord call: {method} {path}")


def _draft(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setenv("WIKI_GATE_DIR", str(tmp_path / "gate"))
    monkeypatch.setenv("WIKI_ROOT", str(tmp_path / "wiki"))
    monkeypatch.setattr(wiki_gate, "GATE_DIR", tmp_path / "gate")
    return wiki_gate.create_draft("create", "reaction-gate", NOTE_TEXT, CHANNEL_ID)


def _post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake: FakeDiscordRest,
) -> dict:
    draft = _draft(tmp_path, monkeypatch)
    interop = tmp_path / "interop.json"
    interop.write_text(
        json.dumps({"owner_id": OWNER_ID, "agent_chat_channel_id": AGENT_CHAT_ID}), encoding="utf-8",
    )
    monkeypatch.setenv("INTEROP_CONFIG", str(interop))
    monkeypatch.setenv("AUTOPHAGY_REPO_ROOT", str(_REPO))
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "unit-test-token")
    monkeypatch.setattr(wiki_gate, "owner_id", lambda: OWNER_ID)
    monkeypatch.setattr(wiki_gate, "_api", fake)
    posted = wiki_gate.post_confirm_message(draft)
    return wiki_gate.load_draft(posted["id"])


def _saved_note(tmp_path: Path) -> Path:
    return tmp_path / "wiki" / "reaction-gate.md"


def test_post_confirm_message_preadds_reactions_and_records_bound_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    fake = FakeDiscordRest({})

    # When
    draft = _post(tmp_path, monkeypatch, fake)

    # Then
    assert draft["confirm_message_id"] == MESSAGE_ID
    assert fake.calls == [
        (
            "POST",
            f"/channels/{AGENT_CHAT_ID}/messages",
            {"content": fake.notices[0]},
        ),
        (
            "POST",
            f"/channels/{AGENT_CHAT_ID}/messages/{NOTICE_ID}/threads",
            {"name": f"📚 위키 · {draft['id']}", "auto_archive_duration": 10080},
        ),
        ("GET", f"/channels/{CHANNEL_ID}", None),
        ("POST", f"/channels/{CHANNEL_ID}/messages", {"content": wiki_gate.confirm_text(draft)}),
        (
            "PUT",
            f"/channels/{CHANNEL_ID}/messages/{MESSAGE_ID}/reactions/%E2%9C%85/@me",
            None,
        ),
        (
            "PUT",
            f"/channels/{CHANNEL_ID}/messages/{MESSAGE_ID}/reactions/%E2%9B%94/@me",
            None,
        ),
    ]
    assert wiki_gate.load_draft(draft["id"])["confirm_message_id"] == MESSAGE_ID


def test_resolve_reaction_saves_when_owner_approves_bound_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    fake = FakeDiscordRest({wiki_gate.APPROVE_EMOJI: [{"id": OWNER_ID, "bot": False}]})
    draft = _post(tmp_path, monkeypatch, fake)
    monkeypatch.setattr(wiki_gate, "owner_id", lambda: OWNER_ID)

    # When
    saved = wiki_gate.resolve_reaction(draft)

    # Then
    assert saved == _saved_note(tmp_path)
    assert _saved_note(tmp_path).read_text(encoding="utf-8") == NOTE_TEXT
    stored = json.loads((tmp_path / "gate" / "drafts" / f"{draft['id']}.json").read_text())
    assert stored["status"] == "saved"
    assert stored["method"] == "reaction"
    audit = (tmp_path / "gate" / "audit.jsonl").read_text(encoding="utf-8")
    assert '"method": "reaction"' in audit


@pytest.mark.parametrize(
    "user",
    [{"id": "other-user", "bot": False}, {"id": OWNER_ID, "bot": True}],
)
def test_resolve_reaction_ignores_non_owner_or_bot_approve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    user: dict[str, str | bool],
) -> None:
    # Given
    fake = FakeDiscordRest({wiki_gate.APPROVE_EMOJI: [user]})
    draft = _post(tmp_path, monkeypatch, fake)
    monkeypatch.setattr(wiki_gate, "owner_id", lambda: OWNER_ID)

    # When
    saved = wiki_gate.resolve_reaction(draft)

    # Then
    assert saved is None
    assert not _saved_note(tmp_path).exists()
    assert wiki_gate.load_draft(draft["id"])["status"] == "pending"


def test_resolve_reaction_discards_when_owner_cancel_and_approve_both_exist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    fake = FakeDiscordRest(
        {
            wiki_gate.APPROVE_EMOJI: [{"id": OWNER_ID, "bot": False}],
            wiki_gate.CANCEL_EMOJI: [{"id": OWNER_ID, "bot": False}],
        }
    )
    draft = _post(tmp_path, monkeypatch, fake)
    monkeypatch.setattr(wiki_gate, "owner_id", lambda: OWNER_ID)

    # When / Then
    with pytest.raises(wiki_gate.GateError, match="취소"):
        wiki_gate.resolve_reaction(draft)
    assert not _saved_note(tmp_path).exists()
    with pytest.raises(wiki_gate.GateError, match="드래프트 없음"):
        wiki_gate.load_draft(draft["id"])


@pytest.mark.parametrize(
    ("content", "message_channel"),
    [("no draft digest here", CHANNEL_ID), (None, "other-channel")],
)
def test_resolve_reaction_rejects_hash_or_channel_binding_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    content: str | None,
    message_channel: str,
) -> None:
    # Given
    fake = FakeDiscordRest({wiki_gate.APPROVE_EMOJI: [{"id": OWNER_ID, "bot": False}]})
    draft = _post(tmp_path, monkeypatch, fake)
    if content is not None:
        fake.content = content
    fake.message_channel_id = message_channel
    monkeypatch.setattr(wiki_gate, "owner_id", lambda: OWNER_ID)

    # When / Then
    with pytest.raises(wiki_gate.GateError):
        wiki_gate.resolve_reaction(draft)
    assert not _saved_note(tmp_path).exists()
    assert wiki_gate.load_draft(draft["id"])["status"] == "pending"


def test_resolve_reaction_missing_message_fails_closed_without_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    fake = FakeDiscordRest(
        {wiki_gate.APPROVE_EMOJI: [{"id": OWNER_ID, "bot": False}]},
        missing_message=True,
    )
    draft = _post(tmp_path, monkeypatch, fake)
    monkeypatch.setattr(wiki_gate, "owner_id", lambda: OWNER_ID)

    # When
    saved = wiki_gate.resolve_reaction(draft)

    # Then
    assert saved is None
    assert not _saved_note(tmp_path).exists()
    assert wiki_gate.load_draft(draft["id"])["status"] == "pending"


def test_resolve_reaction_rejects_tampered_draft_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    fake = FakeDiscordRest({wiki_gate.APPROVE_EMOJI: [{"id": OWNER_ID, "bot": False}]})
    draft = _post(tmp_path, monkeypatch, fake)
    draft_path = tmp_path / "gate" / "drafts" / f"{draft['id']}.json"
    tampered = {**draft, "note_text": draft["note_text"] + "tampered\n"}
    draft_path.write_text(json.dumps(tampered, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    monkeypatch.setattr(wiki_gate, "owner_id", lambda: OWNER_ID)

    # When / Then
    with pytest.raises(wiki_gate.GateError, match="해시"):
        wiki_gate.resolve_reaction(tampered)
    assert not _saved_note(tmp_path).exists()


def test_signed_reaction_injection_validates_owner_channel_hash_and_reaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    draft = _draft(tmp_path, monkeypatch)
    monkeypatch.setenv("E2E_TEST_MODE", "1")
    monkeypatch.setenv("INTEROP_E2E_SECRET", "dummy-secret")
    monkeypatch.setattr(wiki_gate, "owner_id", lambda: OWNER_ID)
    out = tmp_path / "reaction.json"
    draft = {
        **draft,
        "kind": "wiki",
        "policy_version": 1,
        "surface": "owner-dm",
    }
    wiki_gate._write_json(wiki_gate._draft_path(draft["id"]), draft)

    # When
    wiki_gate.sign_injection(
        draft,
        out,
        OWNER_ID,
        CHANNEL_ID,
        forge_signature=False,
        reaction_emoji=wiki_gate.APPROVE_EMOJI,
    )

    # Then
    assert wiki_gate.confirm_via_injection(draft, out) == "injected-reaction:approve"


def test_watcher_is_reactions_only_and_reuses_shared_resolver() -> None:
    # Given
    watcher = _SCRIPTS / "wiki_confirm_reaction_watch.py"
    spec = importlib.util.spec_from_file_location("wiki_confirm_reaction_watch", watcher)
    assert spec and spec.loader

    # When
    source = watcher.read_text(encoding="utf-8")

    # Then
    assert source.count("resolve_reaction") >= 1
    assert "messages?limit" not in source
    assert "attachments" not in source
