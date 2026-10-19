"""Release notice migration pins the transport without replacing audit/storage."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Final

import pytest

from automation import owner_notice, release_abandon, release_retire, skill_gate
from automation.release_abandon import ReleaseAbandonOrder
from automation.interop.owner_message import Action, OwnerMessage, Ref, Result

RECORD: Final = {
    "version": "v1.2.3", "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "message_id": "333", "channel_id": "222",
}
TIP: Final = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
STALE_LEGACY: Final = (
    "⛔ 릴리스 v1.2.3 승인 기준 aaaaaaaaaaaa가 origin/main bbbbbbbbbbbb와 달라 자동 완결할 수 없습니다. "
    "automation/release.sh를 실행하면 옛 요청을 감사 회수하고 새 승인을 요청합니다."
)
ABANDON_LEGACY: Final = "⛔ 만료 — superseded"
STALE_RENDERED: Final = (
    "대상: 릴리스 승인 기준 불일치 (v1.2.3)\n"
    "사실: 승인 기준 aaaaaaaaaaaa ≠ origin/main bbbbbbbbbbbb · 자동 완결 보류 (실행 완료)\n"
    "위치: 링크 없음 (공간 미상); 검색: Discord 검색 / v1.2.3\n"
    "인계: 소유자: 이 메시지 · 답글 automation/release.sh 실행 요청; 다음: 재실행 시 옛 요청 감사 회수 후 새 승인 요청\n"
    "되돌리기: 해당 없음"
)
ABANDON_RENDERED: Final = (
    "대상: 릴리스 승인 회수 (v1.2.3)\n"
    "사실: superseded · 감사 기록과 승인 원본 보존 (취소됨)\n"
    "위치: 여기\n"
    "인계: 소유자: 조치 없음; 다음: 추가 실행 없음\n"
    "되돌리기: 해당 없음"
)
STALE: Final = OwnerMessage(
    subject_key="v1.2.3", subject="릴리스 승인 기준 불일치",
    fact="승인 기준 aaaaaaaaaaaa ≠ origin/main bbbbbbbbbbbb · 자동 완결 보류",
    location=Ref(scope="message", channel_id="222", message_id="333", search=("Discord 검색", "v1.2.3")),
    owner=Action(verb="reply", target=Ref(scope="self"), argument="automation/release.sh 실행 요청"),
    agent_next="재실행 시 옛 요청 감사 회수 후 새 승인 요청",
    recovery="not_applicable", detail=Result(outcome="executed"),
)
ABANDONED: Final = OwnerMessage(
    subject_key="v1.2.3", subject="릴리스 승인 회수",
    fact="superseded · 감사 기록과 승인 원본 보존",
    location=Ref(scope="message", channel_id="222", message_id="333", search=("Discord 검색", "v1.2.3")),
    owner=Action(verb="none"), agent_next="추가 실행 없음",
    recovery="not_applicable", detail=Result(outcome="cancelled"),
)


@pytest.fixture
def deliveries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    sent: list[str] = []
    monkeypatch.setattr(skill_gate, "GATE_DIR", tmp_path)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fixture")
    monkeypatch.setenv("OWNER_NOTICE_CHANNEL_ID", "444")
    monkeypatch.setattr(owner_notice, "send_notice", lambda token, channel, body: sent.append(body))
    return sent


@pytest.mark.parametrize("contract_available", [True, False])
def test_stale_legacy_when_facade_has_no_capability(
    deliveries: list[str], monkeypatch: pytest.MonkeyPatch, contract_available: bool,
) -> None:
    # Given: either the optional contract or facade capability is absent.
    import sys
    if contract_available:
        monkeypatch.delattr(owner_notice, "ACCEPTS_OWNER_MESSAGE")
    else:
        monkeypatch.setitem(sys.modules, "automation.interop.owner_message", None)
    # When: an approved release is behind the tip.
    release_retire.notify_stale_approval(RECORD, TIP)
    # Then: the legacy bytes survive exactly.
    assert deliveries == [STALE_LEGACY]


def test_stale_envelope_when_tip_has_advanced(
    deliveries: list[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the real facade, with message capture before rendering.
    messages: list[OwnerMessage | None] = []
    original = owner_notice.notify_owner

    def capture(content: str, *, message: OwnerMessage | None = None, fault: bool = False) -> bool:
        messages.append(message)
        return original(content, message=message)

    monkeypatch.setattr(owner_notice, "notify_owner", capture)
    # When: stale detection completes (not a release execution).
    release_retire.notify_stale_approval(RECORD, TIP)
    # Then: each field matches an independent fixed fixture.
    assert messages == [STALE]
    assert deliveries == [STALE_RENDERED]


def test_abandon_envelope_when_record_is_archived(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the original request and an injected reply transport.
    from automation.interop import owner_message
    path = tmp_path / "pending/release.json"
    path.parent.mkdir()
    path.write_text(json.dumps(RECORD))
    messages: list[OwnerMessage] = []
    original = owner_message.render

    def capture(message: OwnerMessage, *, destination: Ref) -> str:
        messages.append(message)
        return original(message, destination=destination)

    monkeypatch.setattr(owner_message, "render", capture)
    posts: list[str] = []
    monkeypatch.setattr(skill_gate, "_api", lambda method, path, payload: posts.append(payload["content"]))
    order = ReleaseAbandonOrder("v1.2.3", RECORD["head_sha"], "333", "superseded", "operator")
    # When: the audited operation returns through the real entry point.
    result = release_abandon.abandon(tmp_path, order, tmp_path / "audit.jsonl")
    # Then: cancellation is reported, not a released build.
    assert result.exit_code == 0
    assert messages == [ABANDONED]
    assert posts == [ABANDON_RENDERED]


@pytest.mark.parametrize(("record", "location"), [
    ({**RECORD, "approval_guild_id": "111"}, Ref(
        scope="message", space="guild", guild_id="111", channel_id="222", message_id="333",
        search=("Discord 검색", "v1.2.3"),
    )),
    ({"version": "v1.2.3", "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}, Ref(
        scope="message", search=("Discord 검색", "v1.2.3"),
    )),
])
def test_release_fields_when_coordinates_vary(record: dict[str, str], location: Ref) -> None:
    # Given: stored coordinates or a legacy record with only searchable identity.
    # When: the one-shot detection and retirement envelopes are built.
    messages = (release_retire.stale_message(record, TIP), release_retire.abandoned_message(record, "superseded"))
    # Then: the explicit independent locations are retained in every field.
    assert messages == (replace(STALE, location=location), replace(ABANDONED, location=location))


def test_abandon_legacy_when_contract_import_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a durable record and a runtime without the optional contract.
    import sys
    monkeypatch.setitem(sys.modules, "automation.interop.owner_message", None)
    path = tmp_path / "pending/release.json"
    path.parent.mkdir()
    encoded = json.dumps(RECORD).encode()
    path.write_bytes(encoded)
    posted: list[str] = []
    monkeypatch.setattr(skill_gate, "_api", lambda method, path, payload: posted.append(payload["content"]))
    order = ReleaseAbandonOrder("v1.2.3", RECORD["head_sha"], "333", "superseded", "operator")
    # When: the exact release is abandoned.
    result = release_abandon.abandon(tmp_path, order, tmp_path / "audit.jsonl")
    # Then: the original decision is archived, independently of the notice generation.
    assert result.exit_code == 0
    assert posted == [ABANDON_LEGACY]
    assert result.archived is not None
    assert result.archived.read_bytes() == encoded
    assert json.loads((tmp_path / "audit.jsonl").read_text())["event"] == "release-abandon"
