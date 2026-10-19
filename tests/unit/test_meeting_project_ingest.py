"""과제 양식 + Action Item DB 를 태운 ingest 종단 계약.

`test_meeting_skill.py` 에 붙이지 않고 파일을 가른 이유는 tests/AGENTS.md 의 규칙이다 —
FS3 정산 레코드가 고정한 테스트 파일에 케이스를 더하면 과거 RED/GREEN 증적이 재현되지
않는다. 여기서 보는 것은 기존 파이프라인이 아니라 `--project` 가 붙었을 때만 생기는
경로다: 양식 배치, 관리번호 발급, CSV 갱신, 그리고 같은 회의를 다시 넣었을 때의 멱등성.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "skills" / "meeting"
sys.path.insert(0, str(SKILL / "scripts"))

import meeting_action_db  # noqa: E402
import meeting_actions  # noqa: E402
import meeting_action_id  # noqa: E402
import meeting_cli  # noqa: E402
import meeting_minutes  # noqa: E402
import meeting_project  # noqa: E402
import meeting_template  # noqa: E402

NOW = datetime(2026, 8, 27, 14, 0, 0, tzinfo=ZoneInfo("Asia/Seoul"))
PROJECT = "해양고신뢰성"
ID_SHAPE = re.compile(r"^[A-Z]{4}[0-9]{6}$")
TABLE_HEADER = "| 관리번호 | 내용 | 조치기한 | 담당기관 |"

FORM = """해양고신뢰성 과제 기술회의 회의록

1. 일시 및 장소 :

2. 참석자 :

3. 회의 내용
가. 안건
  ○

4. Action Item 종합
가. 미결 Action Items
나. 신규 Action Items
"""

BODY = """해양고신뢰성 과제 8월 기술회의
차: 계통 열수력 해석 조건을 8월 말까지 정리하겠습니다.
박: 유동 상관성 보고서를 9월 말까지 검토용으로 제출하겠습니다.
"""

RECORDED = json.dumps(
    {
        "meeting": {
            "title": "8월 기술회의", "date": "2026-08-20",
            "attendees": ["차", "박"], "place": "제2회의실",
        },
        "summary": ["상관길이 산출 기준 확정"],
        "decisions": [{"text": "축소모델 데이터로 상관길이를 산출한다", "basis": "결정 발언"}],
        "todos": [{"title": "계통 열수력 해석 조건 정리", "deadline": "2026-08-31",
                   "basis": "차: 8월 말까지 정리하겠습니다"}],
        "others": [{"owner": "한국전력기술", "title": "유동 상관성 보고서 검토용 제출",
                    "deadline": "2026-09-30", "basis": "박: 9월 말까지 제출하겠습니다"}],
        "milestones": [], "discussion": [], "open_questions": [], "next_meeting": None,
        "resolved_actions": [{"id": "HOGS260001", "basis": "완료 확인"},
                             {"id": "ZZZZ990999", "basis": "존재하지 않는 번호"}],
    },
    ensure_ascii=False,
)

_EXISTING = (
    meeting_action_db.Record(
        "HOGS260001", PROJECT, "선행 미결 항목", "두산", "2026-07-31",
        meeting_action_db.OPEN, "2026-06-10", "2026-06-10-meeting-aaaaaaaa.md", "", "", "근거",
    ),
    meeting_action_db.Record(
        "HOGS260002", PROJECT, "계속 열려 있는 항목", "기계연", "2026-12-31",
        meeting_action_db.OPEN, "2026-06-10", "2026-06-10-meeting-aaaaaaaa.md", "", "", "근거",
    ),
)


def _board() -> meeting_project.Board:
    return meeting_project.Board(
        project=PROJECT,
        code="HOGS",
        template=meeting_template.parse(FORM),
        records=_EXISTING,
        registry=(("HOGS", PROJECT),),
        registry_changed=False,
    )


def _run(tmp_path, monkeypatch, capsys, board=None, project=PROJECT, label="8월 기술회의") -> dict:
    monkeypatch.setenv("MEETING_NOTES_DIR", str(tmp_path / "notes"))
    monkeypatch.setenv("MEETING_PROMPT_FILE", str(SKILL / "prompts/meeting-extraction-v5.md"))
    monkeypatch.setenv("MEETING_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MEETING_PLAN_DIR", str(tmp_path / "plan"))
    monkeypatch.setenv("MEETING_CONFIG", str(tmp_path / "absent.json"))
    monkeypatch.setattr(
        meeting_cli.meeting_project, "load_board",
        lambda project, *, client=None: board or _board(),
    )
    body = tmp_path / "body.txt"
    body.write_text(BODY, encoding="utf-8")
    recorded = tmp_path / "recorded.json"
    recorded.write_text(RECORDED, encoding="utf-8")
    rc = meeting_cli.main(
        ["ingest", "--body-file", str(body), "--label", label,
         *(["--project", project] if project else []),
         "--offline", "--recorded-response", str(recorded)]
    )
    assert rc == 0
    captured = capsys.readouterr()
    # 요약 JSON 만 소비하고 stderr 는 호출자에게 돌려준다 — 마커를 보는 테스트가 있다.
    print(captured.err, end="", file=sys.stderr)
    return json.loads(captured.out.strip().splitlines()[-1])


def _note(tmp_path) -> str:
    notes = sorted((tmp_path / "notes").glob("*-meeting-*.md"))
    assert len(notes) == 1, f"expected exactly one note: {notes}"
    return notes[0].read_text(encoding="utf-8")


def test_ingest_with_a_project_issues_sequential_management_numbers(tmp_path, monkeypatch, capsys):
    result = _run(tmp_path, monkeypatch, capsys)
    document = _note(tmp_path)
    issued = re.findall(r"\| ([A-Z]{4}[0-9]{6}) \|", document)

    assert result["actions_new"] == 2, "todo 1건 + others 1건이 신규로 발급되어야 한다"
    assert [number for number in issued if ID_SHAPE.fullmatch(number)] == issued
    assert "HOGS260003" in issued and "HOGS260004" in issued, f"기존 최대 002 다음이어야 한다: {issued}"


def test_ingest_appends_both_action_tables_in_the_form_order(tmp_path, monkeypatch, capsys):
    _run(tmp_path, monkeypatch, capsys)
    head = _note(tmp_path).split("## 부록 · 근거와 원문")[0]

    assert [line for line in head.splitlines() if re.match(r"^## \d+\. ", line)] == [
        "## 1. 일시 및 장소", "## 2. 참석자", "## 3. 회의 내용", "## 4. Action Item 종합",
    ]
    assert head.count(TABLE_HEADER) == 2, "표는 미결·신규 각각 한 번씩만 나와야 한다"
    assert head.index("### 가. 미결 Action Items") < head.index("### 나. 신규 Action Items")


def test_resolved_action_closes_only_a_known_open_row(tmp_path, monkeypatch, capsys):
    result = _run(tmp_path, monkeypatch, capsys)
    rows = {row.id: row for row in meeting_action_db.load(
        (tmp_path / "plan" / meeting_action_db.DB_FILENAME).read_text(encoding="utf-8")
    )}
    outstanding = _note(tmp_path).split("### 가. 미결 Action Items")[1].split("### 나.")[0]

    assert result["actions_closed"] == 1
    assert rows["HOGS260001"].status == meeting_action_db.DONE
    assert rows["HOGS260002"].status == meeting_action_db.OPEN
    assert "ZZZZ990999" not in rows, "DB 에 없는 번호를 지어내 닫으면 안 된다"
    assert "HOGS260001" not in outstanding and "HOGS260002" in outstanding


def test_reingesting_the_same_meeting_does_not_grow_the_database(tmp_path, monkeypatch, capsys):
    _run(tmp_path, monkeypatch, capsys)
    first = (tmp_path / "plan" / meeting_action_db.DB_FILENAME).read_bytes()
    _run(tmp_path, monkeypatch, capsys)
    second = (tmp_path / "plan" / meeting_action_db.DB_FILENAME).read_bytes()

    assert first == second, "같은 회의를 다시 넣으면 행도 번호도 늘지 않아야 한다"
    assert len(meeting_action_db.load(second.decode("utf-8"))) == 4


def test_ingest_without_a_project_still_gets_the_tail_without_numbers(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        meeting_cli.meeting_project, "load_board",
        lambda project, *, client=None: meeting_project.empty_board(),
    )
    monkeypatch.setenv("MEETING_NOTES_DIR", str(tmp_path / "notes"))
    monkeypatch.setenv("MEETING_PROMPT_FILE", str(SKILL / "prompts/meeting-extraction-v5.md"))
    monkeypatch.setenv("MEETING_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MEETING_PLAN_DIR", str(tmp_path / "plan"))
    monkeypatch.setenv("MEETING_CONFIG", str(tmp_path / "absent.json"))
    body = tmp_path / "body.txt"
    body.write_text(BODY, encoding="utf-8")
    recorded = tmp_path / "recorded.json"
    recorded.write_text(RECORDED, encoding="utf-8")

    assert meeting_cli.main(
        ["ingest", "--body-file", str(body), "--label", "8월 기술회의", "--offline",
         "--recorded-response", str(recorded)]
    ) == 0
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    document = _note(tmp_path)

    assert result["actions_new"] == 0 and result["actions_open"] == 0
    assert "## Action Item 종합" in document and TABLE_HEADER in document
    assert not (tmp_path / "plan" / meeting_action_db.DB_FILENAME).exists()
    assert not re.search(r"\| [A-Z]{4}[0-9]{6} \|", document)


def test_exhausted_sequence_still_writes_the_minutes(tmp_path, monkeypatch, capsys):
    """번호가 모자란다고 회의록을 잃으면 교환이 성립하지 않는다 — 추출은 이미 끝난 뒤다."""
    def row(action_id, title, status):
        return meeting_action_db.Record(
            action_id, PROJECT, title, "기관", "", status,
            "2026-01-01", "old.md", "", "", "근거",
        )

    exhausted = meeting_project.Board(
        project=PROJECT, code="HOGS", template=None,
        records=(row("HOGS260001", "선행 미결", meeting_action_db.OPEN),
                 row(f"HOGS26{meeting_action_id.SEQ_MAX:04d}", "마지막 번호", meeting_action_db.DONE)),
        registry=(("HOGS", PROJECT),), registry_changed=False,
    )

    result = _run(tmp_path, monkeypatch, capsys, board=exhausted)
    document = _note(tmp_path)

    assert result["actions_new"] == 0
    assert "ACTION-ID-EXHAUSTED" in capsys.readouterr().err
    assert TABLE_HEADER in document, "번호가 없어도 표는 나와야 한다"
    assert "| — |" in document, "신규 항목은 번호 없이라도 실려야 한다"
    assert not (tmp_path / "plan" / meeting_action_db.DB_FILENAME).exists(), (
        "번호 없는 행으로 원장을 오염시키면 안 된다"
    )
    assert "HOGS260001" in document, "미결은 기존 번호 그대로 보여야 한다"


def test_ingest_without_the_flag_detects_the_project_from_the_label(tmp_path, monkeypatch, capsys):
    """플러그인(`!meeting`)은 --project 를 넘길 수 없다 — 라벨이 유일한 단서다."""
    monkeypatch.setattr(
        meeting_cli.meeting_project, "detect_project",
        lambda label, *, client=None: PROJECT if PROJECT in label else "",
    )

    result = _run(tmp_path, monkeypatch, capsys, project=None, label=f"20260825_{PROJECT}")

    assert result["project"] == PROJECT, "라벨에서 찾은 과제가 요약에 드러나야 한다"
    assert result["actions_new"] == 2
    assert re.search(r"\| [A-Z]{4}[0-9]{6} \|", _note(tmp_path)), "관리번호가 붙어야 한다"


def test_completion_notice_explains_exhausted_action_ids() -> None:
    notice = meeting_actions.format_notify(
        label="x", cards=1, others=0,
        note_name="n.md", team_posted=False, project=PROJECT, action_id_exhausted=True,
    )

    assert "관리번호 소진 안내" in notice


def test_summary_and_notice_say_when_no_project_was_resolved(tmp_path, monkeypatch, capsys):
    """침묵이 이 결함을 여기까지 끌고 왔다 — 번호가 없으면 없다고 말한다."""
    monkeypatch.setattr(
        meeting_cli.meeting_project, "detect_project", lambda label, *, client=None: ""
    )

    result = _run(
        tmp_path, monkeypatch, capsys, board=meeting_project.empty_board(),
        project=None, label="주간 정례회의",
    )

    assert result["project"] == ""
    assert result["actions_new"] == 0 and result["actions_open"] == 0
    assert "| — |" in _note(tmp_path), "번호 없이라도 항목은 실린다"
    notice = meeting_actions.format_notify(
        label="x", cards=1, others=0,
        note_name="n.md", team_posted=False, project="",
    )
    assert "과제 미지정" in notice and "--project" in notice


def _pending(name: str = "2026-08-26_20260825_해양고신뢰성.md"):
    return meeting_project.PendingTranscript(PROJECT, "2026", "file-id", name)


def _run_pending(tmp_path, monkeypatch, capsys, candidates, source=()):
    monkeypatch.setattr(
        meeting_cli.meeting_project, "pending_transcripts",
        lambda *, client=None: tuple(candidates),
    )
    monkeypatch.setattr(
        meeting_cli.meeting_project, "download_transcript",
        lambda pending, dest, *, client=None: dest.write_text(BODY, encoding="utf-8"),
    )
    monkeypatch.setattr(
        meeting_cli.meeting_project, "load_board",
        lambda project, *, client=None: _board() if project else meeting_project.empty_board(),
    )
    for name, value in (
        ("MEETING_NOTES_DIR", tmp_path / "notes"),
        ("MEETING_PROMPT_FILE", SKILL / "prompts/meeting-extraction-v5.md"),
        ("MEETING_LOG_DIR", tmp_path / "logs"), ("MEETING_PLAN_DIR", tmp_path / "plan"),
        ("MEETING_CONFIG", tmp_path / "absent.json"),
    ):
        monkeypatch.setenv(name, str(value))
    recorded = tmp_path / "recorded.json"
    recorded.write_text(RECORDED, encoding="utf-8")
    code = meeting_cli.main([
        "ingest", *(source or ["--from-pending-transcript"]), "--offline",
        "--notify-channel", "TEST", "--recorded-response", str(recorded),
    ])
    return code, capsys.readouterr()


def test_a_single_pending_transcript_becomes_minutes_with_its_project(tmp_path, monkeypatch, capsys):
    """전사본은 `전사본/<과제>/<연도>/` 에 있으므로 과제가 경로에서 확정된다 — 추측이 아니다."""
    code, captured = _run_pending(tmp_path, monkeypatch, capsys, [_pending()])
    result = json.loads(captured.out.strip().splitlines()[-1])

    assert code == 0
    assert result["project"] == PROJECT
    assert result["actions_new"] == 2
    assert re.search(r"\| [A-Z]{4}[0-9]{6} \|", _note(tmp_path)), "관리번호가 붙어야 한다"


def test_no_pending_transcript_says_so_instead_of_failing_silently(tmp_path, monkeypatch, capsys):
    code, captured = _run_pending(tmp_path, monkeypatch, capsys, [])

    assert code == 7
    assert "전사본" in (tmp_path / "plan" / "notify.txt").read_text(encoding="utf-8")
    assert not list((tmp_path / "notes").glob("*.md")) if (tmp_path / "notes").exists() else True


def test_several_pending_transcripts_refuse_to_guess(tmp_path, monkeypatch, capsys):
    """회의록 스무 개가 한 번에 만들어지는 것보다 고르지 않는 편이 낫다."""
    code, captured = _run_pending(
        tmp_path, monkeypatch, capsys,
        [_pending(), _pending("2026-08-27_20260826_해양고신뢰성.md")],
    )
    notice = (tmp_path / "plan" / "notify.txt").read_text(encoding="utf-8")

    assert code == 7
    assert "2026-08-26_20260825_해양고신뢰성.md" in notice
    assert "2026-08-27_20260826_해양고신뢰성.md" in notice


def test_pending_name_picks_one_transcript_out_of_several(tmp_path, monkeypatch, capsys):
    """야간 배치는 다건에서 멈출 수 없다 — 매일 밤 같은 이유로 서면 기능이 죽는다."""
    wanted = "2026-08-27_20260826_해양고신뢰성.md"
    code, captured = _run_pending(
        tmp_path, monkeypatch, capsys,
        [_pending(), _pending(wanted)],
        source=["--pending-name", wanted],
    )
    result = json.loads(captured.out.strip().splitlines()[-1])

    assert code == 0
    assert result["project"] == PROJECT
    assert re.search(r"\| [A-Z]{4}[0-9]{6} \|", _note(tmp_path))


def test_pending_name_that_matches_nothing_is_refused(tmp_path, monkeypatch, capsys):
    """워처가 넘긴 이름을 자식이 다시 검증한다 — 사라진 전사본을 지어내지 않는다."""
    code, _ = _run_pending(
        tmp_path, monkeypatch, capsys, [_pending()],
        source=["--pending-name", "없는-전사본.md"],
    )
    notice = (tmp_path / "plan" / "notify.txt").read_text(encoding="utf-8")

    assert code == 7
    assert "없는-전사본.md" in notice


def test_pending_transcript_minutes_publish_under_its_project(tmp_path, monkeypatch, capsys):
    """야간 배치의 회의록은 전사본이 확정한 과제 폴더로 올라가야 한다.

    `meeting_project.pending_transcripts` 는 과제 쌍둥이 폴더에서만 `회의록-<전사본>` 을
    찾는다. 과제 없이 게시하면 회의록은 과제 없는 옛 `회의록/<연도>/` 로 가고, 다음 밤에도
    전사본은 여전히 '미처리' 라 같은 회의가 매일 다시 원장에 쌓인다(2026-09-02·03 실측).
    """
    import automation.drive_outputs as drive_outputs
    from automation.drive_client import DriveClient

    monkeypatch.setattr(DriveClient, "_run", lambda self, argv, cwd=None: {})
    calls: list[tuple[tuple, dict]] = []
    monkeypatch.setenv("DRIVE_PUBLISH_ENABLED", "1")
    monkeypatch.setattr(
        drive_outputs, "publish_best_effort",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    name = "2026-08-26_20260825_해양고신뢰성.md"
    code, _ = _run_pending(
        tmp_path, monkeypatch, capsys, [_pending(name)],
        source=["--pending-name", name],
    )

    assert code == 0
    assert len(calls) == 1, f"회의록 게시는 정확히 한 번이어야 한다: {calls}"
    args, kwargs = calls[0]
    assert args[0] == "meeting"
    assert args[1] == f"회의록-{name[:-3]}", "다음 밤이 찾는 이름 그대로여야 한다"
    assert kwargs["project"] == PROJECT, "--project 없이도 전사본의 과제로 게시해야 한다"


def test_pending_transcript_cards_cite_the_project_minutes(tmp_path, monkeypatch, capsys):
    """카드의 출처도 같은 과제 정본을 가리켜야 한다 — 게시 경로와 인용이 갈리면 안 된다."""
    name = "2026-08-26_20260825_해양고신뢰성.md"
    code, _ = _run_pending(
        tmp_path, monkeypatch, capsys, [_pending(name)],
        source=["--pending-name", name],
    )
    note_name = sorted((tmp_path / "notes").glob("*-meeting-*.md"))[0].name
    expected = meeting_minutes.source_block(note_name, PROJECT)
    bodies = [
        argv[argv.index("--body") + 1]
        for argv in (
            json.loads(line)
            for line in (tmp_path / "plan" / "kanban-plan.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip()
        )
        if argv[:2] == ["kanban", "create"]
    ]

    assert code == 0
    assert f"회의록/{PROJECT}/" in expected, "출처 조각이 과제를 담고 있어야 이 검사가 성립한다"
    assert bodies, "카드가 한 장은 계획되어야 한다"
    assert all(body.endswith(expected) for body in bodies), (
        f"카드 본문이 과제 정본을 인용해야 한다: {bodies}"
    )
