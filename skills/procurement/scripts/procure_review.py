"""Review-request DM transport for the procurement skill (W4-4).

The ONLY outbound surface of this skill: one owner notice asking for human
review. ≤25 MiB attaches the draft; larger files are uploaded to the owner's
own Drive (gws CLI) and the notice carries the link. Submission is ALWAYS human —
this module has no mail/submit code path at all.

ON-1..ON-3: 목적지 해석도 전송도 `automation.owner_notice` 가 한다. 이 모듈은 본문과
첨부 목록만 만들고, 첨부 multipart 인코딩은 파사드 안에 한 벌만 있다.

Sandbox hooks: PROCURE_DISCORD_STUB=<dir> records the would-be DM as JSON
instead of calling Discord. Drive upload goes through automation.drive_outputs
(opt-in via DRIVE_PUBLISH_ENABLED; unset means zero Drive calls, empty link).
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from types import ModuleType

from procure_core import DM_MAX_BYTES, review_mode


class ReviewError(RuntimeError):
    """Review notice could not be delivered (exit 6)."""


def _facade() -> ModuleType:
    """ON-2/ON-3: 채널 해석도 전송(첨부 포함)도 owner_notice 파사드만 한다."""
    override = os.environ.get("AUTOPHAGY_REPO_ROOT", "").strip()
    release = Path("/srv/autophagy-agent-current")
    root = override or str(release if release.is_dir() else Path("/srv/autophagy-agents"))
    if root not in sys.path:
        sys.path.insert(0, root)
    from automation import owner_notice

    return owner_notice


def _notice_channel() -> str:
    """검토 요청이 갈 채널 — 봉투의 목적지 좌표를 렌더하려면 값 자체가 필요하다."""
    try:
        target = _facade().resolve_notice_target(_token())
    except ReviewError:
        raise
    except Exception as error:  # noqa: BLE001 - 원인 유형만 남기고 exit 6 계약 유지
        raise ReviewError(f"통지 채널 해석 실패: {type(error).__name__}") from None
    if not target:
        raise ReviewError("통지 대상 미해석 — interop config owner_id/owner_notice_channel_id 확인")
    return target


def max_bytes() -> int:
    return int(os.environ.get("PROCURE_DM_MAX_BYTES", DM_MAX_BYTES))


def review_note(file: Path, mode: str, note: str, link: str) -> str:
    body = f"📄 서류 초안 검토 요청: `{file.name}`"
    if note:
        body += f"\n{note}"
    body += f"\n(Drive 링크: {link})" if mode == "drive-link" else ""
    return body + "\n검토 후 **제출은 cha가 직접** 해주세요 — 이 스킬은 어디에도 제출하지 않습니다."


def send_review(file: Path, note: str) -> str:
    """Returns 'REVIEW-DM-SENT message=<id> mode=<mode> size=<bytes>'."""
    size = file.stat().st_size
    mode = review_mode(size, max_bytes())
    link = ""
    if mode == "drive-link":
        try:
            from automation.drive_outputs import publish_best_effort

            result = publish_best_effort("procurement", file.stem, [(file, file.stem)])
            if result is not None and result.links:
                link = result.links[0]
        except ImportError:
            link = ""
    legacy = review_note(file, mode, note, link)
    content = legacy
    stub = os.environ.get("PROCURE_DISCORD_STUB", "")
    channel_id = "" if stub else _notice_channel()
    try:
        from automation.interop.owner_message import Action, OwnerMessage, OwnerMessageError, Ref, Result, render
    except Exception:  # noqa: BLE001 - optional module initialization must preserve string delivery
        content = legacy
    else:
        location = Ref(scope="resource", url=link or None, search=("문서 검색", file.name))
        destination = Ref(scope="channel", channel_id=channel_id) if channel_id else Ref(scope="none")
        try:
            content = render(OwnerMessage(
                subject_key=file.name, subject="구매 서류 초안", fact=note or "검토 요청",
                location=location, owner=Action("open", target=location, argument="검토·제출은 직접"),
                agent_next=None, recovery="not_applicable", detail=Result("executed"),
            ), destination=destination)
        except OwnerMessageError:
            content = legacy
    if stub:
        record = {"mode": mode, "size": size, "file": file.name, "content": content}
        out = Path(stub) / f"dm-{uuid.uuid4().hex[:8]}.json"
        out.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        return f"REVIEW-DM-SENT message=stub:{out.name} mode={mode} size={size}"
    # 목적지·전송은 파사드 몫이다. 25 MiB 이하만 첨부가 붙고, 그 위는 Drive 링크 본문뿐이다.
    attachments = (file,) if mode == "attach" else ()
    if not _facade().notify_owner(content, attachments=attachments):
        raise ReviewError("통지 전송 실패 — owner-notice 마커를 확인하세요")
    return f"REVIEW-DM-SENT message=notice mode={mode} size={size}"



def _token() -> str:
    _facade()
    from automation.notice_bot import resolve_token

    token = resolve_token()
    if not token:
        raise ReviewError("DISCORD_BOT_TOKEN 누락 — 검토 DM 전송 불가")
    return token
