"""Run intentional runtime mutations in isolated pytest children; source files never change.

Run: CUDA_VISIBLE_DEVICES= python3 tests/mutations/relocate_per_line_mutations.py
Each subprocess must exit 1 (a failed test); an import/setup error does not count.
"""
from __future__ import annotations

import os
import json
import subprocess
import sys
from dataclasses import replace
from collections.abc import Callable
from pathlib import Path

import pytest

_PATCH = pytest.MonkeyPatch()
_LINE = "tests/unit/test_memory_relocate_per_line.py::"
_MIGRATION = "tests/unit/test_memory_relocate_line_migration.py::"
_APPLY = "tests/unit/test_memory_relocate_apply.py::"
MUTATIONS = {
    "move": _LINE + "test_single_owner_reaction_decides_immediately",
    "keep": _LINE + "test_single_owner_reaction_decides_immediately",
    "delete": _LINE + "test_single_owner_reaction_decides_immediately",
    "conflict": _LINE + "test_two_owner_reactions_keep",
    "nonowner": _LINE + "test_nonowner_bot_or_absent_reaction_stays_pending",
    "bot": _LINE + "test_nonowner_bot_or_absent_reaction_stays_pending",
    "text": _LINE + "test_unverifiable_line_stays_pending[text]",
    "unreadable": _LINE + "test_unverifiable_line_stays_pending[missing]",
    "message-id": _LINE + "test_unverifiable_line_stays_pending[identity]",
    "freeze": _LINE + "test_removed_before_freeze_waits_and_frozen_choice_survives_restart",
    "peer-delete": _LINE + "test_line_delete_uses_tokenless_peer_and_backs_up_both_accounts",
    "exact-render": _LINE + "test_new_renderer_preserves_exact_multiline_text_and_destination",
    "seeds": _MIGRATION + "test_unreacted_batches_retire_and_repost_all_eleven_lines",
    "pacing": _MIGRATION + "test_unreacted_batches_retire_and_repost_all_eleven_lines",
    "migration": _MIGRATION + "test_unreacted_batches_retire_and_repost_all_eleven_lines",
    "reacted-migration": _MIGRATION + "test_migration_leaves_any_owner_reacted_batch_alone",
    "shared-thread": _MIGRATION + "test_new_line_posts_use_the_existing_lifecycle_and_share_one_request_thread",
    "manifest-hash": _LINE + "test_manifest_text_hash_mismatch_refuses_before_effect",
    "gate-owner": _APPLY + "test_apply_when_status_is_not_approved_blocks_before_other_gates",
    "gate-hash": _APPLY + "test_apply_when_action_hash_drifted_blocks_before_write_proof",
    "gate-destination": _APPLY + "test_apply_when_obsidian_note_is_absent_blocks_before_rag",
    "gate-rag": _APPLY + "test_apply_when_rag_is_not_ingested_blocks_before_native_read",
    "gate-native": _APPLY + "test_apply_when_native_entry_was_edited_blocks_as_absent",
    "missing-summary": _MIGRATION + "test_migration_without_summary_still_reads_each_legacy_owner_reaction",
    "removed-during-read": _LINE + "test_reaction_removed_during_snapshot_is_not_frozen",
}


def pytest_configure() -> None:
    """Inject one broken behavior before the actual regression test runs."""
    name = os.environ.get("MEMORY_LINE_MUTATION")
    if name is None:
        return
    from automation.memory_relocate import choice_agent, choice_verify, line_choices, line_migration, choice_snapshot
    from automation.memory_relocate.choices import Choice, OPTIONS, owner_reacted

    if name in ("move", "keep", "delete", "conflict"):
        original = line_choices.read_choice
        def wrong_choice(read: Callable[[str], tuple[tuple[str, bool], ...]], owner: str) -> Choice | None:
            choice = original(read, owner)
            if name == "conflict":
                selected = [option for emoji, option in zip(line_choices.REACTIONS, OPTIONS, strict=True)
                            if owner_reacted(read(emoji), owner)]
                return selected[0] if selected else None
            if choice is not None and choice.value == name:
                return Choice.KEEP if choice is not Choice.KEEP else Choice.MOVE
            return choice
        _PATCH.setattr(choice_verify, "read_choice", wrong_choice)
    elif name == "nonowner":
        _PATCH.setattr(line_choices, "owner_reacted", lambda users, owner: any(not bot for _, bot in users))
    elif name == "bot":
        _PATCH.setattr(line_choices, "owner_reacted", lambda users, owner: any(user == owner for user, _ in users))
    elif name in ("text", "message-id", "unreadable"):
        _PATCH.setattr(choice_verify.Verifier, "verify_card", lambda self, request: None)
        if name == "unreadable":
            from relocate_line_fakes import LinesTransport
            _PATCH.setattr(LinesTransport, "add_reaction",
                           lambda self, channel, message, emoji: self.seeds.append((message, emoji)))
    elif name == "freeze":
        _PATCH.setattr(choice_snapshot.SnapshotStore, "load", lambda self, request: None)
    elif name == "removed-during-read":
        original_choice = choice_verify.read_choice
        observed = []
        def stale_choice(read, owner):
            if not observed:
                observed.append(original_choice(read, owner))
            return observed[0]
        _PATCH.setattr(choice_verify, "read_choice", stale_choice)
    elif name == "peer-delete":
        from automation.memory_relocate.apply import ApplyOutcome
        _PATCH.setattr(choice_agent.AgentChoices, "delete",
                       lambda self, record: ApplyOutcome(True, None, None, 0))
    elif name == "exact-render":
        from automation.interop import owner_message
        original_render = owner_message.render
        _PATCH.setattr(owner_message, "render", lambda message, *, destination: original_render(
            replace(message, fact="\n".join(s.rstrip() for s in message.fact.split("\n"))),
            destination=destination,
        ))
    elif name == "seeds":
        from automation.memory_relocate import approval_gate
        _PATCH.setattr(approval_gate, "LINE_REACTIONS", ("➡️", "📌", "🗑️", "✅"))
    elif name == "pacing":
        from automation.memory_relocate.cron import memory_relocate_watch
        original_resolve = memory_relocate_watch.resolve_tick
        _PATCH.setattr(memory_relocate_watch, "resolve_tick",
                       lambda state, *, effects, max_posts: original_resolve(state, effects=effects, max_posts=1))
    elif name == "migration":
        _PATCH.setattr(line_migration.Migration, "run", lambda self, state: state)
    elif name == "reacted-migration":
        _PATCH.setattr(line_migration.Migration, "untouched", lambda self, pairs: True)
    elif name == "missing-summary":
        original_untouched = line_migration.Migration.untouched
        def trust_summary(self, pairs):
            record = pairs[0][0]
            raw = self.transport.api("GET", f"/channels/{record.channel_id}/messages/{record.message_id}")
            return True if "reactions" not in raw else original_untouched(self, pairs)
        _PATCH.setattr(line_migration.Migration, "untouched", trust_summary)
    elif name == "shared-thread":
        from automation.memory_relocate import effects_live
        _PATCH.setattr(effects_live, "reuse_request_thread", lambda *args: None)
    elif name == "manifest-hash":
        from automation.memory_relocate import choice_request
        from automation.memory_relocate.model import parse_state
        def unchecked(document: str) -> choice_request.ChoiceRequest:
            raw = json.loads(document)
            records = tuple(parse_state(raw["state"]).relocations.values())
            return choice_request.ChoiceRequest(tuple(zip(records, raw["texts"], strict=True)))
        _PATCH.setattr(choice_request, "parse_request", unchecked)
    elif name.startswith("gate-"):
        from automation.memory_relocate import apply
        from automation.memory_curator.binding import entry_digest
        from automation.memory_curator.watch_steps import read_native
        import test_memory_relocate_apply as gate_tests
        original_apply = apply.apply_relocation
        def bypass(record, body, *, deps):
            if name == "gate-owner":
                record = replace(record, status="approved")
            elif name == "gate-hash":
                deps = replace(deps, recompute_action_hash=lambda r: r.action_hash)
            elif name == "gate-destination":
                deps = replace(deps, read_twin=lambda path: gate_tests.NOTE_BYTES)
            elif name == "gate-rag":
                deps = replace(deps, verify_rag=lambda path, text: True)
            elif name == "gate-native":
                _, memory = read_native(deps.memory_dir, record.source_kind)
                record = replace(record, entry_sha256=entry_digest(record.source_kind, memory.entries[0].text))
            return original_apply(record, body, deps=deps)
        _PATCH.setattr(apply, "apply_relocation", bypass)
        _PATCH.setattr(gate_tests, "apply_relocation", bypass)
    else:
        raise RuntimeError(f"Unknown mutation {name}")


def pytest_unconfigure() -> None:
    _PATCH.undo()


def main() -> int:
    """Require each mutation to make its regression red, then discard the child process."""
    root = Path(__file__).resolve().parents[2]
    selected = sys.argv[1:] or list(MUTATIONS)
    for name in selected:
        target = MUTATIONS[name]
        environment = dict(os.environ, CUDA_VISIBLE_DEVICES="", MEMORY_LINE_MUTATION=name)
        environment["PYTHONPATH"] = os.pathsep.join((
            str(root), str(root / "tests/unit"), str(root / "tests/mutations"),
        ))
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "relocate_per_line_mutations", target],
            cwd=root, env=environment, text=True, capture_output=True, check=False,
        )
        print(result.stdout, end="")
        if result.returncode != 1 or "FAILED " not in result.stdout or "ERROR " in result.stdout:
            print(result.stderr)
            print(f"MUTATION {name}: NOT PROVEN exit={result.returncode}")
            return 1
        print(f"MUTATION {name}: RED exit=1 RESTORED (isolated process)")
    print(f"MUTATIONS PASS {len(selected)}/{len(selected)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
