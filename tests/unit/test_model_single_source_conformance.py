"""Each route follows its own account config; deployed code never names a model.

Owner decision 2026-09-29: the main model and the fallback live in one place,
``~/.hermes/config.yaml`` (``model.provider``/``model.default`` and
``fallback_providers``). Before that, the shared client pinned one model and three
skills pinned another that the subscription had stopped serving, so their calls went to
the fallback without anyone choosing it. The shared client therefore passes neither
``-m`` nor ``--provider``, and a model name written into deployed code is a defect
unless an entry below says why it is not a Hermes chat model.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Final

from automation import codex_llm

_REPO: Final = Path(__file__).resolve().parents[2]
_MODEL_NAME: Final = re.compile(
    r"\b(?:gpt-[0-9][\w.-]*|grok-[0-9][\w.-]*|glm-[0-9][\w.-]*|claude-[\w.-]+|gemini-[\w.-]+)"
)
_SCANNED_SUFFIXES: Final = (".py", ".sh", ".json", ".yaml", ".toml")
_ALLOWED: Final = {
    "automation/provision-agent.sh": "writes the first ~/.hermes/config.yaml — the seed of the one place",
    "automation/openclaw-arm64-smoke.sh": "configures a different product (openclaw) in a smoke test",
    "skills/speechtotext/scripts/stt_client.py": "audio transcription API, not a Hermes chat model",
}


def _named_models() -> dict[str, list[str]]:
    listed = subprocess.run(
        ["git", "ls-files", "automation", "skills"],
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    found: dict[str, list[str]] = {}
    for relative in listed:
        if "/vendor/" in relative or not relative.endswith(_SCANNED_SUFFIXES):
            continue
        names = _MODEL_NAME.findall((_REPO / relative).read_text(encoding="utf-8"))
        if names:
            found[relative] = names
    return found


def test_deployed_code_names_no_model_outside_the_ledger() -> None:
    found = _named_models()

    assert {path: names for path, names in found.items() if path not in _ALLOWED} == {}
    assert sorted(set(_ALLOWED) - set(found)) == []


def test_the_shared_client_lets_the_config_pick_the_model() -> None:
    argv = codex_llm.CodexClient(binary="hermes", home="/nonexistent").argv("prompt")

    assert argv == ["hermes", "-z", "prompt", "-t", "todo"]


def test_the_relay_leaves_model_selection_to_workstation_config() -> None:
    from automation.omo_relay.runner import ARGV

    assert not {"--model", "-m", "--models", "--provider"} & set(ARGV)
    assert {"--no-tools", "--no-extensions", "--no-context-files", "--no-session"} <= set(ARGV)
