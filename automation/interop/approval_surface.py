"""Pure owner-approval surface policy.

S1  Per-kind transitions are append-only and version ordered.
S2  A stored binding is interpreted at its stamped policy version, never the
    current version, and a contradictory stored surface fails closed.
S3  Every binding carries a concrete Discord channel id whose facts match its
    explicit surface; directory failures never trigger a fallback.
S4  This module performs no disk, environment, or network I/O. Collaborators
    provide all channel discovery and facts through ``ChannelDirectory``.
S5  v7 (2026-08-24 owner decision) moves owner-only approvals off the owner DM
    into per-kind threads under the personal ``#agent-chat`` channel so the
    owner reads approvals inside threads. Repair stays on the owner DM until
    its Ops bot joins the personal guild.
S6  2026-09-01 owner decision: inside ``AGENT_CHAT_THREAD`` a producer that passes a
    ``RequestThread`` gets its own per-request thread (anchored on the instruction
    message when that message lives in ``#agent-chat``, else a fresh channel thread)
    so the approval, its reminders and the result notice complete in ONE thread.
    The surface facts are unchanged (public thread whose parent is ``#agent-chat``),
    so no policy bump: stored v7/v8 bindings keep validating as before, and a caller
    that passes no spec still lands on the legacy per-kind thread.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Protocol, TypeAlias, assert_never


class ApprovalSurfaceError(RuntimeError):
    pass


class ApprovalKind(StrEnum):
    MAIL_REPLY = "mail-reply"
    MAIL_COMPOSE = "mail-compose"
    BUDGET_MAIL = "budget-mail"
    PATENT_EXPORT = "patent-export"
    REPAIR = "repair"
    CALENDAR = "calendar"
    COORDINATION = "coordination"
    WIKI = "wiki"
    SKILL_DEPLOY = "skill-deploy"
    SKILL_ATTEST = "skill-attest"
    SKILL_PUBLISH = "skill-publish"
    SKILL_SUBMIT = "skill-submit"
    MANAGED_ACTIVATE = "managed-activate"
    RELEASE = "release"
    OBSIDIAN_WRITE = "obsidian-write"
    TODO = "todo"
    PROJECT_BOARD = "project-board"


class ApprovalSurface(StrEnum):
    OWNER_DM = "owner-dm"
    SKILL_APPROVALS = "skill-approvals"
    AGENT_CHAT_THREAD = "agent-chat-thread"


@dataclass(frozen=True, slots=True)
class RequestThread:
    """One approval request's thread spec (S6).

    ``title`` is already masked by the producer — calendar/wiki/patent pass an id;
    obsidian passes the note filename, never its directories or body, on the owner's
    private surface. It is truncated here, not by the caller. The origin pair is the
    owner's instruction message; it only anchors the thread when that
    message lives in ``#agent-chat`` (owner-only surface), otherwise it is ignored.
    """

    title: str
    origin_channel_id: str = ""
    origin_message_id: str = ""


#: Owner-facing thread label per kind — the thread name is ``<label> · <title>``.
KIND_LABELS: Final[Mapping[ApprovalKind, str]] = MappingProxyType({
    ApprovalKind.MAIL_REPLY: "메일 회신",
    ApprovalKind.MAIL_COMPOSE: "메일 발신",
    ApprovalKind.BUDGET_MAIL: "과제비 메일",
    ApprovalKind.PATENT_EXPORT: "특허 반출",
    ApprovalKind.REPAIR: "수리",
    ApprovalKind.CALENDAR: "캘린더",
    ApprovalKind.COORDINATION: "일정 조율",
    ApprovalKind.WIKI: "위키",
    ApprovalKind.SKILL_DEPLOY: "스킬 배포",
    ApprovalKind.SKILL_ATTEST: "스킬 검증",
    ApprovalKind.SKILL_PUBLISH: "스킬 발행",
    ApprovalKind.SKILL_SUBMIT: "스킬 제출",
    ApprovalKind.MANAGED_ACTIVATE: "관리형 활성화",
    ApprovalKind.RELEASE: "릴리스",
    ApprovalKind.OBSIDIAN_WRITE: "옵시디언 노트 승인 요청",
    ApprovalKind.TODO: "할 일",
    ApprovalKind.PROJECT_BOARD: "프로젝트 제안",
})

REQUEST_TITLE_LIMIT: Final = 40
THREAD_NAME_LIMIT: Final = 100  # Discord channel/thread name limit


def _request_title(request: RequestThread) -> str:
    """The producer-masked title, whitespace-folded and clipped to the shared limit."""
    return " ".join(request.title.split())[:REQUEST_TITLE_LIMIT] or "요청"


def request_thread_name(kind: ApprovalKind, request: RequestThread) -> str:
    """``<kind label> · <title≤40>`` clipped to Discord's 100-char thread name."""
    return f"{KIND_LABELS[kind]} · {_request_title(request)}"[:THREAD_NAME_LIMIT]


def request_thread_notice(kind: ApprovalKind, request: RequestThread) -> str:
    """The channel-visible announcement a request thread hangs on (2026-09-09).

    A thread created directly on the channel renders in #agent-chat as a contentless
    "started a thread" system line: the owner sees a name but nothing saying a decision
    is waiting, and calendar approvals went unnoticed until they were discarded (repair
    ticket t_e23d85a1, owner report 2026-09-09). Anchoring the thread on this message
    puts the request itself in the channel. It repeats only what the thread name already
    shows — the producer-masked title — so it widens no disclosure.
    """
    try:
        from automation.interop.owner_message import Action, Approval, OwnerMessage, Ref, render

        # This message becomes the thread anchor; its id does not exist until after POST.
        # Name that native relationship, never guess a link or invite reactions here.
        anchor = Ref(scope="self")
        message = OwnerMessage(
            subject_key=_request_title(request), subject=KIND_LABELS[kind],
            fact="🔔 승인 대기 — 이 메시지의 스레드에서 ✅ 실행 / ⛔ 취소로 결정해 주세요.",
            location=anchor, owner=Action(verb="open", target=anchor),
            agent_next="이 메시지에 승인 스레드를 열고 그 안에 승인 카드를 게시합니다",
            recovery="not_applicable", detail=Approval(expires_at=None, cancel_effect="실행 안 함"),
        )
        return render(message, destination=anchor)
    except (ImportError, ValueError):
        # Optional/older envelope runtimes must not prevent a valid approval post.
        return (
            f"🔔 승인 대기 · {KIND_LABELS[kind]} · {_request_title(request)}\n"
            "이 메시지의 스레드에서 ✅ 실행 / ⛔ 취소로 결정해 주세요."
        )


def kind_thread_name(kind: ApprovalKind) -> str:
    """The frozen name of the legacy per-kind thread (v7) — never reused for a request."""
    return f"승인-{kind.value}"


class LiveRequest(Protocol):
    @property
    def channel_id(self) -> str: ...


# v8 (2026-08-28 §10-7): Ops 봇의 개인 서버 초대를 전제로 repair 승인도 agent-chat
# 스레드로 이관한다 — 마지막 DM 잔존이 0이 된다. 초대 전 배포는 새 repair 승인을
# fail-closed(게시 불가)로 만드므로, 릴리스 컷은 초대 확인 뒤에만 한다(계획 ON-4).
POLICY_VERSION: Final = 8

TransitionLedger: TypeAlias = Mapping[
    ApprovalKind,
    tuple[tuple[int, ApprovalSurface], ...],
]

_TRANSITIONS: Final[TransitionLedger] = MappingProxyType({
    ApprovalKind.MAIL_REPLY: (
        (0, ApprovalSurface.SKILL_APPROVALS),
        (2, ApprovalSurface.OWNER_DM),
        (7, ApprovalSurface.AGENT_CHAT_THREAD),
    ),
    ApprovalKind.MAIL_COMPOSE: (
        (0, ApprovalSurface.OWNER_DM),
        (7, ApprovalSurface.AGENT_CHAT_THREAD),
    ),
    ApprovalKind.BUDGET_MAIL: (
        (0, ApprovalSurface.SKILL_APPROVALS),
        (3, ApprovalSurface.OWNER_DM),
        (7, ApprovalSurface.AGENT_CHAT_THREAD),
    ),
    ApprovalKind.PATENT_EXPORT: (
        (0, ApprovalSurface.SKILL_APPROVALS),
        (4, ApprovalSurface.OWNER_DM),
        (7, ApprovalSurface.AGENT_CHAT_THREAD),
    ),
    ApprovalKind.REPAIR: (
        (0, ApprovalSurface.SKILL_APPROVALS),
        (5, ApprovalSurface.OWNER_DM),
        (8, ApprovalSurface.AGENT_CHAT_THREAD),
    ),
    ApprovalKind.CALENDAR: (
        (0, ApprovalSurface.OWNER_DM),
        (7, ApprovalSurface.AGENT_CHAT_THREAD),
    ),
    ApprovalKind.COORDINATION: (
        (0, ApprovalSurface.OWNER_DM),
        (7, ApprovalSurface.AGENT_CHAT_THREAD),
    ),
    ApprovalKind.WIKI: (
        (0, ApprovalSurface.OWNER_DM),
        (7, ApprovalSurface.AGENT_CHAT_THREAD),
    ),
    ApprovalKind.SKILL_DEPLOY: ((0, ApprovalSurface.SKILL_APPROVALS),),
    ApprovalKind.SKILL_ATTEST: ((0, ApprovalSurface.SKILL_APPROVALS),),
    ApprovalKind.SKILL_PUBLISH: ((0, ApprovalSurface.SKILL_APPROVALS),),
    ApprovalKind.SKILL_SUBMIT: ((0, ApprovalSurface.SKILL_APPROVALS),),
    ApprovalKind.MANAGED_ACTIVATE: ((0, ApprovalSurface.SKILL_APPROVALS),),
    ApprovalKind.RELEASE: ((0, ApprovalSurface.SKILL_APPROVALS),),
    ApprovalKind.OBSIDIAN_WRITE: (
        (0, ApprovalSurface.OWNER_DM),
        (7, ApprovalSurface.AGENT_CHAT_THREAD),
    ),
    ApprovalKind.TODO: (
        (0, ApprovalSurface.OWNER_DM),
        (7, ApprovalSurface.AGENT_CHAT_THREAD),
    ),
    ApprovalKind.PROJECT_BOARD: ((0, ApprovalSurface.AGENT_CHAT_THREAD),),
})


def surface_at_policy(
    kind: ApprovalKind,
    policy_version: int,
    transitions: TransitionLedger = _TRANSITIONS,
) -> ApprovalSurface:
    rows = transitions.get(kind)
    if rows is None:
        raise ApprovalSurfaceError(f"approval kind has no transition ledger: {kind}")
    selected: ApprovalSurface | None = None
    for from_version, surface in rows:
        if from_version > policy_version:
            break
        selected = surface
    if selected is None:
        raise ApprovalSurfaceError(
            f"approval kind {kind} has no surface at policy version {policy_version}",
        )
    return selected


def required_surface(kind: ApprovalKind) -> ApprovalSurface:
    match kind:
        case (
            ApprovalKind.MAIL_REPLY
            | ApprovalKind.MAIL_COMPOSE
            | ApprovalKind.BUDGET_MAIL
            | ApprovalKind.PATENT_EXPORT
            | ApprovalKind.REPAIR
            | ApprovalKind.CALENDAR
            | ApprovalKind.COORDINATION
            | ApprovalKind.WIKI
            | ApprovalKind.SKILL_DEPLOY
            | ApprovalKind.SKILL_ATTEST
            | ApprovalKind.SKILL_PUBLISH
            | ApprovalKind.SKILL_SUBMIT
            | ApprovalKind.MANAGED_ACTIVATE
            | ApprovalKind.RELEASE
            | ApprovalKind.OBSIDIAN_WRITE
            | ApprovalKind.TODO
            | ApprovalKind.PROJECT_BOARD
        ):
            return surface_at_policy(kind, POLICY_VERSION)
        case unreachable:
            assert_never(unreachable)


@dataclass(frozen=True, slots=True)
class ChannelFacts:
    channel_type: int
    name: str
    recipient_ids: tuple[str, ...]
    parent_id: str | None = None
    guild_id: str | None = None


class ChannelDirectory(Protocol):
    def owner_dm(self) -> str: ...

    def skill_approvals(self) -> str: ...

    def agent_chat(self) -> str: ...

    def agent_chat_thread(self, kind: ApprovalKind) -> str: ...

    def agent_chat_request_thread(self, kind: ApprovalKind, request: RequestThread) -> str: ...

    def describe(self, channel_id: str) -> ChannelFacts: ...


@dataclass(frozen=True, slots=True)
class ApprovalBinding:
    kind: ApprovalKind
    surface: ApprovalSurface
    channel_id: str
    policy_version: int
    guild_id: str | None = None

    def __post_init__(self) -> None:
        if (
            not self.channel_id
            or self.channel_id in {"dm", "approvals"}
            or not self.channel_id.isdigit()
        ):
            raise ApprovalSurfaceError(
                f"approval binding requires a concrete Discord channel id: {self.channel_id!r}",
            )


def _resolved_channel_id(
    kind: ApprovalKind,
    surface: ApprovalSurface,
    directory: ChannelDirectory,
    skill_channel_id: str | None,
    request: RequestThread | None = None,
) -> str:
    try:
        match surface:
            case ApprovalSurface.OWNER_DM:
                return directory.owner_dm()
            case ApprovalSurface.SKILL_APPROVALS:
                if skill_channel_id is not None:
                    return skill_channel_id
                return directory.skill_approvals()
            case ApprovalSurface.AGENT_CHAT_THREAD:
                if request is None:
                    return directory.agent_chat_thread(kind)
                return directory.agent_chat_request_thread(kind, request)
            case unreachable:
                assert_never(unreachable)
    except Exception as error:  # noqa: BLE001 - fail closed; # noqa: BROAD_EXCEPT_OK
        raise ApprovalSurfaceError("approval channel resolution failed") from error


def _validate_channel(
    binding: ApprovalBinding,
    directory: ChannelDirectory,
    owner_id: str,
    *,
    resolve_guild: bool = False,
) -> ApprovalBinding:
    try:
        facts = directory.describe(binding.channel_id)
    except Exception as error:  # noqa: BLE001 - fail closed; # noqa: BROAD_EXCEPT_OK
        raise ApprovalSurfaceError("approval channel facts are unavailable") from error
    match binding.surface:
        case ApprovalSurface.OWNER_DM:
            is_valid = facts.channel_type == 1 and owner_id in facts.recipient_ids
        case ApprovalSurface.SKILL_APPROVALS:
            is_valid = facts.channel_type == 0 and facts.name == "approvals"
        case ApprovalSurface.AGENT_CHAT_THREAD:
            is_valid = facts.channel_type in (11, 12) and (
                facts.parent_id is not None and facts.parent_id == directory.agent_chat()
            )
        case unreachable:
            assert_never(unreachable)
    if not is_valid:
        raise ApprovalSurfaceError(
            f"channel {binding.channel_id} does not match surface {binding.surface}",
        )
    if resolve_guild:
        return ApprovalBinding(
            binding.kind, binding.surface, binding.channel_id, binding.policy_version,
            facts.guild_id,
        )
    return binding


def resolve_new_binding(
    kind: ApprovalKind,
    directory: ChannelDirectory,
    owner_id: str,
    skill_channel_id: str | None = None,
    *,
    request: RequestThread | None = None,
) -> ApprovalBinding:
    """Resolve a fresh binding; ``request`` selects the per-request thread (S6)."""
    surface = required_surface(kind)
    channel_id = _resolved_channel_id(kind, surface, directory, skill_channel_id, request)
    binding = ApprovalBinding(kind, surface, channel_id, POLICY_VERSION)
    return _validate_channel(binding, directory, owner_id, resolve_guild=True)


def reuse_request_thread(
    kind: ApprovalKind,
    outstanding: Iterable[LiveRequest],
    directory: ChannelDirectory,
    owner_id: str,
) -> ApprovalBinding | None:
    """The per-request thread a LIVE request of the same key already opened (S6).

    One approval key keeps one thread: a same-hash re-request (PENDING) and a content
    change (supersede) both land where the first post did instead of opening an empty
    thread per attempt. Only a per-request thread qualifies — the legacy per-kind thread
    and an owner DM never do, so a legacy pending request migrates on its next post and
    the shared kind thread is never renamed or archived. Unverifiable candidates are
    skipped, not fatal: the caller then resolves a fresh binding as before.
    """
    del owner_id  # a request thread is validated by its parent, not by a DM recipient
    if required_surface(kind) is not ApprovalSurface.AGENT_CHAT_THREAD:
        return None
    for request in outstanding:
        channel_id = str(request.channel_id or "")
        if not channel_id.isdigit():
            continue
        try:
            facts = directory.describe(channel_id)
            parent = directory.agent_chat()
        except Exception:  # noqa: BLE001 - reuse is opportunistic; resolution stays fail-closed
            continue
        if (
            facts.channel_type in (11, 12)
            and facts.parent_id == parent
            and facts.name != kind_thread_name(kind)
        ):
            # Lifecycle requests may carry only a channel; producer records also carry guild metadata.
            prior_guild = getattr(request, "approval_guild_id", None)
            guild_id = prior_guild if isinstance(prior_guild, str) else facts.guild_id
            return ApprovalBinding(
                kind, ApprovalSurface.AGENT_CHAT_THREAD, channel_id, POLICY_VERSION,
                guild_id,
            )
    return None


def validate_stored_binding(
    binding: ApprovalBinding,
    directory: ChannelDirectory,
    owner_id: str,
    *,
    transitions: TransitionLedger = _TRANSITIONS,
) -> ApprovalBinding:
    stamped_surface = surface_at_policy(
        binding.kind,
        binding.policy_version,
        transitions,
    )
    if binding.surface is not stamped_surface:
        raise ApprovalSurfaceError(
            f"stored surface {binding.surface} contradicts policy version {binding.policy_version}",
        )
    return _validate_channel(binding, directory, owner_id)


def legacy_binding(
    kind: ApprovalKind,
    raw_channel_id: str | None,
    directory: ChannelDirectory,
    owner_id: str,
) -> ApprovalBinding:
    match raw_channel_id:
        case None | "":
            surface = surface_at_policy(kind, 0)
            channel_id = _resolved_channel_id(kind, surface, directory, None)
        case "dm":
            surface = ApprovalSurface.OWNER_DM
            channel_id = _resolved_channel_id(kind, surface, directory, None)
        case "approvals":
            surface = ApprovalSurface.SKILL_APPROVALS
            channel_id = _resolved_channel_id(kind, surface, directory, None)
        case str() as channel_id:
            surface = surface_at_policy(kind, 0)
        case unreachable:
            assert_never(unreachable)
    binding = ApprovalBinding(kind, surface, channel_id, 0)
    return _validate_channel(binding, directory, owner_id)


def reaction_instruction(
    kind: ApprovalKind,
    surface: ApprovalSurface,
    *,
    name_surface: bool = False,
) -> str:
    del kind
    instruction = "이 메시지에 ✅ 실행 / ⛔ 취소"
    if not name_surface:
        return instruction
    match surface:
        case ApprovalSurface.OWNER_DM:
            surface_name = "소유자 DM"
        case ApprovalSurface.SKILL_APPROVALS:
            surface_name = "개인 서버 #approvals"
        case ApprovalSurface.AGENT_CHAT_THREAD:
            surface_name = "개인 서버 #agent-chat 스레드"
        case unreachable:
            assert_never(unreachable)
    return f"{instruction} ({surface_name})"
