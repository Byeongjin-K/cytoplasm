"""Characterization locks for the owner-approval surface (AS-0.2).

Every assertion pins CURRENT behaviour, never desired behaviour, so that a later
AS task moving a flow has to update this file deliberately — and that deliberate
diff is the machine-checked "before/after" the migration is judged against.

All six flows — mail, budget, patent-export, repair,
calendar+coordination+wiki and the skill supply chain — now resolve their surface
ONCE through ``automation/interop/approval_directory.py`` and PERSIST the whole
binding on the pending record; the locks below assert that, and assert the
post-time resolvers are gone.

All four migrating flows — mail reply, budget, patent-export and repair —
coordination and wiki. Only the skill supply chain still lands on the guild
``#approvals`` surface, because the peer attestation bot posts there too.
The binding is
decided once before the post and written down instead of being re-derived on
every later read.

This file owns the mail locks (a), (i), the per-flow intent channel sources
(b), (e), (f), (g) and patent (c). Three siblings hold the rest, split out so
every file stays under the 250 pure-LOC ceiling:
``test_approval_surface_records.py`` (persisted record field sets and the skill
supply chain, items (c)-(h)), ``test_approval_surface_inventory.py`` (the static
inventory of channel resolvers, owner-DM openers and env overrides, item (j)),
and the two uncollected helpers ``approval_characterization_fixtures`` (the fake
directory and the mail gate bootstrap) and ``approval_characterization_ast``
(the verbatim source readers).
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

# Load-bearing import order: the fixtures module puts ``skills/mail/scripts`` on
# ``sys.path``, which is what makes the bare ``triage_*`` imports below resolve.
from approval_characterization_fixtures import (
    AGENT_CHAT_THREAD_ID,
    BOUND_APPROVALS_CHANNEL_ID,
    OWNER_DM_CHANNEL_ID,
    OWNER_ID,
    _bind_mail,
    _BINDING_FIELDS,
    _FakeDirectory,
    _mail_draft,
)
from approval_characterization_ast import (
    _called_names,
    _channel_keyword_source,
    _definition,
    _package_string_constants,
    _return_sources,
)
from approval_conformance_inventory import _REPO

import triage_approval
import triage_confirm
import triage_gate
from automation.interop.approval_surface import (
    POLICY_VERSION,
    ApprovalBinding,
    ApprovalKind,
    ApprovalSurface,
    ApprovalSurfaceError,
    legacy_binding,
)
from automation.repair import repair_ops_binding


# ---------------------------------------------------------------- mail (a), (i)
@pytest.mark.parametrize(
    ("kind", "surface", "channel_id"),
    (
        ("reply", "agent-chat-thread", AGENT_CHAT_THREAD_ID),
        ("compose", "agent-chat-thread", AGENT_CHAT_THREAD_ID),
    ),
    ids=("reply", "compose"),
)
def test_mail_draft_resolves_its_surface_once_and_persists_the_binding(
    kind: str, surface: str, channel_id: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: an unposted draft whose record carries no binding yet.
    directory = _bind_mail(tmp_path, monkeypatch)
    draft = _mail_draft(kind)
    assert (draft["channel_id"], draft["surface"], draft["policy_version"]) == ("", None, None)

    # When: the approval intent is built — the one place the surface is decided.
    intent = triage_approval.confirm_intent(draft)

    # Then: it is a concrete Discord snowflake, never a sentinel or a blank...
    assert intent.channel_id == channel_id
    assert channel_id.isdigit() and channel_id not in {"", "dm", "approvals"}
    # ...resolved exactly once on the mandated surface and fact-checked before it is trusted...
    assert (directory.dm_calls, directory.approvals_calls, directory.thread_calls) == (0, 0, 1)
    assert directory.described == [channel_id]
    # ...and the WHOLE binding is persisted, so no later read re-resolves it.
    assert [
        tuple(record[name] for name in _BINDING_FIELDS) for record in triage_gate.list_drafts()
    ] == [(kind, surface, channel_id, POLICY_VERSION)]


@pytest.mark.parametrize(
    ("record", "expected", "thread_calls"),
    (
        ({"kind": "reply"}, AGENT_CHAT_THREAD_ID, 1),
        (
            {"kind": "reply", "surface": "skill-approvals",
             "channel_id": BOUND_APPROVALS_CHANNEL_ID, "policy_version": 1},
            BOUND_APPROVALS_CHANNEL_ID,
            0,
        ),
    ),
    ids=("unbound-record-is-resolved-not-inherited", "bound-record-wins"),
)
def test_mail_gate_reads_a_pending_record_channel_from_its_binding(
    record: dict, expected: str, thread_calls: int,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a fake directory with both approval surfaces and a stored v1 guild record.
    directory = _bind_mail(tmp_path, monkeypatch)

    # When: the gate reads the channel one outstanding request lives in.
    channel_id = triage_approval._request_channel_id(record)  # noqa: SLF001

    # Then: a stored binding wins outright; an unbound record resolves through the
    # shared directory and is fact-checked fail-closed.
    assert channel_id == expected
    assert directory.thread_calls == thread_calls
    assert directory.dm_calls == 0
    assert directory.described == [expected]


def test_mail_delete_message_requires_an_explicit_channel() -> None:
    # Given / When: the deletion primitive's signature and its body.
    signature = inspect.signature(triage_confirm.delete_message)
    node = _definition("skills/mail/scripts/triage_confirm.py", "delete_message")

    # Then: the caller MUST name the channel — no default, nothing to re-resolve.
    # This is what stops a superseded DM-bound draft being deleted out of
    # #approvals (SI-5): the body reaches Discord and nothing else.
    assert tuple(signature.parameters) == ("message_id", "channel_id")
    assert [item.default for item in signature.parameters.values()] == [inspect.Parameter.empty] * 2
    assert _called_names(node) == frozenset({"_api"})


# ------------------------------------------------- intent channel sources (b), (e), (f), (g)
def test_wiki_intent_takes_the_channel_from_its_resolved_binding() -> None:
    # Given / When / Then: the intent reads the binding it was HANDED, and the gate
    # helper it used to read the draft's raw channel field through now resolves —
    # and can ONLY resolve — through the wiki binding module, so a draft written
    # before the schema drains as legacy instead of being refused.
    assert (
        _channel_keyword_source("skills/wiki/scripts/wiki_approval.py", "confirm_intent")
        == "resolved.channel_id"
    )
    relative = "skills/wiki/scripts/wiki_gate.py"
    assert _return_sources(relative, "_draft_channel_id") == frozenset(
        {"wiki_binding.stored_binding(draft).channel_id"}
    )
    assert _called_names(_definition(relative, "_draft_channel_id")) == frozenset(
        {"stored_binding"}
    )


@pytest.mark.parametrize(
    ("relative", "kind"),
    (
        ("skills/calendar/scripts/calendar_approval.py", ApprovalKind.CALENDAR),
        ("skills/coordination/scripts/coordination_approval.py", ApprovalKind.COORDINATION),
    ),
    ids=("calendar", "coordination"),
)
def test_calendar_and_coordination_intents_carry_a_resolved_dm_channel(
    relative: str, kind: ApprovalKind
) -> None:
    # Given: a fake directory standing in for THIS bot's DM with the owner — a DM
    # channel id is only valid for the bot that opened it, so it is never shared.
    directory = _FakeDirectory()

    # When / Then: a new intent takes its channel off the binding it was handed, so
    # the "dm" sentinel is never written again — the record type itself refuses it.
    assert _channel_keyword_source(relative, "confirm_intent") == "resolved.channel_id"
    with pytest.raises(ApprovalSurfaceError):
        _ = ApprovalBinding(kind, ApprovalSurface.OWNER_DM, "dm", POLICY_VERSION)

    # ...yet a record still HOLDING it is read, not stranded: production carries 85
    # calendar confirmations bound to "dm", and each migrates to this bot's own DM
    # at the policy version it was written under, so no live approval is orphaned.
    migrated = legacy_binding(kind, "dm", directory, OWNER_ID)
    assert (migrated.kind, migrated.surface, migrated.channel_id, migrated.policy_version) == (
        kind, "owner-dm", OWNER_DM_CHANNEL_ID, 0,
    )
    assert (directory.dm_calls, directory.described) == (1, [OWNER_DM_CHANNEL_ID])


def test_repair_resolves_a_concrete_binding_instead_of_a_channel_literal() -> None:
    # Given: the ops repair bot's own fake directory — the gate never reaches Discord.
    directory = _FakeDirectory()

    # When: the one place repair decides where an approval request may live.
    binding = repair_ops_binding.new_binding(directory, OWNER_ID)

    # Then: a concrete snowflake on the agent-chat thread surface (v8, §10-7 —
    # the Ops bot is invited, the last DM approval surface is gone), resolved
    # exactly once and fact-checked before it is trusted...
    assert (binding.kind, binding.surface, binding.channel_id, binding.policy_version) == (
        "repair", "agent-chat-thread", AGENT_CHAT_THREAD_ID, POLICY_VERSION,
    )
    assert (directory.thread_calls, directory.approvals_calls, directory.dm_calls) == (1, 0, 0)
    assert directory.described == [AGENT_CHAT_THREAD_ID]
    # ...and the `APPROVALS_CHANNEL: Final = "approvals"` constant the gate used to
    # post with is gone, with no string left anywhere in the repair package that
    # could stand in for a channel id — so the ops bot opens its own owner DM rather
    # than addressing a guild channel it cannot even see.
    gate = (_REPO / "automation/repair/repair_ops_approval_gate.py").read_text(encoding="utf-8")
    assert "APPROVALS_CHANNEL" not in gate
    assert _package_string_constants("automation/repair") & {"approvals", "dm"} == frozenset()


# ------------------------------------------------------------------ patent (c)
def test_patent_reaction_state_takes_its_channel_from_the_manifest_binding() -> None:
    # Given: the reaction-state reader and the binding reader it delegates to.
    relative = "skills/patent-prep/scripts/patent_export_gate.py"
    node = _definition(relative, "reaction_state")

    # When: the manifest attributes it touches are collected.
    manifest_attributes = frozenset(
        child.attr
        for child in ast.walk(node)
        if isinstance(child, ast.Attribute) and isinstance(child.value, ast.Name)
        and child.value.id == "manifest"
    )

    # Then: no resolver is reachable from this path — the channel comes from the
    # manifest's OWN stored binding, so the poll reads the channel the approval
    # message actually lives in, and only the message id is read off the manifest.
    assert _called_names(node) == frozenset({
        "ExportGateError", "_binding_channel_id", "_owner_reacted", "_reaction_users",
        "approval_binding_matches", "approval_message_content", "owner_id",
    })
    assert _called_names(_definition(relative, "_binding_channel_id")) == frozenset(
        {"stored_binding"}
    )
    assert manifest_attributes == frozenset({"message_id"})
