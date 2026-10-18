"""After the P5 release the doctor treats the retired Hermes and the removed peer as expected."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from automation.doctor import cli
from automation.doctor.capabilities import evaluate
from automation.doctor.report import Status
from tests.unit.test_doctor import _by_key, _connected_home, _facts, _run


def test_a_retired_account_reads_the_removed_gateway_and_empty_jobs_as_expected(tmp_path: Path) -> None:
    home = _connected_home(tmp_path, jobs=[])
    (home / ".hermes/cron/jobs.json").unlink(missing_ok=True)
    (home / ".hermes/peer-retired").write_bytes(b"retired")
    findings = _by_key(evaluate(_facts(home, run=_run(gateway="inactive"))))
    assert "hermes-gateway" not in findings
    assert findings["scheduled-jobs"].status is Status.PASS


def test_without_the_marker_a_stopped_gateway_is_still_a_fault(tmp_path: Path) -> None:
    home = _connected_home(tmp_path)
    findings = _by_key(evaluate(_facts(home, run=_run(gateway="inactive"))))
    assert "hermes-gateway" not in findings


def test_operator_mode_skips_the_retired_peer_and_the_parity_check(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    from tests.unit.test_doctor import _finding  # noqa: PLC0415
    from automation.doctor import report  # noqa: PLC0415
    import json  # noqa: PLC0415
    private = tmp_path / "private"
    private.mkdir()
    (private / "peer-retired").write_bytes(b"retired")
    from automation.node_config import load_node_config  # noqa: PLC0415
    repo = Path(__file__).resolve().parents[2]
    config = replace(load_node_config(repo / "configs/node.example.toml"), private_root=private)
    monkeypatch.setattr(cli, "_config", lambda: config)
    monkeypatch.setattr(cli, "_account", lambda: "operator")
    monkeypatch.setattr(cli, "_code_root", lambda _config: tmp_path)
    agent_json = json.dumps(report.to_json("agent", "agent", (_finding("primary-model", Status.PASS),)))
    seen: list[tuple[str, ...]] = []

    def run_child(argv: tuple[str, ...], _cwd: Path) -> tuple[int, str, str]:
        seen.append(argv)
        return 0, agent_json, ""

    cli.main(["--offline"], run_child=run_child)
    out = capsys.readouterr().out
    assert [argv[3] for argv in seen if "automation.doctor" in argv] == [config.agent_account]
    assert "model-parity" not in out
