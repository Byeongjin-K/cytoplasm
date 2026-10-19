"""A report fits the model relay's input limit and says what it cut (live QA 4, 2026-10-19).

Twelve whole notes made a 262,145-byte prompt; the workstation relay refuses anything over
256 KiB unread, and the report only printed the error class. New file so the recorded report
and relay cases keep their shape.
"""
from __future__ import annotations

import subprocess
import sys
from importlib import import_module
from pathlib import Path

import pytest

from automation import codex_llm
from automation.omo_relay.runner import LIMIT
from tests.unit.test_omo_relay import relay_client

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills"))
report_cli = import_module("report.scripts.report_cli")
report_core = import_module("report.scripts.report_core")
report_llm = import_module("report.scripts.report_llm")


def _notes(root: Path, sizes: list[int]) -> Path:
    root.mkdir()
    for index, size in enumerate(sizes):
        (root / f"n{index}.md").write_text(f"# 노트{index}\n\n" + "가" * (size // 3), encoding="utf-8")
    return root


def test_a_report_over_the_budget_is_cut_and_says_what_went(tmp_path: Path, monkeypatch) -> None:
    # Given: notes far larger than the relay takes, and a recorded model.
    notes = _notes(tmp_path / "notes", [150_000, 150_000, 150_000])
    prompts: list[str] = []
    monkeypatch.setattr(report_llm, "generate", lambda prompt: (prompts.append(prompt), "초안")[1])
    monkeypatch.setattr(report_cli, "_publish_report", lambda *a: None)
    # When: a report is made from that folder.
    args = report_cli.build_parser().parse_args(
        ["report", "--notes-root", str(notes), "--outputs-root", str(tmp_path / "out")])
    assert args.func(args) == 0
    # Then: the prompt stays under the relay limit and the report names each cut.
    assert len(prompts[0].encode("utf-8")) < LIMIT
    report = next((tmp_path / "out").glob("report-*.md")).read_text(encoding="utf-8")
    assert "자료 줄임(모델 입력 한도):" in report
    assert report.count("바이트") >= 2


def test_material_files_replace_the_notes_folder(tmp_path: Path, monkeypatch) -> None:
    # Given: one staged note file and an unrelated notes folder.
    staged = tmp_path / "e7.md"
    staged.write_text("# E7 강화학습 주간 정리\n\n보상 곡선 정리", encoding="utf-8")
    other = _notes(tmp_path / "notes", [300])
    prompts: list[str] = []
    monkeypatch.setattr(report_llm, "generate", lambda prompt: (prompts.append(prompt), "초안")[1])
    monkeypatch.setattr(report_cli, "_publish_report", lambda *a: None)
    # When: the report is asked for with that file as material.
    args = report_cli.build_parser().parse_args(
        ["report", "--material-file", str(staged), "--notes-root", str(other),
         "--outputs-root", str(tmp_path / "out")])
    assert args.func(args) == 0
    # Then: only the file is material, and nothing was cut.
    assert "보상 곡선 정리" in prompts[0] and "노트0" not in prompts[0]
    report = next((tmp_path / "out").glob("report-*.md")).read_text(encoding="utf-8")
    assert "E7 강화학습 주간 정리" in report and "자료 줄임" not in report


def test_a_refused_model_call_prints_its_reason(monkeypatch, capsys) -> None:
    # Given: the model call fails with a reason.
    def fail(_prompt: str) -> str:
        raise report_llm.LlmInvocationError("omo relay input too large (300000 bytes > 262144)")

    monkeypatch.setattr(report_cli, "_report", lambda args: fail(""))
    # When / Then: the refusal carries the reason, not only the class name.
    assert report_cli.main(["report"]) == 1
    assert "input too large (300000 bytes > 262144)" in capsys.readouterr().err


def test_the_gate_passes_a_staged_material_file_to_the_report(tmp_path: Path, monkeypatch) -> None:
    # Given: a note YooDori staged on the node.
    from automation.yoodori_gate import yoodori_read_gate as gate

    monkeypatch.setattr(gate, "STAGING", tmp_path / "staging")
    gate.STAGING.mkdir()
    staged = gate.STAGING / "e7-weekly.md"
    staged.write_text("# E7\n", encoding="utf-8")
    # When: the owner-command report job is built with it.
    job = gate.build_job(["doc", "report", "report", "--material-file", str(staged), "--title", "E7 주간",
                          "--origin-channel-id", "1", "--origin-message-id", "2"])
    # Then: the report CLI receives that file as material.
    assert job.argv[job.argv.index("--material-file") + 1] == str(staged) and job.write


def test_the_relay_client_names_an_oversize_prompt_without_sending_it(tmp_path, monkeypatch) -> None:
    # Given: an enabled relay.
    client = relay_client(tmp_path, monkeypatch)
    calls: list[object] = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a))
    # When / Then: an oversize prompt is refused here with its size, and nothing is sent.
    with pytest.raises(codex_llm.CodexUnavailableError, match="input too large"):
        client.complete("가" * (LIMIT // 3 + 10))
    assert calls == []
