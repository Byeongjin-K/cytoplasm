"""Append-only card versions; separate file keeps FS3 replay evidence unchanged."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from automation import release_card, skill_gate, skill_gate_publish, skill_gate_request
from automation.interop import owner_message
from automation.managed_skills import submission_message as wire
from automation.managed_skills.submission_errors import SubmissionArtifactError
from automation.release_spec import ReleaseSpecError, spec_from_record
from automation.repair import repair_approval_render
from automation.repair.repair_approval_content import approval_content_matches
from automation.repair.repair_ops_pending import PendingApprovalError, PendingRepairApprovalStore
from automation.repair.repair_patch_binding import PatchFileDelta
from automation.skill_gate_specs import DeploySpec, Provenance, PublishSpec
from automation.stored_content import hash_parts
from tests.unit.test_release_approval import _binding, _spec
from tests.unit.test_repair_pending_v2_schema import _v2
from tests.unit.test_submission_message import _STORED_V2, stored_v1
from tests.unit.test_skill_gate_owner_message import install


def _supply() -> tuple[DeploySpec, PublishSpec]:
    return (
        DeploySpec("wiki", "a" * 64, "b" * 32, "- review: PASS",
                   Provenance("", "", ""), skill_gate._REQUEST_BINDING),
        PublishSpec("managed-wiki", "a" * 64, "c" * 64, "v1", "b" * 32,
                    skill_gate_publish._PUBLISH_BINDING),
    )


def test_release_v6_record_accepts_exact_bytes_without_renderer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = replace(_spec(), render_version=6, posted_text="posted-release-v6")
    record = spec.new_record("123", _binding())
    replay = spec_from_record(record)
    monkeypatch.setattr(owner_message, "render", _unavailable)
    assert replay.render_version == 6
    assert replay.bound(spec.posted_text, record)
    assert not replay.bound(spec.posted_text + "!", record)
    assert not replay.bound(spec.posted_text, {**record, "render_version": "9"})
    assert not replay.bound(spec.posted_text, {k: v for k, v in record.items() if k != "content_sha256"})
    assert replay.action_hash() == replace(spec, render_version=1).action_hash()


@pytest.mark.parametrize("spec", _supply(), ids=["deploy", "publish"])
def test_supply_v3_accepts_bound_wire_and_refuses_unknown_versions(
    spec: DeploySpec | PublishSpec, monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = spec.render_v1()
    posted = replace(spec, render_version=3, posted_text=content)
    record = posted.new_record("123", _binding())
    monkeypatch.setattr(owner_message, "render", _unavailable)
    assert posted.bound(content, record)
    assert not posted.bound(content + "!", record)
    assert not posted.bound(content, {**record, "render_version": "4"})
    assert not posted.bound(content, {**record, "content_sha256": ""})
    altered = content.replace("a" * 64, "d" * 64)
    assert not posted.bound(altered, {**record, "content_sha256": hash_parts(altered)})
    assert posted.action_hash() == spec.action_hash()


def test_repair_v4_persists_and_verifies_without_rendering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft = replace(_v2(), render_version=4)
    content = repair_approval_render.approval_request_content(draft)
    record = replace(draft, content_sha256=hash_parts(content))
    store = PendingRepairApprovalStore(tmp_path / "pending")
    store.save(record)
    replay = store.get(record.ticket_id)
    assert replay == record
    monkeypatch.setattr(owner_message, "render", _unavailable)
    assert approval_content_matches(record, content)
    assert not approval_content_matches(record, content + "!")
    assert not approval_content_matches(replace(record, content_sha256=None), content)
    assert not approval_content_matches(replace(record, nonce="changed"), content)
    assert record.action_hash == _v2().action_hash


@pytest.mark.parametrize("version", [True, 1, 5, "4", 4.0])
def test_repair_decoder_keeps_unknown_version_rejection(version: str | int | float) -> None:
    import json
    from tests.unit.test_repair_pending_v2_schema import _legacy

    record = _legacy()
    payload = {
        "ticket_id": record.ticket_id, "patch_name": record.patch_name,
        "action_hash": record.action_hash, "nonce": record.nonce,
        "message_id": record.message_id, "created_at": record.created_at.isoformat(),
        "render_version": version,
    }
    with pytest.raises(PendingApprovalError):
        _ = PendingRepairApprovalStore._decode(json.dumps(payload))


def _unavailable(message: owner_message.OwnerMessage, *, destination: owner_message.Ref) -> str:
    del message, destination
    raise owner_message.OwnerMessageError(detail="test.renderer-unavailable")


def test_new_release_selects_v7_and_owner_v2() -> None:
    selected, error = release_card.card_for_new_request(_spec())
    assert not error and selected is not None
    assert selected.render_version == 8


def test_new_submission_v3_roundtrips_without_renderer(monkeypatch: pytest.MonkeyPatch) -> None:
    envelope = wire.parse_submission_message(_STORED_V2)
    content = wire.render_submission_message(envelope)
    body, separator, footer = content.rpartition("\n")
    assert separator and footer.startswith("-# [personal-skill-submission-v3] ")
    assert body.startswith("**🔔 managed-x**\n")
    # The immutable JSON is moved, not recreated with different fields or spacing.
    assert footer.removeprefix("-# [personal-skill-submission-v3] ") == (
        _STORED_V2.partition("\n")[0].removeprefix("[personal-skill-submission-v2] ")
    )
    monkeypatch.setattr(owner_message, "render", _unavailable)
    assert wire.parse_submission_message(content) == envelope
    assert wire.parse_submission_message(_STORED_V2) == envelope
    assert wire.parse_submission_message(stored_v1(envelope)) == envelope
    for altered in (content + "!", content.replace("submission-v3", "submission-v4"),
                    content.replace('"skill":"managed-x"', '"skill":"managed-y"'),
                    content.replace("group-a ·", "group-b ·"),
                    footer.removeprefix("-# ") + "\n" + body,
                    content + "\n" + footer, content + "\n",
                    body + "\n" + footer.removeprefix("-# ")):
        with pytest.raises(SubmissionArtifactError):
            _ = wire.parse_submission_message(altered)


def test_release_keeps_unknown_version_rejection() -> None:
    with pytest.raises(ReleaseSpecError):
        _ = replace(_spec(), render_version=9)


def test_new_repair_v4_renders_owner_v2() -> None:
    content = repair_approval_render.approval_request_content(replace(_v2(), render_version=4))
    assert len(content) <= 1900


@pytest.mark.parametrize("path", ["notes/a b.md", "notes/a\nb.md", "notes/a\tb.md"])
def test_repair_v4_wire_keeps_embedded_whitespace_inside_one_file(path: str) -> None:
    draft = replace(_v2(), render_version=4, changes=(PatchFileDelta(None, path, 1, 0),))
    content = repair_approval_render.approval_request_content(draft)
    record = replace(draft, content_sha256=hash_parts(content))
    assert approval_content_matches(record, content)


def test_repair_v4_wire_retains_omission_totals() -> None:
    draft = replace(_v2(), render_version=4, changes=tuple(
        PatchFileDelta(None, f"file-{index}.py", 1, 0) for index in range(11)
    ))
    content = repair_approval_render.approval_request_content(draft)
    assert approval_content_matches(replace(draft, content_sha256=hash_parts(content)), content)


def test_owner_v2_unavailable_selects_previous_card_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = owner_message.render

    def old_runtime(message: owner_message.OwnerMessage, *, destination: owner_message.Ref) -> str:
        if message.render_version == "owner-ko-v2":
            raise owner_message.OwnerMessageError(detail="test.old-runtime")
        return original(message, destination=destination)

    monkeypatch.setattr(owner_message, "render", old_runtime)
    selected, error = release_card.card_for_new_request(_spec())
    assert not error and selected is not None and selected.render_version == 4
    envelope = wire.parse_submission_message(_STORED_V2)
    assert wire.render_submission_message(envelope) == _STORED_V2


def test_supply_final_render_failure_keeps_refused_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    fake, args, _, make_gate = install(tmp_path, monkeypatch, "deploy")

    def refused(spec: DeploySpec) -> str:
        del spec
        raise ValueError("synthetic-render-refusal")

    monkeypatch.setattr(DeploySpec, "render", refused)
    result = skill_gate_request._prepare_card(make_gate(args))
    assert isinstance(result, skill_gate_request.Requested) and result.exit_code == 6
    assert fake.calls == []
    assert capsys.readouterr().err.splitlines() == [
        "APPROVAL-RENDER-FALLBACK: ValueError",
        "APPROVAL-RENDER-FALLBACK: ValueError",
        "APPROVAL-RENDER-REFUSED: ValueError",
    ]
