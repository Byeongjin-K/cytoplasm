"""Static inventory locks for the owner-approval surface (AS-0.2, item (j)).

Split out of ``test_approval_surface_characterization.py`` to keep both files
small. The sets below are what a full AST scan of the deployed tree
(``skills/*/scripts/*.py`` + ``automation/**/*.py``) actually finds today, so a
newly added resolver, DM opener, or env override anywhere fails these tests. The
walk and the source glob are the shared ones in ``approval_conformance_ast``
(AS-1.11); this file keeps no private copy of either.

AS-1.x migrated EVERY approval flow onto the shared directory
``automation/interop/approval_directory.py``. Each one now reaches it through its
own binding adapter — ``triage_binding`` /
``patent_export_binding._directory`` /
``repair_ops_binding`` / ``calendar_binding`` / ``coordination_binding`` /
``wiki_binding`` / ``skill_gate_surface`` — so no flow defines an
``approvals_channel_id`` resolver or opens the owner DM itself any more. AS-3.2
then removed the five per-flow env overrides those adapters used to carry.

approvals-channel resolver definitions (0 — ``DiscordChannelDirectory``'s
``skill_approvals`` replaced all three; ``skill_gate`` now declares a kind and
``peer_attest`` is handed the channel its deploy request already resolved):
  (none)

owner-DM openers, i.e. sites naming ``/users/@me/channels`` (1 — ON-2, 2026-08-30,
migrated every notice sender onto the ``automation/owner_notice.py`` facade, which
resolves its DM through the central directory; the facade owns the destination —
configured ``owner_notice_channel_id`` or owner DM — so a NEW opener anywhere in
the deployed tree is a conformance failure (ON-3), not an inventory update):
  automation/interop/approval_directory.py:27           <module>  (AS-1.2 central directory)

``*_APPROVALS_CHANNEL_ID`` env overrides (0 — AS-3.2 retired every one; the
compatibility source that survives is the `personal_approvals_channel_id` config
key read inside the shared directory, which is not an env var and is not per-flow):
  (none)
"""
from __future__ import annotations

import ast
from typing import Final

from approval_conformance_ast import _deployed_sources, _qualnames
from approval_conformance_inventory import _REPO

_DM_PATH: Final = "/users/@me/channels"
_DIRECTORY: Final = "automation/interop/approval_directory.py"

# Empty on purpose: the shared directory's resolver is named for the SURFACE it
# serves (``DiscordChannelDirectory.skill_approvals``), so this scan finds it only
# if a flow re-introduces a channel-id resolver of its own.
_KNOWN_RESOLVERS: Final = frozenset()

# ON-3 conformance (2026-08-30): this set IS the registered-exception list. A new
# `/users/@me/channels` site anywhere in the deployed tree fails the lock below —
# notices go through `automation.owner_notice` (destination: configured
# `owner_notice_channel_id`, else owner DM), never through a fresh DM opener.
_KNOWN_DM_OPENERS: Final = frozenset({
    # The central directory (AS-1.2). Every flow — including the owner_notice
    # facade's DM fallback — resolves through it, so this is the only opener an
    # approval or a notice can reach.
    f"{_DIRECTORY}::<module>",
})

# Empty on purpose: AS-3.2 removed the env branch from the shared directory, so no
# adapter has one to carry. A re-introduced override lands here and fails the lock
# below — together with the conformance tripwire
# `test_no_flow_specific_approvals_env_var_is_read`.
_KNOWN_ENV_OVERRIDES: Final = frozenset()


def _scan_source(
    relative: str,
    tree: ast.Module,
) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """(resolver defs, owner-DM opener sites, env overrides) found in ONE parsed source."""
    resolvers: set[str] = set()
    openers: set[str] = set()
    overrides: set[str] = set()
    scopes: list[tuple[str, ast.AST]] = [
        (qualname, node)
        for qualname, node in _qualnames(tree).items()
        if not isinstance(node, ast.ClassDef)
    ]
    # A module-level constant belongs to no function, so scanning only function
    # scopes would let any caller dodge this inventory by hoisting the path into
    # a module Final. Scan the module body itself as the "<module>" scope.
    scopes.extend(
        ("<module>", statement)
        for statement in tree.body
        if not isinstance(statement, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    )
    for qualname, node in scopes:
        if qualname.split(".")[-1].endswith("approvals_channel_id"):
            resolvers.add(f"{relative}::{qualname}")
        for child in ast.walk(node):
            if not isinstance(child, ast.Constant) or not isinstance(child.value, str):
                continue
            if _DM_PATH in child.value:
                openers.add(f"{relative}::{qualname}")
            if child.value.endswith("_APPROVALS_CHANNEL_ID"):
                overrides.add(f"{relative}::{child.value}")
    return frozenset(resolvers), frozenset(openers), frozenset(overrides)


def _scan() -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """The same scan unioned over the whole deployed tree."""
    resolvers: set[str] = set()
    openers: set[str] = set()
    overrides: set[str] = set()
    for path in _deployed_sources():
        relative = str(path.relative_to(_REPO))
        found_resolvers, found_openers, found_overrides = _scan_source(
            relative, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        )
        resolvers |= found_resolvers
        openers |= found_openers
        overrides |= found_overrides
    return frozenset(resolvers), frozenset(openers), frozenset(overrides)


def test_no_flow_defines_its_own_approvals_channel_resolver() -> None:
    # Given / When: every `*approvals_channel_id` definition in the deployed tree.
    resolvers, _, _ = _scan()

    # Then: SI-2 is complete — not one flow resolves an approvals channel itself,
    # so putting one back has to be a deliberate diff against this lock. The single
    # surviving resolver is the shared directory's own method, which is named for
    # the SURFACE it serves rather than for a channel id and is reached only through
    # a binding adapter — never re-implemented per flow.
    assert resolvers == _KNOWN_RESOLVERS
    assert "DiscordChannelDirectory.skill_approvals" in _qualnames(
        ast.parse((_REPO / _DIRECTORY).read_text(encoding="utf-8"), filename=_DIRECTORY)
    )


def test_owner_dm_openers_are_only_the_directory_and_non_approval_senders_today() -> None:
    # Given / When: every function naming the DM-open REST path.
    _, openers, _ = _scan()

    # Then: SI-2 collapsed every approval flow into that one directory, and ON-2
    # (2026-08-30) collapsed every notice sender into the owner_notice facade,
    # which resolves its DM through the directory. What is left is the directory
    # facade — a NEW opener anywhere is an ON-3 conformance failure, not an
    # inventory update.
    assert openers == _KNOWN_DM_OPENERS
    assert f"{_DIRECTORY}::<module>" in openers
    assert len(openers) == 1


def test_no_flow_reads_an_approvals_channel_env_override_after_r3() -> None:
    # Given / When: every `*_APPROVALS_CHANNEL_ID` name read in the deployed tree.
    _, _, overrides = _scan()

    # Then: AS-3.2 retired all five compatibility branches, so nothing in the
    # deployed tree names one any more — the only surviving compatibility source is
    # the `personal_approvals_channel_id` config key the shared directory reads.
    assert overrides == _KNOWN_ENV_OVERRIDES
    assert overrides == frozenset()

    # And: that emptiness is a fact about the tree, not a scanner that stopped
    # looking. The same scan still reports a re-introduced override, so this lock
    # cannot rot into a no-op.
    reintroduced = "skills/demo/scripts/demo_binding.py"
    _, _, regression = _scan_source(
        reintroduced, ast.parse('OVERRIDE: Final = "DEMO_APPROVALS_CHANNEL_ID"\n')
    )
    assert regression == frozenset({f"{reintroduced}::DEMO_APPROVALS_CHANNEL_ID"})
