"""릴리스 적용 통지: 형 채널은 한 줄, 운영자가 고칠 것은 페어 채널 (소유자 2026-10-19 "릴리스 안내 간략하게 만들어").

기존 통지 회귀(test_release_applied_notice.py)와 갈라 둔 이유: 이 파일은 새 계약 — 영수증의 형/운영자
분리, 릴리스 노트 제목, 스윕이 노드로 넘기는 인자, 전달 순서 — 만 고정한다.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from automation import deploy_receipt, owner_notice, release_applied_notice, release_applied_state

_HEAD = "c" * 40
_ROOT_FAIL = {
    "probe": "release_helper_drift", "node": "primary", "check": "root helpers", "status": "FAIL",
    "guidance": ["re-run the provisioner on the node: sudo bash <release>/automation/provision-deploy-converge.sh"],
}
_WRAPPER_FAIL = {
    "probe": "healthcheck_wrapper_current", "node": "primary", "check": "allowlist", "status": "FAIL",
    "guidance": ["regenerate on the node as the operator"],
}


def _receipt(*actions: dict[str, object]) -> dict[str, object]:
    return {"version": 2, "pending_unknown": False, "held": [], "pending_owner_actions": list(actions)}


def test_only_a_failed_root_helper_is_owner_work() -> None:
    # Given: a receipt with one root-helper failure and one operator-fixable wrapper drift
    receipt = _receipt(_ROOT_FAIL, _WRAPPER_FAIL)

    # When
    owner = deploy_receipt.owner_lines(receipt)
    operator = deploy_receipt.pending_lines(receipt, operator_only=True)

    # Then: the owner gets the sudo command; the wrapper drift stays with the operator
    assert owner == ("노드에서 `sudo bash <release>/automation/provision-deploy-converge.sh` 한 번",)
    assert any("allowlist" in line for line in operator)
    assert not any("root helpers" in line for line in operator)


def test_an_unknown_root_helper_probe_is_operator_work() -> None:
    receipt = _receipt({**_ROOT_FAIL, "status": "UNKNOWN"})

    assert deploy_receipt.owner_lines(receipt) == ()
    assert any("root helpers" in line for line in deploy_receipt.pending_lines(receipt, operator_only=True))


def test_the_summary_is_the_release_notes_title(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RELEASE_NOTES_DIR", str(tmp_path))
    _ = (tmp_path / f"{_HEAD}.md").write_text("# 회신 새 서식은 기본 꺼짐\n\n## 바뀐 것\n- x\n", encoding="utf-8")

    assert release_applied_notice.release_summary(_HEAD) == "회신 새 서식은 기본 꺼짐"
    assert release_applied_notice.release_summary("d" * 40) == ""


def test_the_sweep_hands_the_title_and_owner_work_to_the_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: release notes exist and the receipt holds one owner-only item
    monkeypatch.setenv("RELEASE_NOTES_DIR", str(tmp_path))
    _ = (tmp_path / f"{_HEAD}.md").write_text("# 짧은 완료 알림\n", encoding="utf-8")
    commands: list[tuple[str, ...]] = []

    def run(command: tuple[str, ...], *, env: dict[str, str], check: bool) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(release_applied_state.subprocess, "run", run)
    monkeypatch.setenv("RELEASE_APPROVAL_CMD", "fake-sender")
    owner = deploy_receipt.owner_lines(_receipt(_ROOT_FAIL))
    sweep = release_applied_state.Sweep(tmp_path, tmp_path, _HEAD, _HEAD, (), owner)

    # When
    sweep._notify(_HEAD, "v2.6.6")

    # Then
    assert commands == [(
        "fake-sender", "send", "--version", "v2.6.6", "--head", _HEAD,
        "--owner-pending", json.dumps(list(owner)), "--summary", "짧은 완료 알림",
    )]


def test_the_owner_gets_one_line_and_the_pair_gets_the_operator_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the node pointer is this release
    pointer = tmp_path / "current"
    pointer.symlink_to(tmp_path / _HEAD)
    monkeypatch.setenv("NODE_RELEASE_CURRENT", str(pointer))
    sent: list[tuple[bool, str, str]] = []

    def capture(content: str, *, message: object = None, fault: bool = False) -> bool:
        render = getattr(message, "render_version", "")
        sent.append((fault, render, getattr(message, "fact", content)))
        return True

    monkeypatch.setattr(owner_notice, "notify_owner", capture)
    receipt = _receipt(_ROOT_FAIL, _WRAPPER_FAIL)

    # When
    rc = release_applied_notice.main([
        "send", "--version", "v2.6.6", "--head", _HEAD, "--summary", "짧은 완료 알림",
        "--pending", json.dumps(list(deploy_receipt.pending_lines(receipt, operator_only=True))),
        "--owner-pending", json.dumps(list(deploy_receipt.owner_lines(receipt))),
    ])

    # Then: the pair message goes first, then the owner's single v5 line
    assert rc == 0
    assert [(fault, render) for fault, render, _ in sent] == [(True, "owner-ko-v2"), (False, "owner-ko-v5")]
    assert "allowlist" in sent[0][2]
    assert sent[1][2] == (
        "✅ v2.6.6 적용됨 — 짧은 완료 알림 · 형이 할 일: "
        "노드에서 `sudo bash <release>/automation/provision-deploy-converge.sh` 한 번"
    )


def test_a_failed_pair_delivery_never_sends_the_owner_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pointer = tmp_path / "current"
    pointer.symlink_to(tmp_path / _HEAD)
    monkeypatch.setenv("NODE_RELEASE_CURRENT", str(pointer))
    faults: list[bool] = []

    def refuse_pair(content: str, *, message: object = None, fault: bool = False) -> bool:
        faults.append(fault)
        return not fault

    monkeypatch.setattr(owner_notice, "notify_owner", refuse_pair)
    pending = deploy_receipt.pending_lines(_receipt(_WRAPPER_FAIL), operator_only=True)

    rc = release_applied_notice.main(
        ["send", "--version", "v2.6.6", "--head", _HEAD, "--pending", json.dumps(list(pending))]
    )

    assert rc == 3
    assert faults == [True]
