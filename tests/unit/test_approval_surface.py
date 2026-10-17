"""Tests for the versioned owner-approval surface policy."""
from __future__ import annotations

import pytest

from automation.interop.approval_surface import (
    POLICY_VERSION,
    _TRANSITIONS,
    ApprovalBinding,
    ApprovalKind,
    ApprovalSurface,
    ApprovalSurfaceError,
    ChannelFacts,
    TransitionLedger,
    legacy_binding,
    reaction_instruction,
    required_surface,
    resolve_new_binding,
    surface_at_policy,
    validate_stored_binding,
)

OWNER_ID = "280680578404010625"
DM_CHANNEL_ID = "1526487936065952385"
SKILL_CHANNEL_ID = "1528936606946122421"
AGENT_CHAT_CHANNEL_ID = "1601000000000000001"
AGENT_CHAT_THREAD_ID = "1601000000000000002"

class FakeDirectory:
    """Mutable in-memory directory used to exercise the injected boundary."""

    def __init__(self) -> None:
        self.dm_channel_id: str = DM_CHANNEL_ID
        self.skill_channel_id: str = SKILL_CHANNEL_ID
        self.agent_chat_channel_id: str = AGENT_CHAT_CHANNEL_ID
        self.agent_chat_thread_id: str = AGENT_CHAT_THREAD_ID
        self.facts: dict[str, ChannelFacts] = {
            DM_CHANNEL_ID: ChannelFacts(1, "", (OWNER_ID,)),
            SKILL_CHANNEL_ID: ChannelFacts(0, "approvals", ()),
            AGENT_CHAT_THREAD_ID: ChannelFacts(11, "승인-todo", (), AGENT_CHAT_CHANNEL_ID),
        }
        self.calls: list[str] = []

    def owner_dm(self) -> str:
        self.calls.append("owner_dm")
        return self.dm_channel_id

    def skill_approvals(self) -> str:
        self.calls.append("skill_approvals")
        return self.skill_channel_id

    def agent_chat(self) -> str:
        self.calls.append("agent_chat")
        return self.agent_chat_channel_id

    def agent_chat_thread(self, kind: ApprovalKind) -> str:
        self.calls.append("agent_chat_thread")
        return self.agent_chat_thread_id

    def describe(self, channel_id: str) -> ChannelFacts:
        self.calls.append(f"describe:{channel_id}")
        return self.facts[channel_id]


class RaisingDirectory:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def owner_dm(self) -> str:
        self.calls.append("owner_dm")
        raise ApprovalSurfaceError("directory unavailable")

    def skill_approvals(self) -> str:
        self.calls.append("skill_approvals")
        return SKILL_CHANNEL_ID

    def agent_chat(self) -> str:
        self.calls.append("agent_chat")
        return AGENT_CHAT_CHANNEL_ID

    def agent_chat_thread(self, kind: ApprovalKind) -> str:
        self.calls.append("agent_chat_thread")
        raise ApprovalSurfaceError("agent-chat channel is not configured")

    def describe(self, channel_id: str) -> ChannelFacts:
        self.calls.append(f"describe:{channel_id}")
        return ChannelFacts(0, "approvals", ())


def _mail_flipped_at_v2() -> TransitionLedger:
    return {
        **_TRANSITIONS,
        ApprovalKind.MAIL_REPLY: (
            *_TRANSITIONS[ApprovalKind.MAIL_REPLY],
            (2, ApprovalSurface.OWNER_DM),
        ),
    }


def test_approval_kind_has_exactly_eighteen_members() -> None:
    # Given / When: the closed set of approval kinds is enumerated.
    kinds = tuple(ApprovalKind)

    # Then: every planned flow has exactly one kind.
    assert len(kinds) == 18


def test_todo_kind_lands_on_dm_then_moves_to_agent_chat() -> None:
    # Given / When: the todo kind is parsed through the closed approval enum.
    kind = ApprovalKind("todo")

    # Then: it landed on the owner DM and current policy moves it to the agent-chat thread.
    assert surface_at_policy(kind, 0) is ApprovalSurface.OWNER_DM
    assert required_surface(kind) is ApprovalSurface.AGENT_CHAT_THREAD


@pytest.mark.parametrize(
    "kind",
    (
        ApprovalKind.MAIL_COMPOSE,
        ApprovalKind.CALENDAR,
        ApprovalKind.COORDINATION,
        ApprovalKind.WIKI,
        ApprovalKind.OBSIDIAN_WRITE,
    ),
)
def test_already_dm_kinds_land_on_owner_dm(kind: ApprovalKind) -> None:
    # Given / When: an already-DM flow is resolved at the landing version.
    surface = surface_at_policy(kind, 0)

    # Then: its initial transition points to the owner DM.
    assert surface is ApprovalSurface.OWNER_DM


@pytest.mark.parametrize(
    "kind",
    (
        ApprovalKind.MAIL_REPLY,
        ApprovalKind.BUDGET_MAIL,
        ApprovalKind.PATENT_EXPORT,
        ApprovalKind.REPAIR,
    ),
)
def test_migrating_kinds_stay_on_skill_approvals_at_v1(kind: ApprovalKind) -> None:
    # Given / When: a migrating flow is resolved at the R1 baseline.
    surface = surface_at_policy(kind, 1)

    # Then: R1 has not moved it to DM.
    assert surface is ApprovalSurface.SKILL_APPROVALS


@pytest.mark.parametrize(
    "kind",
    (
        ApprovalKind.SKILL_DEPLOY,
        ApprovalKind.SKILL_ATTEST,
        ApprovalKind.SKILL_PUBLISH,
        ApprovalKind.SKILL_SUBMIT,
        ApprovalKind.MANAGED_ACTIVATE,
        ApprovalKind.RELEASE,
    ),
)
@pytest.mark.parametrize("policy_version", range(POLICY_VERSION + 6))
def test_supply_chain_never_moves_surfaces(
    kind: ApprovalKind,
    policy_version: int,
) -> None:
    # Given / When: a supply-chain flow is resolved at any foreseeable version.
    surface = surface_at_policy(kind, policy_version)

    # Then: it remains guild-bound and has no transition prepared for a flip.
    assert surface is ApprovalSurface.SKILL_APPROVALS
    assert len(_TRANSITIONS[kind]) == 1


@pytest.mark.parametrize("kind", tuple(ApprovalKind))
def test_each_transition_ledger_starts_at_zero_and_strictly_increases(
    kind: ApprovalKind,
) -> None:
    # Given: one kind's append-only transition tuple.
    versions = tuple(version for version, _surface in _TRANSITIONS[kind])

    # When / Then: its baseline exists and every append advances the version.
    assert versions[0] == 0
    assert all(left < right for left, right in zip(versions, versions[1:], strict=False))


@pytest.mark.parametrize("kind", tuple(ApprovalKind))
def test_required_surface_is_current_policy_projection(kind: ApprovalKind) -> None:
    # Given / When: both new-record and historical policy APIs resolve the current version.
    required = required_surface(kind)
    historical = surface_at_policy(kind, POLICY_VERSION)

    # Then: they are one policy viewed at different times.
    assert required is historical


def test_appending_a_flip_does_not_reinterpret_an_older_version() -> None:
    # Given: mail's transition tuple is copied and a v2 DM flip is appended.
    transitions = _mail_flipped_at_v2()

    # When: the old R1 version is resolved against the extended ledger.
    surface = surface_at_policy(ApprovalKind.MAIL_REPLY, 1, transitions=transitions)

    # Then: the v1 meaning remains guild-bound forever.
    assert surface is ApprovalSurface.SKILL_APPROVALS


def test_stored_binding_survives_a_policy_flip() -> None:
    # Given: a v1 guild binding and a copied ledger whose current v2 policy is DM.
    directory = FakeDirectory()
    binding = ApprovalBinding(
        ApprovalKind.MAIL_REPLY,
        ApprovalSurface.SKILL_APPROVALS,
        SKILL_CHANNEL_ID,
        1,
    )
    transitions = _mail_flipped_at_v2()
    assert surface_at_policy(ApprovalKind.MAIL_REPLY, 2, transitions=transitions) is ApprovalSurface.OWNER_DM

    # When: the stored record is validated using its stamped version.
    validated = validate_stored_binding(
        binding,
        directory,
        OWNER_ID,
        transitions=transitions,
    )

    # Then: it still points to the original guild message.
    assert validated is binding
    assert validated.channel_id == SKILL_CHANNEL_ID


def test_stored_binding_with_contradictory_surface_raises() -> None:
    # Given: a v1 mail record claiming the surface that only starts at v2.
    directory = FakeDirectory()
    binding = ApprovalBinding(
        ApprovalKind.MAIL_REPLY,
        ApprovalSurface.OWNER_DM,
        DM_CHANNEL_ID,
        1,
    )

    # When / Then: validation refuses rather than retargeting the message.
    with pytest.raises(ApprovalSurfaceError):
        _ = validate_stored_binding(binding, directory, OWNER_ID, transitions=_mail_flipped_at_v2())


@pytest.mark.parametrize("channel_id", ("", "dm", "approvals", "abc"))
def test_binding_refuses_an_implicit_or_non_snowflake_channel(channel_id: str) -> None:
    # Given / When / Then: an unresolved channel cannot enter a binding.
    with pytest.raises(ApprovalSurfaceError):
        _ = ApprovalBinding(
            ApprovalKind.MAIL_REPLY,
            ApprovalSurface.SKILL_APPROVALS,
            channel_id,
            POLICY_VERSION,
        )


def test_resolve_new_binding_routes_repair_to_the_agent_chat_thread() -> None:
    # Given: v8 (§10-7) — the Ops bot joins the personal guild, so repair leaves
    # the last remaining owner-DM surface for the agent-chat thread.
    directory = FakeDirectory()
    directory.facts[AGENT_CHAT_THREAD_ID] = ChannelFacts(
        11, "승인-repair", (), AGENT_CHAT_CHANNEL_ID,
    )

    # When: repair creates a new binding at current policy.
    binding = resolve_new_binding(ApprovalKind.REPAIR, directory, OWNER_ID)

    # Then: the concrete thread and current policy version are stamped.
    assert binding == ApprovalBinding(
        ApprovalKind.REPAIR,
        ApprovalSurface.AGENT_CHAT_THREAD,
        AGENT_CHAT_THREAD_ID,
        POLICY_VERSION,
    )


def test_resolve_new_binding_validates_skill_approvals() -> None:
    # Given: an injected directory with a valid approvals channel.
    directory = FakeDirectory()

    # When: a guild-routed kind creates a new binding.
    binding = resolve_new_binding(ApprovalKind.SKILL_ATTEST, directory, OWNER_ID)

    # Then: the concrete guild channel and current policy version are stamped.
    assert binding.channel_id == SKILL_CHANNEL_ID
    assert binding.surface is ApprovalSurface.SKILL_APPROVALS


def test_resolve_new_binding_accepts_an_explicit_skill_channel_id() -> None:
    # Given: a supply-chain caller already has the concrete approvals channel id.
    directory = FakeDirectory()

    # When: the id is supplied explicitly.
    binding = resolve_new_binding(
        ApprovalKind.SKILL_DEPLOY,
        directory,
        OWNER_ID,
        skill_channel_id=SKILL_CHANNEL_ID,
    )

    # Then: it is described and used without another guild-channel lookup.
    assert binding.channel_id == SKILL_CHANNEL_ID
    assert "skill_approvals" not in directory.calls


def _stored_repair_dm_binding() -> ApprovalBinding:
    """A v5-era repair record — v8 flips NEW bindings, stored DMs still validate."""
    return ApprovalBinding(ApprovalKind.REPAIR, ApprovalSurface.OWNER_DM, DM_CHANNEL_ID, 5)


def test_dm_with_guild_channel_type_raises() -> None:
    # Given: the stored DM binding's facts describe a guild text channel.
    directory = FakeDirectory()
    directory.facts[DM_CHANNEL_ID] = ChannelFacts(0, "approvals", (OWNER_ID,))

    # When / Then: the mismatch fails closed.
    with pytest.raises(ApprovalSurfaceError):
        _ = validate_stored_binding(_stored_repair_dm_binding(), directory, OWNER_ID)


def test_dm_without_owner_recipient_raises() -> None:
    # Given: the DM facts omit the owner.
    directory = FakeDirectory()
    directory.facts[DM_CHANNEL_ID] = ChannelFacts(1, "", ("999",))

    # When / Then: the mismatch fails closed.
    with pytest.raises(ApprovalSurfaceError):
        _ = validate_stored_binding(_stored_repair_dm_binding(), directory, OWNER_ID)


def test_skill_channel_with_wrong_name_raises() -> None:
    # Given: the guild channel is named general rather than approvals.
    directory = FakeDirectory()
    directory.facts[SKILL_CHANNEL_ID] = ChannelFacts(0, "general", ())

    # When / Then: the mismatch fails closed.
    with pytest.raises(ApprovalSurfaceError):
        _ = resolve_new_binding(ApprovalKind.SKILL_ATTEST, directory, OWNER_ID)


def test_directory_exception_becomes_surface_error_without_fallback() -> None:
    # Given: agent-chat thread lookup fails before yielding a channel (v8: repair's
    # resolution — e.g. the ops account has no agent_chat_channel_id configured).
    directory = RaisingDirectory()

    # When / Then: the typed error propagates and no DM/guild fallback is attempted.
    with pytest.raises(ApprovalSurfaceError):
        _ = resolve_new_binding(ApprovalKind.REPAIR, directory, OWNER_ID)
    assert directory.calls == ["agent_chat_thread"]


@pytest.mark.parametrize(
    ("raw_channel_id", "expected_surface"),
    (
        ("", ApprovalSurface.SKILL_APPROVALS),
        (None, ApprovalSurface.SKILL_APPROVALS),
        ("dm", ApprovalSurface.OWNER_DM),
        ("approvals", ApprovalSurface.SKILL_APPROVALS),
    ),
)
def test_legacy_binding_resolves_sentinels_to_concrete_ids(
    raw_channel_id: str | None,
    expected_surface: ApprovalSurface,
) -> None:
    # Given: a legacy mail record with no versioned binding.
    directory = FakeDirectory()

    # When: the named migrator resolves its historical channel shape.
    binding = legacy_binding(ApprovalKind.MAIL_REPLY, raw_channel_id, directory, OWNER_ID)

    # Then: policy v0 and a real snowflake replace the legacy sentinel.
    assert binding.surface is expected_surface
    assert binding.channel_id.isdigit()
    assert binding.policy_version == 0


def test_legacy_binding_keeps_a_concrete_historical_channel() -> None:
    # Given: an already-concrete legacy owner-DM id for a DM-at-v0 flow.
    directory = FakeDirectory()

    # When: the named migrator creates the first-class binding.
    binding = legacy_binding(ApprovalKind.CALENDAR, DM_CHANNEL_ID, directory, OWNER_ID)

    # Then: the concrete id is preserved and validated at policy zero.
    assert binding.channel_id == DM_CHANNEL_ID
    assert binding.surface is ApprovalSurface.OWNER_DM


def test_reaction_instruction_names_no_surface_by_default() -> None:
    # Given / When: an in-message instruction is rendered with the default.
    instruction = reaction_instruction(ApprovalKind.MAIL_REPLY, ApprovalSurface.SKILL_APPROVALS)

    # Then: it is surface-neutral.
    assert instruction == "이 메시지에 ✅ 실행 / ⛔ 취소"


@pytest.mark.parametrize(
    ("surface", "surface_name"),
    (
        (ApprovalSurface.OWNER_DM, "소유자 DM"),
        (ApprovalSurface.SKILL_APPROVALS, "개인 서버 #approvals"),
        (ApprovalSurface.AGENT_CHAT_THREAD, "개인 서버 #agent-chat 스레드"),
    ),
)
def test_reaction_instruction_names_surface_when_requested(
    surface: ApprovalSurface,
    surface_name: str,
) -> None:
    # Given / When: an outside hint explicitly asks to name the destination.
    instruction = reaction_instruction(
        ApprovalKind.MAIL_REPLY,
        surface,
        name_surface=True,
    )

    # Then: the central formatter includes the correct human surface name.
    assert surface_name in instruction


AGENT_CHAT_KINDS = (
    ApprovalKind.MAIL_REPLY,
    ApprovalKind.MAIL_COMPOSE,
    ApprovalKind.BUDGET_MAIL,
    ApprovalKind.PATENT_EXPORT,
    ApprovalKind.CALENDAR,
    ApprovalKind.COORDINATION,
    ApprovalKind.WIKI,
    ApprovalKind.OBSIDIAN_WRITE,
    ApprovalKind.TODO,
)


@pytest.mark.parametrize("kind", AGENT_CHAT_KINDS)
def test_v7_moves_owner_approvals_to_the_agent_chat_thread(kind: ApprovalKind) -> None:
    # Given / When: an owner-approval kind is resolved before and after the flip.
    before = surface_at_policy(kind, 6)
    current = surface_at_policy(kind, POLICY_VERSION)

    # Then: v6 stays on the owner DM and v7 lands on the agent-chat thread.
    assert before is ApprovalSurface.OWNER_DM
    assert current is ApprovalSurface.AGENT_CHAT_THREAD


def test_v8_moves_repair_to_the_agent_chat_thread() -> None:
    # Given / When: repair is resolved before and after v8 (§10-7 Ops-bot invite).
    before = surface_at_policy(ApprovalKind.REPAIR, 7)
    current = required_surface(ApprovalKind.REPAIR)

    # Then: v7 records stay on the owner DM; new bindings land on the thread —
    # the last DM-resident approval surface is gone.
    assert before is ApprovalSurface.OWNER_DM
    assert current is ApprovalSurface.AGENT_CHAT_THREAD


def test_resolve_new_binding_validates_agent_chat_thread() -> None:
    # Given: an injected directory with a configured agent-chat thread.
    directory = FakeDirectory()

    # When: an owner-approval kind creates a new binding.
    binding = resolve_new_binding(ApprovalKind.TODO, directory, OWNER_ID)

    # Then: the concrete thread and current policy version are stamped.
    assert binding == ApprovalBinding(
        ApprovalKind.TODO,
        ApprovalSurface.AGENT_CHAT_THREAD,
        AGENT_CHAT_THREAD_ID,
        POLICY_VERSION,
    )
    assert "agent_chat_thread" in directory.calls


def test_agent_chat_thread_with_a_foreign_parent_raises() -> None:
    # Given: the resolved thread hangs under a channel that is not agent-chat.
    directory = FakeDirectory()
    directory.facts[AGENT_CHAT_THREAD_ID] = ChannelFacts(
        11, "승인-todo", (), "999000000000000000",
    )

    # When / Then: the mismatch fails closed.
    with pytest.raises(ApprovalSurfaceError):
        _ = resolve_new_binding(ApprovalKind.TODO, directory, OWNER_ID)


def test_agent_chat_thread_with_a_plain_channel_type_raises() -> None:
    # Given: the resolver returned a plain guild channel rather than a thread.
    directory = FakeDirectory()
    directory.facts[AGENT_CHAT_THREAD_ID] = ChannelFacts(
        0, "agent-chat", (), AGENT_CHAT_CHANNEL_ID,
    )

    # When / Then: the mismatch fails closed.
    with pytest.raises(ApprovalSurfaceError):
        _ = resolve_new_binding(ApprovalKind.TODO, directory, OWNER_ID)


def test_agent_chat_private_thread_is_accepted() -> None:
    # Given: the agent-chat thread is a private thread under the configured channel.
    directory = FakeDirectory()
    directory.facts[AGENT_CHAT_THREAD_ID] = ChannelFacts(
        12, "승인-todo", (), AGENT_CHAT_CHANNEL_ID,
    )

    # When: the binding is resolved.
    binding = resolve_new_binding(ApprovalKind.TODO, directory, OWNER_ID)

    # Then: the private thread is a valid agent-chat surface.
    assert binding.channel_id == AGENT_CHAT_THREAD_ID


def test_agent_chat_resolution_failure_fails_closed_without_dm_fallback() -> None:
    # Given: the agent-chat thread resolver fails (for example an unset config key).
    directory = RaisingDirectory()

    # When / Then: the typed error propagates and no owner-DM fallback is attempted.
    with pytest.raises(ApprovalSurfaceError):
        _ = resolve_new_binding(ApprovalKind.TODO, directory, OWNER_ID)
    assert directory.calls == ["agent_chat_thread"]


def test_stored_v6_dm_binding_survives_the_v7_flip() -> None:
    # Given: a pending v6 owner-DM record written before the agent-chat flip.
    directory = FakeDirectory()
    binding = ApprovalBinding(ApprovalKind.TODO, ApprovalSurface.OWNER_DM, DM_CHANNEL_ID, 6)

    # When: the stored record is validated at its stamped version.
    validated = validate_stored_binding(binding, directory, OWNER_ID)

    # Then: it still drains through the owner DM it was posted to.
    assert validated is binding


# ------------------------------------------------------ per-request thread (2026-09-01)

REQUEST_THREAD_ID = "1601000000000000003"


class RequestThreadDirectory(FakeDirectory):
    """FakeDirectory that also serves the per-request thread seam."""

    def __init__(self) -> None:
        super().__init__()
        self.request_threads: list[tuple[ApprovalKind, object]] = []
        self.facts[REQUEST_THREAD_ID] = ChannelFacts(
            11, "✉️ 메일 발신 · 세미나 안내", (), AGENT_CHAT_CHANNEL_ID,
        )

    def agent_chat_request_thread(self, kind: ApprovalKind, request: object) -> str:
        self.calls.append("agent_chat_request_thread")
        self.request_threads.append((kind, request))
        return REQUEST_THREAD_ID


def test_resolve_new_binding_with_request_lands_on_the_request_thread() -> None:
    # Given: a producer that knows the instruction origin and a masked title
    from automation.interop import approval_surface as module

    directory = RequestThreadDirectory()
    request = module.RequestThread(
        title="세미나 안내", origin_channel_id=AGENT_CHAT_CHANNEL_ID, origin_message_id="msg-1",
    )

    # When: a fresh binding is resolved with the request spec
    binding = resolve_new_binding(ApprovalKind.MAIL_COMPOSE, directory, OWNER_ID, request=request)

    # Then: the binding is the per-request thread, still on the agent-chat surface at v8
    assert binding.channel_id == REQUEST_THREAD_ID
    assert binding.surface is ApprovalSurface.AGENT_CHAT_THREAD
    assert binding.policy_version == POLICY_VERSION
    assert directory.request_threads == [(ApprovalKind.MAIL_COMPOSE, request)]
    assert "agent_chat_thread" not in directory.calls


def test_resolve_new_binding_without_request_keeps_the_kind_thread() -> None:
    # Given: a legacy caller that passes no request spec
    directory = RequestThreadDirectory()

    # When: a fresh binding is resolved the old way
    binding = resolve_new_binding(ApprovalKind.TODO, directory, OWNER_ID)

    # Then: the per-kind thread is still used and the request seam is untouched
    assert binding.channel_id == AGENT_CHAT_THREAD_ID
    assert "agent_chat_request_thread" not in directory.calls


def test_request_thread_name_labels_and_truncates() -> None:
    # Given: an over-long title on a labelled kind
    from automation.interop import approval_surface as module

    long_title = "가" * 80
    name = module.request_thread_name(
        ApprovalKind.MAIL_COMPOSE, module.RequestThread(title=long_title),
    )

    # Then: label + separator + a 40-char title, never beyond Discord's 100-char limit
    assert name == "✉️ 메일 발신 · " + "가" * 40
    assert len(name) <= 100
    assert module.request_thread_name(
        ApprovalKind.CALENDAR, module.RequestThread(title="draft-42"),
    ) == "🗓️ 캘린더 · draft-42"


@pytest.mark.parametrize("title", ["2026-09-08 라이프로그 회의 준비.md", "가" * 57 + ".md"])
def test_obsidian_request_thread_name_preserves_the_bounded_note_filename(title: str) -> None:
    from automation.interop import approval_surface as module

    name = module.request_thread_name(
        ApprovalKind.OBSIDIAN_WRITE, module.RequestThread(title=title),
    )

    label, separator, filename = name.partition(" · ")
    assert label == f"{module.KIND_ICONS[ApprovalKind.OBSIDIAN_WRITE]} {module.KIND_LABELS[ApprovalKind.OBSIDIAN_WRITE]}"
    assert separator == " · "
    assert filename == title[:module.REQUEST_TITLE_LIMIT]
    assert len(name) <= module.THREAD_NAME_LIMIT == 100


def test_request_thread_name_covers_every_kind() -> None:
    # Given / When: every approval kind is rendered with an id-only title
    from automation.interop import approval_surface as module

    names = {
        kind: module.request_thread_name(kind, module.RequestThread(title="id-1"))
        for kind in ApprovalKind
    }

    # Then: each kind has a non-empty label and the title survives
    assert all(name.endswith(" · id-1") and len(name) > len(" · id-1") for name in names.values())
    assert len(set(names.values())) == len(ApprovalKind)


def test_request_thread_spec_has_no_origin_by_default() -> None:
    # Given: a producer without an instruction message (cron draft, repair)
    from automation.interop import approval_surface as module

    request = module.RequestThread(title="t_abc")

    # Then: the spec is origin-less and immutable
    assert (request.origin_channel_id, request.origin_message_id) == ("", "")
    with pytest.raises((AttributeError, TypeError)):
        request.title = "other"  # type: ignore[misc]


def test_validate_stored_binding_accepts_a_request_thread_at_v8() -> None:
    # Given: a stored v8 binding whose channel is a per-request thread under agent-chat
    directory = RequestThreadDirectory()
    binding = ApprovalBinding(
        ApprovalKind.MAIL_COMPOSE, ApprovalSurface.AGENT_CHAT_THREAD, REQUEST_THREAD_ID, 8,
    )

    # When / Then: the stored binding validates without any policy bump
    assert validate_stored_binding(binding, directory, OWNER_ID) == binding


# ------------------------------------------- live-thread reuse (2026-09-01, S6)
# 한 승인 키는 스레드 하나: 같은 키의 살아 있는 요청이 이미 연 요청별 스레드가 있으면 새 요청
# (같은 해시의 재요청·내용이 바뀐 대체)도 그 스레드로 간다. kind 스레드와 DM 은 재사용하지 않는다.

LIVE_REQUEST_THREAD_ID = "1601000000000000004"


class LiveRequest:
    def __init__(self, channel_id: str) -> None:
        self.channel_id = channel_id


def _reuse_directory() -> RequestThreadDirectory:
    directory = RequestThreadDirectory()
    directory.facts[LIVE_REQUEST_THREAD_ID] = ChannelFacts(
        11, "✉️ 메일 발신 · 세미나 안내", (), AGENT_CHAT_CHANNEL_ID,
    )
    return directory


def test_reuse_returns_the_live_request_thread_of_the_same_key() -> None:
    # Given: a live request of this key already sits in its own thread
    from automation.interop import approval_surface as module

    directory = _reuse_directory()

    # When: the producer asks before resolving a fresh binding
    binding = module.reuse_request_thread(
        ApprovalKind.MAIL_COMPOSE, [LiveRequest(LIVE_REQUEST_THREAD_ID)], directory, OWNER_ID,
    )

    # Then: the same thread is bound at the current policy and nothing new is opened
    assert binding == ApprovalBinding(
        ApprovalKind.MAIL_COMPOSE, ApprovalSurface.AGENT_CHAT_THREAD, LIVE_REQUEST_THREAD_ID, POLICY_VERSION,
    )
    assert "agent_chat_request_thread" not in directory.calls


def test_reuse_never_picks_the_legacy_kind_thread_or_a_dm() -> None:
    # Given: live requests that still sit on the per-kind thread and on the owner DM
    from automation.interop import approval_surface as module

    directory = _reuse_directory()

    # When / Then: neither is a per-request thread, so a fresh one must be opened
    assert module.reuse_request_thread(
        ApprovalKind.TODO, [LiveRequest(AGENT_CHAT_THREAD_ID), LiveRequest(DM_CHANNEL_ID)], directory, OWNER_ID,
    ) is None
    assert module.kind_thread_name(ApprovalKind.TODO) == "승인-todo"


def test_reuse_skips_unverifiable_or_empty_candidates_and_other_surfaces() -> None:
    from automation.interop import approval_surface as module

    directory = _reuse_directory()
    # Then: an unknown channel, a blank one and an empty set all yield None, fail-soft
    assert module.reuse_request_thread(
        ApprovalKind.WIKI, [LiveRequest(""), LiveRequest("9" * 18)], directory, OWNER_ID,
    ) is None
    assert module.reuse_request_thread(ApprovalKind.WIKI, [], directory, OWNER_ID) is None
    # And: a supply-chain kind never reuses anything (it is not on the agent-chat surface)
    assert module.reuse_request_thread(
        ApprovalKind.SKILL_DEPLOY, [LiveRequest(LIVE_REQUEST_THREAD_ID)], directory, OWNER_ID,
    ) is None
