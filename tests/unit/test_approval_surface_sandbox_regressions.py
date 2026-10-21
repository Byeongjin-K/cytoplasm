from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import unit_node

_REPO = Path(__file__).resolve().parents[2]
_WIKI_SCRIPTS = _REPO / "skills" / "wiki" / "scripts"
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_WIKI_SCRIPTS))

from automation.interop import injection_adapter  # noqa: E402
import wiki_binding  # noqa: E402
import wiki_gate  # noqa: E402

_OWNER_ID = "owner-sandbox-regression"
_CHANNEL_ID = "999000000000000031"
_NOTE_TEXT = (
    "---\n"
    'title: "Sandbox binding"\n'
    "tags: [test]\n"
    "created: 2026-07-26T00:00:00Z\n"
    "updated: 2026-07-26T00:00:00Z\n"
    "links: []\n"
    "---\n"
    "binding regression\n"
)


@pytest.mark.parametrize(
    ("skill", "retired_name"),
    (("mail", "TRIAGE_APPROVALS_CHANNEL_ID"), ("wiki", "WIKI_APPROVALS_CHANNEL_ID")),
)
def test_scenario_uses_no_retired_flow_specific_approval_override(
    skill: str,
    retired_name: str,
) -> None:
    # Given: a deploy scenario whose fixtures must not resemble production overrides.
    scenario = (_REPO / "skills" / skill / "scripts" / "scenario.sh").read_text(encoding="utf-8")

    # When: retired flow-specific approval environment names are inspected.
    # Then: the scenario cannot imply that setting the retired name moves a surface.
    assert retired_name not in scenario


def _scenario(skill: str) -> subprocess.CompletedProcess[str]:
    environment = {
        **unit_node.env(),
        "AUTOPHAGY_DEMO_SECRET": "DUMMY-local-sandbox-regression",
        # an absent mirror on every host (the real one is unreadable on the node)
        "AUTOPHAGY_REPO_ROOT": str(Path(os.environ["AUTOPHAGY_UNIT_NODE_ROOT"]) / "mirror"),
        "HOME": str(Path.home()),
        "INTEROP_RUNTIME": str(_REPO),
        "PATH": "/usr/bin:/bin",
    }
    return subprocess.run(
        ["bash", str(_REPO / "skills" / skill / "scripts" / "scenario.sh")],
        capture_output=True,
        cwd=_REPO,
        env=environment,
        check=False,
        text=True,
        timeout=120,
    )


def test_wiki_persisted_channel_id_requires_all_binding_fields() -> None:
    # Given: a complete persisted approval binding and incomplete variants
    binding = {
        "channel_id": _CHANNEL_ID,
        "surface": "owner-dm",
        "policy_version": 1,
    }
    # When / Then: raw persisted reads return a channel only for the complete record
    assert wiki_binding.persisted_channel_id(binding) == _CHANNEL_ID
    for field in binding:
        incomplete = {key: value for key, value in binding.items() if key != field}
        assert wiki_binding.persisted_channel_id(incomplete) is None


def test_wiki_injected_confirm_refuses_an_unbound_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a valid signed owner event for a draft that was never posted and never bound
    monkeypatch.setattr(wiki_gate, "GATE_DIR", tmp_path / "gate")
    monkeypatch.setattr(wiki_gate, "owner_id", lambda: _OWNER_ID)
    monkeypatch.setattr(wiki_gate, "_adapter", lambda: injection_adapter)
    monkeypatch.setenv("E2E_TEST_MODE", "1")
    monkeypatch.setenv("INTEROP_E2E_SECRET", "dummy-secret")
    draft = wiki_gate.create_draft("create", "sandbox-binding", _NOTE_TEXT, _CHANNEL_ID)
    event = injection_adapter.InboundEvent(
        event_id="sandbox-event",
        user_id=_OWNER_ID,
        channel_id=_CHANNEL_ID,
        text=wiki_gate.confirm_text(draft),
    )
    envelope = tmp_path / "signed.json"
    envelope.write_text(
        json.dumps(
            {
                "event": {
                    "channel_id": event.channel_id,
                    "event_id": event.event_id,
                    "text": event.text,
                    "user_id": event.user_id,
                },
                "signature": injection_adapter.sign_event(event, b"dummy-secret"),
            }
        ),
        encoding="utf-8",
    )
    # When / Then: the event cannot create a channel binding for the unbound record
    with pytest.raises(wiki_gate.GateError, match="저장된 승인 바인딩"):
        wiki_gate.confirm_via_injection(draft, envelope)


def test_wiki_scenario_completes_the_signed_confirm_leg() -> None:
    # Given: the isolated deploy environment with the injection adapter available
    # When: the wiki scenario exercises signed confirmation and reaction branches
    result = _scenario("wiki")
    # Then: every existing approval fixture carries the persisted binding it consumes
    assert result.returncode == 0, result.stderr
    assert "SCENARIO-PASS leg=signed-confirm" in result.stdout
