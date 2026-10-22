"""Deterministic pieces of the v2 lifelog note (B안, 2026-09-04): frontmatter, 한눈에, 결정 · 할 일, 접힌 전문.

Pure text shaping. The frontmatter mirrors what the owner's Obsidian Linter leaves in
place (yaml-key-sort tags→title→source→created→modified, yaml-timestamp
'YYYY-MM-DDTHH:mm:ss', yaml-title = first H1, single-line arrays), so a freshly
pushed note is already lint-clean — verified headlessly against the real plugin
build (.omo/plans/plaud-lifelog-format-v2.md 결정 3·9). The Linter unquotes a
plain 'title' and double-quotes one that carries ': ', so 'yaml_scalar' quotes
only when YAML needs it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, tzinfo
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .lifelog_model import (
    ExtractionOutcome,
    ExtractionSkipped,
    LifelogExtraction,
    LifelogRecording,
)

DEFAULT_TIMEZONE: Final = ZoneInfo("Asia/Seoul")
TIMEZONE_ENV: Final = "PLAUD_SYNC_TIMEZONE"
GLANCE_HEADING: Final = "## 한눈에"
SUMMARY_HEADING: Final = "## 요약"
DECISIONS_HEADING: Final = "## 결정 · 할 일"
TRANSCRIPT_HEADING: Final = "## 전문"
#: 동결 전 교정이 전문을 바꿨을 때만 나온다 — 무엇을 무엇으로, 왜 바꿨는지.
CORRECTION_HEADING: Final = "## 교정 기록"
SOURCE_RULE: Final = "---"
OWN_HEADINGS: Final = (
    GLANCE_HEADING, SUMMARY_HEADING, DECISIONS_HEADING, CORRECTION_HEADING, TRANSCRIPT_HEADING,
)
NO_SUMMARY: Final = "- (요약 없음)"
#: 요약이 왜 없는지까지 말한다 — 게이트 생략과 모델 실패는 소유자에게 다른 사실이다.
NO_SUMMARY_REASON: Final = "없음 (모델이 전사본에서 요약을 만들지 못함)"
NO_TRANSCRIPT: Final = "- (전사 없음)"
TAG_ROOT: Final = "lifelog"
MAX_TOPIC_TAGS: Final = 8
TIMESTAMP_FORMAT: Final = "%Y-%m-%dT%H:%M:%S"
TRANSCRIPT_CALLOUT: Final = "> [!quote]- 전문 펼치기 ({count} 발화)"

_WEEKDAYS: Final = ("월", "화", "수", "목", "금", "토", "일")
#: Plaud sub-headings that name a *kind* of section, not a topic — never a tag.
_GENERIC_HEADINGS: Final = frozenset(
    heading.casefold()
    for heading in (
        "개요", "요약", "결론", "결정", "결정 사항", "결정사항", "할 일", "할일", "액션 아이템",
        "핵심", "핵심 내용", "주요 내용", "주요 논의", "논의", "논의 사항", "후속 조치", "다음 단계",
        "회의 개요", "전체 요약", "한눈에", "전문", "action items", "action item", "summary",
        "overview", "conclusion", "conclusions", "next steps", "key points", "todo", "todos",
    )
)
_HEADING_RE: Final = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)
_RULE_RE: Final = re.compile(r"^(-{3,}|_{3,}|\*{3,})$")
_IMAGE_RE: Final = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_UNRESOLVABLE_IMAGE_RE: Final = re.compile(r"!\[[^\]]*\]\((?!https?://)[^)]*\)")
_BLANK_RUN_RE: Final = re.compile(r"\n{3,}")
_SPEAKER_RE: Final = re.compile(r"^\[[^\]]*? · ([^\]]+)\]", re.MULTILINE)
_SPEAKER_HEADER_RE: Final = re.compile(
    r"^\[(?:\d{2}|--):(?:\d{2}|--):(?:\d{2}|--)\]\s+(화자\d+)(?:\s+·\s+.+)?\s*$",
    re.MULTILINE,
)
_CATALOG_SPEAKER_RE: Final = re.compile(r"^화자[1-9]\d*$")
_YAML_LEAD_CHARS: Final = "#-?:,[]{}&*!|>'\"%@\x60"
_YAML_RESERVED: Final = frozenset({"true", "false", "null", "yes", "no", "on", "off", "~"})


def note_timezone(env: Mapping[str, str]) -> tuple[ZoneInfo, str | None]:
    """(zone, warning): PLAUD_SYNC_TIMEZONE else Asia/Seoul — a bad name is reported, never raised.

    Shared by the cron (discovery) and transcribe_live (finalize) so both stamps agree.
    """
    name = env.get(TIMEZONE_ENV, "").strip()
    if not name:
        return DEFAULT_TIMEZONE, None
    try:
        return ZoneInfo(name), None
    except (ZoneInfoNotFoundError, ValueError):
        return DEFAULT_TIMEZONE, f"unknown {TIMEZONE_ENV} {name!r}; using {DEFAULT_TIMEZONE.key}"


def strip_unresolvable_images(summary: str) -> str:
    """Drop image markdown whose target is not an absolute URL.

    A Plaud summary opens with a poster image keyed into Plaud's own storage
    (permanent/<user>/<file>/summary_poster/card_*.png). Obsidian resolves a
    relative target inside the vault, where it cannot exist (2026-09-04 실측).
    Lines that held only such images disappear; https:// images stay.
    """
    kept: list[str] = []
    for raw in summary.splitlines():
        line = _UNRESOLVABLE_IMAGE_RE.sub("", raw)
        if raw.strip() and not line.strip():
            continue
        kept.append(line.rstrip() if line != raw else line)
    return _BLANK_RUN_RE.sub("\n\n", "\n".join(kept)).strip()


def yaml_scalar(value: str) -> str:
    """Quote a plain scalar exactly the way the owner's Linter (escape char '"') would.

    Mirrors escapeStringIfNecessaryAndPossible of the vault build, verified headlessly
    against 61 titles (docs/qa/PLV2): a value holding '"' but no "'" is wrapped in single
    quotes; a value holding "'" or one YAML would misread (': ', ' #', leading special,
    reserved word) is double-quoted with backslash and '"' escaped; a plain value stays
    bare — the Linter *unquotes* a quoted plain title, so quoting defensively would never
    be lint-clean — and a value holding both quote kinds with nothing else to escape is
    left bare too (the Linter cannot wrap it without escaping, so it does not).
    """
    has_double = '"' in value
    has_single = "'" in value
    structural = (
        not value
        or value != value.strip()
        or ": " in value
        or value.endswith(":")
        or " #" in value
        or value[0] in _YAML_LEAD_CHARS
        or value.casefold() in _YAML_RESERVED
    )
    if has_double and not has_single:
        return f"'{value}'"
    if not structural and (has_double or not has_single):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def frontmatter(*, tags: tuple[str, ...], title: str, source: str, stamp: datetime) -> str:
    when = stamp.strftime(TIMESTAMP_FORMAT)
    return "\n".join(
        (
            "---",
            f"tags: [{', '.join(tags)}]",
            f"title: {yaml_scalar(title)}",
            f"source: {source}",
            f"created: {when}",
            f"modified: {when}",
            "---",
        )
    )


def local_stamp(recording: LifelogRecording, tz: tzinfo) -> datetime | None:
    """Recording start in the note's zone (naive, like the Linter writes it); None if unparsable."""
    for raw in (recording.start_at, recording.created_at):
        if not raw:
            continue
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            continue
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(tz).replace(tzinfo=None)
        return parsed
    return None


def render_duration(duration_ms: int) -> str:
    minutes, seconds = divmod(duration_ms // 1_000, 60)
    return f"{minutes}분 {seconds}초" if minutes else f"{seconds}초"


def speaker_count(transcript: str) -> int:
    labels = {match.group(1).strip() for match in _SPEAKER_RE.finditer(transcript)}
    labels.update(_SPEAKER_HEADER_RE.findall(transcript))
    return len(labels - {"화자0"})


def speaker_labels(transcript: str) -> tuple[str, ...]:
    """목소리 등록 양식에 쓸 실제 ``화자N`` 라벨을 숫자 순서로 돌려준다."""
    candidates = {match.group(1).strip() for match in _SPEAKER_RE.finditer(transcript)}
    candidates.update(_SPEAKER_HEADER_RE.findall(transcript))
    labels = {label for label in candidates if _CATALOG_SPEAKER_RE.fullmatch(label)}
    return tuple(sorted(labels, key=lambda label: int(label.removeprefix("화자"))))


def _tag_for(heading: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_/" else " " for ch in heading.lstrip("#"))
    return "-".join(cleaned.split()).strip("-_/")


def topic_tags(summary: str) -> tuple[str, ...]:
    """'lifelog/<소제목>' for each topical Plaud sub-heading, in order, generic kinds excluded."""
    tags: list[str] = []
    for match in _HEADING_RE.finditer(summary):
        heading = " ".join(match.group(1).split())
        if heading.casefold() in _GENERIC_HEADINGS:
            continue
        slug = _tag_for(heading)
        tag = f"{TAG_ROOT}/{slug}" if slug else ""
        if tag and tag not in tags:
            tags.append(tag)
        if len(tags) >= MAX_TOPIC_TAGS:
            break
    return tuple(tags)
# 2026-09-07 소유자 지시로 네 줄을 걷어냈다. 지운 이유는 저마다 다르다:
#   사람:: — ASR 오인식을 `[[위키링크]]` 로 굳혀 vault 에 없는 문서를 만든다(실측
#            `[[신영구원님]]` = "신영구 연구원님"의 오인식). 사람 이름은 요약 본문이 말한다.
#   장소:: — 같은 이유이고 소비자도 없었다.
#   한 줄:: — 바로 아래 `## 요약` 첫 줄의 글자 그대로의 복사본이다.
#   주제:: — frontmatter 의 `tags:` 와 같은 값을 두 번 적는다. 태그는 frontmatter 가 소유한다.
# 남는 것은 녹음 한 줄과 **진단**이다 — 진단은 무엇이 잘못됐는지 알려주는 유일한 신호라
# 지우면 실패가 침묵이 된다.
def glance_lines(
    recording: LifelogRecording,
    extraction: ExtractionOutcome,
    *,
    stamp: datetime,
    summary: str,
) -> tuple[str, ...]:
    """녹음·소유자 화자 교정 양식·진단 — 나머지는 요약이 말한다."""
    recorded = f"{stamp:%Y-%m-%d} ({_WEEKDAYS[stamp.weekday()]}) {stamp:%H:%M} · {render_duration(recording.duration_ms)}"
    speakers = speaker_count(recording.transcript_text)
    lines = [f"- 녹음:: {recorded}" + (f" · 화자 {speakers}명" if speakers else "")]
    labels = speaker_labels(recording.transcript_text)
    if labels:
        # `미상`은 voice-catalog 워처가 무시한다. 소유자가 실제 이름으로 바꾸는 행위만
        # 명령=동의가 되어 등록되므로 자동 식별 결과가 곧바로 학습 자료가 되지 않는다.
        lines.append("- 화자:: " + " · ".join(f"{label}=미상" for label in labels))
    if isinstance(extraction, ExtractionSkipped):
        lines.append(f"- 추출:: 생략 ({extraction.reason})")
    elif not summary.strip():
        # 추출이 **돌았는데도** 요약이 없는 경우 — 생략(위)과 다른 사실이고, 그 구별이
        # 소유자가 무엇을 고쳐야 하는지를 정한다.
        lines.append(f"- 요약:: {NO_SUMMARY_REASON}")
    return tuple(lines)


def _at(stamp: str) -> str:
    return f" [{stamp}]" if stamp else ""


def decisions_block(extraction: ExtractionOutcome) -> str:
    """'- 결정: …' then '- [ ] …' checklist; empty string when there is nothing to list."""
    if not isinstance(extraction, LifelogExtraction):
        return ""
    lines = [f"- 결정: {decision.text}{_at(decision.at)}" for decision in extraction.decisions]
    for todo in extraction.todos:
        meta = [part for part in (f"담당 {todo.owner}" if todo.owner else "", f"기한 {todo.due}" if todo.due else "") if part]
        line = f"- [ ] {todo.text}"
        if meta:
            line += " — " + " · ".join(meta)
        lines.append(line + _at(todo.at))
    return "\n".join(lines)


def transcript_block(transcript: str) -> str:
    """The transcript folded into a collapsed quote callout; the placeholder when empty."""
    text = transcript.strip()
    if not text:
        return NO_TRANSCRIPT
    lines = text.splitlines()
    count = sum(1 for line in lines if line.strip() and not _RULE_RE.match(line.strip()))
    quoted = "\n".join(f"> {line}" if line else ">" for line in lines)
    return TRANSCRIPT_CALLOUT.format(count=count) + "\n" + quoted


def unquote_transcript(text: str) -> str:
    """Inverse of 'transcript_block' — leaves a v1 (unquoted) transcript untouched."""
    lines = text.splitlines()
    if not lines or not lines[0].startswith("> [!quote]"):
        return text.strip()
    body = [line[2:] if line.startswith("> ") else line[1:] if line.startswith(">") else line for line in lines[1:]]
    return "\n".join(body).strip()


def lifelog_sections(body: str) -> dict[str, str]:
    """Our own H2 sections (first occurrence each) → stripped text; frontmatter and 출처 fall outside."""
    collected: dict[str, list[str]] = {}
    current: str | None = None
    for line in body.splitlines():
        if line in OWN_HEADINGS and line not in collected:
            current = line
            collected[current] = []
        elif current is not None:
            collected[current].append(line)
    sections: dict[str, str] = {}
    for heading, lines in collected.items():
        text = "\n".join(lines)
        if heading == TRANSCRIPT_HEADING:
            text = text.rsplit(f"\n{SOURCE_RULE}\n", 1)[0]
        sections[heading] = text.strip()
    return sections
