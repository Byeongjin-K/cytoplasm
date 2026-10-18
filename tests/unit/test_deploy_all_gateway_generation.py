"""게이트웨이 세대가 전량 반영의 조건이다 — 관측(`OBS|gateway|…`)·판정·재시동 뒤 대기·영수증.

파일을 놓는 것만으로는 반영이 아니다: 게이트웨이가 다시 떠서 현재 릴리스에서 게이트 플러그인을
등록했다는 기록(`automation.gateway_generation`)까지가 반영이다. 셸 경계는 실제
`automation/deploy_all.sh --apply` 를 가짜 ssh·가짜 배포기로 돌리고, 그 가짜 ssh 에 넘기는 행동
목록은 진짜 판정 코어(`deploy_all.render_actions`)가 같은 관측에서 만든 것이다 — 판정과 실행이
따로 맞고 함께 틀리는 일이 없게. 대기는 `DEPLOY_ALL_GATEWAY_WAIT_INTERVAL=0` 이라 실제로 자지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Final

import pytest

from automation import deploy_all, deploy_all_gateway, deploy_all_observe, deploy_all_probe, deploy_receipt

_REPO: Final = Path(__file__).resolve().parents[2]
_COMMAND: Final = _REPO / "automation" / "deploy_all.sh"
_EXAMPLE_CONFIG: Final = _REPO / "configs" / "node.example.toml"
_SHA: Final = "a" * 64
_DROPIN: Final = ".config/systemd/user/hermes-gateway.service.d/20-interop-guard.conf"
_PLUGIN: Final = ".hermes/plugins/interop-protocol/__init__.py"
_DEPLOYER: Final = "automation/interop/deploy.sh"


def _artifact(account: str, destination: str, status: str, have: str = _SHA) -> str:
    return (f"OBS|artifact|file|{account}|{destination}|automation/interop|required|gateway"
            f"|{status}|{_SHA}|{have}")


def _plan(artifacts: list[str], gateways: list[str]) -> deploy_all.Plan:
    return deploy_all.parse_observations([
        "OBS|release|rel-2", "OBS|mounts|judged",
        f"OBS|home|agent|.hermes/scripts/x.py|automation/x/x.py|required|{_SHA}|{_SHA}",
        *artifacts, *gateways, f"OBS|artifacts|{len(artifacts)}", "OBS|end",
    ])


def _dropins(status: str) -> list[str]:
    return [_artifact(account, _DROPIN, status) for account in ("agent", "peer")]


def _gw(account: str, status: str, have: str = "rel-1", detail: str = "import-root") -> str:
    if status == "ok":
        have, detail = "rel-2", "-"
    return f"OBS|gateway|{account}|{status}|rel-2|{have}|{detail}"



_FAKE_SSH: Final = r"""#!/usr/bin/env bash
set -uo pipefail
cmd="${*: -1}"
printf "%s\n" "$cmd" >> "$FAKE_CALLS"
case "$cmd" in
  readlink*) printf "/srv/releases/%s\n" "$FAKE_HEAD"; exit 0 ;;
  *"--format actions"*) cat "$FAKE_ACTIONS"; exit "$FAKE_ACTIONS_RC" ;;
  *"--format gateways"*)
    n="$(cat "$FAKE_GW_COUNTER" 2>/dev/null || printf 0)"; n=$((n + 1)); printf "%s" "$n" > "$FAKE_GW_COUNTER"
    f="$FAKE_GW_DIR/$n"; [[ -e "$f" ]] || f="$FAKE_GW_DIR/last"
    cat "$f"; exit "$(cat "$f.rc")" ;;
  *"systemctl --user restart"*) printf "active\n"; exit 0 ;;
  *"--format report"*) printf "DEPLOY-ALL: clean\n"; exit 0 ;;
  *"--format receipt"*) printf '{"release_sha":"%s"}\n' "$FAKE_HEAD"; exit 0 ;;
  *"cat >"*"/receipt.json"*) cat > "$FAKE_RECEIPT"; exit 0 ;;
  *"sha256sum"*"/receipt.json"*) sha256sum "$FAKE_RECEIPT" | cut -d" " -f1; exit 0 ;;
esac
printf "unexpected ssh command: %s\n" "$cmd" >&2
exit 97
"""


def _apply(
    tmp_path: Path,
    plan: deploy_all.Plan,
    gateway_answers: list[tuple[int, str]],
    *,
    deployer_rc: int = 0,
    wait_seconds: str = "120",
) -> subprocess.CompletedProcess[str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    ssh = fake_bin / "ssh"
    _ = ssh.write_text(_FAKE_SSH, encoding="utf-8")
    ssh.chmod(0o755)
    deployer = tmp_path / "deployer.sh"
    _ = deployer.write_text(
        f'#!/usr/bin/env bash\nprintf "deploy\\n" >> "{tmp_path}/deployer.log"\nexit {deployer_rc}\n',
        encoding="utf-8",
    )
    deployer.chmod(0o755)
    actions = deploy_all.render_actions(plan).replace(_DEPLOYER, os.path.relpath(deployer, _REPO))
    _ = (tmp_path / "actions").write_text(actions + "\n", encoding="utf-8")
    answers = tmp_path / "gw"
    answers.mkdir()
    for index, (rc, out) in enumerate(gateway_answers, start=1):
        for name in (str(index), "last") if index == len(gateway_answers) else (str(index),):
            _ = (answers / name).write_text(out + "\n", encoding="utf-8")
            _ = (answers / f"{name}.rc").write_text(str(rc), encoding="utf-8")
    head = subprocess.check_output(("git", "rev-parse", "HEAD"), cwd=_REPO, text=True).strip()
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "DEPLOY_SSH_HOST": "fake-node",
        "DEPLOY_ALL_WRAPPER_REFRESH_CMD": "true",
        "DEPLOY_ALL_RAG_MCP_REFRESH_CMD": "true",
        "DEPLOY_ALL_RECEIPT_DIR": str(tmp_path / "private" / "deploy-all"),
        "DEPLOY_ALL_LOCK_DIR": str(tmp_path / "locks"),
        "DEPLOY_ALL_GATEWAY_WAIT_SECONDS": wait_seconds,
        "DEPLOY_ALL_GATEWAY_WAIT_INTERVAL": "0",
        "FAKE_CALLS": str(tmp_path / "calls.log"),
        "FAKE_RECEIPT": str(tmp_path / "receipt.json"),
        "FAKE_HEAD": head,
        "FAKE_ACTIONS": str(tmp_path / "actions"),
        "FAKE_ACTIONS_RC": "0" if plan.clean else "1",
        "FAKE_GW_DIR": str(answers),
        "FAKE_GW_COUNTER": str(tmp_path / "gw-counter"),
        "HEALTHCHECK_NODE_CONFIG_PATH": str(_EXAMPLE_CONFIG),
    }
    return subprocess.run(("bash", str(_COMMAND), "--apply"), cwd=_REPO, env=env,
                          capture_output=True, text=True, check=False)


def _calls(tmp_path: Path) -> list[str]:
    return (tmp_path / "calls.log").read_text(encoding="utf-8").splitlines()


def _restarts(tmp_path: Path) -> list[str]:
    return [call for call in _calls(tmp_path) if "systemctl --user restart" in call]


def _assert_pair_restarted(tmp_path: Path) -> None:
    restarts = _restarts(tmp_path)
    assert len(restarts) == 2
    assert "sudo -n -u agent " in restarts[0]
    assert "sudo -n -u peer " in restarts[1]


def _gateway_probes(tmp_path: Path) -> int:
    return sum("--format gateways" in call for call in _calls(tmp_path))


def test_a_stale_generation_restarts_the_pair_and_then_passes(tmp_path: Path) -> None:
    plan = _plan(_dropins("ok"), [_gw("agent", "stale"), _gw("peer", "stale", detail="pid")])
    assert "ACT|restart-gateway|agent+peer" in deploy_all.render_actions(plan).splitlines()
    assert not plan.clean

    result = _apply(tmp_path, plan, [(1, _gw("agent", "stale", detail="no-record")),
                                      (0, f"{_gw('agent', 'ok')}\n{_gw('peer', 'ok')}")])

    assert result.returncode == 0, result.stderr
    _assert_pair_restarted(tmp_path)
    assert _gateway_probes(tmp_path) == 2
    calls = "\n".join(_calls(tmp_path))
    assert calls.index("systemctl --user restart") < calls.index("--format gateways")
    assert calls.index("--format gateways") < calls.index("--format report") < calls.index("--format receipt")
    assert (tmp_path / "receipt.json").exists()


def test_the_first_bundle_deploy_waits_even_though_the_plan_had_no_gateway_line(
    tmp_path: Path,
) -> None:
    plan = _plan([*_dropins("absent"), _artifact("agent", _PLUGIN, "stale", have="b" * 64)], [])
    assert plan.gateways == ()
    actions = deploy_all.render_actions(plan).splitlines()
    assert f"ACT|run-deployer|{_DEPLOYER}" in actions
    assert "ACT|restart-gateway|agent+peer" in actions

    result = _apply(tmp_path, plan, [(1, _gw("agent", "stale", detail="no-record")),
                                      (1, _gw("peer", "stale", detail="plugin:interop-protocol")),
                                      (0, f"{_gw('agent', 'ok')}\n{_gw('peer', 'ok')}")])

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "deployer.log").read_text(encoding="utf-8") == "deploy\n"
    _assert_pair_restarted(tmp_path)
    assert _gateway_probes(tmp_path) == 3
    assert (tmp_path / "receipt.json").exists()


def test_an_unknown_reading_right_after_the_restart_is_waited_out(tmp_path: Path) -> None:
    """재시동은 exec 직후 돌아온다 — pidfile 이 바뀌는 순간의 unknown 은 판정이 아니라 기다릴 이유다."""
    plan = _plan(_dropins("ok"), [_gw("agent", "stale"), _gw("peer", "ok")])

    result = _apply(tmp_path, plan, [(1, _gw("agent", "unknown", have="-", detail="pidfile")),
                                      (0, f"{_gw('agent', 'ok')}\n{_gw('peer', 'ok')}")])

    assert result.returncode == 0, result.stderr
    _assert_pair_restarted(tmp_path)
    assert _gateway_probes(tmp_path) == 2


def test_an_unknown_generation_blocks_without_a_restart(tmp_path: Path) -> None:
    plan = _plan(_dropins("ok"), [_gw("agent", "unknown", have="-", detail="pidfile"), _gw("peer", "ok")])
    assert not plan.clean
    assert not plan.gateway_restart_needed
    assert "ACT|manual|gateway-unknown:agent" in deploy_all.render_actions(plan).splitlines()

    result = _apply(tmp_path, plan, [(0, _gw("agent", "ok"))])

    assert result.returncode == 1
    assert _restarts(tmp_path) == []
    assert _gateway_probes(tmp_path) == 0
    assert not (tmp_path / "receipt.json").exists()


def test_a_failed_deployer_prevents_the_restart(tmp_path: Path) -> None:
    plan = _plan(_dropins("stale"), [])

    result = _apply(tmp_path, plan, [(0, _gw("agent", "ok"))], deployer_rc=1)

    assert result.returncode == 1
    assert "RESTART-SKIPPED: deployer failures — a partial deploy is not activated" in result.stderr
    assert _restarts(tmp_path) == []
    assert _gateway_probes(tmp_path) == 0
    assert not (tmp_path / "receipt.json").exists()


def test_a_generation_that_never_turns_ok_is_incomplete(tmp_path: Path) -> None:
    plan = _plan(_dropins("ok"), [_gw("agent", "stale"), _gw("peer", "stale")])

    result = _apply(tmp_path, plan, [(1, _gw("agent", "stale", detail="no-record"))], wait_seconds="0")

    assert result.returncode == 1
    _assert_pair_restarted(tmp_path)
    assert _gateway_probes(tmp_path) == 1
    assert "gateway-generation" in result.stderr
    assert not any("--format report" in call for call in _calls(tmp_path))
    assert not (tmp_path / "receipt.json").exists()




def test_a_held_generation_is_clean_and_listed_in_the_receipt(tmp_path: Path) -> None:
    root = _runtime(tmp_path)
    lines = deploy_all_observe.collect(root, _runner({"roster": (1, "")}), "ops")
    gateway = [line for line in lines if line.startswith("OBS|gateway|")]
    assert gateway == ["OBS|gateway|agent|held|-|-|roster-required",
                       "OBS|gateway|peer|held|-|-|roster-required"]

    plan = deploy_all.parse_observations(_wrap(lines))

    assert plan.clean
    assert not plan.gateway_restart_needed
    receipt = json.loads(deploy_all.render_receipt(plan, verified_at="2026-10-01T00:00:00+00:00"))
    for account in ("agent", "peer"):
        assert {"account": account, "destination": "gateway-generation",
                "reason": "roster-required"} in receipt["held"]


def test_a_stale_drop_in_row_emits_no_gateway_line(tmp_path: Path) -> None:
    root = _runtime(tmp_path)
    seen: list[tuple[str, ...]] = []
    lines = deploy_all_observe.collect(root, _runner({"roster": (0, ""), "sha": (0, "b" * 64 + "\n")}, seen), "ops")

    assert not [line for line in lines if line.startswith("OBS|gateway|")]
    assert not [args for args in seen if args[0] == "gateway"]
    plan = deploy_all.parse_observations(_wrap(lines))
    assert not plan.clean
    assert plan.gateways == ()


def test_an_ok_drop_in_row_runs_the_check_with_the_declared_plugins(tmp_path: Path) -> None:
    root = _runtime(tmp_path)
    seen: list[tuple[str, ...]] = []
    shas = _sources(root)
    out = f"GATEWAY-GENERATION ok want={root.resolve().name} have={root.resolve().name} detail=-\n"

    lines = deploy_all_observe.collect(root, _runner({"roster": (0, ""), "gateway": (0, out)}, seen, shas), "ops")

    checks = [args for args in seen if args[0] == "gateway"]
    assert checks == [("gateway", str(root), "--require", "interop-protocol")] * 2
    assert [line for line in lines if line.startswith("OBS|gateway|")] == [
        f"OBS|gateway|{account}|ok|{root.resolve().name}|{root.resolve().name}|-" for account in ("agent", "peer")
    ]
    assert deploy_all.parse_observations(_wrap(lines)).clean


@pytest.mark.parametrize(
    ("rc", "out", "detail"),
    [
        (0, "GATEWAY-GENERATION stale want=W have=W detail=-", "unreadable-check"),
        (0, "GATEWAY-GENERATION ok want=W have=W detail=-\nGATEWAY-GENERATION ok want=W have=W detail=-",
         "unreadable-check"),
        (0, "GATEWAY-GENERATION ok want=W have=W|x detail=-", "unreadable-check"),
        (0, "GATEWAY-GENERATION ok want=W have=W detail=\x1b[31m", "unreadable-check"),
        (0, "GATEWAY-GENERATION ok want=other have=other detail=-", "inconsistent-check"),
        (0, "GATEWAY-GENERATION ok want=W have=rel-1 detail=-", "inconsistent-check"),
        (127, "", "unreadable-check"),
    ],
)
def test_misleading_or_malformed_check_output_is_unknown(
    tmp_path: Path, rc: int, out: str, detail: str
) -> None:
    root = _runtime(tmp_path)
    name = root.resolve().name
    answer = (rc, out.replace("=W", f"={name}"))

    lines = deploy_all_observe.collect(root, _runner({"roster": (0, ""), "gateway": answer}, shas=_sources(root)), "ops")

    gateway = [line.split("|") for line in lines if line.startswith("OBS|gateway|")]
    assert [(parts[3], parts[6]) for parts in gateway] == [("unknown", detail)] * 2
    plan = deploy_all.parse_observations(_wrap(lines))
    assert not plan.clean
    assert not plan.gateway_restart_needed


@pytest.mark.parametrize(
    "line",
    [
        "OBS|gateway|agent|ok|rel-2|rel-1|-",  # ok 이면서 세대가 다르다
        "OBS|gateway|agent|held|-|-|-",  # held 에 사유가 없다
        "OBS|gateway|agent|fine|rel-2|rel-2|-",  # 모르는 상태
        "OBS|gateway|agent|stale|rel-2|rel 1|pid",  # 토큰이 아니다
        "OBS|gateway|agent|stale|rel-2|rel-1",  # 칸이 모자란다
        "OBS|gateway|ag;ent|stale|rel-2|rel-1|pid",  # 계정 모양이 아니다
    ],
)
def test_malformed_gateway_lines_are_unverifiable(line: str) -> None:
    with pytest.raises(deploy_all.ObservationError):
        _ = _plan(_dropins("ok"), [line, _gw("peer", "ok")])


@pytest.mark.parametrize(
    ("dropins", "gateways"),
    [
        ("ok", [_gw("agent", "ok")]),  # peer 의 드롭인은 ok 인데 세대 줄이 없다 — 깨끗함으로 읽으면 안 된다
        ("ok", [_gw("agent", "ok"), _gw("peer", "ok"), _gw("peer", "ok")]),  # 같은 계정 두 줄
        ("ok", [_gw("agent", "ok"), "OBS|gateway|peer|held|-|-|roster-required"]),  # 보류가 아닌 행에 held
        ("stale", [_gw("agent", "ok"), _gw("peer", "ok")]),  # 결함인 드롭인 행에 세대 줄
        ("ok", [_gw("agent", "ok"), _gw("peer", "ok"), _gw("ops", "ok")]),  # 드롭인 없는 계정
    ],
)
def test_gateway_lines_must_match_the_drop_in_rows(dropins: str, gateways: list[str]) -> None:
    with pytest.raises(deploy_all.ObservationError):
        _ = _plan(_dropins(dropins), gateways)


def test_the_gateway_probe_reports_only_gateway_lines_and_fails_while_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    answers = {"stale": [_gw("agent", "stale"), _gw("peer", "ok")], "ok": [_gw("agent", "ok")],
               "held": ["OBS|gateway|agent|held|-|-|roster-required"], "bad": ["OBS|gateway|agent|ok|x|y|-"]}
    for case, want_rc in (("stale", 1), ("ok", 0), ("held", 0), ("bad", 4)):
        calls: list[bool] = []

        def fake(root: Path, *, gateway_only: bool = False, _case: str = case,
                 dori_agent_chat: str = "off", hermes_gateways: str = "running",
                 report_hub: str = "on", hermes_dashboard: str = "on", hermes_memory: str = "on",
                 agent_chat_channel: str = "off", peer_retired: bool = False) -> list[str]:
            calls.append(gateway_only)
            return answers[_case]

        monkeypatch.setattr(deploy_all_observe, "observe_node", fake)
        rc = deploy_all_probe.main(["--dori-agent-chat", "off", "--hermes-gateways", "running",
                                    "--runtime-root", str(tmp_path), "--format", "gateways"])
        out = capsys.readouterr().out
        assert (case, rc) == (case, want_rc)
        assert calls == [True]
        if want_rc != 4:
            assert out.splitlines() == answers[case]




def _runtime(tmp_path: Path) -> Path:
    root = tmp_path / "rel-2"
    rows = []
    for account in ("agent", "peer"):
        rows += [
            f"{account}|automation/interop/shim.py|{_PLUGIN}|v2:file;requires=roster;activation=gateway",
            f"{account}|automation/interop/dropin.conf|{_DROPIN}|v2:file;requires=roster;activation=gateway",
        ]
    for name, text in (("deploy-manifest.txt", "\n".join(rows) + "\n"), ("shim.py", "x = 1\n"),
                       ("dropin.conf", "[Service]\n")):
        path = root / "automation/interop" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text(text, encoding="utf-8")
    return root


def _sources(root: Path) -> dict[str, str]:
    return {
        _PLUGIN: hashlib.sha256((root / "automation/interop/shim.py").read_bytes()).hexdigest(),
        _DROPIN: hashlib.sha256((root / "automation/interop/dropin.conf").read_bytes()).hexdigest(),
    }


def _runner(
    answers: dict[str, tuple[int, str]],
    seen: list[tuple[str, ...]] | None = None,
    shas: dict[str, str] | None = None,
) -> deploy_all_observe.Runner:
    def run(account: str, args: tuple[str, ...]) -> tuple[int, str]:
        assert account in ("agent", "peer", "ops")
        if seen is not None:
            seen.append(args)
        if args[0] == "delegated":
            return 0, "DELEGATED-END|0\n"
        if args[0] == "sha" and shas is not None:
            return 0, shas[args[1]] + "\n"
        if args[0] == "gateway-service":
            return 0, "active\nunmarked\n"
        return answers.get(args[0], (1, ""))

    return run


def _wrap(lines: list[str]) -> list[str]:
    return ["OBS|release|rel-2", "OBS|mounts|judged",
            f"OBS|home|agent|.hermes/scripts/x.py|automation/x/x.py|required|{_SHA}|{_SHA}",
            *lines, "OBS|end"]


def test_the_module_names_the_drop_in_destination_the_interop_bundle_declares() -> None:
    manifest = (_REPO / "automation/interop/deploy-manifest.txt").read_text(encoding="utf-8")
    assert f"|{deploy_all_gateway.DROPIN}|" in manifest


_ROSTER_EXPECTED: Final = {
    0: "roster-sibling", 1: "roster-required", 127: "roster-unverified",
}
_GUIDANCE: Final = {
    "roster-required": deploy_receipt._ROSTER_GUIDANCE,
    "roster-unverified": deploy_receipt._ROSTER_UNVERIFIED,
    "roster-sibling": deploy_receipt._ROSTER_SIBLING,
}


@pytest.mark.parametrize(("agent_rc", "peer_rc"), [(0, 1), (1, 0), (1, 127), (127, 127), (0, 127)])
def test_a_held_generation_carries_each_accounts_own_roster_reason(
    tmp_path: Path, agent_rc: int, peer_rc: int
) -> None:
    """보류 사유는 계정마다의 roster 판정이다 — 검증하지 못한 계정이나 정상 계정에 init-local 을 말하지 않는다."""
    root = _runtime(tmp_path)
    roster = {"agent": agent_rc, "peer": peer_rc}

    def run(account: str, args: tuple[str, ...]) -> tuple[int, str]:
        if args[0] == "gateway-service":
            return 0, "active\nunmarked\n"
        if args[0] == "roster":
            return roster[account], ""
        if args[0] == "delegated":
            return 0, "DELEGATED-END|0\n"
        return 1, ""

    lines = deploy_all_observe.collect(root, run, "ops")
    plan = deploy_all.parse_observations(_wrap(lines))
    receipt = json.loads(deploy_all.render_receipt(plan, verified_at="2026-10-01T00:00:00+00:00"))
    pending = deploy_receipt.pending_lines(receipt)

    want = {account: _ROSTER_EXPECTED[rc] for account, rc in roster.items()}
    assert plan.clean
    assert {g.account: g.detail for g in plan.gateways} == want
    gateway_held = {e["account"]: e["reason"] for e in receipt["held"] if e["destination"] == "gateway-generation"}
    assert gateway_held == want
    for account, reason in want.items():
        assert {e["reason"] for e in receipt["held"] if e["account"] == account} == {reason}
    counts = {reason: 3 * list(want.values()).count(reason) for reason in set(want.values())}
    guided = [line for line in pending if line.startswith("- 보류 ")]
    assert sorted(guided) == sorted(f"- 보류 {n}건 — {_GUIDANCE[reason]}" for reason, n in counts.items())
    assert any(deploy_receipt._ROSTER_GUIDANCE in line for line in pending) == ("roster-required" in want.values())


@pytest.mark.parametrize("reason", ["roster-other", "held", "-"])
def test_a_held_gateway_line_with_an_unknown_reason_is_unverifiable(reason: str) -> None:
    with pytest.raises(deploy_all.ObservationError):
        _ = _plan(_dropins("held"), [f"OBS|gateway|agent|held|-|-|{reason}",
                                     "OBS|gateway|peer|held|-|-|roster-required"])
