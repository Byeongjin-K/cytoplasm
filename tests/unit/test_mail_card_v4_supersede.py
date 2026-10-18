"""Mail card v4 (no draft id or hash in Discord) and one live card per owner request thread."""
from __future__ import annotations

import pytest

from tests.unit import test_mail_single_live_request as mail
from triage_approval_reaction import resolve_reaction
from tests.unit.test_mail_single_live_request import mail_env as mail_env

THREAD = "100000000000000777"


@pytest.fixture
def owner_thread(mail_env):
    fake = mail_env[0]
    fake.threads[THREAD] = "점심 메일"
    patched: list[tuple[str, str]] = []
    original = fake.api

    def api(method, path, payload=None):
        if method == "PATCH":
            message_id = path.strip("/").split("/")[3]
            fake.contents[message_id] = str((payload or {}).get("content", ""))
            patched.append((message_id, fake.contents[message_id]))
            return {"id": message_id}
        return original(method, path, payload)

    mail.triage_confirm._api = api
    return fake, patched


def _compose(body: str) -> dict:
    return mail.triage_gate.create_draft(
        uid=f"compose:{body}", sender="", mail_subject="", to="peer@example.invalid", cc="",
        subject="점심 약속", body=body, category="compose", flags=(), kind="compose",
        origin_channel_id=THREAD, origin_message_id="100000000000000888",
    )


def _post(draft: dict) -> dict:
    mail.triage_approval.post_for_approval(draft)
    return mail.triage_gate.load_draft(draft["id"])


def test_v4_card_hides_draft_and_hash_yet_owner_approval_still_resolves(owner_thread) -> None:
    # Given: a compose card posted in the owner's request thread.
    fake, _ = owner_thread
    stored = _post(_compose("첫 초안"))
    content = fake.contents[stored["message_id"]]
    # Then: Discord shows who, subject, body and the reaction line only.
    assert stored["render_version"] == "4" and stored["channel_id"] == THREAD
    for hidden in (stored["id"], stored["sha256"], "취소 시", "되돌리기", "참조"):
        assert hidden not in content
    assert "peer@example.invalid" in content and "점심 약속" in content and "첫 초안" in content
    # When: the owner presses ✅; Then: the record-bound card resolves to approve.
    fake.approved.add(stored["message_id"])
    assert resolve_reaction(stored) == mail.triage_confirm.APPROVE_EMOJI
    # When: the posted text changes; Then: the binding refuses it.
    fake.contents[stored["message_id"]] += " (변조)"
    with pytest.raises(mail.triage_gate.GateError):
        resolve_reaction(stored)


def test_a_newer_draft_in_the_same_thread_closes_the_older_card(owner_thread) -> None:
    import triage_supersede

    # Given: two revisions of one mail written in the same owner thread.
    fake, patched = owner_thread
    old = _post(_compose("첫 초안"))
    new = _post(_compose("고친 초안"))
    # When: the newer card is live.
    closed = triage_supersede.supersede_older(new)
    # Then: the older card is terminal and marked; only the new one stays pending.
    assert closed == [old["id"]]
    assert patched == [(old["message_id"], triage_supersede.NOTICE)]
    pending = [str(record["id"]) for _, record, _ in mail.triage_approval._pending_drafts()]
    assert pending == [new["id"]]
    # When: the owner presses ✅ on the old card; Then: no pending draft holds it, so nothing runs.
    fake.approved.add(old["message_id"])
    assert old["message_id"] not in [record.get("message_id") for _, record, _ in mail.triage_approval._pending_drafts()]
    with pytest.raises(mail.triage_gate.GateError):
        mail.triage_gate.load_draft(old["id"])


def test_a_decided_or_unrelated_card_is_left_alone(owner_thread) -> None:
    import triage_supersede

    # Given: an older card the owner already approved, and a reply draft in the same thread.
    fake, patched = owner_thread
    old = _post(_compose("첫 초안"))
    fake.approved.add(old["message_id"])
    new = _post(_compose("고친 초안"))
    # When/Then: the decided card is not closed behind the owner's back.
    assert triage_supersede.supersede_older(new) == []
    assert patched == []
    reply = {**new, "kind": "reply"}
    assert triage_supersede.supersede_older(reply) == []


def test_compose_twice_from_one_thread_leaves_one_live_card(owner_thread) -> None:
    import triage_pipeline

    # Given/When: the owner's revision is composed twice from the same thread.
    fake, _ = owner_thread
    args = dict(post=True, origin_channel_id=THREAD, origin_message_id="100000000000000888")
    first = triage_pipeline.compose_and_post("peer@example.invalid", "점심 약속", "첫 초안", **args)
    second = triage_pipeline.compose_and_post("peer@example.invalid", "점심 약속", "고친 초안", **args)
    # Then: only the newest card is still pending.
    pending = [str(record["id"]) for _, record, _ in mail.triage_approval._pending_drafts()]
    assert pending == [second["id"]] and first["id"] not in pending
