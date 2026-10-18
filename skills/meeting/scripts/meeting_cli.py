"""Meeting ingest CLI (W2-3): file/body -> LLM -> Kanban/milestones/#team.

Deterministic pipeline wrapper. All content stays out of argv/logs; the
routing log records metadata only (filenames, counts, provider), never text.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime
from pathlib import Path
from typing import Final
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))

import meeting_action_db
import meeting_action_id
import meeting_governed
import meeting_actions
import meeting_evidence
import meeting_extract
import meeting_llm
import meeting_knowledge
import meeting_reference
import meeting_result_notice
import meeting_project
import meeting_slides
import meeting_terms
from meeting_runtime import runtime_root

KST = ZoneInfo("Asia/Seoul")
_DRIVE_UNSAFE: Final = re.compile(r"[/\\]+")
_DOC_SUFFIX: Final = re.compile(r"\.(?:md|markdown|txt|pdf)$", re.IGNORECASE)


def _drive_title(label: str) -> str:
    """`회의록-<라벨>` — what SKILL.md always promised; the code shipped a content hash.

    The label defaults to the uploaded file's name, so it can arrive carrying `.md`;
    the taxonomy appends the real suffix itself and a title must not spell one.
    """
    cleaned = _DOC_SUFFIX.sub("", _DRIVE_UNSAFE.sub("-", label).strip())
    return f"회의록-{cleaned}" if cleaned else "회의록"


def _publish_note(
    note_path: Path, *, label: str, on: date, project: str = ""
) -> None:
    """Best-effort Drive publication — never touches the local note or the exit code."""
    root = str(runtime_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from automation.drive_outputs import publish_best_effort
    except ImportError:
        print(f"DRIVE-PUBLISH-SKIP reason=ImportError root={root}")
        return
    title = _drive_title(label)
    publish_best_effort(
        "meeting", title, [(note_path, title)], on=on, project=project or None
    )


def _correct_terms(
    extraction: meeting_llm.Extraction, *, label: str, project: str
) -> meeting_llm.Extraction:
    """회의록 본문의 용어를 중첩된 회의록 참고 문서로 고치고 교정 내역을 남긴다.

    교정은 전사본이 아니라 **산출 문서**를 만들 때 한다(소유자 결정 2026-09-05,
    `docs/guide/용어-교정-규약.md`) — 그래서 여기서 고치는 것은 해석 결과뿐이고, 부록에
    실릴 원문은 추출된 그대로 `write_note` 로 간다.

    참고 문서 조회도 로그 기록도 그 자체가 fail-soft 라서 감싸지 않는다: 여기서 다시
    잡으면 그 모듈들이 이미 찍은 마커 위에 마커가 겹치고, 실패의 종류가 지워진다.
    automation 자체가 없는 마운트만 한 줄 남기고 지나간다 — 참고 문서가 없다고 회의록을
    잃는 것이 더 나쁘다.
    """
    root = str(runtime_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from automation import term_correction_log, term_glossary  # noqa: PLC0415
    except ImportError:
        print(f"TERM-CORRECTION-SKIP reason=ImportError root={root}", file=sys.stderr)
        return extraction
    corrected, corrections = meeting_terms.correct(
        extraction, term_glossary.glossary_for("meeting", project)
    )
    term_correction_log.record(
        corrections, document="meeting", label=label, project=project, stage="minutes"
    )
    return corrected


def _env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default)).expanduser()


def _config() -> dict:
    config_path = _env_path("MEETING_CONFIG", "~/.hermes/meeting/config.json")
    if config_path.exists():
        return json.loads(config_path.read_text(encoding="utf-8"))
    return {}


def _log(record: dict) -> None:
    log_dir = _env_path("MEETING_LOG_DIR", "~/.hermes/meeting/logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"ingest-{datetime.now(KST).strftime('%Y%m%d')}.jsonl"
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    log_file.chmod(0o600)


def _interop_runtime() -> Path:
    configured = os.environ.get("INTEROP_RUNTIME")
    if configured:
        return Path(configured).expanduser()
    root = Path("~/.hermes/autophagy-import").expanduser()
    if (root / "automation" / "interop").is_dir():
        return root
    return Path("~/.hermes/interop_runtime").expanduser()


def _transport(channel_id: str):
    runtime = _interop_runtime()
    sys.path.insert(0, str(runtime))
    from automation.notice_bot import resolve_token
    from automation.interop.discord_transport import DiscordTransport  # noqa: PLC0415

    return DiscordTransport(token=resolve_token(required=True), channel_id=channel_id)


def _discord_api(method: str, path: str, payload: dict | None = None) -> object:
    try:
        from automation.notice_bot import resolve_token
    except ImportError:
        sys.path.insert(0, str(_interop_runtime()))
        from automation.notice_bot import resolve_token

    request = Request(
        f"https://discord.com/api/v10{path}",
        data=None if payload is None else json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bot {resolve_token(required=True)}",
            "Content-Type": "application/json",
            "User-Agent": "DiscordBot (https://github.com/orientpine/autophagy-agents, 0)",
        },
        method=method,
    )
    with urlopen(request, timeout=30) as response:  # noqa: S310
        body = response.read().decode("utf-8")
    return json.loads(body) if body else None


def _origin_notice():
    runtime = _interop_runtime()
    sys.path.insert(0, str(runtime))
    from automation.interop import origin_notice  # noqa: PLC0415

    return origin_notice


def _notify(
    channel_id: str | None, content: str, *, offline_dir: Path | None, message_id: str = "",
    completed: meeting_result_notice.CompletedMeeting | None = None,
) -> None:
    """Result notice: the thread anchored on the `!meeting` message when known, else the channel.

    앵커(지시 메시지 id)가 있으면 공유 `origin_notice.deliver`가 스레드 해석·게시·폴백을
    소유한다(plugin이 ACK로 이미 만든 스레드는 400→재사용). 없으면 레거시 채널 게시.
    """
    if not channel_id:
        return
    if offline_dir is not None:
        anchor = f"thread-anchor={message_id}\n" if message_id else ""
        (offline_dir / "notify.txt").write_text(
            f"{channel_id}\n{content}\n{anchor}", encoding="utf-8"
        )
        return
    if not message_id:
        _transport(channel_id).send(content)
        return
    try:
        origin_notice = _origin_notice()
    except ImportError as error:  # 낡은 interop 런타임/샌드박스 — 결과는 그래도 채널에 닿아야 한다
        print(f"NOTIFY-HELPER-MISSING anchor={message_id} err={type(error).__name__}", file=sys.stderr)
        _transport(channel_id).send(content)
        return
    record = {"id": completed.ref if completed else message_id,
              "origin_channel_id": channel_id, "origin_message_id": message_id}
    message = meeting_result_notice.result_message(record, content, completed)
    if message is not None and getattr(origin_notice, "ACCEPTS_OWNER_MESSAGE", False):
        origin_notice.deliver(
            api=_discord_api, transport_factory=_transport, record=record,
            thread_name="회의록 처리", content=content, message=message,
            fallback=lambda body: _transport(channel_id).send(body), fallback_destination=None,
        )
        return
    origin_notice.deliver(
        api=_discord_api,
        transport_factory=_transport,
        record=record,
        thread_name="회의록 처리",
        content=content,
        fallback=lambda content: _transport(channel_id).send(content),
    )


def _hermes_kanban(argv: list[str]) -> subprocess.CompletedProcess[bytes]:
    sys.path.insert(0, str(runtime_root()))
    from automation.ledger.switch import command_lock, command_prefix

    with command_lock():
        return subprocess.run(  # noqa: S603 - fixed executable, agent-owned arguments
            [*command_prefix(), *argv[1:]],
            capture_output=True,
            timeout=120,
            cwd=os.path.expanduser("~"),
            check=False,
        )


def _kanban_failure(step: str, completed: subprocess.CompletedProcess[bytes]) -> RuntimeError:
    detail = (completed.stderr or completed.stdout).decode("utf-8", errors="replace").strip()
    return RuntimeError(f"kanban {step} failed rc={completed.returncode}: {detail[:200]}")


def _card_already_blocked(card_id: str) -> bool:
    shown = _hermes_kanban(["kanban", "show", card_id])
    if shown.returncode != 0:
        return False
    status = re.search(
        r"^\s*status:\s*(\S+)", shown.stdout.decode("utf-8", errors="replace"), re.MULTILINE
    )
    return status is not None and status.group(1) == "blocked"


def _run_kanban(card: meeting_actions.PlannedCard) -> str:
    created = _hermes_kanban(card.argv())
    if created.returncode != 0:
        raise _kanban_failure("create", created)
    try:
        card_id: object = json.loads(created.stdout.decode("utf-8", errors="replace"))["id"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError("kanban create returned no card id") from error
    if not isinstance(card_id, str) or not card_id:
        raise ValueError("kanban create returned invalid card id")
    _, block_argv = card.argv_sequence(card_id)
    blocked = _hermes_kanban(block_argv)
    if blocked.returncode != 0:
        # Idempotent create returns yesterday's existing card on a nightly retry.
        # Hermes rejects re-blocking it even though the required state already holds.
        if _card_already_blocked(card_id):
            print(f"KANBAN-BLOCK-REDUNDANT card={card_id}")
            return card_id
        raise _kanban_failure("block", blocked)
    return card_id


def _pending_notice(candidates: "tuple[object, ...]", *, wanted: str = "") -> str:
    """Say which silence this is — the named one is gone, none pending, or too many."""
    listed = "\n".join(f"- {item.project}/{item.year}/{item.name}" for item in candidates)
    if wanted:
        remaining = f"지금 남은 것:\n{listed}" if candidates else "미처리 전사본이 없습니다."
        return f"지정한 전사본 `{wanted}` 를 미처리 목록에서 찾지 못했습니다.\n{remaining}"
    if not candidates:
        return (
            "회의록으로 만들 전사본을 찾지 못했습니다 — Drive 의 모든 전사본에 이미 회의록이 있습니다.\n"
            "새 회의라면 전사본을 먼저 올리거나, `!meeting` 뒤에 회의 내용을 붙여 주세요."
        )
    return (
        f"회의록이 없는 전사본이 {len(candidates)}건이라 하나를 고르지 않았습니다:\n{listed}\n"
        "그중 하나를 골라 파일로 첨부해 `!meeting` 으로 다시 요청하거나, "
        "에이전트에게 그 전사본 경로를 `--file` 로 지정해 달라고 하세요."
    )


def _extract_pending(pending: object) -> object:
    """Download the chosen transcript into a temporary directory and extract it there."""
    tmp = tempfile.mkdtemp(prefix="meeting-pending-")
    try:
        local = Path(tmp) / str(getattr(pending, "name", "transcript.md"))
        try:
            meeting_project.download_transcript(pending, local)
        except Exception as error:  # noqa: BLE001 - report the fetch, do not crash the CLI
            raise meeting_extract.ExtractionRefused(
                f"전사본을 내려받지 못했습니다: {type(error).__name__}", exit_code=7
            ) from error
        return meeting_extract.extract_file(local)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def cmd_ingest(args: argparse.Namespace, evidence_pack: object | None = None) -> int:
    started = time.monotonic()
    config = _config()
    now = datetime.now(KST)
    offline_dir = _env_path("MEETING_PLAN_DIR", "~/.hermes/meeting/plan") if args.offline else None
    if offline_dir is not None:
        offline_dir.mkdir(parents=True, exist_ok=True)
    pending = None
    wanted = (getattr(args, "pending_name", "") or "").strip()
    if wanted or getattr(args, "from_pending_transcript", False):
        candidates = meeting_project.pending_transcripts()
        chosen = (
            [item for item in candidates if item.name == wanted] if wanted else list(candidates)
        )
        if len(chosen) != 1:
            notice = _pending_notice(candidates, wanted=wanted)
            print(notice)
            _notify(
                args.notify_channel, notice, offline_dir=offline_dir,
                message_id=str(getattr(args, "notify_message_id", "") or ""),
            )
            _log({
                "ts": now.isoformat(timespec="seconds"), "label": "!meeting 전사본 탐색",
                "codex_called": False, "exit": 7, "pending": len(candidates),
            })
            return 7
        pending = chosen[0]
    label = args.label or (
        pending.stem if pending else (Path(args.file).name if args.file else "!meeting 본문")
    )
    record: dict = {"ts": now.isoformat(timespec="seconds"), "label": label, "codex_called": False}

    try:
        if pending is not None:
            extracted = _extract_pending(pending)
        elif args.file:
            extracted = meeting_extract.extract_file(Path(args.file))
        else:
            extracted = meeting_extract.extract_body(
                Path(args.body_file).read_text(encoding="utf-8", errors="replace")
            )
    except meeting_extract.ExtractionRefused as refusal:
        print(refusal.notice)
        _notify(
            args.notify_channel, refusal.notice, offline_dir=offline_dir,
            message_id=str(getattr(args, "notify_message_id", "") or ""),
        )
        record.update({"exit": refusal.exit_code, "refused": True})
        _log(record)
        return refusal.exit_code

    pack = evidence_pack
    if args.with_evidence and pack is None:
        pack = meeting_knowledge.collect(label, extracted.text, extracted.text)
    decks = tuple(
        meeting_slides.extract_deck(Path(path))
        for path in (getattr(args, "slides", None) or ())
    )
    slide_notes = tuple(meeting_slides.note_label(deck) for deck in decks)
    references = meeting_reference.collect(
        meeting_reference.query(label, getattr(args, "project", "") or "", extracted.text)
    )
    reference_notes = meeting_reference.note_labels(references)
    ref = hashlib.sha256(extracted.text.encode("utf-8")).hexdigest()[:8]
    record.update(
        {"kind": extracted.kind, "bytes": extracted.input_bytes, "ref": ref,
         "slides": list(slide_notes), "references": list(reference_notes)}
    )
    project = (getattr(args, "project", "") or "").strip() or (pending.project if pending else "")
    # 플러그인(`!meeting`)은 --project 를 넘길 수 없다 — 라벨이 유일한 단서이고,
    # 없는 과제를 지어내지 않도록 실재하는 과제 폴더와 일치할 때만 채택된다.
    if not project:
        project = meeting_project.detect_project(label)
    board = meeting_project.load_board(project)
    open_rows = tuple(row for row in board.records if row.status == meeting_action_db.OPEN)

    recorded = (
        Path(args.recorded_response).read_text(encoding="utf-8")
        if args.recorded_response
        else None
    )
    try:
        extraction, provider = meeting_llm.extract(
            extracted.text,
            prompt_path=_env_path(
                "MEETING_PROMPT_FILE",
                "/srv/autophagy-skills/live/meeting/prompts/meeting-extraction-v6.md",
            ),
            my_names=str(config.get("my_names", "cha,차")),
            recorded_response=recorded,
            evidence=meeting_evidence.prompt_block(pack) if pack is not None else "",
            slides=meeting_reference.merged_prompt(meeting_slides.prompt_block(decks), references),
            open_actions=meeting_action_db.prompt_block(open_rows),
        )
    except (meeting_llm.ExtractionParseError, OSError) as error:
        notice = "회의록 추출 실패: LLM 응답을 해석하지 못했습니다. 다시 시도해 주세요."
        print(notice)
        _notify(
            args.notify_channel, notice, offline_dir=offline_dir,
            message_id=str(getattr(args, "notify_message_id", "") or ""),
        )
        record.update({"exit": 6, "error": type(error).__name__})
        _log(record)
        return 6
    record["provider"] = provider
    record["codex_called"] = provider == meeting_llm.CODEX_PROVIDER
    evidence_footer = ""
    if pack is not None:
        extraction, evidence_footer = meeting_evidence.finalize(extraction, pack)
    # 노트·카드·원장이 갈라지기 전에 한 번만 고친다 — 뒤에서 고치면 같은 항목이 문서마다
    # 다른 표기로 남는다. 부록에 실릴 `extracted.text` 는 여기서도 그대로다.
    extraction = _correct_terms(extraction, label=label, project=project)

    items = meeting_action_db.items_from(extraction.todos, extraction.others)
    on = meeting_actions.note_date(extraction, now=now)
    action_id_exhausted = False
    try:
        merged = (
            meeting_action_db.merge(
                board.records, project=project, code=board.code, year=on.year % 100,
                new_items=items,
                resolved_ids=tuple(item.id for item in extraction.resolved_actions),
                note_name=meeting_actions.note_name(extraction, ref=ref, now=now), on=on,
            )
            if board.code
            else None
        )
    except meeting_action_id.ActionIdError as error:
        # 번호가 모자란다고 회의록을 잃으면 교환이 성립하지 않는다 — 추출은 이미 끝난 뒤다.
        print(f"ACTION-ID-EXHAUSTED project={project} {error}", file=sys.stderr)
        action_id_exhausted = True
        merged = None
    action_sections = meeting_action_db.render_sections(
        outstanding=merged.outstanding if merged else open_rows,
        created=merged.created if merged else meeting_action_db.unnumbered(items),
    )
    note_path = meeting_actions.write_note(
        _env_path("MEETING_NOTES_DIR", "~/notes/meetings"),
        label=label,
        kind=extracted.kind,
        original_text=extracted.text,
        extraction=extraction,
        ref=ref,
        now=now,
        evidence_footer=evidence_footer,
        slide_notes=slide_notes,
        reference_notes=reference_notes,
        action_sections=action_sections,
        template=board.template,
    )
    if pack is not None:
        meeting_evidence.write_sidecar(note_path, pack)

    # Drive publication is strictly best-effort and must never affect the local note.
    try:
        match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})-meeting-(.+)\.md", note_path.name)
        if match is None:
            print("DRIVE-PUBLISH-SKIP reason=malformed-date")
        else:
            _publish_note(
                note_path,
                label=label,
                on=date.fromisoformat(match.group(1)),
                project=project,
            )
    except Exception:
        # A malformed date or unavailable publisher skips the upload; local save stands.
        print("DRIVE-PUBLISH-SKIP reason=gate-unavailable")

    if merged is not None:
        if offline_dir is not None:
            (offline_dir / meeting_action_db.DB_FILENAME).write_text(
                meeting_action_db.dump(merged.records), encoding="utf-8"
            )
        else:
            meeting_project.save_board(board, merged.records)

    cards = meeting_actions.plan_cards(
        extraction, note_name=note_path.name, ref=ref, project=project,
    )
    card_ids: list[str] = []
    if offline_dir is not None:
        (offline_dir / "kanban-plan.jsonl").write_text(
            "".join(
                json.dumps(argv, ensure_ascii=False) + "\n"
                for card in cards
                for argv in card.argv_sequence("<created-card-id>")
            ),
            encoding="utf-8",
        )
    else:
        card_ids = [_run_kanban(card) for card in cards]

    milestones_added = meeting_actions.update_milestones(
        _env_path("MEETING_STATE_FILE", "~/state/milestones.yaml"),
        extraction.milestones,
        note_name=note_path.name,
        ref=ref,
        now=now,
    )

    team_posted = False
    team_post = meeting_actions.format_team_post(
        extraction.others, agent_id=str(config.get("agent_id", "agent")), ref=ref, now=now
    )
    if team_post:
        if offline_dir is not None:
            (offline_dir / "team-post.txt").write_text(team_post, encoding="utf-8")
        else:
            team_channel = str(config.get("team_channel_id", ""))
            if team_channel:
                _transport(team_channel).send(team_post)
                team_posted = True

    notice = meeting_actions.format_notify(
        label=label,
        cards=len(cards),
        milestones_added=milestones_added,
        others=len(extraction.others),
        note_name=note_path.name,
        team_posted=team_posted,
        project=project,
        action_id_exhausted=action_id_exhausted,
    )
    _notify(
        args.notify_channel, notice, offline_dir=offline_dir,
        message_id=str(getattr(args, "notify_message_id", "") or ""),
        completed=meeting_result_notice.CompletedMeeting(ref, label),
    )

    record.update(
        {"exit": 0, "todos": len(extraction.todos), "milestones": len(extraction.milestones),
         "others": len(extraction.others), "cards": len(cards), "card_ids": card_ids,
         "milestones_added": milestones_added, "note": note_path.name,
         "team_posted": team_posted, "elapsed_s": round(time.monotonic() - started, 1),
         "evidence_count": len(getattr(pack, "items", ())) if pack is not None else 0,
         "layers": getattr(pack, "layers", {}) if pack is not None else {},
         "actions_new": len(merged.created) if merged else 0,
         "actions_open": len(merged.outstanding) if merged else 0,
         "actions_closed": len(merged.closed) if merged else 0, "project": project}
    )
    _log(record)
    output = {key: record[key] for key in
              ("exit", "ref", "provider", "codex_called", "todos",
               "milestones", "others", "cards", "milestones_added", "note", "team_posted",
               "evidence_count", "layers", "slides", "actions_new", "actions_open",
               "actions_closed", "project")}
    # 화자 이름은 회의 내용이다 — 결과 JSON(호출자에게 돌려주는 값)에만 싣고
    # 라우팅 로그에는 남기지 않는다(로그는 메타데이터만 적는다는 이 모듈의 계약).
    output["speakers"] = [
        {"label": speaker.label, "name": speaker.name, "basis": speaker.basis}
        for speaker in extraction.speakers
    ]
    print(json.dumps(output, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="meeting", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest = subparsers.add_parser("ingest", help="회의록 파일/본문 인제스트")
    source = ingest.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", help="md/txt/pdf 파일 경로")
    source.add_argument("--body-file", help="!meeting 본문이 담긴 임시 파일")
    source.add_argument(
        "--from-pending-transcript", action="store_true",
        help="파일 없이 — 아직 회의록이 없는 Drive 전사본이 정확히 1건이면 그것으로 만든다",
    )
    source.add_argument(
        "--pending-name", metavar="파일명",
        help="아직 회의록이 없는 전사본 중 이 이름을 골라 만든다(야간 배치가 다건을 순차 처리)",
    )
    ingest.add_argument("--label", help="회의 라벨(기본: 파일명)")
    ingest.add_argument("--project", help="과제명 — 회의록을 회의록/<과제명>/<연도>/ 에 둔다")
    ingest.add_argument("--notify-channel", help="결과 통지 Discord 채널 ID")
    ingest.add_argument(
        "--notify-message-id", default="",
        help="결과 통지를 앵커할 지시 메시지 ID — 있으면 그 메시지의 스레드에 게시",
    )
    ingest.add_argument(
        "--slides", action="append", metavar="경로",
        help="발표자료(pdf/pptx/md/txt) — 대명사·모호 지시어 교정 재료. 반복 지정 가능",
    )
    ingest.add_argument("--recorded-response", help="녹화된 LLM 응답 파일(테스트 전용)")
    ingest.add_argument("--offline", action="store_true", help="외부 부작용 없이 계획만 기록")
    ingest.add_argument("--with-evidence", action="store_true", help="선행 회의·노트 근거 수집")
    ingest.set_defaults(func=cmd_ingest)

    evidence = subparsers.add_parser("evidence", help="선행 회의·노트 근거 미리보기")
    evidence.add_argument("--title", required=True)
    evidence.add_argument("--attendees", default="")
    evidence.add_argument("--topics", required=True)
    evidence.add_argument("--limit", type=int, default=8)
    evidence.add_argument("--json", action="store_true")
    evidence.set_defaults(func=meeting_evidence.command)

    args = parser.parse_args(argv)
    if args.command == "ingest":
        message = meeting_governed.refusal(Path(__file__))
        if message:
            print(message, file=sys.stderr)
            return 3
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
