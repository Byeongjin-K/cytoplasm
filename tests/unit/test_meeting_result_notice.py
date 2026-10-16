"""Meeting result delivery contracts, separate from the FS3-pinned skill test file."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Final, Literal, assert_never

import pytest

from automation.interop import origin_notice

ROOT: Final = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills/meeting/scripts"))
import meeting_cli  # noqa: E402

# Captured from the unchanged producer with fixed input and extracted meeting date.
PUBLIC: Final = (
    "회의록 처리 완료: 합성 회의\n"
    "- 내 액션아이템 카드: 0건 (Kanban)\n"
    "- 마일스톤 갱신: 0건 (milestones.yaml)\n"
    "- 과제 미지정 — 관리번호 없이 표만 그렸습니다(원장 미갱신). "
    "`--project <과제명>` 을 주거나 파일명에 과제명을 넣으세요.\n"
    "- 노트: ~/notes/meetings/2026-09-01-meeting-f5f76d7f.md"
)


# Captured once through CLI delivery; independent pins for each destination.
THREAD_PUBLIC: Final = (
    "대상: 합성 회의 (f5f76d7f)\n"
    "사실: 회의록 처리 완료: 합성 회의 - 내 액션아이템 카드: 0건 (Kanban) "
    "- 마일스톤 갱신: 0건 (milestones.yaml) "
    "- 과제 미지정 — 관리번호 없이 표만 그렸습니다(원장 미갱신). "
    "`--project <과제명>` 을 주거나 파일명에 과제명을 넣으세요. "
    "- 노트: ~/notes/meetings/2026-09-01-meeting-f5f76d7f.md (실행 완료)\n"
    "위치: 링크 없음 (공간 미상); 검색: Discord 검색 / f5f76d7f\n"
    "인계: 소유자: 조치 없음; 다음: 추가 실행 없음\n"
    "되돌리기: 해당 없음"
)
FALLBACK_PUBLIC: Final = (
    "대상: 합성 회의 (f5f76d7f)\n"
    "사실: 회의록 처리 완료: 합성 회의 - 내 액션아이템 카드: 0건 (Kanban) "
    "- 마일스톤 갱신: 0건 (milestones.yaml) "
    "- 과제 미지정 — 관리번호 없이 표만 그렸습니다(원장 미갱신). "
    "`--project <과제명>` 을 주거나 파일명에 과제명을 넣으세요. "
    "- 노트: ~/notes/meetings/2026-09-01-meeting-f5f76d7f.md (실행 완료)\n"
    "위치: 링크 없음 (공간 미상); 검색: Discord 검색 / f5f76d7f\n"
    "인계: 소유자: 조치 없음; 다음: 추가 실행 없음\n"
    "되돌리기: 해당 없음"
)


@dataclass(frozen=True, slots=True)
class Chunk:
    message_id: str = "444"


@dataclass(frozen=True, slots=True)
class Ingest:
    argv: list[str]
    posts: list[tuple[str, str]]
    calls: list[tuple[str, str]]


@pytest.fixture
def ingest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Ingest:
    """Real CLI, local artifacts and recorded LLM response; only Discord is injected."""
    monkeypatch.chdir(tmp_path)
    for key, value in {
        "MEETING_CONFIG": "absent.json", "MEETING_NOTES_DIR": "notes",
        "MEETING_LOG_DIR": "logs", "MEETING_STATE_FILE": "state/milestones.yaml",
        "MEETING_PLAN_DIR": "plan", "DRIVE_PUBLISH_ENABLED": "0",
        "MEETING_PROMPT_FILE": str(ROOT / "skills/meeting/prompts/meeting-extraction-v6.md"),
        "AUTOPHAGY_RUNTIME_ROOT": str(ROOT),
    }.items():
        monkeypatch.setenv(key, value)
    # Optional external reference/glossary lookup is not the delivery integration.
    monkeypatch.setattr(meeting_cli.meeting_reference, "collect", lambda query: ())
    monkeypatch.setattr(meeting_cli, "_correct_terms", lambda extraction, **kw: extraction)
    Path("body.txt").write_text("합성 회의 본문입니다. 다음 회의에서 진행 상황을 확인합니다.", encoding="utf-8")
    Path("recorded.json").write_text(
        '{"meeting":{"date":"2026-09-01"},"decisions":[],"todos":[],"others":[],"milestones":[]}',
        encoding="utf-8",
    )
    posts: list[tuple[str, str]] = []
    calls: list[tuple[str, str]] = []

    def api(method: str, path: str, payload=None):
        calls.append((method, path))
        return {"id": "333"}

    @dataclass(frozen=True, slots=True)
    class Transport:
        channel_id: str

        def send(self, body: str) -> tuple[Chunk, ...]:
            posts.append((self.channel_id, body))
            return (Chunk(),)

    monkeypatch.setattr(meeting_cli, "_discord_api", api)
    monkeypatch.setattr(meeting_cli, "_transport", Transport)
    monkeypatch.setattr(meeting_cli, "_origin_notice", lambda: origin_notice)
    return Ingest(
        ["ingest", "--body-file", "body.txt", "--label", "합성 회의",
         "--recorded-response", "recorded.json", "--notify-channel", "111",
         "--notify-message-id", "222"], posts, calls,
    )


@pytest.mark.parametrize("runtime", ["missing_module", "old_signature"])
def test_legacy_bytes_when_runtime_predates_envelopes(
    ingest, monkeypatch, runtime: Literal["missing_module", "old_signature"],
):
    # Given: the unchanged notice golden and either kind of old runtime.
    match runtime:
        case "missing_module":
            monkeypatch.setitem(sys.modules, "automation.interop.owner_message", None)
        case "old_signature":
            def deliver(*, api, transport_factory, record, thread_name, content, fallback):
                return origin_notice.deliver(
                    api=api, transport_factory=transport_factory, record=record,
                    thread_name=thread_name, content=content, fallback=fallback,
                )
            monkeypatch.setattr(meeting_cli, "_origin_notice", lambda: SimpleNamespace(deliver=deliver))
        case _:
            assert_never(runtime)
    # When: live ingest completes and notifies through the actual facade.
    code = meeting_cli.main(ingest.argv)
    # Then: every byte survives, and delivery still uses the instruction anchor.
    assert code == 0
    assert ingest.posts == [("333", PUBLIC)]
    assert ingest.calls == [("POST", "/channels/111/messages/222/threads"), ("POST", "/channels/333/messages")]


def test_legacy_bytes_when_offline(ingest):
    # Given an offline run; when ingest completes; then its artifact stays byte-identical.
    code = meeting_cli.main([*ingest.argv, "--offline"])
    assert code == 0
    assert Path("plan/notify.txt").read_text(encoding="utf-8") == "111\n" + PUBLIC + "\nthread-anchor=222\n"
    assert ingest.posts == []


def test_refusal_bytes_when_extraction_fails(ingest):
    # Given: an invalid recorded response, not a completed execution.
    Path("recorded.json").write_text("invalid json", encoding="utf-8")
    # When: CLI reports extraction failure through the live notice route.
    code = meeting_cli.main(ingest.argv)
    # Then: the existing refusal stays a refusal, never an executed result.
    assert code == 6
    assert ingest.posts == [("333", "회의록 추출 실패: LLM 응답을 해석하지 못했습니다. 다시 시도해 주세요.")]


@pytest.mark.parametrize("fallback", [False, True], ids=["thread", "fallback"])
def test_envelope_when_ingest_effects_complete(ingest, monkeypatch, fallback):
    from automation.interop.owner_message import Action, OwnerMessage, Ref, Result

    # Given: known input, a real facade and an optional failed thread creation.
    if fallback:
        def fail_thread(method, path, payload=None):
            raise OSError("injected thread failure")
        monkeypatch.setattr(meeting_cli, "_discord_api", fail_thread)
    seen: list[OwnerMessage | None] = []

    def deliver(*, message=None, **kwargs):
        seen.append(message)
        return origin_notice.deliver(message=message, **kwargs)

    monkeypatch.setattr(meeting_cli, "_origin_notice", lambda: SimpleNamespace(
        ACCEPTS_OWNER_MESSAGE=True, deliver=deliver,
    ))
    ref = "f5f76d7f"
    expected = OwnerMessage(
        subject_key=ref, subject="합성 회의",
        fact=PUBLIC,
        location=Ref(scope="message", space="unknown", search=("Discord 검색", ref)),
        owner=Action(verb="none"), agent_next=None, recovery="not_applicable",
        detail=Result(outcome="executed"),
    )
    # When: the actual CLI writes artifacts and sends the result.
    code = meeting_cli.main(ingest.argv)
    # Then: only the notice facts enter the envelope; the facade renders for the actual surface.
    assert code == 0
    assert seen == [expected]
    golden = FALLBACK_PUBLIC if fallback else THREAD_PUBLIC
    assert ingest.posts == [("111" if fallback else "333", golden)]
    assert len(ingest.posts[0][1].splitlines()) == 5
    print("DELIVERED", ingest.posts[0][0], ingest.posts[0][1], sep="\n")
