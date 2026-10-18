"""릴리스 변경 상세: 경로→배포 번들 판정과 커밋 귀속.

승인 카드(`release_spec`)는 무엇을 승인하는지(버전·기준·번들·바인딩)만 싣는다. 어떤
커밋이 어느 번들을 바꾸는지는 이 모듈이 계산해 로컬 상세 문서로 보존한다. 경로 판정 규칙은
여기 한 벌뿐이며 `release_plan.build_plan` 의 표면 분류가 같은 규칙을 부른다 — 사본이
생기면 카드의 번들 목록과 커밋 귀속이 서로 다른 답을 말하게 된다.
"""
from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from automation.interop.discord_transport import SentMessage
    from automation.release_plan import ReleasePlan

from automation.release_spec_message import split_messages

_RAG_PREFIXES: Final = ("configs/rag/", "automation/rag_ingest/", "automation/rag_stack/")
_ROOT_PREFIXES: Final = ("automation/systemd/", "automation/sudoers.d/", "automation/libexec/")
_RUNTIME_PREFIXES: Final = ("automation/", "configs/")

#: ``git log`` 이 커밋 하나를 여는 표식과 sha·제목 구분자 — 커밋 본문에는 나오지 않는다.
COMMIT_FORMAT: Final = "%x00%H%x1f%s"
_MULTI_SECTION: Final = "여러 번들"
_OTHER_SECTION: Final = "공통·기타"
_EMPTY_LINE: Final = "- 커밋 없음"


@dataclass(frozen=True, slots=True)
class BundleMap:
    """배포 번들 판정의 단일 규칙 — 스킬 디렉터리와 매니페스트 원본이 이름을 먼저 가진다."""

    skills: tuple[str, ...]
    home_sources: tuple[tuple[str, str], ...]

    def bundles(self, path: str) -> tuple[str, ...]:
        """``path`` 가 속한 배포 번들 이름들(정렬) — 이름 있는 표면이 없으면 위치로 정한다."""
        named = {
            f"skill:{skill}" for skill in self.skills if path.startswith(f"skills/{skill}/")
        } | {package for source, package in self.home_sources if path == source}
        if named:
            return tuple(sorted(named))
        if path.startswith(_RAG_PREFIXES):
            return ("rag",)
        if path.startswith(_ROOT_PREFIXES):
            return ("root",)
        if path.startswith(_RUNTIME_PREFIXES):
            return ("runtime",)
        return ("repo",)


@dataclass(frozen=True, slots=True)
class ReleaseCommit:
    """릴리스 범위의 커밋 하나: 소유자가 읽는 제목과 그것이 바꾸는 번들."""

    sha12: str
    subject: str
    paths: tuple[str, ...]
    bundles: tuple[str, ...]


def parse_commits(log: str, bundle_map: BundleMap) -> tuple[ReleaseCommit, ...]:
    """``git log --reverse --format=COMMIT_FORMAT --name-only`` 출력을 커밋 단위로 읽는다."""
    parsed: list[ReleaseCommit] = []
    for chunk in log.split("\0"):
        lines = [line for line in chunk.splitlines() if line.strip()]
        if not lines:
            continue
        sha, _, subject = lines[0].partition("\x1f")
        paths = tuple(lines[1:])
        parsed.append(
            ReleaseCommit(
                sha12=sha[:12],
                subject=subject.strip(),
                paths=paths,
                bundles=tuple(
                    sorted({name for path in paths for name in bundle_map.bundles(path)})
                ),
            )
        )
    return tuple(parsed)


def major_note(plan: ReleasePlan, *, p5_retired: bool = False) -> str:
    """카드가 싣는 운영자 경보 한 줄 — 신호가 없으면 빈 문자열이다."""
    if not plan.major_signals:
        return ""
    note = f"MAJOR: 운영자 조치 필요 — {', '.join(plan.major_signals)}"
    if "automation/install/profiles.py:DEFAULT_PROFILE" in plan.major_signals:
        note += (" · P5 비루트 제거 포함(백업 뒤); peer 계정·/etc는 별도 소유자 블록."
                 if p5_retired else
                 " · P5 삭제 미포함: 멈춘 Hermes 잡·peer 계정·게이트웨이 유닛은 보존.")
    return note


def _sections(commits: tuple[ReleaseCommit, ...]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """번들별 절 — 여러 번들을 건드린 커밋은 한 번만, 자기 번들 이름을 달고 모인다."""
    grouped: dict[str, list[str]] = {}
    for commit in commits:
        line = f"- {commit.subject} ({commit.sha12})"
        if len(commit.bundles) == 1:
            grouped.setdefault(commit.bundles[0], []).append(line)
        elif commit.bundles:
            grouped.setdefault(_MULTI_SECTION, []).append(
                f"{line} — {', '.join(commit.bundles)}"
            )
        else:
            grouped.setdefault(_OTHER_SECTION, []).append(line)
    named = sorted(name for name in grouped if name not in (_MULTI_SECTION, _OTHER_SECTION))
    tail = [name for name in (_MULTI_SECTION, _OTHER_SECTION) if name in grouped]
    return tuple((name, tuple(grouped[name])) for name in (*named, *tail))


def detail_body(plan: ReleasePlan) -> str:
    """번들별 변경 내역 원문 — 승인 레코드가 통째로 보관하는 그 본문이다."""
    sections = _sections(plan.commits) or ((_OTHER_SECTION, (_EMPTY_LINE,)),)
    return "\n".join(
        line for name, lines in sections for line in (f"### {name}", *lines)
    )


def save_detail(
    directory: Path, head: str, surfaces: tuple[tuple[str, str], ...], body: str,
) -> Path:
    """Keep the full approval detail beside the host's draft before any card is posted."""
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / f"{head}.details.md"
    bundles = "\n".join(f"- `{name}`" for name, _digest in surfaces) or "- 변경 없음"
    content = f"## 변경 상세\n### 배포 묶음 전체\n{bundles}\n{body}\n"
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return path


def card_summary(directory: Path, head: str) -> str:
    """The note draft's first ``# `` line: the one-line summary the short card shows."""
    try:
        first = (directory / f"{head}.md").read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError, UnicodeError):
        return ""
    return first.removeprefix("# ").strip()[:120] if first.startswith("# ") else ""


def save_surfaces(directory: Path, head: str, surfaces: tuple[tuple[str, str], ...]) -> None:
    """Remember what this card carried so the next card can show only what changed."""
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / f"{head}.surfaces.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps([list(row) for row in surfaces]), encoding="utf-8")
    os.replace(temporary, path)


def previous_surfaces(directory: Path, base: str) -> list[list[str]] | None:
    """The surfaces the release at ``base`` carried, or None when this host never planned it."""
    try:
        rows = json.loads((directory / f"{base}.surfaces.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(rows, list) or any(
            not isinstance(row, list) or len(row) != 2 or not all(isinstance(v, str) for v in row)
            for row in rows):
        return None
    return rows


def render_detail_messages(plan: ReleasePlan) -> tuple[str, ...]:
    """구판 계획이 만드는 상세 메시지 전부 — 재생 순서 그대로."""
    return split_messages(version=plan.version, head_sha=plan.head, body=detail_body(plan))


@dataclass(frozen=True, slots=True)
class DetailDelivery:
    """상세 메시지 게시 결과 — 실패해도 카드(승인 바인딩)는 그대로 살아 있다."""

    message_ids: tuple[str, ...]
    failure: str = ""


def post_details(
    send: Callable[[str], Sequence[SentMessage]], messages: tuple[str, ...]
) -> DetailDelivery:
    """순서대로 보낸다 — 도중에 끊기면 몇 통이 올라갔는지 기계 판독 줄로 남긴다."""
    posted: list[str] = []
    for message in messages:
        try:
            sent = send(message)
        except (OSError, ValueError) as error:
            return DetailDelivery(
                tuple(posted),
                f"RELEASE-DETAIL-POST-FAIL {type(error).__name__}"
                f" posted={len(posted)}/{len(messages)}",
            )
        posted.extend(item.message_id for item in sent)
    return DetailDelivery(tuple(posted))
