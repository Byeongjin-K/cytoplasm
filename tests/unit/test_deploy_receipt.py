"""영수증 v2 — 릴리스가 올리지 못한 소유자 조치를 영수증과 통지에 목록으로 싣는다.

영수증은 "이 릴리스가 한 번 전량 반영되었다"의 증명이다(RC-4). v2 는 그 증명 옆에 릴리스가
스스로 닫지 못한 것 — 보류 행, 퇴역 행, 릴리스 시점에 다시 돌린 위임 프로브의 FAIL·UNKNOWN —
을 적는다. pending 은 영수증을 막지 않는다(릴리스가 올릴 수 없는 표면이다). 판 번호 관문은
상시 프로브 하나이고, 판 번호가 2 미만이거나 없는 영수증은 현재 릴리스를 증명하지 못한다.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Final

import pytest

from automation import deploy_all, deploy_all_observe, deploy_all_probe, deploy_receipt, notice_bot_release
from automation.deploy_all import parse_observations
from automation.deploy_all_kinds import ObservationError

_REPO: Final = Path(__file__).resolve().parents[2]
_PROBE: Final = _REPO / "automation" / "release_receipt_probe.sh"
_SHA: Final = "abc123"
_HELPER_GUIDANCE: Final = (
    "[release-helper] re-run the provisioner on the node: "
    "sudo bash <release>/automation/provision-deploy-converge.sh"
)
_WRAPPER_GUIDANCE: Final = (
    "bash /srv/autophagy-agent-current/automation/healthcheck_probe_wrapper.sh --install primary"
)


def _artifact(
    destination: str,
    status: str,
    *,
    kind: str = "tree",
    account: str = "agent",
    policy: str = "required",
    want: str = "w",
    have: str = "w",
) -> str:
    return f"OBS|artifact|{kind}|{account}|{destination}|automation/pkg|{policy}|none|{status}|{want}|{have}"


def _plan(*family: str, count: int | None = None) -> deploy_all.Plan:
    artifacts = sum(1 for line in family if line.startswith("OBS|artifact|"))
    return parse_observations(
        [
            f"OBS|release|{_SHA}",
            "OBS|mounts|judged",
            "OBS|home|agent|.hermes/scripts/d.py|automation/pkg/d.py|required|aaa|aaa",
            *family,
            f"OBS|artifacts|{artifacts if count is None else count}",
            "OBS|end",
        ]
    )


def _receipt(
    plan: deploy_all.Plan, declared_reasons: dict[tuple[str, str, str], str] | None = None
) -> dict[str, object]:
    document = json.loads(
        deploy_receipt.render_receipt(
            plan, verified_at="2026-10-01T00:00:00+00:00", declared_reasons=declared_reasons or {}
        )
    )
    assert isinstance(document, dict)
    return document


_HELPER_FAIL: Final = (
    "OBS|pending|release_helper_drift|primary|FAIL|primary privileged release helpers match release",
    "OBS|pending-detail|release_helper_drift|primary|[release-helper] HELPER-DRIFT: converge.d/release_store.py",
    f"OBS|pending-detail|release_helper_drift|primary|{_HELPER_GUIDANCE}",
)
_WRAPPER_UNKNOWN: Final = (
    "OBS|pending|healthcheck_wrapper_current|primary|UNKNOWN|primary healthcheck probe allowlist matches the checks",
    f"OBS|pending-detail|healthcheck_wrapper_current|primary|WRAPPER-DRIFT-UNKNOWN {_WRAPPER_GUIDANCE}",
)


def test_receipt_v2_lists_pending_owner_actions() -> None:
    plan = _plan(
        _artifact("a", "ok"),
        *_HELPER_FAIL,
        "OBS|pending|rag_stack_current|rag|PASS|rag personal RAG source and MCP image match the release",
        *_WRAPPER_UNKNOWN,
    )

    receipt = _receipt(plan)

    assert receipt["version"] == 2
    assert receipt["release_sha"] == _SHA
    assert receipt["delegated"] == list(deploy_all.DELEGATED_SURFACES)
    assert receipt["delegated_checked"] == 1
    assert receipt["pending_unknown"] is False
    assert receipt["pending_owner_actions"] == [
        {
            "probe": "release_helper_drift",
            "node": "primary",
            "check": "primary privileged release helpers match release",
            "status": "FAIL",
            "guidance": [
                "[release-helper] HELPER-DRIFT: converge.d/release_store.py",
                _HELPER_GUIDANCE,
            ],
        },
        {
            "probe": "healthcheck_wrapper_current",
            "node": "primary",
            "check": "primary healthcheck probe allowlist matches the checks",
            "status": "UNKNOWN",
            "guidance": [f"WRAPPER-DRIFT-UNKNOWN {_WRAPPER_GUIDANCE}"],
        },
    ]
    assert receipt["judged_at_release_only"] == ["cron", "file", "gateway"]
    surfaces = receipt["surfaces"]
    assert isinstance(surfaces, dict)
    assert surfaces["skill_mounts"] == "ok"
    assert surfaces["home_artifacts"] == {"ok": 1, "ok_absent_optional": 0}
    assert surfaces["artifacts"]["tree"] == {"ok": 1, "held": 0, "retired": 0}


def test_held_rows_are_recorded_with_their_reason() -> None:
    plan = _plan(
        _artifact("p1", "held", kind="tree"),
        "OBS|artifact-detail|tree|agent|p1|roster-required|-",
        _artifact("p2", "held", kind="file", account="peer"),
        "OBS|artifact-detail|file|peer|p2|roster-required|-",
        _artifact("cfg", "held", policy="held", want="x", have="?"),
        "OBS|artifact-detail|tree|agent|cfg|unreadable|-",
        _artifact("old", "retired-present", policy="retired"),
        _artifact("gone", "retired-absent", policy="retired", have="-"),
    )

    declared = {
        ("tree", "agent", "cfg"): "owner-migrates-legacy-state-first",
        ("tree", "agent", "p1"): "declared-but-roster-wins",
    }

    receipt = _receipt(plan, declared)

    assert plan.clean
    assert receipt["held"] == [
        {"account": "agent", "destination": "p1", "reason": "roster-required"},
        {"account": "peer", "destination": "p2", "reason": "roster-required"},
        {"account": "agent", "destination": "cfg", "reason": "owner-migrates-legacy-state-first"},
    ]
    assert receipt["retired"] == [
        {"account": "agent", "destination": "old", "present": True},
        {"account": "agent", "destination": "gone", "present": False},
    ]
    surfaces = receipt["surfaces"]
    assert isinstance(surfaces, dict)
    assert surfaces["artifacts"]["tree"] == {"ok": 0, "held": 2, "retired": 2}
    assert surfaces["artifacts"]["file"] == {"ok": 0, "held": 1, "retired": 0}


def test_held_items_with_the_same_reason_become_one_line_with_a_count() -> None:
    family: list[str] = []
    for index in range(11):
        family += [
            _artifact(f"interop/{index}", "held"),
            f"OBS|artifact-detail|tree|agent|interop/{index}|roster-required|-",
        ]
    family += [
        _artifact("verify", "held", account="peer"),
        "OBS|artifact-detail|tree|peer|verify|roster-unverified|-",
        _artifact("cfg", "held", policy="held", want="x", have="y"),
        "OBS|artifact-detail|tree|agent|cfg|different-file|-",
    ]

    lines = deploy_receipt.pending_lines(
        _receipt(_plan(*family), {("tree", "agent", "cfg"): "owner-migrates-legacy-state-first"})
    )

    roster = [line for line in lines if line.startswith("- 보류 11건 ")]
    assert len(roster) == 1
    assert "python3 -m automation.group_roster init-local" in roster[0]
    unverified = [line for line in lines if line.startswith("- 보류 1건 ") and "roster" in line]
    assert len(unverified) == 1
    assert "확인 불가" in unverified[0]
    assert "init-local" not in unverified[0]
    assert any(line.startswith("- 보류 1건 ") and "owner-migrates-legacy-state-first" in line for line in lines)
    assert not any("different-file" in line for line in lines)


def test_without_a_declared_reason_the_observed_token_is_kept() -> None:
    plan = _plan(
        _artifact("cfg", "held", policy="held", want="x", have="y"),
        "OBS|artifact-detail|tree|agent|cfg|different-file|-",
    )

    receipt = _receipt(plan)

    assert receipt["held"] == [{"account": "agent", "destination": "cfg", "reason": "different-file"}]


def test_the_receipt_command_records_the_declared_reason_of_a_held_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runtime = tmp_path / _SHA
    legacy = runtime / "automation/pkg/d.py"
    legacy.parent.mkdir(parents=True)
    _ = legacy.write_text("d = 1\n", encoding="utf-8")
    _ = (runtime / "automation/pkg/h.py").write_text("h = 1\n", encoding="utf-8")
    central = runtime / "configs/watcher-deploy-manifest.txt"
    central.parent.mkdir()
    _ = central.write_text("agent|automation/pkg/d.py|.hermes/scripts/d.py|required\n", encoding="utf-8")
    _ = (runtime / "automation/pkg/deploy-manifest.txt").write_text(
        "agent|automation/pkg/h.py|.hermes/held/h.py|v2:file;policy=held;reason=owner-migrates-legacy-state-first\n",
        encoding="utf-8",
    )
    home = tmp_path / "homes" / "agent"
    (home / ".hermes/held").mkdir(parents=True)
    _ = (home / ".hermes/held/h.py").write_text("h = 2\n", encoding="utf-8")
    legacy_sha = hashlib.sha256(legacy.read_bytes()).hexdigest()

    def runner(account: str, args: tuple[str, ...]) -> tuple[int, str]:
        if args[0] == "delegated":
            return 0, "DELEGATED-END|0\n"
        proc = subprocess.run(
            ("bash", "-c", deploy_all_observe.REMOTE_SCRIPT, "_", *args),
            env={"HOME": str(tmp_path / "homes" / account), "PATH": os.environ["PATH"]},
            capture_output=True, text=True, check=False, timeout=60,
        )
        return proc.returncode, proc.stdout

    monkeypatch.setattr(
        deploy_all_probe, "inspect_mounts", lambda _r, _l: SimpleNamespace(stale=(), unmounted=(), orphaned=())
    )
    monkeypatch.setattr(deploy_all_probe, "_read_home",
                        lambda _a, path: "" if path.endswith(".mode") else legacy_sha)
    monkeypatch.setattr(deploy_all_probe, "_list_home", lambda _a: (".hermes/scripts/d.py",))
    monkeypatch.setattr(deploy_all_observe, "observe_node",
                        lambda root, **kw: deploy_all_observe.collect(root, runner, "ops", **kw))
    # This fixture replaces node I/O; include the new account readback seam too.
    monkeypatch.setattr(notice_bot_release, "observe",
                        lambda *_: {"agent": "ok", "peer": "ok", "ops": "ok"})

    rc = deploy_all_probe.main(["--dori-agent-chat", "off", "--hermes-gateways", "running",
                                "--runtime-root", str(runtime), "--live-root", str(tmp_path / "live"), "--format", "receipt"]
    )

    assert rc == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["notice_bot"] == {
        "digest": "off", "accounts": {"agent": "ok", "peer": "ok", "ops": "ok"},
    }
    assert receipt["held"] == [
        {"account": "agent", "destination": ".hermes/held/h.py", "reason": "owner-migrates-legacy-state-first"}
    ]


def test_the_standing_probe_accepts_a_v2_receipt(tmp_path: Path) -> None:
    receipt = tmp_path / "receipt.json"
    _ = receipt.write_text(deploy_receipt.render_receipt(_plan(*_HELPER_FAIL), verified_at="t"), encoding="utf-8")

    result = _run_probe(tmp_path, receipt, _SHA)

    assert result.returncode == 0, result.stderr
    assert "RECEIPT-PASS" in result.stderr


def _run_probe(tmp_path: Path, receipt: Path, current_sha: str) -> subprocess.CompletedProcess[str]:
    store = tmp_path / "releases" / current_sha
    store.mkdir(parents=True, exist_ok=True)
    current = tmp_path / "current"
    if current.is_symlink():
        current.unlink()
    current.symlink_to(store)
    return subprocess.run(
        ("bash", "-c", f'source "{_PROBE}"; probe_release_fully_deployed node ops "{receipt}"'),
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", "HEALTHCHECK_RELEASE_SOURCE_ROOT": str(current)},
    )


def test_non_clean_plan_is_never_attested() -> None:
    for plan in (
        _plan(_artifact("a", "stale", have="x")),
        _plan(_artifact("a", "unknown", have="?")),
        parse_observations(
            [f"OBS|release|{_SHA}", "OBS|mounts|judged", "OBS|mount-stale|meeting|a|b",
             "OBS|home|agent|.hermes/scripts/d.py|automation/pkg/d.py|required|aaa|aaa", "OBS|end"]
        ),
    ):
        with pytest.raises(ObservationError):
            _ = deploy_receipt.render_receipt(plan, verified_at="t")


@pytest.mark.parametrize("version", [1, 0, "2", True, None])
def test_v1_receipt_cannot_attest_the_current_release(tmp_path: Path, version: object) -> None:
    receipt = tmp_path / "receipt.json"
    _ = receipt.write_text(json.dumps({"release_sha": _SHA, "version": version}), encoding="utf-8")

    result = _run_probe(tmp_path, receipt, _SHA)

    assert result.returncode != 0
    assert "RECEIPT-PASS" not in result.stderr


def test_a_receipt_without_a_version_cannot_attest_the_current_release(tmp_path: Path) -> None:
    receipt = tmp_path / "receipt.json"
    _ = receipt.write_text(json.dumps({"release_sha": _SHA}), encoding="utf-8")

    result = _run_probe(tmp_path, receipt, _SHA)

    assert result.returncode != 0
    assert "RECEIPT-PASS" not in result.stderr


def test_an_old_v2_receipt_for_another_release_cannot_attest(tmp_path: Path) -> None:
    receipt = tmp_path / "receipt.json"
    _ = receipt.write_text(deploy_receipt.render_receipt(_plan(), verified_at="t"), encoding="utf-8")

    result = _run_probe(tmp_path, receipt, "fff999")

    assert result.returncode != 0
    assert "RECEIPT-STALE" in result.stderr


def test_delegated_probes_that_could_not_run_are_reported_as_unconfirmed() -> None:
    plan = _plan("OBS|pending-unknown|delegated-probes-unavailable")

    receipt = _receipt(plan)

    assert receipt["pending_unknown"] is True
    assert receipt["pending_owner_actions"] == []
    lines = deploy_receipt.pending_lines(receipt)
    assert any("확인 불가" in line for line in lines)


def test_pending_actions_carry_the_probes_own_guidance() -> None:
    receipt = _receipt(_plan(*_HELPER_FAIL, *_WRAPPER_UNKNOWN))

    lines = deploy_receipt.pending_lines(receipt)

    assert any(line.endswith(_HELPER_GUIDANCE) for line in lines)
    assert any(line.endswith(f"WRAPPER-DRIFT-UNKNOWN {_WRAPPER_GUIDANCE}") for line in lines)
    unknown = [line for line in lines if "healthcheck probe allowlist" in line]
    assert len(unknown) == 1 and "확인 불가" in unknown[0]


@pytest.mark.parametrize(
    "document",
    [
        {"version": 1, "release_sha": _SHA, "delegated": ["release-helpers"]},
        {"release_sha": _SHA},
        {"version": 2},
        {"version": 2, "pending_owner_actions": "nope", "held": [1, None, {"reason": 3}]},
        {"version": 2, "pending_owner_actions": [{"guidance": [None, 4]}, "x"], "pending_unknown": "yes"},
        [],
        None,
        "text",
    ],
)
def test_pending_lines_never_raise_on_old_or_partial_documents(document: object) -> None:
    lines = deploy_receipt.pending_lines(document)

    assert isinstance(lines, tuple)
    assert all(isinstance(line, str) for line in lines)
    if not isinstance(document, dict) or document.get("version") != 2:
        assert lines == ()


def test_node_text_cannot_add_lines_or_mentions() -> None:
    receipt = _receipt(
        _plan(
            "OBS|pending|release_helper_drift|primary|FAIL|check @everyone",
            "OBS|pending-detail|release_helper_drift|primary|run it\u2028<@123> now\x1b[31m",
        )
    )

    lines = deploy_receipt.pending_lines(receipt)

    assert all("\n" not in line and "\u2028" not in line and "\x1b" not in line for line in lines)
    assert not any("@everyone" in line or "<@123>" in line for line in lines)


@pytest.mark.parametrize("text", ["@everyone <@123> a@b", "plain", "@\u200beveryone", "x\n@here\x1b"])
def test_escaping_is_idempotent_across_the_workstation_and_the_node(text: str) -> None:
    once = deploy_receipt.one_line(text)
    framed = deploy_receipt.notice_line(f"  · {text}")

    assert deploy_receipt.one_line(once) == once
    assert deploy_receipt.notice_line(framed) == framed
    assert deploy_receipt.notice_line(once) == once


def test_a_newer_receipt_version_keeps_its_pending_list() -> None:
    document = {**_receipt(_plan(*_HELPER_FAIL)), "version": 3}

    lines = deploy_receipt.pending_lines(document)

    assert any(line.endswith(_HELPER_GUIDANCE) for line in lines)


def test_a_roster_check_that_could_not_run_reaches_the_owner_as_unconfirmed(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    (root / "automation/pkg").mkdir(parents=True)
    _ = (root / "automation/pkg/p.py").write_text("p = 1\n", encoding="utf-8")
    _ = (root / "automation/pkg/deploy-manifest.txt").write_text(
        "agent|automation/pkg/p.py|.hermes/plugins/p/p.py|v2:file;requires=roster;activation=gateway\n"
        "peer|automation/pkg/p.py|.hermes/plugins/p/p.py|v2:file;requires=roster;activation=gateway\n",
        encoding="utf-8",
    )
    no_python = tmp_path / "empty-bin"
    no_python.mkdir()

    def runner(account: str, args: tuple[str, ...]) -> tuple[int, str]:
        if args[0] == "delegated":
            return 0, "DELEGATED-END|0\n"
        home = tmp_path / "homes" / account
        home.mkdir(parents=True, exist_ok=True)
        path = str(no_python) if args[0] == "roster" else os.environ["PATH"]
        proc = subprocess.run(
            ("/bin/bash", "-c", deploy_all_observe.REMOTE_SCRIPT, "_", *args),
            env={"HOME": str(home), "PATH": path}, capture_output=True, text=True, check=False, timeout=60,
        )
        return proc.returncode, proc.stdout

    observed = deploy_all_observe.collect(root, runner, "ops")
    plan = parse_observations(
        [f"OBS|release|{_SHA}", "OBS|mounts|judged",
         "OBS|home|agent|.hermes/scripts/d.py|automation/pkg/d.py|required|aaa|aaa", *observed, "OBS|end"]
    )

    receipt = _receipt(plan)
    lines = deploy_receipt.pending_lines(receipt)

    held_items = receipt["held"]
    assert isinstance(held_items, list)
    assert [item["reason"] for item in held_items] == ["roster-unverified", "roster-unverified"]
    held = [line for line in lines if line.startswith("- 보류 2건 ")]
    assert len(held) == 1
    assert "확인 불가" in held[0]
    assert not any("init-local" in line for line in lines)
