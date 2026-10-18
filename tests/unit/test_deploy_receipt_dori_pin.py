"""배포 영수증의 `dori_pin` — 이 릴리스가 고정한 도리 포크 커밋을 영수증이 말한다.

기존 영수증 시험 파일들은 FS3 원장이 출력 해시를 고정할 수 있어 케이스를 더하지 않고 여기에
둔다(tests/AGENTS.md). 노드 프로브가 릴리스 트리의 `dori/pin.toml` 을 읽어 영수증에 싣는지,
고정값이 깨졌으면 영수증을 쓰지 않는지(관측 불가 exit 4)를 본다.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Final

import pytest

from automation import deploy_all, deploy_all_probe, deploy_receipt
from automation.deploy_all import parse_observations
from automation.dori_pin import load_pin

_REPO: Final = Path(__file__).resolve().parents[2]


def _plan() -> deploy_all.Plan:
    return parse_observations(
        ["OBS|release|abc123", "OBS|mounts|judged",
         "OBS|home|agent|.hermes/scripts/d.py|automation/pkg/d.py|required|aaa|aaa",
         "OBS|artifacts|0", "OBS|end"]
    )


def test_the_receipt_carries_the_pin_it_was_given() -> None:
    pin = load_pin().as_receipt()

    document = json.loads(deploy_receipt.render_receipt(_plan(), verified_at="t", dori_pin=pin))

    assert document["dori_pin"] == pin
    assert "dori_pin" not in json.loads(deploy_receipt.render_receipt(_plan(), verified_at="t"))


def _probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pin_text: str | None) -> Path:
    runtime = tmp_path / "release"
    if pin_text is not None:
        (runtime / "dori").mkdir(parents=True)
        _ = (runtime / "dori" / "pin.toml").write_text(pin_text, encoding="utf-8")
    runtime.mkdir(exist_ok=True)
    lines = ["OBS|release|abc123", "OBS|mounts|judged",
             "OBS|home|agent|.hermes/scripts/d.py|automation/pkg/d.py|required|aaa|aaa",
             "OBS|artifacts|0", "OBS|end"]
    monkeypatch.setattr(deploy_all_probe, "observations", lambda *_a, **_k: lines)
    monkeypatch.setattr(deploy_all_probe, "_read_home", lambda *_a: "")
    monkeypatch.setattr(deploy_all_probe, "all_declarations", lambda _root: ())
    return runtime


def test_the_node_probe_reads_the_pin_from_the_release_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runtime = _probe(tmp_path, monkeypatch, (_REPO / "dori" / "pin.toml").read_text(encoding="utf-8"))

    assert deploy_all_probe.main(("--dori-agent-chat", "off", "--hermes-gateways", "running",
                                  "--runtime-root", str(runtime), "--format", "receipt")) == 0

    assert json.loads(capsys.readouterr().out)["dori_pin"] == load_pin().as_receipt()


def test_a_broken_pin_in_the_release_tree_writes_no_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runtime = _probe(tmp_path, monkeypatch, 'schema = 1\nrepo = "https://x/y.git"\ncommit = "short"\n')

    assert deploy_all_probe.main(("--dori-agent-chat", "off", "--hermes-gateways", "running",
                                  "--runtime-root", str(runtime), "--format", "receipt")) == 4

    captured = capsys.readouterr()
    assert captured.out == "" and "DORI-PIN-INVALID" in captured.err
    shutil.rmtree(runtime)
