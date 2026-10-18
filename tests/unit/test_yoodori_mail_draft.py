"""The digest reply key enters the existing draft-card path, never send or watch."""
from __future__ import annotations

import json
import sys

import pytest

from automation.yoodori_gate import yoodori_read_gate as gate
from tests.unit.test_yoodori_gate import LIVE, ORIGIN, PY, b64


@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "HOME", tmp_path)
    config = tmp_path / ".hermes/interop/config.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"owner_id": "12345"}))


def test_mail_draft_builds_only_the_existing_reply_request(owner) -> None:
    # Given: an owner instruction on a digest key.
    words = ["mail-draft", "1008-0800-3", "--instruction-b64", b64("참석합니다.\n자료도 부탁드립니다."), *ORIGIN]
    # When: the node gate validates and builds argv.
    name, argv, cwd = gate.build(words)
    # Then: the exact draft-card command keeps key, instruction and origin; no execution path.
    assert name == "mail-draft" and cwd == gate.RELEASE
    assert argv == [PY, f"{LIVE}/mail/scripts/triage_cli.py", "reply-request",
                    "--key", "1008-0800-3", "--author-id", "12345",
                    "--instruction", "참석합니다.\n자료도 부탁드립니다.", *ORIGIN]
    assert not {"send", "watch", "confirm", "--no-post"} & set(argv)


@pytest.mark.parametrize("key", ["123", "1008-0800", "1008-0800-1234", "../1008-0800-3", "１００８-0800-3", "1008-0800-3;"])
def test_bad_reply_keys_are_refused(owner, key) -> None:
    # Given / When / Then: no malformed key can reach even the producer.
    with pytest.raises(gate.Refused):
        gate.build(["mail-draft", key, "--instruction-b64", b64("답장"), *ORIGIN])


@pytest.mark.parametrize("instruction", ["", "가" * 2001, "$(send)", "a; send", "a & b", "x|y", "<html>", "\\path"])
def test_decoded_instruction_limits_and_shell_characters_are_refused(owner, instruction) -> None:
    # Given / When / Then: Base64 cannot bypass the instruction boundary.
    with pytest.raises(gate.Refused):
        gate.build(["mail-draft", "1008-0800-3", "--instruction-b64", b64(instruction), *ORIGIN])


def test_maximum_instruction_is_accepted_and_origins_are_required(owner) -> None:
    # Given: exactly 2000 decoded Unicode characters.
    args = ["mail-draft", "1008-0800-3", "--instruction-b64", b64("가" * 2000)]
    # When / Then: the upper bound is inclusive, but both origin coordinates are mandatory.
    assert "가" * 2000 in gate.build([*args, *ORIGIN])[1]
    for tail in ([], ORIGIN[:2], ORIGIN[2:], [*ORIGIN, "--watch", "1"]):
        with pytest.raises(gate.Refused):
            gate.build([*args, *tail])
    with pytest.raises(gate.Refused):
        gate.build(["mail-draft", "1008-0800-3", "--instruction", "답장", *ORIGIN])


def test_missing_owner_config_never_guesses_author(tmp_path, monkeypatch) -> None:
    # Given / When / Then: the trusted owner identity must be available.
    monkeypatch.setattr(gate, "HOME", tmp_path)
    with pytest.raises(gate.Refused, match="owner config"):
        gate.build(["mail-draft", "1008-0800-3", "--instruction-b64", b64("답장"), *ORIGIN])


def test_gate_argv_resolves_real_key_and_posts_draft_without_sending(tmp_path, monkeypatch) -> None:
    # Given: the skill's real digest DB, draft store and model-boundary fake.
    from tests.unit.test_mail_digest_reply import _reply_env, OWNER_ID, triage_cli
    _reply_env(tmp_path, monkeypatch)
    monkeypatch.setattr(gate, "_owner_id", lambda: OWNER_ID)
    posted = []
    monkeypatch.setattr(triage_cli.triage_pipeline, "_post_draft_for_approval",
                        lambda draft, **_kw: posted.append(draft) or "card-id")
    monkeypatch.setattr(triage_cli.triage_gate, "execute_draft", lambda *_a, **_kw: pytest.fail("send"))
    _, argv, _ = gate.build([
        "mail-draft", "1008-0800-3", "--instruction-b64", b64("참석한다고 회신"), *ORIGIN,
    ])
    # When: the real parser dispatches the exact gate argv, including posting enabled.
    monkeypatch.setattr(sys, "argv", [argv[1], *argv[2:]])
    assert triage_cli.main() == 0
    # Then: key-to-UID and origin binding reached the existing draft-card boundary.
    assert len(posted) == 1
    assert posted[0]["uid"] == "u-3"
    assert posted[0]["status"] == "pending"
    assert posted[0]["origin_channel_id"] == ORIGIN[1]
    assert posted[0]["origin_message_id"] == ORIGIN[3]
