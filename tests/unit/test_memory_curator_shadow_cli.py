"""The owner-run shadow CLI is a read-only diagnostic: no writes, no posting."""

from __future__ import annotations

import ast
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

import pytest

from automation.memory_curator import cli, shadow_cli
from automation.memory_curator.shadow import SHADOW_SCHEMA

JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)
JsonLoader: TypeAlias = Callable[[str], JsonValue]
_JSON_LOADS: JsonLoader = json.loads

#: Long enough for the LLM path, free of every credential/native cue.
_LONG_ENTRY = (
    "The nightly export job writes its manifest to "
    "/var/log/autophagy/export-manifest.json before the archive step runs."
)
_EVIDENCE = "/var/log/autophagy/export-manifest.json"
_SHORT_MEMORY = "coffee at 3"
_SHORT_USER = "prefers short replies"

#: Symbols a read-only diagnostic must never import or name.
_FORBIDDEN_NAMES: frozenset[str] = frozenset(
    {
        "post_confirm",
        "post_confirm_message",
        "post_promotion",
        "alert_owner",
        "create_draft",
        "apply_curation",
        "delete_entry",
        "save_state",
    }
)


@dataclass(frozen=True, slots=True)
class _FakeLlm:
    """Records prompts and returns prepared responses in insertion order."""

    responses: list[str]
    prompts: list[str]

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.responses.pop(0)


@dataclass(frozen=True, slots=True)
class _FakeLlmFactory:
    """Stands in for ``CodexLlmClient`` at the narrowest seam the CLI uses."""

    client: _FakeLlm

    def from_environment(self, environment: Mapping[str, str]) -> _FakeLlm:
        _ = environment
        return self.client


def _memory_dir(tmp_path: Path) -> Path:
    memories = tmp_path / "memories"
    memories.mkdir()
    _ = (memories / "MEMORY.md").write_text(
        f"{_LONG_ENTRY}\n§\n{_SHORT_MEMORY}\n", encoding="utf-8"
    )
    _ = (memories / "USER.md").write_text(f"{_SHORT_USER}\n", encoding="utf-8")
    return memories


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _as_dict(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def _as_list(value: JsonValue) -> list[JsonValue]:
    assert isinstance(value, list)
    return value


def _as_int(value: JsonValue) -> int:
    assert isinstance(value, int)
    return value


def _as_str(value: JsonValue) -> str:
    assert isinstance(value, str)
    return value


def _twin_response() -> str:
    return json.dumps(
        {"route": "TWIN", "evidence": _EVIDENCE, "reason": "durable operations rule"}
    )


def _run_with_fake_llm(
    memory_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    extra: tuple[str, ...],
) -> dict[str, JsonValue]:
    monkeypatch.setattr(
        shadow_cli,
        "CodexLlmClient",
        _FakeLlmFactory(_FakeLlm(responses=[_twin_response(), _twin_response()], prompts=[])),
    )
    exit_code = shadow_cli.main(["--memory-dir", str(memory_dir), *extra])
    assert exit_code == 0
    return _as_dict(_JSON_LOADS(capsys.readouterr().out))


def test_offline_run_emits_the_shadow_schema_without_calling_an_llm(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a populated memory dir and no reachable Codex OAuth tier in the env.
    memories = _memory_dir(tmp_path)
    monkeypatch.delenv("HOME", raising=False)

    # When: the owner runs the shadow diagnostic offline.
    exit_code = shadow_cli.main(
        ["--memory-dir", str(memories), "--kind", "both", "--offline"]
    )

    # Then: a complete v1 report is printed, with the veto histogram and zero LLM calls.
    report = _as_dict(_JSON_LOADS(capsys.readouterr().out))
    assert exit_code == 0
    assert _as_str(report["schema"]) == SHADOW_SCHEMA
    assert _as_int(report["llm_calls"]) == 0
    assert set(_as_dict(report["side_effects"]).values()) == {0}
    assert _as_int(_as_dict(report["vetoes"])["too_short"]) == 2
    assert _as_int(_as_dict(_as_dict(report["routes"])["UNCERTAIN"])["count"]) == 1
    assert _as_int(_as_dict(_as_dict(report["routes"])["KEEP_NATIVE"])["count"]) == 2


def test_offline_run_leaves_every_file_byte_identical(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: the exact bytes of the native memory files before the run.
    memories = _memory_dir(tmp_path)
    before = _snapshot(tmp_path)

    # When: the shadow diagnostic classifies both files.
    exit_code = shadow_cli.main(["--memory-dir", str(memories), "--offline"])

    # Then: nothing on disk moved — no rewrite, no backup, no state.json.
    _ = capsys.readouterr()
    assert exit_code == 0
    assert _snapshot(tmp_path) == before
    assert not list(tmp_path.rglob("state.json"))


def test_cli_verb_dispatches_to_the_shadow_entrypoint(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a populated memory dir reachable through the package CLI.
    memories = _memory_dir(tmp_path)

    # When: the owner runs the `shadow` verb on the shared entrypoint.
    exit_code = cli.main(["shadow", "--memory-dir", str(memories), "--offline"])

    # Then: the shadow report is produced instead of the legacy compaction report.
    report = _as_dict(_JSON_LOADS(capsys.readouterr().out))
    assert exit_code == 0
    assert _as_str(report["schema"]) == SHADOW_SCHEMA


def test_limit_caps_the_number_of_classified_entries(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a memory dir holding three entries across both files.
    memories = _memory_dir(tmp_path)

    # When: the owner asks for at most one classification.
    exit_code = shadow_cli.main(["--memory-dir", str(memories), "--offline", "--limit", "1"])

    # Then: exactly one verdict is accounted for in the route histogram.
    report = _as_dict(_JSON_LOADS(capsys.readouterr().out))
    assert exit_code == 0
    routes = _as_dict(report["routes"])
    assert sum(_as_int(_as_dict(routes[route])["count"]) for route in routes) == 1


def test_unavailable_codex_oauth_refuses_on_stderr_without_a_traceback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an online run whose environment cannot reach the Codex OAuth tier.
    memories = _memory_dir(tmp_path)
    monkeypatch.delenv("HOME", raising=False)

    # When: the owner runs the shadow diagnostic without --offline.
    exit_code = shadow_cli.main(["--memory-dir", str(memories)])

    # Then: it refuses with one masked line, names Codex, and prints no report at all.
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "MEMORY-SHADOW-REFUSED" in captured.err
    assert "Codex OAuth" in captured.err
    assert "Traceback" not in captured.err
    assert captured.out == ""


def test_missing_memory_dir_refuses_with_exit_two(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: a memory dir path that does not exist.
    absent = tmp_path / "absent"

    # When: the owner points the shadow diagnostic at it.
    exit_code = shadow_cli.main(["--memory-dir", str(absent), "--offline"])

    # Then: it refuses on stderr without a traceback and emits no report.
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "MEMORY-SHADOW-REFUSED" in captured.err
    assert "Traceback" not in captured.err
    assert captured.out == ""


def test_full_prints_raw_entry_text_while_the_default_masks_it(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a classifier verdict that promotes one long entry to the twin.
    memories = _memory_dir(tmp_path)

    # When: the same run is rendered with the default masking and then with --full.
    masked = _run_with_fake_llm(memories, monkeypatch, capsys, ())
    full = _run_with_fake_llm(memories, monkeypatch, capsys, ("--full",))

    # Then: only --full exposes the raw entry text; the default truncates and redacts it.
    masked_preview = _as_str(_as_dict(_as_list(masked["candidates"])[0])["preview"])
    full_preview = _as_str(_as_dict(_as_list(full["candidates"])[0])["preview"])
    assert _EVIDENCE not in masked_preview
    assert masked_preview.endswith("…")
    assert full_preview == " ".join(_LONG_ENTRY.split())
    assert _EVIDENCE in full_preview
    assert _as_int(masked["llm_calls"]) == 2  # the long MEMORY entry + the short USER entry (Q138 ②)


def _named_symbols(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            names.update(alias.name for alias in node.names)
    return names


def test_shadow_cli_source_never_names_a_posting_or_discord_symbol() -> None:
    # Given: the deployed shadow CLI source.
    source = Path(shadow_cli.__file__)

    # When: an AST scan collects every name the module imports or references.
    names = _named_symbols(source)

    # Then: no Discord/posting/mutating symbol is reachable from this entrypoint.
    assert not names & _FORBIDDEN_NAMES
    assert not [name for name in names if "discord" in name.casefold()]
