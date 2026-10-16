"""W2-3 meeting ingest skill — deterministic tests (LLM mocked via recording)."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "skills" / "meeting"
FIXTURES = SKILL / "fixtures"
sys.path.insert(0, str(SKILL / "scripts"))

import meeting_actions  # noqa: E402
import meeting_cli  # noqa: E402
import meeting_extract  # noqa: E402
import meeting_llm  # noqa: E402
import meeting_minutes  # noqa: E402

NOW = datetime(2026, 7, 15, 12, 0, 0, tzinfo=ZoneInfo("Asia/Seoul"))


def _load_plugin():
    spec = importlib.util.spec_from_file_location(
        "meeting_gate_plugin", SKILL / "plugin" / "__init__.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _Source:
    def __init__(self, *, user_id="OWNER1", is_bot=False, chat_type="dm", chat_id="C1"):
        self.user_id = user_id
        self.is_bot = is_bot
        self.chat_type = chat_type
        self.chat_id = chat_id
        self.thread_id = None


_MISSING = object()


class _RawMessage:
    def __init__(self, content):
        self.content = content


class _Event:
    def __init__(
        self,
        text="",
        media_urls=(),
        media_types=(),
        metadata=None,
        raw_content=_MISSING,
        **source_kwargs,
    ):
        self.text = text
        self.media_urls = list(media_urls)
        self.media_types = list(media_types)
        self.metadata = dict(metadata or {})
        if raw_content is not _MISSING:
            self.raw_message = _RawMessage(raw_content)
        self.source = _Source(**source_kwargs)


# --- acceptance: fixed fixture -> milestones >= 2 AND todos >= 3 -------------


def test_recorded_extraction_meets_thresholds():
    raw = (FIXTURES / "recorded-clean.json").read_text(encoding="utf-8")
    extraction = meeting_llm.parse_extraction(raw)
    assert len(extraction.milestones) >= 2
    assert len(extraction.todos) >= 3
    for item in extraction.todos + extraction.milestones:
        assert item.title and item.basis
        assert item.deadline is None or len(item.deadline) == 10


def test_full_offline_pipeline_on_fixture(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MEETING_NOTES_DIR", str(tmp_path / "notes"))
    monkeypatch.setenv("MEETING_STATE_FILE", str(tmp_path / "state/milestones.yaml"))
    monkeypatch.setenv("MEETING_PROMPT_FILE", str(REPO / "prompts/meeting-extraction-v3.md"))
    monkeypatch.setenv("MEETING_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MEETING_PLAN_DIR", str(tmp_path / "plan"))
    monkeypatch.setenv("MEETING_CONFIG", str(tmp_path / "absent.json"))
    rc = meeting_cli.main(
        [
            "ingest",
            "--file", str(FIXTURES / "meeting-clean.md"),
            "--recorded-response", str(FIXTURES / "recorded-clean.json"),
            "--offline",
            "--notify-channel", "TEST",
        ]
    )
    assert rc == 0
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["todos"] >= 3 and result["milestones"] >= 2
    assert result["codex_called"] is False and result["provider"] == "recorded"
    cards = (tmp_path / "plan/kanban-plan.jsonl").read_text().splitlines()
    assert len(cards) >= 3
    state = (tmp_path / "state/milestones.yaml").read_text()
    assert state.count("  - title: ") >= 2
    assert (tmp_path / "plan/team-post.txt").read_text().startswith("```json")


def test_rules_and_prompt_copies_in_sync():
    for canonical, copy in [
        ("prompts/meeting-extraction-v4.md", "skills/meeting/prompts/meeting-extraction-v4.md"),
    ]:
        assert (REPO / canonical).read_bytes() == (REPO / copy).read_bytes()

def test_prompt_template_excludes_doc_header():
    """Header prose mentions <<<PROMPT>>>; the loader must anchor to the marker LINE.

    Regression: substring split leaked header text ("변경 시 버전 파일명을
    올린다") into the LLM prompt and codex created v3/v4 template files.
    """
    template = meeting_llm.load_prompt_template(REPO / "prompts/meeting-extraction-v4.md")
    assert template.startswith("아래 회의록을 읽어라.")
    prompt = meeting_llm.build_prompt(template, meeting_text="본문", my_names="cha")
    assert "버전 파일명" not in prompt and "치환해" not in prompt


# --- refusals -----------------------------------------------------------------


def test_size_reject_before_read(tmp_path):
    big = tmp_path / "big.md"
    with big.open("wb") as handle:
        handle.seek(26 * 1024 * 1024)
        handle.write(b"x")
    with pytest.raises(meeting_extract.ExtractionRefused) as exc:
        meeting_extract.extract_file(big)
    assert exc.value.exit_code == 3
    assert "크기 초과" in exc.value.notice


def test_scanned_pdf_manual_conversion(tmp_path):
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-fake")
    with pytest.raises(meeting_extract.ExtractionRefused) as exc:
        meeting_extract.extract_file(pdf, pdf_runner=lambda _: "\x0c\n ")
    assert exc.value.exit_code == 4
    assert "수동 변환 요청" in exc.value.notice


@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="poppler absent")
def test_generated_pdfs_roundtrip(tmp_path):
    for flag, expect_ok in [("--text", True), ("--scanned", False)]:
        out = tmp_path / f"f{flag.strip('-')}.pdf"
        subprocess.run(
            [sys.executable, str(SKILL / "scripts/make_fixture_pdf.py"), str(out), flag],
            check=True,
        )
        if expect_ok:
            extracted = meeting_extract.extract_file(out)
            assert extracted.kind == "pdf" and "2026-08-01" in extracted.text
        else:
            with pytest.raises(meeting_extract.ExtractionRefused):
                meeting_extract.extract_file(out)


def test_unsupported_extension(tmp_path):
    other = tmp_path / "x.docx"
    other.write_text("hello meeting")
    with pytest.raises(meeting_extract.ExtractionRefused) as exc:
        meeting_extract.extract_file(other)
    assert exc.value.exit_code == 5


# --- patent-keyword meetings are ordinary meetings ---------------------------------------------


def test_patent_keyword_meeting_takes_the_same_model_route_as_any_meeting(monkeypatch):
    """민감 판정이 없다 — 특허 낱말이 든 회의도 같은 호출, 같은 인자로 모델에 간다."""
    calls: list[tuple[tuple, dict]] = []
    recorded = (FIXTURES / "recorded-clean.json").read_text(encoding="utf-8")

    def call(*args, **kwargs):
        calls.append((args, kwargs))
        return recorded

    monkeypatch.setattr(meeting_llm, "call_codex", call)
    for fixture in ("meeting-clean.md", "meeting-patent.md"):
        _, provider = meeting_llm.extract(
            (FIXTURES / fixture).read_text(encoding="utf-8"),
            prompt_path=REPO / "prompts/meeting-extraction-v3.md",
            my_names="cha",
        )
        assert provider == meeting_llm.CODEX_PROVIDER
    assert [kwargs for _, kwargs in calls] == [{}, {}]
    assert "청구항" in calls[1][0][0], "특허 낱말이 든 본문도 그대로 모델에 간다"


def test_extraction_fails_closed_when_codex_credentials_are_missing(tmp_path, monkeypatch):
    """자격증명이 없으면 rc 1 + 빈 stdout — 다른 티어로 내려가지 않고 예외로 멈춘다."""
    hermes = tmp_path / "hermes"
    hermes.write_text(
        '#!/bin/sh\necho "No Codex credentials stored." >&2\nexit 1\n', encoding="utf-8"
    )
    hermes.chmod(0o755)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AUTOPHAGY_HERMES_BIN", str(hermes))

    with pytest.raises(meeting_llm.ExtractionUnavailableError):
        meeting_llm.extract(
            "회의 본문",
            prompt_path=REPO / "prompts/meeting-extraction-v3.md",
            my_names="cha",
        )


def test_ingest_fails_visibly_when_the_codex_tier_is_unavailable(tmp_path, monkeypatch, capsys):
    """야간 배치 포함 모든 비대화형 경로: 조용한 성공도 대체 티어도 없다 — exit 6."""
    hermes = tmp_path / "hermes"
    hermes.write_text(
        '#!/bin/sh\necho "No Codex credentials stored." >&2\nexit 1\n', encoding="utf-8"
    )
    hermes.chmod(0o755)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AUTOPHAGY_HERMES_BIN", str(hermes))
    monkeypatch.setenv("MEETING_NOTES_DIR", str(tmp_path / "notes"))
    monkeypatch.setenv("MEETING_STATE_FILE", str(tmp_path / "state/milestones.yaml"))
    monkeypatch.setenv("MEETING_PROMPT_FILE", str(REPO / "prompts/meeting-extraction-v3.md"))
    monkeypatch.setenv("MEETING_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MEETING_PLAN_DIR", str(tmp_path / "plan"))
    monkeypatch.setenv("MEETING_CONFIG", str(tmp_path / "absent.json"))

    rc = meeting_cli.main(
        ["ingest", "--file", str(FIXTURES / "meeting-clean.md"), "--offline",
         "--notify-channel", "TEST"]
    )

    out = capsys.readouterr().out
    assert rc == 6
    assert "회의록 추출 실패" in out
    log_line = next((tmp_path / "logs").glob("*.jsonl")).read_text(encoding="utf-8").splitlines()
    record = json.loads(log_line[-1])
    assert record["exit"] == 6 and record["error"] == "ExtractionUnavailableError"
    assert record["codex_called"] is False
    assert "provider" not in record, "실패한 호출은 성공한 공급자로 기록되지 않는다"
    assert not list((tmp_path / "notes").glob("*.md")), "실패했는데 회의록이 생기면 안 된다"


def test_patent_keyword_meeting_publishes_cards_milestones_and_team_post(
    tmp_path, monkeypatch, capsys
):
    """특허 낱말이 든 회의도 카드·마일스톤·#team 게시가 일반 회의와 똑같다."""
    monkeypatch.setenv("MEETING_NOTES_DIR", str(tmp_path / "notes"))
    monkeypatch.setenv("MEETING_STATE_FILE", str(tmp_path / "state/milestones.yaml"))
    monkeypatch.setenv("MEETING_PROMPT_FILE", str(REPO / "prompts/meeting-extraction-v3.md"))
    monkeypatch.setenv("MEETING_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MEETING_PLAN_DIR", str(tmp_path / "plan"))
    monkeypatch.setenv("MEETING_CONFIG", str(tmp_path / "absent.json"))
    rc = meeting_cli.main([
        "ingest", "--file", str(FIXTURES / "meeting-patent.md"),
        "--recorded-response", str(FIXTURES / "recorded-patent.json"),
        "--offline", "--notify-channel", "TEST",
    ])
    assert rc == 0
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert "sensitive" not in result
    cards = (tmp_path / "plan/kanban-plan.jsonl").read_text(encoding="utf-8")
    assert "청구항(claim) 초안 작성 (마감 2026-07-28)" in cards
    assert "[민감" not in cards
    state = (tmp_path / "state/milestones.yaml").read_text(encoding="utf-8")
    assert "특허 출원서 제출" in state
    assert "선행기술 조사" in (tmp_path / "plan/team-post.txt").read_text(encoding="utf-8")
    notify = (tmp_path / "plan/notify.txt").read_text(encoding="utf-8")
    assert "회의록 처리 완료: meeting-patent" in notify
    note = next((tmp_path / "notes").glob("*.md")).read_text(encoding="utf-8")
    assert "patent-sensitive" not in note
    assert "# 발명 회의" in note.split(meeting_minutes.APPENDIX_HEADING)[0]


def _todo(title, deadline, basis):
    return meeting_llm.ActionItem(title=title, deadline=deadline, basis=basis)


def test_plan_cards_keep_titles_and_idempotency_keys():
    extraction = meeting_llm.Extraction(
        todos=(
            _todo("주간 보고서 정리", "2026-08-01", "회의에서 합의"),
            _todo("기술이전 검토 자료 정리", None, "기술이전 가능성 검토"),
        )
    )
    cards = meeting_actions.plan_cards(extraction, note_name="n.md", ref="deadbeef")
    assert [card.title for card in cards] == [
        "주간 보고서 정리 (마감 2026-08-01)", "기술이전 검토 자료 정리",
    ]
    assert [card.idempotency_key for card in cards] == [
        "meeting:deadbeef:todo:1",
        "meeting:deadbeef:todo:2",
    ]


def test_note_filename_uses_the_extracted_meeting_date(tmp_path):
    extraction = meeting_llm.Extraction(
        meeting=meeting_llm.MeetingHeader(date="2026-07-01")
    )

    note = meeting_actions.write_note(
        tmp_path, label="회의", kind="md", original_text="원문", extraction=extraction,
        ref="deadbeef", now=NOW,
    )

    assert note.name == "2026-07-01-meeting-deadbeef.md"


@pytest.mark.parametrize("meeting_date", [None, "not-a-date"])
def test_note_filename_falls_back_to_processing_date_without_meeting_date(
    tmp_path, meeting_date
):
    extraction = meeting_llm.Extraction(
        meeting=meeting_llm.MeetingHeader(date=meeting_date)
    )
    note = meeting_actions.write_note(
        tmp_path, label="회의", kind="md", original_text="원문", extraction=extraction,
        ref="deadbeef", now=NOW,
    )

    assert note.name == "2026-07-15-meeting-deadbeef.md"


def test_rerunning_a_meeting_ref_updates_one_canonical_note(tmp_path):
    extraction = meeting_llm.Extraction(
        meeting=meeting_llm.MeetingHeader(date="2026-07-01")
    )
    first = meeting_actions.write_note(
        tmp_path, label="회의", kind="md", original_text="첫 원문", extraction=extraction,
        ref="deadbeef", now=NOW,
    )
    second = meeting_actions.write_note(
        tmp_path, label="회의", kind="md", original_text="갱신 원문", extraction=extraction,
        ref="deadbeef", now=NOW.replace(day=16),
    )

    assert first == second
    assert [path.name for path in tmp_path.iterdir()] == [first.name]


def test_owner_action_card_plan_blocks_dispatch_after_creation():
    card = meeting_actions.sanitize_card(
        _todo("참석자 단체 채팅방 만들기", None, "담당자 미정"),
        seq=1,
        note_name="n.md",
        ref="deadbeef",
    )

    assert card.argv_sequence("t_created") == (
        card.argv(),
        [
            "kanban",
            "block",
            "--kind",
            "needs_input",
            "t_created",
            "Needs human owner action; do not dispatch an LLM worker.",
        ],
    )


def test_note_keeps_original_detail(tmp_path):
    raw = (FIXTURES / "recorded-patent.json").read_text(encoding="utf-8")
    extraction = meeting_llm.parse_extraction(raw)
    original = (FIXTURES / "meeting-patent.md").read_text(encoding="utf-8")
    note = meeting_actions.write_note(
        tmp_path, label="발명 회의", kind="md", original_text=original,
        extraction=extraction, ref="deadbeef", now=NOW,
    )
    content = note.read_text(encoding="utf-8")
    assert "청구항" in content and "patent-sensitive" not in content
    assert note.stat().st_mode & 0o777 == 0o600


# --- parsing robustness ---------------------------------------------------------


def test_parse_extraction_with_fences_and_junk():
    raw = 'chatter\n```json\n{"todos": [{"title": "t", "deadline": "2026-01-02", "basis": "b"}], "milestones": [], "others": [], "decisions": ["d"]}\n```\ntrailing'
    extraction = meeting_llm.parse_extraction(raw)
    assert extraction.todos[0].deadline == "2026-01-02"
    assert extraction.decisions == (meeting_llm.Decision("d", ""),)


def test_parse_extraction_rejects_garbage():
    with pytest.raises(meeting_llm.ExtractionParseError):
        meeting_llm.parse_extraction("no json here")


def test_milestones_dedupe(tmp_path):
    state = tmp_path / "m.yaml"
    items = meeting_llm.parse_extraction(
        (FIXTURES / "recorded-clean.json").read_text(encoding="utf-8")
    ).milestones
    first = meeting_actions.update_milestones(
        state, items, note_name="n.md", ref="r1", now=NOW
    )
    second = meeting_actions.update_milestones(
        state, items, note_name="n.md", ref="r1", now=NOW
    )
    assert first == len(items) and second == 0
    assert state.read_text().count("  - title: ") == len(items)


# --- plugin trigger logic --------------------------------------------------------


def _plugin_spies(monkeypatch):
    plugin = _load_plugin()
    monkeypatch.setattr(plugin, "_config", lambda: {"owner_id": "OWNER1"})
    launches: list = []
    posts: list = []
    monkeypatch.setattr(plugin, "_launch", lambda trigger, python_bin: launches.append(trigger))
    monkeypatch.setattr(plugin, "_post", lambda chat_id, content: posts.append((chat_id, content)))
    return plugin, launches, posts


def test_plugin_does_not_trigger_for_nonmeeting_message_txt(monkeypatch):
    plugin, launches, posts = _plugin_spies(monkeypatch)
    event = _Event(
        text="첨부된 메일 내용을 확인해줘",
        media_urls=["/cache/doc_ab12cd34ef56_message.txt"],
        media_types=["text/plain; charset=utf-8"],
    )
    assert plugin.pre_gateway_dispatch(event, None, None) is None
    assert launches == []
    assert posts == []


@pytest.mark.parametrize(
    ("path", "mime"),
    [
        ("/cache/doc_ab12cd34ef56_notes.txt", ""),
        ("/cache/doc_ab12cd34ef56_blob", "text/plain"),
        ("/cache/doc_ab12cd34ef56_agenda.pdf", "application/pdf"),
        ("/cache/doc_ab12cd34ef56_weekly-meeting-minutes.md", "text/markdown"),
    ],
)
def test_plugin_does_not_infer_intent_from_extension_or_mime(monkeypatch, path, mime):
    plugin, launches, posts = _plugin_spies(monkeypatch)
    event = _Event(text="자료 전달", media_urls=[path], media_types=[mime])
    assert plugin.pre_gateway_dispatch(event, None, None) is None
    assert launches == []
    assert posts == []


@pytest.mark.parametrize(
    "text",
    [
        "메일 예시에는 !meeting 명령이 포함되어 있습니다",
        "!meetingfoo 합성 회의 본문",
        "!meeting-archive 합성 회의 본문",
    ],
)
def test_plugin_requires_bounded_command_at_start(monkeypatch, text):
    plugin, launches, posts = _plugin_spies(monkeypatch)
    assert plugin.pre_gateway_dispatch(_Event(text=text), None, None) is None
    assert launches == []
    assert posts == []


@pytest.mark.parametrize("value", [False, None, "true", "True", 1, [], {}])
def test_plugin_requires_boolean_trusted_metadata_signal(monkeypatch, value):
    plugin, launches, posts = _plugin_spies(monkeypatch)
    event = _Event(
        media_urls=["/cache/doc_ab12cd34ef56_notes.md"],
        media_types=["text/markdown"],
        metadata={plugin.MEETING_INTENT_METADATA_KEY: value},
    )
    assert plugin.pre_gateway_dispatch(event, None, None) is None
    assert launches == []
    assert posts == []


def test_plugin_preserves_explicit_meeting_body_command(monkeypatch):
    plugin, launches, posts = _plugin_spies(monkeypatch)
    command = _Event(text="  !MeEtInG 오늘 회의: 차는 보고서 작성  ")
    assert plugin.pre_gateway_dispatch(command, None, None) == {
        "action": "skip",
        "reason": "meeting_ingest",
    }
    assert launches[0].doc_paths == ()
    assert launches[0].body == "오늘 회의: 차는 보고서 작성"
    assert posts == [("C1", plugin.ACK_MESSAGE)]


def test_plugin_preserves_explicit_upload_using_raw_caption(monkeypatch):
    plugin, launches, posts = _plugin_spies(monkeypatch)
    # Discord inlines small text attachments before the caption in event.text.
    event = _Event(
        text="[Content of notes.txt]\n합성 회의 본문\n!meeting",
        raw_content="!meeting",
        media_urls=["/cache/doc_ab12cd34ef56_notes.txt"],
        media_types=["text/plain"],
    )
    assert plugin.pre_gateway_dispatch(event, None, None)["action"] == "skip"
    assert launches[0].doc_paths == ("/cache/doc_ab12cd34ef56_notes.txt",)
    assert launches[0].body is None
    assert posts == [("C1", plugin.ACK_MESSAGE)]


def test_plugin_does_not_promote_command_inside_attachment_when_raw_caption_empty(monkeypatch):
    plugin, launches, posts = _plugin_spies(monkeypatch)
    event = _Event(
        text="[Content of message.txt]\n!meeting quoted example from the attachment",
        raw_content="",
        media_urls=["/cache/doc_ab12cd34ef56_message.txt"],
        media_types=["text/plain"],
    )
    assert plugin.pre_gateway_dispatch(event, None, None) is None
    assert launches == []
    assert posts == []


def test_plugin_preserves_trusted_metadata_intent(monkeypatch):
    plugin, launches, posts = _plugin_spies(monkeypatch)
    event = _Event(
        media_urls=["/cache/doc_ab12cd34ef56_notes.md"],
        media_types=["text/markdown"],
        metadata={plugin.MEETING_INTENT_METADATA_KEY: True},
    )
    assert plugin.pre_gateway_dispatch(event, None, None)["action"] == "skip"
    assert launches[0].doc_paths == ("/cache/doc_ab12cd34ef56_notes.md",)
    assert launches[0].body is None
    assert posts == [("C1", plugin.ACK_MESSAGE)]


def test_plugin_rejects_other_users_and_bots_even_with_explicit_signal(monkeypatch):
    plugin, launches, posts = _plugin_spies(monkeypatch)
    assert plugin.pre_gateway_dispatch(
        _Event(text="!meeting x", user_id="OTHER"), None, None
    ) is None
    assert plugin.pre_gateway_dispatch(
        _Event(metadata={plugin.MEETING_INTENT_METADATA_KEY: True}, is_bot=True), None, None
    ) is None
    assert launches == []
    assert posts == []


def test_nonmeeting_gate_has_zero_meeting_side_effects(tmp_path, monkeypatch):
    plugin = _load_plugin()
    monkeypatch.setattr(plugin, "_config", lambda: {"owner_id": "OWNER1"})

    def forbidden_launch(trigger, python_bin):
        for name in (
            "kanban-card.json",
            "meeting-note.md",
            "milestones.yaml",
            "team-action.txt",
            "ingest-log.jsonl",
        ):
            (tmp_path / name).write_text("unexpected")

    def forbidden_post(chat_id, content):
        (tmp_path / "team-post.txt").write_text("unexpected")

    monkeypatch.setattr(plugin, "_launch", forbidden_launch)
    monkeypatch.setattr(plugin, "_post", forbidden_post)
    event = _Event(
        text="이 첨부를 검토해줘",
        media_urls=["/cache/doc_ab12cd34ef56_message.txt"],
        media_types=["text/plain"],
    )
    assert plugin.pre_gateway_dispatch(event, None, None) is None
    assert list(tmp_path.iterdir()) == []


# --- t_f409f47b: artifact producers must be call-counted, not merely absent ----

# Every function that materializes a meeting artifact. `plan_cards` builds the
# Kanban cards, `write_note` the meeting note, `update_milestones` the
# milestones.yaml rows, `format_team_post` the #team action items.
_ARTIFACT_PRODUCERS = (
    "write_note",
    "plan_cards",
    "update_milestones",
    "format_team_post",
)


def _artifact_call_counts(monkeypatch) -> dict[str, int]:
    """Count calls on every artifact producer while keeping real behaviour."""
    counts = dict.fromkeys(_ARTIFACT_PRODUCERS, 0)

    def install(name: str) -> None:
        original = getattr(meeting_actions, name)

        def counted(*args, **kwargs):
            counts[name] += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(meeting_actions, name, counted)

    for name in _ARTIFACT_PRODUCERS:
        install(name)
    return counts


def test_nonmeeting_message_txt_upload_calls_zero_artifact_producers(monkeypatch):
    """Given the incident input (a non-meeting `message.txt` upload with no
    `!meeting` signal), When the gate runs with the REAL ``_launch``, Then no CLI
    process is spawned and every artifact producer records zero calls.

    ``_spawn == []`` strictly dominates the live Kanban path: ``_run_kanban``
    only executes inside the spawned CLI process, so no spawn means no card.
    """
    plugin = _load_plugin()
    monkeypatch.setattr(plugin, "_config", lambda: {"owner_id": "OWNER1"})
    spawns: list = []
    posts: list = []
    monkeypatch.setattr(plugin, "_spawn", lambda argv: spawns.append(argv))
    monkeypatch.setattr(plugin, "_post", lambda chat_id, content: posts.append(chat_id))
    counts = _artifact_call_counts(monkeypatch)

    event = _Event(
        text="첨부된 메일 내용을 확인해줘",
        media_urls=["/cache/doc_ab12cd34ef56_message.txt"],
        media_types=["text/plain; charset=utf-8"],
    )
    assert plugin.pre_gateway_dispatch(event, None, None) is None

    assert spawns == []
    assert posts == []
    assert counts == dict.fromkeys(_ARTIFACT_PRODUCERS, 0)


def test_explicit_meeting_command_calls_every_artifact_producer(tmp_path, monkeypatch):
    """Given an explicit `!meeting` body, When the same spy harness runs the
    offline pipeline, Then every artifact producer records exactly one call.

    This is the discriminating half of the pair: without it the zero-call
    assertions above would hold even if the spies were never wired up.
    """
    monkeypatch.setenv("MEETING_NOTES_DIR", str(tmp_path / "notes"))
    monkeypatch.setenv("MEETING_STATE_FILE", str(tmp_path / "state/milestones.yaml"))
    monkeypatch.setenv("MEETING_PROMPT_FILE", str(REPO / "prompts/meeting-extraction-v3.md"))
    monkeypatch.setenv("MEETING_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MEETING_PLAN_DIR", str(tmp_path / "plan"))
    monkeypatch.setenv("MEETING_CONFIG", str(tmp_path / "absent.json"))
    plugin = _load_plugin()
    monkeypatch.setattr(plugin, "_config", lambda: {"owner_id": "OWNER1"})
    monkeypatch.setattr(plugin, "_post", lambda chat_id, content: None)
    counts = _artifact_call_counts(monkeypatch)

    def offline_launch(trigger, python_bin):
        body_file = tmp_path / "explicit-meeting.txt"
        body_file.write_text(trigger.body, encoding="utf-8")
        assert meeting_cli.main(
            [
                "ingest",
                "--body-file", str(body_file),
                "--label", "합성 명시 회의",
                "--recorded-response", str(FIXTURES / "recorded-clean.json"),
                "--offline",
                "--notify-channel", trigger.chat_id,
            ]
        ) == 0

    monkeypatch.setattr(plugin, "_launch", offline_launch)
    event = _Event(text="!meeting 합성 주간회의 기록입니다\n- 차: 다음 주까지 보고서 작성")
    assert plugin.pre_gateway_dispatch(event, None, None)["action"] == "skip"

    assert counts == dict.fromkeys(_ARTIFACT_PRODUCERS, 1)


def test_explicit_meeting_command_still_creates_offline_outputs(tmp_path, monkeypatch, capsys):
    plugin = _load_plugin()
    monkeypatch.setattr(plugin, "_config", lambda: {"owner_id": "OWNER1"})
    monkeypatch.setenv("MEETING_NOTES_DIR", str(tmp_path / "notes"))
    monkeypatch.setenv("MEETING_STATE_FILE", str(tmp_path / "state/milestones.yaml"))
    monkeypatch.setenv("MEETING_PROMPT_FILE", str(REPO / "prompts/meeting-extraction-v3.md"))
    monkeypatch.setenv("MEETING_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MEETING_PLAN_DIR", str(tmp_path / "plan"))
    monkeypatch.setenv("MEETING_CONFIG", str(tmp_path / "absent.json"))
    posts = []
    monkeypatch.setattr(plugin, "_post", lambda chat_id, content: posts.append((chat_id, content)))

    def offline_launch(trigger, python_bin):
        assert trigger.doc_paths == ()
        body_file = tmp_path / "explicit-meeting.txt"
        body_file.write_text(trigger.body, encoding="utf-8")
        assert meeting_cli.main(
            [
                "ingest",
                "--body-file", str(body_file),
                "--label", "합성 명시 회의",
                "--recorded-response", str(FIXTURES / "recorded-clean.json"),
                "--offline",
                "--notify-channel", trigger.chat_id,
            ]
        ) == 0

    monkeypatch.setattr(plugin, "_launch", offline_launch)
    event = _Event(
        text="!meeting 합성 주간회의 기록입니다\n- 차: 다음 주까지 보고서 작성\n- 결정: 초안을 검토한다"
    )
    assert plugin.pre_gateway_dispatch(event, None, None) == {
        "action": "skip",
        "reason": "meeting_ingest",
    }
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result["todos"] >= 3 and result["milestones"] >= 2
    assert len((tmp_path / "plan/kanban-plan.jsonl").read_text().splitlines()) >= 3
    assert len(list((tmp_path / "notes").glob("*.md"))) == 1
    assert (tmp_path / "state/milestones.yaml").exists()
    assert (tmp_path / "plan/team-post.txt").exists()
    assert posts == [("C1", plugin.ACK_MESSAGE)]


def test_plugin_triggers_multiple_supported_docs_only(monkeypatch):
    plugin, launches, posts = _plugin_spies(monkeypatch)
    event = _Event(
        text="!meeting",
        media_urls=["/cache/a.md", "/cache/b.pdf", "/cache/photo.png"],
        media_types=["text/markdown", "application/pdf", "image/png"],
    )
    assert plugin.pre_gateway_dispatch(event, None, None)["action"] == "skip"
    assert launches[0].doc_paths == ("/cache/a.md", "/cache/b.pdf")
    assert posts == [("C1", plugin.ACK_MESSAGE)]


def test_plugin_preserves_nontrigger_general_flow(monkeypatch):
    plugin, launches, posts = _plugin_spies(monkeypatch)
    downstream = []
    result = plugin.pre_gateway_dispatch(_Event(text="일반 요청"), None, None)
    if result is None:
        downstream.append("called")
    assert downstream == ["called"]
    assert launches == []
    assert posts == []


def test_plugin_fail_closed_after_trigger(monkeypatch):
    plugin = _load_plugin()
    monkeypatch.setattr(plugin, "_config", lambda: {"owner_id": "OWNER1"})

    def boom(trigger, python_bin):
        raise RuntimeError("launch broke")

    monkeypatch.setattr(plugin, "_launch", boom)
    monkeypatch.setattr(plugin, "_post", lambda chat_id, content: None)
    event = _Event(
        text="!meeting",
        media_urls=["/cache/doc_ab12cd34ef56_m.pdf"],
        media_types=["application/pdf"],
    )
    assert plugin.pre_gateway_dispatch(event, None, None)["action"] == "skip"


# --- origin-thread result notices (owner instruction 2026-08-23) ----------------
# The `!meeting` instruction message anchors a thread; the plugin ACK and the CLI
# completion notice both land there (thread id == message id), the channel itself
# stays clean. No anchor → exact legacy behaviour (channel posts).


def _meeting_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MEETING_NOTES_DIR", str(tmp_path / "notes"))
    monkeypatch.setenv("MEETING_STATE_FILE", str(tmp_path / "state/milestones.yaml"))
    monkeypatch.setenv("MEETING_PROMPT_FILE", str(REPO / "prompts/meeting-extraction-v3.md"))
    monkeypatch.setenv("MEETING_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MEETING_PLAN_DIR", str(tmp_path / "plan"))
    monkeypatch.setenv("MEETING_CONFIG", str(tmp_path / "absent.json"))


def test_plugin_trigger_captures_the_instruction_message_id(monkeypatch):
    # Given: an owner !meeting message whose adapter exposes the message id
    plugin, launches, _posts = _plugin_spies(monkeypatch)
    event = _Event(text="!meeting 합성 회의 본문")
    event.message_id = "M1"
    # When: the gate intercepts it
    assert plugin.pre_gateway_dispatch(event, None, None)["action"] == "skip"
    # Then: the trigger carries the anchor
    [trigger] = launches
    assert trigger.message_id == "M1"


def test_plugin_trigger_without_message_id_keeps_empty_anchor(monkeypatch):
    # Given: an adapter that exposes no message id
    plugin, launches, _posts = _plugin_spies(monkeypatch)
    event = _Event(text="!meeting 합성 회의 본문")
    # When: the gate intercepts it
    assert plugin.pre_gateway_dispatch(event, None, None)["action"] == "skip"
    # Then: the anchor is empty, never guessed
    [trigger] = launches
    assert trigger.message_id == ""


def test_plugin_launch_passes_the_anchor_to_the_cli(monkeypatch, tmp_path):
    # Given: a trigger with an anchor and a spied spawner
    plugin = _load_plugin()
    monkeypatch.setattr(plugin, "_INBOX", tmp_path / "inbox")
    spawned: list[list[str]] = []
    monkeypatch.setattr(plugin, "_spawn", lambda argv: spawned.append(argv))
    # When: the CLI is launched
    plugin._launch(
        plugin.Trigger(chat_id="C1", doc_paths=(), body="본문", message_id="M1"), "python3"
    )
    # Then: both the channel and the anchor reach the CLI
    [argv] = spawned
    assert argv[argv.index("--notify-channel") + 1] == "C1"
    assert argv[argv.index("--notify-message-id") + 1] == "M1"


def test_plugin_acks_into_the_anchored_thread(monkeypatch):
    # Given: thread resolution succeeds for the anchor
    plugin, _launches, posts = _plugin_spies(monkeypatch)
    monkeypatch.setattr(plugin, "_thread_for", lambda chat_id, message_id: f"thread-of-{message_id}")
    event = _Event(text="!meeting 합성 회의 본문")
    event.message_id = "M1"
    # When: the gate intercepts it
    assert plugin.pre_gateway_dispatch(event, None, None)["action"] == "skip"
    # Then: the ACK lands in the thread, not the channel
    assert posts == [("thread-of-M1", plugin.ACK_MESSAGE)]


def test_plugin_ack_falls_back_to_the_channel_when_thread_fails(monkeypatch):
    # Given: thread resolution blows up
    plugin, _launches, posts = _plugin_spies(monkeypatch)

    def boom(chat_id, message_id):
        raise RuntimeError("thread api down")

    monkeypatch.setattr(plugin, "_thread_for", boom)
    event = _Event(text="!meeting 합성 회의 본문")
    event.message_id = "M1"
    # When: the gate intercepts it
    assert plugin.pre_gateway_dispatch(event, None, None)["action"] == "skip"
    # Then: the ACK still reaches the owner in the channel
    assert posts == [("C1", plugin.ACK_MESSAGE)]


class _SentChunk:
    def __init__(self, message_id: str) -> None:
        self.message_id = message_id


def test_cli_notify_routes_to_the_origin_thread_when_anchored(monkeypatch):
    # Given: an anchor, a thread-creating api and a spied transport
    calls: list[tuple[str, str]] = []
    posts: list[tuple[str, str]] = []

    def api(method, path, payload=None):
        calls.append((method, path))
        return {"id": "T1"}

    class _Transport:
        def __init__(self, channel_id):
            self.channel_id = channel_id

        def send(self, body):
            posts.append((self.channel_id, body))
            return (_SentChunk("p1"),)

    monkeypatch.setattr(meeting_cli, "_discord_api", api)
    monkeypatch.setattr(meeting_cli, "_transport", _Transport)
    # When: the notice is delivered live
    meeting_cli._notify("C1", "회의록 처리 완료: x", offline_dir=None, message_id="M1")
    # Then: the thread is anchored on the instruction and the notice lands inside it
    assert calls == [("POST", "/channels/C1/messages/M1/threads"), ("POST", "/channels/T1/messages")]
    assert posts == [("T1", "회의록 처리 완료: x")]


def test_cli_notify_without_anchor_posts_to_the_channel(monkeypatch):
    # Given: no anchor and an api that must stay silent
    posts: list[tuple[str, str]] = []

    class _Transport:
        def __init__(self, channel_id):
            self.channel_id = channel_id

        def send(self, body):
            posts.append((self.channel_id, body))
            return (_SentChunk("p1"),)

    monkeypatch.setattr(meeting_cli, "_transport", _Transport)
    monkeypatch.setattr(
        meeting_cli, "_discord_api", lambda *a, **k: pytest.fail("no thread api without anchor")
    )
    # When: the notice is delivered live
    meeting_cli._notify("C1", "msg", offline_dir=None)
    # Then: legacy channel post
    assert posts == [("C1", "msg")]


def test_ingest_offline_records_the_thread_anchor(tmp_path, monkeypatch, capsys):
    # Given: an offline ingest instructed with an anchor
    _meeting_env(tmp_path, monkeypatch)
    body_file = tmp_path / "m.txt"
    body_file.write_text("합성 주간회의 기록입니다\n- 차: 다음 주까지 보고서 작성", encoding="utf-8")
    # When: the CLI runs offline with the anchor
    assert meeting_cli.main([
        "ingest", "--body-file", str(body_file), "--label", "합성",
        "--recorded-response", str(FIXTURES / "recorded-clean.json"),
        "--offline", "--notify-channel", "C1", "--notify-message-id", "M1",
    ]) == 0
    # Then: the offline notify artifact keeps the legacy head and records the anchor
    text = (tmp_path / "plan" / "notify.txt").read_text(encoding="utf-8")
    assert text.startswith("C1\n")
    assert "회의록 처리 완료" in text
    assert "thread-anchor=M1" in text


def test_cli_notify_falls_back_to_the_channel_when_helper_is_unavailable(monkeypatch, capsys):
    # Given: an anchor but an interop runtime without origin_notice
    posts: list[tuple[str, str]] = []

    class _Transport:
        def __init__(self, channel_id):
            self.channel_id = channel_id

        def send(self, body):
            posts.append((self.channel_id, body))
            return (_SentChunk("p1"),)

    def missing():
        raise ImportError("No module named 'automation'")

    monkeypatch.setattr(meeting_cli, "_transport", _Transport)
    monkeypatch.setattr(meeting_cli, "_origin_notice", missing)
    # When: the notice is delivered live with an anchor
    meeting_cli._notify("C1", "회의록 처리 완료: x", offline_dir=None, message_id="M1")
    # Then: the legacy channel post still happens, with a marker
    assert posts == [("C1", "회의록 처리 완료: x")]
    assert "NOTIFY-HELPER-MISSING" in capsys.readouterr().err


# --- todo 8 Drive publication regressions ------------------------------------


def _run_meeting_ingest(tmp_path, monkeypatch, fixture, response, *, publish=None):
    monkeypatch.setenv("MEETING_NOTES_DIR", str(tmp_path / "notes"))
    monkeypatch.setenv("MEETING_STATE_FILE", str(tmp_path / "state/milestones.yaml"))
    monkeypatch.setenv("MEETING_PROMPT_FILE", str(REPO / "prompts/meeting-extraction-v3.md"))
    monkeypatch.setenv("MEETING_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MEETING_PLAN_DIR", str(tmp_path / "plan"))
    monkeypatch.setenv("MEETING_CONFIG", str(tmp_path / "absent.json"))
    monkeypatch.setenv("DRIVE_PUBLISH_ENABLED", "1")
    if publish is not None:
        import automation.drive_outputs as drive_outputs
        monkeypatch.setattr(drive_outputs, "publish_best_effort", publish)
    return meeting_cli.main([
        "ingest", "--file", str(FIXTURES / fixture),
        "--recorded-response", str(FIXTURES / response), "--offline",
    ])


def test_meeting_drive_publish_uses_note_date_and_label(tmp_path, monkeypatch):
    """The title carries the meeting's name, which is what SKILL.md always documented.

    It used to carry `note_path`'s 8-hex content ref — unfindable in a Drive folder,
    and the one thing the owner would never search for. Changed together with the fix
    that made the publication happen at all (the import had never worked from the mount).
    """
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 20, 12, tzinfo=tz)

    monkeypatch.setattr(meeting_cli, "datetime", FixedDateTime)
    calls = []

    def publish(*args, **kwargs):
        calls.append((args, kwargs))

    assert _run_meeting_ingest(tmp_path, monkeypatch, "meeting-clean.md", "recorded-clean.json", publish=publish) == 0
    args, kwargs = calls[0]
    assert args[0] == "meeting"
    assert args[1] == "회의록-meeting-clean"
    assert kwargs["on"].isoformat() == "2026-07-15"
    assert args[2][0][1] == args[1]


def test_meeting_drive_publish_disabled_makes_zero_runner_calls(tmp_path, monkeypatch):
    from automation.drive_client import DriveClient
    import automation.drive_outputs as drive_outputs

    artifact = tmp_path / "2026-08-20-meeting-x.md"
    artifact.write_text("note", encoding="utf-8")
    calls = []

    def runner(argv):
        calls.append(argv)
        return {}

    client = DriveClient("fake-gws", tmp_path / "folders.json", runner=runner)
    monkeypatch.delenv("DRIVE_PUBLISH_ENABLED", raising=False)
    assert drive_outputs.publish_best_effort(
        "meeting", "회의록-x", [(artifact, "회의록-x")], client=client
    ) is None
    assert calls == []


def test_patent_keyword_meeting_is_published_to_drive(tmp_path, monkeypatch, capsys):
    """민감 차단이 없다 — 특허 낱말이 든 회의록도 Drive 에 한 번 발행된다."""
    calls = []
    assert _run_meeting_ingest(
        tmp_path, monkeypatch, "meeting-patent.md", "recorded-patent.json",
        publish=lambda *args, **kwargs: calls.append((args, kwargs)),
    ) == 0
    assert [args[:2] for args, _ in calls] == [("meeting", "회의록-meeting-patent")]
    assert "DRIVE-PUBLISH-SKIP" not in capsys.readouterr().out


def test_drive_facade_import_failure_does_not_block_local_save(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DRIVE_PUBLISH_ENABLED", "0")
    monkeypatch.setitem(sys.modules, "automation.drive_outputs", None)
    assert _run_meeting_ingest(tmp_path, monkeypatch, "meeting-clean.md", "recorded-clean.json") == 0
    assert list((tmp_path / "notes").glob("*.md"))
    assert "DRIVE-PUBLISH-SKIP reason=ImportError" in capsys.readouterr().out


# --- the note reaches Drive only if `automation` is importable from the mount ---


def test_runtime_root_resolves_from_the_mounted_scripts_directory(
    tmp_path: Path, monkeypatch
) -> None:
    """This file runs from /srv/autophagy-skills/live/meeting/scripts, where `automation`
    is not a sibling — so the plain import had failed on every production run."""
    monkeypatch.delenv("AUTOPHAGY_RUNTIME_ROOT", raising=False)
    monkeypatch.delenv("AUTOPHAGY_REPO_ROOT", raising=False)
    mounted = tmp_path / "live" / "meeting" / "scripts" / "meeting_cli.py"
    mounted.parent.mkdir(parents=True)
    mounted.write_text("", encoding="utf-8")
    release = tmp_path / "release"
    (release / "automation").mkdir(parents=True)
    (release / "automation" / "drive_outputs.py").write_text("", encoding="utf-8")

    resolved = meeting_cli.runtime_root(mounted, current=release, mirror=tmp_path / "absent")

    assert resolved == release


def test_runtime_root_prefers_the_checkout_that_carries_automation(monkeypatch) -> None:
    monkeypatch.delenv("AUTOPHAGY_RUNTIME_ROOT", raising=False)
    monkeypatch.delenv("AUTOPHAGY_REPO_ROOT", raising=False)
    assert meeting_cli.runtime_root() == REPO


def test_note_is_published_under_the_meeting_label(tmp_path: Path, monkeypatch) -> None:
    """`회의록-3efbec52` is a content hash; the owner looks for the meeting by name."""
    from datetime import date

    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from automation import drive_outputs

    calls: list[tuple[str, str, object]] = []
    monkeypatch.setattr(
        drive_outputs, "publish_best_effort",
        lambda kind, title, artifacts, **kwargs: calls.append((kind, title, kwargs.get("on"))),
    )
    note = tmp_path / "2026-08-26-meeting-3efbec52.md"
    note.write_text("# 회의 요약\n", encoding="utf-8")

    meeting_cli._publish_note(note, label="킥오프 회의", on=date(2026, 8, 26))

    assert calls == [("meeting", "회의록-킥오프 회의", date(2026, 8, 26))]


def test_note_is_published_under_its_project(tmp_path: Path, monkeypatch) -> None:
    """Minutes live beside the transcript and the glossary of the same project."""
    from datetime import date

    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from automation import drive_outputs

    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        drive_outputs, "publish_best_effort",
        lambda kind, title, artifacts, **kw: calls.append((title, kw.get("project"))),
    )
    note = tmp_path / "2026-08-26-meeting-99678d3a.md"
    note.write_text("# 회의 요약\n", encoding="utf-8")

    meeting_cli._publish_note(
        note, label="킥오프", on=date(2026, 8, 26), project="합성과제"
    )

    assert calls == [("회의록-킥오프", "합성과제")]


# --- 산출물 출처는 정본 회의록이다 (2026-08-26 소유자 지시) -----------------------


def _rendered_note(original_text: str) -> str:
    """A rendered note carrying a transcript sentence we can look for."""
    extraction = meeting_llm.parse_extraction(
        (FIXTURES / "recorded-clean.json").read_text(encoding="utf-8")
    )
    return meeting_minutes.render(
        label="출처 규약", kind="md", extraction=extraction,
        original_text=original_text, ref="deadbeef", now=NOW,
    )


def test_finalized_view_drops_the_transcript_appendix():
    """산출물 작성자는 부록 위만 봐야 한다 — 경계를 렌더러가 한 곳에서 소유한다.

    전사본은 음성 인식 결과라 고유명사가 틀린다. 실제로 2026-08-26 에 전사본의 '한정기술'
    (정본은 한국전력기술)이 외부 배포용 공정표 템플릿에 그대로 실렸다.
    """
    marker = "전사본에만있는고유명사오기"
    text = _rendered_note(marker)
    view = meeting_minutes.finalized_view(text)

    assert marker in text, "픽스처 전제: 전사본이 노트에 들어가 있어야 한다"
    assert marker not in view
    assert meeting_minutes.APPENDIX_HEADING not in view
    assert "## 결정사항" in view


def test_transcript_section_forbids_authoring_from_it():
    """`### C. 원문 전사본` 바로 아래에 산출물 작성 금지가 적혀 있어야 한다."""
    text = _rendered_note("원문 한 줄")
    heading = text.index("### C. 원문 전사본")
    fence = text.index("```", heading)
    between = text[heading:fence]

    assert "산출물" in between
    assert "출처로 쓰지" in between


def test_card_points_at_the_finalized_minutes_first():
    """카드의 출처는 Drive 정본을 먼저 가리켜야 한다 — 로컬 노트는 전사본을 안고 있다."""
    card = meeting_actions.sanitize_card(
        _todo("공정표 템플릿 작성", "2026-09-02", "템플릿을 잡아 드리겠다"),
        seq=1, note_name="n.md", ref="deadbeef",
        project="해양고신뢰성",
    )

    assert "회의록/해양고신뢰성" in card.body
    assert "정본" in card.body
    assert "~/notes/meetings/n.md" in card.body
    assert card.body.index("회의록/해양고신뢰성") < card.body.index("~/notes/meetings")


def test_card_without_project_keeps_the_existing_body():
    """과제명을 모르면 기존 본문 그대로 — 없는 경로를 지어내지 않는다."""
    item = _todo("공정표 템플릿 작성", "2026-09-02", "템플릿을 잡아 드리겠다")
    with_default = meeting_actions.sanitize_card(
        item, seq=1, note_name="n.md", ref="deadbeef"
    )
    explicit_empty = meeting_actions.sanitize_card(
        item, seq=1, note_name="n.md", ref="deadbeef", project=""
    )

    assert with_default == explicit_empty
    assert "정본" not in with_default.body
    assert "출처: ~/notes/meetings/n.md" in with_default.body


def test_plan_cards_forwards_the_project_to_every_card():
    extraction = meeting_llm.Extraction(
        todos=(_todo("가", None, "나"), _todo("다", None, "라"))
    )
    cards = meeting_actions.plan_cards(
        extraction, note_name="n.md", ref="deadbeef",
        project="해양고신뢰성",
    )

    assert len(cards) == 2
    assert all("회의록/해양고신뢰성" in card.body for card in cards)


# --- 용어 교정은 회의록에서, 전사본에는 새기지 않는다 (2026-09-05 소유자 결정) ---------

_MISHEARD_TRANSCRIPT = """# 주간 연구 미팅 (합성 픽스처 — 실제 회의 아님)

일시: 2026-07-15 (수) 10:00, 장소: 공학관 402호, 참석: 차, 박

## 논의/결정
- 항정기술 쪽 계측 자료는 8월 1일까지 받기로 했다.

## 액션아이템
- 차: 항정기술 계측 자료 정리 7/24까지
"""

_MISHEARD_RECORDED = {
    "meeting": {"title": "계측 자료 협의", "date": "2026-07-15", "attendees": ["차", "박"]},
    "summary": ["항정기술 계측 자료를 8월 1일까지 받기로 했다"],
    "decisions": [
        {
            "text": "항정기술 계측 자료를 8월 1일까지 받는다",
            "basis": "항정기술 쪽 계측 자료는 8월 1일까지 받기로 했다",
        }
    ],
    "todos": [
        {
            "title": "항정기술 계측 자료 정리",
            "deadline": "2026-07-24",
            "basis": "차: 항정기술 계측 자료 정리 7/24까지",
        }
    ],
    "milestones": [],
    "others": [],
}


def test_the_minutes_body_is_corrected_and_the_appendix_transcript_is_not(
    tmp_path, monkeypatch, capsys
):
    """회의록 본문은 회의록 참고 문서로 고치고, 부록 전사본은 인식된 그대로 남긴다.

    전사본은 증거다 — 오인식을 거기에 새기면 원래 낱말이 어디에도 남지 않는다. 반대로
    본문의 오인식은 그대로 두면 공정표·보고서로 옮겨 간다(2026-08-26 실측).
    """
    glossary = tmp_path / "용어집.csv"
    glossary.write_text("한전기술\n", encoding="utf-8")
    transcript = tmp_path / "meeting-misheard.md"
    transcript.write_text(_MISHEARD_TRANSCRIPT, encoding="utf-8")
    recorded = tmp_path / "recorded-misheard.json"
    recorded.write_text(json.dumps(_MISHEARD_RECORDED, ensure_ascii=False), encoding="utf-8")
    log = tmp_path / "corrections.jsonl"
    monkeypatch.setenv("TERM_GLOSSARY_FILE", str(glossary))
    monkeypatch.setenv("TERM_CORRECTION_LOG", str(log))
    monkeypatch.setenv("MEETING_NOTES_DIR", str(tmp_path / "notes"))
    monkeypatch.setenv("MEETING_STATE_FILE", str(tmp_path / "state/milestones.yaml"))
    monkeypatch.setenv("MEETING_PROMPT_FILE", str(REPO / "prompts/meeting-extraction-v3.md"))
    monkeypatch.setenv("MEETING_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MEETING_PLAN_DIR", str(tmp_path / "plan"))
    monkeypatch.setenv("MEETING_CONFIG", str(tmp_path / "absent.json"))

    rc = meeting_cli.main(
        [
            "ingest",
            "--file", str(transcript),
            "--recorded-response", str(recorded),
            "--label", "계측 자료 협의",
            "--offline",
        ]
    )
    capsys.readouterr()

    assert rc == 0
    note = next((tmp_path / "notes").glob("*.md")).read_text(encoding="utf-8")
    body = meeting_minutes.finalized_view(note)
    appendix = note[note.index("### C. 원문 전사본") :]
    assert "한전기술" in body and "항정기술" not in body
    assert "항정기술" in appendix and "한전기술" not in appendix
    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert records, "교정 로그가 비어 있다"
    assert {(item["document"], item["stage"]) for item in records} == {("meeting", "minutes")}
    assert {(item["before"], item["after"]) for item in records} == {("항정기술", "한전기술")}


def test_the_transcript_handed_to_the_note_is_the_uncorrected_one(tmp_path, monkeypatch):
    """부록에 실리는 원문은 추출된 텍스트 그대로여야 한다 — 바이트 하나도 다르지 않게."""
    glossary = tmp_path / "용어집.csv"
    glossary.write_text("한전기술\n", encoding="utf-8")
    transcript = tmp_path / "meeting-misheard.md"
    transcript.write_text(_MISHEARD_TRANSCRIPT, encoding="utf-8")
    recorded = tmp_path / "recorded-misheard.json"
    recorded.write_text(json.dumps(_MISHEARD_RECORDED, ensure_ascii=False), encoding="utf-8")
    seen: dict[str, str] = {}
    real_write_note = meeting_actions.write_note

    def spy(notes_dir, **kwargs):
        seen["original_text"] = kwargs["original_text"]
        return real_write_note(notes_dir, **kwargs)

    monkeypatch.setattr(meeting_cli.meeting_actions, "write_note", spy)
    monkeypatch.setenv("TERM_GLOSSARY_FILE", str(glossary))
    monkeypatch.setenv("TERM_CORRECTION_LOG", str(tmp_path / "corrections.jsonl"))
    monkeypatch.setenv("MEETING_NOTES_DIR", str(tmp_path / "notes"))
    monkeypatch.setenv("MEETING_STATE_FILE", str(tmp_path / "state/milestones.yaml"))
    monkeypatch.setenv("MEETING_PROMPT_FILE", str(REPO / "prompts/meeting-extraction-v3.md"))
    monkeypatch.setenv("MEETING_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MEETING_PLAN_DIR", str(tmp_path / "plan"))
    monkeypatch.setenv("MEETING_CONFIG", str(tmp_path / "absent.json"))

    rc = meeting_cli.main(
        [
            "ingest",
            "--file", str(transcript),
            "--recorded-response", str(recorded),
            "--label", "계측 자료 협의",
            "--offline",
        ]
    )

    assert rc == 0
    assert seen["original_text"] == _MISHEARD_TRANSCRIPT
