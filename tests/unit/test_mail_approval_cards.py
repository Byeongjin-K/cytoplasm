"""Posted-copy equality pins for the three SAFE_SUBSTRING approval producers."""
from __future__ import annotations

import sys
import builtins
from dataclasses import asdict
from argparse import Namespace
from pathlib import Path
from importlib import import_module

from automation.entity_preflight.contracts import JsonValue

import pytest

ROOT = Path(__file__).resolve().parents[2]
for skill in ("mail", "calendar", "coordination"):
    sys.path.insert(0, str(ROOT / "skills" / skill / "scripts"))

calendar_confirm = import_module("calendar_confirm")
calendar_gate = import_module("calendar_gate")
coordination_approval = import_module("coordination_approval")
coordination_lifecycle = import_module("coordination_lifecycle")
triage_core = import_module("triage_core")
from automation.interop import owner_message  # noqa: E402
from tests.unit.mail_approval_card_golden import BYTES, FIELDS  # noqa: E402

INSTRUCTION = "이 메시지에 ✅ 실행 / ⛔ 취소"


def record() -> dict[str, JsonValue]:
    return dict(
        id="abc123", sha256="digest-1", created="2026-09-01T00:00:00Z",
        kind="reply", provider="mailon", category="important",
        tags=["private"], flags=["reply_needed"], sender_masked="sha256:masked",
        uid_opaque="sha256:opaque", mail_subject="문의", subject="Re: 문의", body="회신 본문",
        to="to@example.invalid", cc="cc@example.invalid", sender_account="sender@example.invalid",
        reply_target="reply-1", gmail_approval_snapshot={"action_kind": "reply"},
        approval_action_hash="sha256:action-1", changes=[["재료비", "잔액", "100", "50"]],
        prev_hash="previous", new_hash="next", project="예제", year=2026,
        action="create", summary="일정 제목", start="2026-09-01T09:00:00+09:00",
        end="2026-09-01T09:30:00+09:00", calendar_id="primary", event_id="",
    )


def render_case(name: str, draft: dict[str, JsonValue], monkeypatch: pytest.MonkeyPatch) -> str:
    """Drive the existing render root, replacing only its external effects."""
    match name:
        case "calendar":
            posts: list[str] = []

            def api(method: str, path: str, payload: dict[str, str] | None = None) -> dict[str, str] | None:
                if method == "POST":
                    assert payload is not None
                    posts.append(payload["content"])
                    return {"id": "333"}
                return None

            monkeypatch.setattr(calendar_confirm, "_api", api)
            calendar_confirm.post_confirmation_message(draft, "222")
            return posts.pop()
        case "coordination":
            posts = []

            def request(payload, owner, *, prepare):
                posts.append(prepare().content)
                return Namespace(draft_id="abc123")

            monkeypatch.setattr(calendar_gate, "build_draft", lambda **kw: draft)
            monkeypatch.setattr(calendar_gate, "persist_draft", lambda record: None)
            monkeypatch.setattr(coordination_approval, "request_confirmation", request)
            monkeypatch.setattr(coordination_lifecycle.io, "obs", lambda **kw: None)
            coordination_lifecycle.owner_leg(
                Namespace(summary="일정 제목", duration_min=30, calendar="primary", e2e_confirm=False,
                          peer="peer", origin_channel_id="", origin_message_id=""),
                {"owner_id": "111"}, "coord-1", None, draft["start"],
            )
            return posts.pop()
        case _:
            extra = {}
            if name == "compose":
                extra = {"kind": "compose"}
            if name == "gmail":
                extra = {"provider": "gmail"}
            destination = (triage_core.ApprovalRenderDestination.CONSOLE
                           if name == "original"
                           else triage_core.ApprovalRenderDestination.OWNER_DM)
            return triage_core.render_approvals_message(
                {**draft, **extra}, destination=destination, instruction=INSTRUCTION,
            )


LEGACY = {
    "original": '[mail-triage] 수신메일 회신 발송 승인 요청\n- 분류: important / 플래그: reply_needed\n- 발신(마스킹): `sha256:masked`\n- 원문 제목: 문의\n- Cc: `cc@example.invalid`\n- 회신 제목: Re: 문의\n- 회신 본문:\n```\n회신 본문\n```\n- draft: `abc123` sha256: `digest-1`\n- 반응(기본): 이 메시지에 ✅ 실행 / ⛔ 취소',
    "compose": '[mail-triage] 새 메일 발송 승인 요청 (DM 확정)\n- To: `to@example.invalid`\n- Cc: `cc@example.invalid`\n- 제목: `Re: 문의`\n- 본문:\n```\n회신 본문\n```\n- draft: `abc123` sha256: `digest-1`\n- 반응(기본): 이 메시지에 ✅ 실행 / ⛔ 취소',
    "gmail": '[mail-triage] Gmail 발송 승인 요청 (DM 확정)\n- 발신 계정: `sender@example.invalid`\n- 작업: `reply`\n- 수신자: `to@example.invalid`\n- Cc: `cc@example.invalid`\n- 회신 대상: `reply-1`\n- 제목: `Re: 문의`\n- 본문:\n```\n회신 본문\n```\n- draft: `abc123` sha256: `digest-1`\n- action hash: `sha256:action-1`\n- 반응(기본): 이 메시지에 ✅ 실행 / ⛔ 취소',
    "calendar": 'CHANGE-SUMMARY\n동작: 생성\n제목: 일정 제목\n일시: 2026-09-01 (화) 09:00 ~ 09:30 KST\n캘린더: primary\n대상 이벤트: (신규)\n\n이 메시지에 ✅ 실행 / ⛔ 취소. 텍스트 fallback: `실행 abc123`/`취소 abc123`\nsha256:digest-1',
    "coordination": '📅 일정 조율 (coord-1): 상대 에이전트(peer)가 2026-09-01 (화) 09:00~09:30 KST 슬롯을 승인했습니다.\n제목: 일정 제목\n이 메시지에 ✅ 실행 / ⛔ 취소 — 또는 `실행 abc123`/`취소 abc123` 텍스트도 가능\nsha256:digest-1',
}


@pytest.mark.parametrize("name", ["original", "compose", "gmail", "calendar", "coordination"])
def test_legacy_bytes_when_record_has_no_version(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given a fixed pre-version record; when its real render root runs; then posted bytes replay.
    assert render_case(name, {**record(), "render_version": "1"}, monkeypatch) == LEGACY[name]


@pytest.mark.parametrize("name", ["original", "compose", "gmail", "calendar", "coordination"])
def test_envelope_when_record_uses_version_two(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given a new rendering version; when rendered; then all five fields precede the binding.
    content = render_case(name, {**record(), "render_version": "2"}, monkeypatch)
    assert content.startswith("대상: ")
    assert [line.split(":", 1)[0] for line in content.splitlines()[:5]] == [
        "대상", "사실", "위치", "인계", "되돌리기",
    ]
    assert "위치: 이 메시지" in content.splitlines()
    assert "digest-1" in content
    assert "https://" not in content


@pytest.mark.parametrize("name", list(LEGACY))
def test_fields_and_posted_bytes_when_rendering_v2(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given independent literal captures; when the producer builds its envelope; then every field matches.
    seen: list[owner_message.OwnerMessage] = []
    real = owner_message.render

    def capture(message: owner_message.OwnerMessage, *, destination: owner_message.Ref) -> str:
        seen.append(message)
        assert destination == owner_message.Ref(scope="self")
        assert asdict(message) == FIELDS[name]
        return real(message, destination=destination)

    monkeypatch.setattr(owner_message, "render", capture)
    content = render_case(name, {**record(), "render_version": "2"}, monkeypatch)
    assert len(seen) == 1
    assert content == BYTES[name]


@pytest.mark.parametrize("name", list(LEGACY))
@pytest.mark.parametrize("failure", ["import", "render", "capability"])
def test_fallback_bytes_when_optional_envelope_is_unavailable(name: str, failure: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given the previous-version literal and an unavailable optional renderer.
    from automation.interop.approval_card import PreparedCard, prepare
    original = builtins.__import__

    def importing(module, globals=None, locals=None, fromlist=(), level=0):
        if module == "automation.interop" and "owner_message" in fromlist:
            raise ImportError("synthetic optional-runtime absence")
        return original(module, globals, locals, fromlist, level)

    def broken(*args, **kwargs):
        raise owner_message.OwnerMessageError()

    match failure:
        case "import":
            monkeypatch.setattr(builtins, "__import__", importing)
        case "render":
            monkeypatch.setattr(owner_message, "render", broken)
        case "capability":
            monkeypatch.delattr(owner_message, "render")
    # When new-card selection fails; then it selects AND records the exact previous bytes.
    def renderer(version: str) -> str:
        draft = {**record(), "render_version": version}
        if name == "coordination":
            legacy = import_module("coordination_card").legacy
            args = Namespace(summary="일정 제목", peer="peer", duration_min=30)
            return coordination_lifecycle.render_owner_card(draft, legacy(draft, args, "coord-1"))
        return render_case(name, draft, monkeypatch)

    assert prepare(renderer) == PreparedCard("1", LEGACY[name])


@pytest.mark.parametrize("name", list(LEGACY))
def test_unknown_version_is_refused_when_replaying(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given unknown stored metadata; when replayed; then no silent version fallback occurs.
    from automation.interop.approval_card import CardRenderError
    with pytest.raises((CardRenderError, coordination_lifecycle.io.CoordinationError)):
        render_case(name, {**record(), "render_version": "future"}, monkeypatch)


@pytest.mark.parametrize("version", ["1", "2"])
@pytest.mark.parametrize("binding", ["intact", "digest", "prefix"])
def test_gmail_probe_when_action_hash_wire_changes(version: str, binding: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given a real Gmail card with the distinct draft/action hashes and no owner reaction.
    triage_approval = import_module("triage_approval")
    triage_confirm = import_module("triage_confirm")
    from automation.interop.approval_lifecycle import ApprovalRequest, Probe
    content = render_case("gmail", {**record(), "render_version": version}, monkeypatch)
    assert "- draft: `abc123` sha256: `digest-1`" in content.splitlines()
    assert "- action hash: `sha256:action-1`" in content.splitlines()
    if binding == "digest":
        content = content.replace("sha256:action-1", "sha256:wrong-action")
    if binding == "prefix":
        content = content.replace("sha256:action-1", "action-1")
    monkeypatch.setattr(triage_confirm, "owner_id", lambda: "111")
    monkeypatch.setattr(triage_confirm, "_api", lambda method, path: [] if "/reactions/" in path else {"content": content})
    bound = ApprovalRequest("mail:reply:abc123", "sha256:action-1", "333", "222", "2026-09-01T00:00:00Z")
    # When the actual consumer probes; then neither a draft digest nor a bare action digest authorizes.
    probe = triage_approval.MailApprovalGate(record()).probe(bound)
    assert probe is (Probe.BOUND_PENDING if binding == "intact" else Probe.BINDING_MISMATCH)


def test_card_selector_is_staged_when_skills_use_versioned_cards() -> None:
    # Given the real deployment asset list; when staged; then the new runtime dependency exists.
    from tests.unit.test_deploy_staging_includes_lifecycle import _script, _staged_modules
    assert "interop/approval_card.py" in _staged_modules(_script())
