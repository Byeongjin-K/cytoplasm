"""People/handling-rule moves out of USER.md under one approval card (owner decision Q138 ②).

New file: the existing relocation tests pin the single-entry OPS_REFERENCE path; these cover the
person note and MEMORY.md destinations and the batch card that approves them together.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from automation.memory_curator.binding import entry_digest
from automation.memory_curator.classify_model import EntryVerdict
from automation.memory_curator.curator import parse_memory_file
from automation.memory_curator.deletion import MemoryCapError, append_entry
from automation.memory_relocate.apply import ApplyDeps, apply_relocation
from automation.memory_relocate.approval_gate import approval_hash, approval_key
from automation.memory_relocate.binding import (
    RelocationHashFields,
    batch_action_hash,
    relocation_action_hash,
)
from automation.memory_relocate.discover import select_batch
from automation.memory_relocate.model import RelocationRecord, RelocationState, parse_state, record_key, serialize_state
from automation.memory_relocate.plan import build_relocation_plan
from automation.memory_relocate.propose import build_batch, build_proposed_record, card_fits
from automation.memory_relocate.render import prepare_batch_card
from automation.memory_relocate.watch_step import ResolveEffects, resolve_tick

NOW = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
PERSON = "김가상의 호칭은 \u2018선임님\u2019이다."
RULE = "할 일 등록 요청은 원격 할 일 목록을 뜻하며 기본 목록은 '오늘할일'이다."
OWNER = "사용자는 가상연구소의 연구원이다."


def _record(text: str, destination: str | None, kind: str = "user") -> RelocationRecord:
    return build_proposed_record(
        text, source_kind=kind, entry_sha256=entry_digest(kind, text), reclaimable_chars=len(text),
        binding_kind="obsidian-write", binding_surface="owner-dm", binding_channel_id="",
        binding_policy_version=8, now=NOW, destination=destination,
    )


def _verdict(text: str, route: str, kind: str = "user") -> EntryVerdict:
    return EntryVerdict(kind, text, route, text[:10], "r", None, True)


def test_destinations_plan_a_person_note_or_memory_md_and_bind_into_the_hash() -> None:
    person = build_relocation_plan(PERSON, source_kind="user", destination="person")
    native = build_relocation_plan(RULE, source_kind="user", destination="memory")
    assert person.note_plan.relpath.name.startswith("person--")
    assert "사람 정보" in person.note_plan.body and PERSON in person.note_plan.body
    assert native.note_plan.relpath.as_posix() == "MEMORY.md"
    fields = RelocationHashFields("user", "d", "MEMORY.md", native.note_plan_sha256)
    assert relocation_action_hash(replace(fields, destination="memory")) != relocation_action_hash(fields)


def test_select_batch_takes_people_and_user_rules_only() -> None:
    files = {
        "user": parse_memory_file("\n§\n".join((OWNER, PERSON, RULE)), kind="user"),
        "memory": parse_memory_file(RULE, kind="memory"),
    }
    verdicts = [
        _verdict(OWNER, "KEEP_NATIVE"),
        _verdict(PERSON, "PERSON_REFERENCE"),
        _verdict(RULE, "AGENT_RULE"),
        _verdict(RULE, "AGENT_RULE", kind="memory"),
    ]
    found = select_batch(verdicts, files, frozenset())
    assert [(c.candidate.entry_text, c.destination) for c in found] == [(PERSON, "person"), (RULE, "memory")]
    known = frozenset({entry_digest("user", PERSON)})
    assert [c.candidate.entry_text for c in select_batch(verdicts, files, known)] == [RULE]


def test_one_batch_hash_binds_exactly_the_members_and_the_card_shows_it() -> None:
    members = [(_record(PERSON, "person"), PERSON), (_record(RULE, "memory"), RULE)]
    records = build_batch(members, fits=card_fits)
    assert {r.batch_hash for r in records} == {batch_action_hash([r.action_hash for r, _ in members])}
    version, card = prepare_batch_card(list(zip(records, (PERSON, RULE), strict=True)))
    assert version == "mc-reloc-batch-render-v1"
    assert records[0].batch_hash in card and PERSON in card and RULE in card and "MEMORY.md" in card
    assert approval_key(records[0]) == approval_key(records[1]) == f"batch:{records[0].batch_hash}"
    assert approval_hash(records[0]) == records[0].batch_hash


def test_a_batch_that_would_overflow_one_card_keeps_the_prefix_that_fits() -> None:
    long_rules = [f"{RULE} {index} " + "가" * 600 for index in range(4)]
    members = [(_record(text, "memory"), text) for text in long_rules]
    records = build_batch(members, fits=card_fits)
    assert 0 < len(records) < len(members)
    assert all(r.batch_hash == records[0].batch_hash for r in records)


def test_batch_fields_round_trip_and_unknown_destinations_fail_closed() -> None:
    record = build_batch([(_record(PERSON, "person"), PERSON)], fits=card_fits)[0]
    state = RelocationState(1, {record_key(record.source_kind, record.entry_sha256): record})
    assert parse_state(serialize_state(state)) == state
    raw = serialize_state(state)
    next(iter(raw["relocations"].values()))["destination"] = "elsewhere"  # type: ignore[union-attr,index]
    with pytest.raises(ValueError, match="destination"):
        _ = parse_state(raw)


def test_one_card_posts_once_and_one_reaction_moves_every_member() -> None:
    records = build_batch(
        [(_record(PERSON, "person"), PERSON), (_record(RULE, "memory"), RULE), (_record(OWNER, "person"), OWNER)],
        fits=card_fits,
    )
    state = RelocationState(1, {record_key(r.source_kind, r.entry_sha256): r for r in records})
    posts: list[str] = []
    probes: list[str] = []
    effects = ResolveEffects(
        post_approval=lambda record: posts.append(record.entry_sha256) or ("m1", "c1"),
        probe_reaction=lambda record: probes.append(record.entry_sha256) or "approved",
        write_obsidian=lambda record: None,
        verify_rag=lambda record: False,
        apply_delete=lambda record: pytest.fail("nothing is written yet"),
        now=NOW,
    )
    posted = resolve_tick(state, effects=effects, max_posts=1)
    assert len(posts) == 1
    assert {(r.status, r.message_id) for r in posted.state.relocations.values()} == {("posted", "m1")}
    approved = resolve_tick(posted.state, effects=effects, max_posts=1)
    assert len(probes) == 1
    assert {r.status for r in approved.state.relocations.values()} == {"approved"}


def test_a_cancelled_batch_abandons_every_member() -> None:
    records = build_batch([(_record(PERSON, "person"), PERSON), (_record(RULE, "memory"), RULE)], fits=card_fits)
    state = RelocationState(1, {
        record_key(r.source_kind, r.entry_sha256): replace(r, status="posted", message_id="m1", channel_id="c1")
        for r in records
    })
    effects = ResolveEffects(lambda r: None, lambda r: "cancelled", lambda r: None, lambda r: False,
                             lambda r: pytest.fail("cancelled"), NOW)
    result = resolve_tick(state, effects=effects)
    assert {r.status for r in result.state.relocations.values()} == {"abandoned"}


def _memory_dir(tmp_path: Path, user: str, memory: str) -> Path:
    _ = (tmp_path / "USER.md").write_text(user, encoding="utf-8")
    _ = (tmp_path / "MEMORY.md").write_text(memory, encoding="utf-8")
    return tmp_path


def test_append_entry_is_idempotent_backs_up_and_respects_the_cap(tmp_path: Path) -> None:
    memory_dir = _memory_dir(tmp_path, OWNER, "기존 운영 사실 하나.")
    outcome = append_entry(memory_dir, "memory", RULE, cap=3300, now=NOW)
    assert outcome.appended and outcome.backup_path is not None and outcome.backup_path.exists()
    assert (tmp_path / "MEMORY.md").read_text(encoding="utf-8") == f"기존 운영 사실 하나.\n§\n{RULE}"
    assert not append_entry(memory_dir, "memory", RULE, cap=3300, now=NOW).appended
    with pytest.raises(MemoryCapError):
        _ = append_entry(memory_dir, "memory", "x" * 40, cap=60, now=NOW)


def test_a_rule_leaves_user_md_only_after_memory_md_holds_it(tmp_path: Path) -> None:
    memory_dir = _memory_dir(tmp_path, f"{OWNER}\n§\n{RULE}", "기존 운영 사실 하나.")
    record = replace(_record(RULE, "memory"), status="written")

    def in_memory(_relpath: str) -> bytes | None:
        entries = parse_memory_file((tmp_path / "MEMORY.md").read_text(encoding="utf-8"), kind="memory").entries
        return RULE.encode() if any(entry.text == RULE for entry in entries) else None

    def rehash(current: RelocationRecord) -> str:
        return relocation_action_hash(RelocationHashFields(
            current.source_kind, current.entry_sha256, current.note_relpath, current.note_plan_sha256, current.destination,
        ))

    deps = ApplyDeps(memory_dir, in_memory, lambda _r, _b: in_memory(_r) is not None, rehash, NOW)
    assert apply_relocation(record, "", deps=deps).reason == "obsidian_not_written"
    assert RULE in (tmp_path / "USER.md").read_text(encoding="utf-8")
    _ = append_entry(memory_dir, "memory", RULE, cap=3300, now=NOW)
    outcome = apply_relocation(record, "", deps=deps)
    assert outcome.deleted
    assert (tmp_path / "USER.md").read_text(encoding="utf-8") == OWNER
    assert RULE in (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
