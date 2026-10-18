"""Stored release v4 envelope replay; staged beside the release spec."""
from __future__ import annotations

from functools import partial
from typing import Final, Protocol

from automation.interop.chunker import chunk_lines

YOODORI_CARD_COMMAND_COUNT: Final = 7
YOODORI_GMAIL_READ_COUNT: Final = 2


class ReleaseView(Protocol):
    @property
    def version(self) -> str: ...

    @property
    def head_sha(self) -> str: ...

    @property
    def release_nonce(self) -> str: ...

    @property
    def surface_digests(self) -> tuple[tuple[str, str], ...]: ...

    @property
    def patch_notes(self) -> str: ...

    @property
    def major_note(self) -> str: ...

    @property
    def detail_message_ids(self) -> tuple[str, ...]: ...

    @property
    def detail_channel_id(self) -> str: ...

    @property
    def detail_guild_id(self) -> str: ...

    def action_hash(self) -> str: ...

    def detail_messages(self) -> tuple[str, ...]: ...


class ReleaseMessageError(ValueError):
    """A release presentation cannot be split or rendered safely."""


def render_v1(spec: ReleaseView, *, approval_line: str) -> str:
    surfaces = "\n".join(
        f"- surface `{name}`: `{digest}`"
        for name, digest in spec.surface_digests
    ) or "- surface: 변경 없음"
    return (
        f"[release] {spec.version} 배포 승인 요청\n"
        f"- version: `{spec.version}`\n"
        f"- HEAD: `{spec.head_sha}`\n"
        f"- release_nonce: `{spec.release_nonce}`\n"
        f"{surfaces}\n"
        "- 패치노트:\n"
        f"{spec.patch_notes.rstrip()}\n"
        f"{approval_line}"
    )


def render_v2(
    spec: ReleaseView, *, bundle_names: str, approval_line: str
) -> str:
    return (
        f"[release] {spec.version} 배포 승인 요청\n"
        f"- 배포 기준: `{spec.head_sha}`\n"
        f"- 배포 번들 ({len(spec.surface_digests)}): {bundle_names}\n"
        f"- 승인 바인딩: `{spec.action_hash()}`\n"
        "- 변경 내용:\n"
        f"{spec.patch_notes.rstrip()}\n"
        f"{approval_line}"
    )


def render_v3(
    spec: ReleaseView, *, bundle_names: str, approval_line: str
) -> str:
    operator = f"{spec.major_note}\n" if spec.major_note else ""
    return (
        f"[release] {spec.version} 배포 승인 요청\n"
        f"- 배포 기준: `{spec.head_sha}`\n"
        f"- 배포 번들 ({len(spec.surface_digests)}): {bundle_names}\n"
        f"- 승인 바인딩: `{spec.action_hash()}`\n"
        f"{operator}"
        f"- 변경 상세: 아래 {len(spec.detail_messages())}개 메시지"
        f" (같은 릴리스 {spec.version} · 기준 {spec.head_sha[:12]})\n"
        f"{approval_line}"
    )

MESSAGE_LIMIT: Final = 1900
DETAIL_BACKLINK_BUDGET: Final = 160


def detail_header(version: str, head_sha: str, index: int, total: int) -> str:
    """Attribute each split detail message to one release and deployment head."""
    return f"[release] {version} 변경 상세 ({index}/{total}) — 기준 {head_sha[:12]}"


def split_messages(
    *,
    version: str,
    head_sha: str,
    body: str,
    suffix_budget: int = 0,
    preserve_urls: bool = False,
) -> tuple[str, ...]:
    """Split without truncation while keeping every message below Discord's budget.

    줄 경계·URL 경계 규칙은 전송 쪽과 공유하는 `interop.chunker` 한 벌이 소유한다 — 사본이
    생기면 한쪽만 링크를 지키고 다른 쪽은 자른다. ``preserve_urls`` 를 끈 기본값은 저장된
    판본의 조각 수와 바이트를 그대로 재생하고, 신규 판본만 켜서 URL 이 두 통으로 찢기지
    않게 한다.
    """
    try:
        return chunk_lines(
            body,
            limit=MESSAGE_LIMIT - suffix_budget,
            header=partial(detail_header, version, head_sha),
            preserve_urls=preserve_urls,
        )
    except ValueError as error:
        raise ReleaseMessageError(
            "release detail messages do not converge on a stable count"
        ) from error


def render_v4(spec: ReleaseView, *, bundle_names: str) -> str:
    """Replay only stored fields, without consulting a clock or release plan."""
    from automation.release_spec import EnvelopeUnavailable

    try:
        from automation.interop.owner_message import Action, Approval, OwnerMessage, OwnerMessageError, Ref, render
    except ImportError as error:
        raise EnvelopeUnavailable("release owner envelope is unavailable") from error
    here = Ref(scope="self")
    note = f"; {spec.major_note}" if spec.major_note else ""
    # This approval has no render-time expiry: introducing a clock changes the
    # replay bytes and invalidates an already-posted owner's approval.
    try:
        return render(OwnerMessage(
            subject_key=f"release:{spec.version}", subject="릴리스 배포 승인",
            fact=(f"배포 기준 `{spec.head_sha}`;"
                  f" 배포 번들 ({len(spec.surface_digests)}): {bundle_names};"
                  f" 승인 바인딩 `{spec.action_hash()}`;"
                  f" 변경 상세 아래 {len(spec.detail_messages())}개 메시지"
                  f" (같은 릴리스 {spec.version} · 기준 {spec.head_sha[:12]}){note}"),
            location=here,
            owner=Action("react", here, "✅ 승인 또는 ⛔ 취소 (소유자 전용 — 봇/타인 리액션은 거부됨)"),
            agent_next="승인된 릴리스만 태그·배포", recovery="not_applicable",
            detail=Approval(None, "배포 미실행, 요청 폐기"),
        ), destination=here)
    except OwnerMessageError as error:
        raise EnvelopeUnavailable("release owner envelope cannot render") from error


def render_v5(spec: ReleaseView, *, bundle_names: str) -> str:
    """Readable approval facts, retaining every binding and detail-message reference."""
    from automation.release_spec import EnvelopeUnavailable

    try:
        from automation.interop.owner_message import Action, Approval, OwnerMessage, OwnerMessageError, Ref, render
    except ImportError as error:
        raise EnvelopeUnavailable("release owner envelope is unavailable") from error
    here = Ref(scope="self")
    facts = [
        f"배포 기준: `{spec.head_sha}`",
        f"배포 묶음 ({len(spec.surface_digests)}): {bundle_names}",
        f"승인 식별값: `{spec.action_hash()}`",
        f"변경 상세: 아래 {len(spec.detail_messages())}개 메시지 "
        + f"(같은 릴리스 {spec.version} · 기준 {spec.head_sha[:12]})",
    ]
    if spec.major_note:
        facts.append(spec.major_note)
    try:
        return render(OwnerMessage(
            subject_key=f"release:{spec.version}", subject=f"릴리스 {spec.version} 배포 승인",
            fact="\n".join(facts), location=here,
            owner=Action("react", here, "✅ 승인 또는 ⛔ 취소 (소유자 전용 — 봇/타인 리액션은 거부됨)"),
            agent_next="승인된 릴리스만 태그·배포", recovery="not_applicable",
            detail=Approval(None, "배포 미실행, 요청 폐기"), render_version="owner-ko-v2",
        ), destination=here)
    except OwnerMessageError as error:
        raise EnvelopeUnavailable("release owner envelope cannot render") from error


def render_v6(spec: ReleaseView, *, bundle_summary: str) -> str:
    """Compact card with a replayable link to the first posted detail message.

    A ``healthcheck-wrapper:<node>`` surface adds one line naming those hosts. Without such
    surfaces the bytes are unchanged, so every stored v6 card still replays exactly.
    """
    from automation.release_spec import (
        DORI_SURFACE_PREFIX, GATEWAY_STOP_SURFACE, RAG_MCP_SURFACE,
        WRAPPER_SURFACE_PREFIX, YOODORI_CHANNEL_PREFIX, EnvelopeUnavailable,
    )
    from automation.notice_bot import CARD_LINE, SURFACE
    from automation.auto_write import CARD_LINES, SURFACE_PREFIX

    wrapper_hosts = [
        name.removeprefix(WRAPPER_SURFACE_PREFIX)
        for name, _digest in spec.surface_digests
        if name.startswith(WRAPPER_SURFACE_PREFIX)
    ]

    try:
        from automation.interop.owner_message import (
            Action,
            Approval,
            LinkStatus,
            OwnerMessage,
            OwnerMessageError,
            Ref,
            discord_link,
            render,
        )
    except ImportError as error:
        raise EnvelopeUnavailable("release owner envelope is unavailable") from error
    here = Ref(scope="self")
    facts = [
        f"배포 기준: `{spec.head_sha}`",
        f"배포 묶음: {bundle_summary}",
        *([CARD_LINE] if any(name == SURFACE for name, _digest in spec.surface_digests) else []),
        *[CARD_LINES[name.removeprefix(SURFACE_PREFIX)]
          for name, _digest in spec.surface_digests
          if name.startswith(SURFACE_PREFIX)],
        *(
            [f"헬스체크 허용 목록도 갱신: {', '.join(wrapper_hosts)}"]
            if wrapper_hosts
            else []
        ),
        *(["도리 전환 켬: 노드 agent 게이트웨이가 #agent-chat 무시(유돌이 받음)",
           f"유돌 게이트: 카드 올리기 명령 {YOODORI_CARD_COMMAND_COUNT}개 추가(실행 권한 없음)",
           f"유돌 게이트: Gmail 읽기 {YOODORI_GMAIL_READ_COUNT}개 추가"] if any(
            name.startswith(DORI_SURFACE_PREFIX) for name, _digest in spec.surface_digests
        ) else []),
        *(["게이트웨이 멈춤: 노드 agent·peer Hermes 게이트웨이 정지(제거 아님, 파일 삭제 후 재배포로 다시 켬)"]
          if any(name == GATEWAY_STOP_SURFACE for name, _digest in spec.surface_digests) else []),
        *[
            f"채널 합치기: 노드의 대화(#agent-chat)·알림(#notifications)을 채널 "
            f"{name.removeprefix(YOODORI_CHANNEL_PREFIX)}(유돌 채널)로"
            for name, _digest in spec.surface_digests if name.startswith(YOODORI_CHANNEL_PREFIX)
        ],
        *(["검색 잠깐 끊김(~1분)·되돌림 있음"] if any(
            name == RAG_MCP_SURFACE for name, _digest in spec.surface_digests
        ) else []),
        f"승인 식별값: `{spec.action_hash()}`",
        f"변경 상세: {len(spec.detail_messages())}개 메시지",
    ]
    if spec.major_note:
        facts.append(spec.major_note)
    detail_id = spec.detail_message_ids[0] if spec.detail_message_ids else None
    link = discord_link(
        space="guild" if spec.detail_guild_id else "unknown",
        guild_id=spec.detail_guild_id or None,
        channel_id=spec.detail_channel_id or None,
        message_id=detail_id,
    )
    reference = (
        link.url
        if link.status is LinkStatus.AVAILABLE and link.url is not None
        else "검색: 변경 상세 / "
        f"{spec.version} {spec.head_sha[:12]}"
    )
    try:
        content = render(
            OwnerMessage(
                subject_key=spec.version,
                subject=f"릴리스 {spec.version} 배포 승인",
                fact="\n".join(facts),
                location=here,
                owner=Action(
                    "react",
                    here,
                    "✅ 승인 또는 ⛔ 취소 (소유자 전용 — 봇/타인 리액션은 거부됨)",
                ),
                agent_next="승인된 릴리스만 태그·배포",
                recovery="not_applicable",
                detail=Approval(None, "배포 미실행, 요청 폐기"),
                render_version="owner-ko-v2",
            ),
            destination=here,
        )
    except OwnerMessageError as error:
        raise EnvelopeUnavailable("release owner envelope cannot render") from error
    body, separator, _footer = content.rpartition("\n")
    if not separator:
        raise EnvelopeUnavailable("release owner envelope has no footer")
    return (
        f"{body}\n-# 참조: 변경 상세 → {reference}"
        f" · {spec.version} · {spec.head_sha[:12]}"
    )
