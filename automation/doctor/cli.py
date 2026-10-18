"""`python3 -m automation.doctor` — 서비스 계정(agent·peer)에서는 자기 상태를, 그 밖의
계정(운영자·root)에서는 `sudo -n -u <계정>` 으로 각 계정의 진단을 돌려 모아 보여준다.

종료코드: 0 고장 없음 · 1 고장 있음 · 2 사용법 오류. 확인(WARN)은 종료코드를 바꾸지 않는다.
"""
from __future__ import annotations

import argparse
import json
import os
import pwd
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Final, cast

from automation import model_sync
from automation.doctor import alarm, report
from automation.doctor.capabilities import Finding, evaluate
from automation.doctor.facts import Role, gather
from automation.install.checks import Status
from automation.node_config import NodeConfig, NodeConfigError, load_node_config

ChildRun = Callable[[tuple[str, ...], Path], tuple[int, str, str]]
_REPO: Final = Path(__file__).resolve().parents[2]
_DEFAULT_UNIT: Final = "hermes-gateway.service"


class _Arguments(argparse.Namespace):
    json: bool = False
    offline: bool = False
    notify: bool = False
    role: str | None = None
    account: list[str] = []


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m automation.doctor",
                                     description="연결·승인·동작 점검 — 읽기 전용")
    _ = parser.add_argument("--json", action="store_true", help="기계용 JSON 출력")
    _ = parser.add_argument("--offline", action="store_true", help="네트워크 검사(Discord API·gws 토큰) 생략")
    _ = parser.add_argument("--notify", action="store_true", help="바뀐 문제만 소유자에게 알린다(서비스 계정 전용)")
    _ = parser.add_argument("--role", choices=("agent", "peer"), default=None, help="이 계정의 역할을 명시한다")
    _ = parser.add_argument("--account", action="append", default=[], metavar="NAME",
                            help="운영자 모드에서 점검할 계정(반복 가능, 기본: agent·peer)")
    return parser


def _config() -> NodeConfig | None:
    try:
        return load_node_config()
    except (NodeConfigError, OSError):
        return None


def _role_of(account: str, config: NodeConfig | None) -> Role | None:
    agent, peer = (config.agent_account, config.peer_account) if config else ("agent", "peer")
    return "agent" if account == agent else "peer" if account == peer else None


def _unit(role: Role, config: NodeConfig | None) -> str:
    if config is None:
        return _DEFAULT_UNIT
    return config.agent_gateway_unit if role == "agent" else config.peer_gateway_unit


def _code_root(config: NodeConfig | None) -> Path:
    candidates = ((config.release_current,) if config else ()) + (_REPO,)
    return next((root for root in candidates if (root / "automation" / "doctor" / "cli.py").is_file()), _REPO)


def _child(argv: tuple[str, ...], cwd: Path) -> tuple[int, str, str]:
    try:
        done = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=300, check=False)  # noqa: S603
    except (OSError, subprocess.TimeoutExpired) as error:
        return 127, "", type(error).__name__
    return done.returncode, done.stdout, done.stderr


def _account() -> str:
    return pwd.getpwuid(os.geteuid()).pw_name


def _worst(findings: Sequence[Finding]) -> int:
    return 1 if any(f.status is Status.FAIL for f in findings) else 0


def _unreachable(account: str, root: Path, code: int, stderr: str) -> str:
    tail = next((line for line in reversed(stderr.splitlines()) if line.strip()), f"rc={code}")
    return "\n".join((
        f"[WARN] doctor-run · {account} 계정 진단 — 실행하지 못했다: {tail[:160]}",
        f"  수동: cd {root} && sudo -u {account} -H python3 -m automation.doctor",
        "  (sudo 비밀번호가 필요하거나, 그 계정이 코드를 읽지 못하면 이렇게 된다 — 설치 직후라면 릴리스 수렴(약 2분) 뒤 다시 실행한다)",
    ))


def _peer_retired(config: NodeConfig | None) -> bool:
    """P5 writes this ops-owned marker only after the release removed the peer's Hermes."""
    if config is None:
        return False
    try:
        return (config.private_root / "peer-retired").read_bytes() == b"retired"
    except OSError:
        return False


def _operator(args: _Arguments, config: NodeConfig | None, run_child: ChildRun) -> int:
    retired = _peer_retired(config)
    defaults = [config.agent_account, config.peer_account] if config else ["agent", "peer"]
    if retired:
        defaults = defaults[:1]
    accounts = args.account or defaults
    root = _code_root(config)
    worst, payloads = 0, []
    for account in accounts:
        role = _role_of(account, config) or "agent"
        argv = ("sudo", "-n", "-u", account, "-H", "env", f"PYTHONPATH={root}", "python3", "-m",
                "automation.doctor", "--json", "--role", role, *(("--offline",) if args.offline else ()))
        code, out, err = run_child(argv, root)
        try:
            parsed = report.from_json(json.loads(out))
        except ValueError:
            parsed = None
        if parsed is None:
            payloads.append({"account": account, "role": role, "error": "unreachable"})
            print(_unreachable(account, root, code, err))
            continue
        _, _, findings = parsed
        worst = max(worst, _worst(findings))
        payloads.append(report.to_json(account, role, findings))
        if not args.json:
            print(report.render_text(account, role, findings) + "\n")
    parity = ("[PASS] model-parity · peer 는 P5로 퇴역했다 — 비교하지 않는다(정상)" if retired
              else _model_parity(config, root, run_child))
    payloads.append({"check": "model-parity", "line": parity})
    from automation.interop.approval_directory import CHANNEL_OVERRIDE_NOTICE, channel_override

    overridden = channel_override() is not None
    from automation.notice_bot import NOTICE, read_override

    notice_override = read_override() is not None
    if notice_override:
        payloads.append({"check": "notice-bot", "status": "PASS", "line": NOTICE})
    if overridden:
        payloads.append({"check": "channel-override", "status": "PASS", "line": CHANNEL_OVERRIDE_NOTICE})
    if args.json:
        print(json.dumps(payloads, ensure_ascii=False, indent=2))
    else:
        print(parity)
        if overridden:
            print(f"[PASS] {CHANNEL_OVERRIDE_NOTICE}")
        if notice_override:
            print(f"[PASS] {NOTICE}")
    return worst


def _model_parity(config: NodeConfig | None, root: Path, run_child: ChildRun) -> str:
    """peer 는 agent 의 주 모델·폴백을 따른다(2026-09-29) — 어긋나면 운영자에게 한 줄로 알린다."""
    agent, peer = (config.agent_account, config.peer_account) if config else ("agent", "peer")
    try:
        plan = model_sync.plan(agent, peer, lambda argv, _stdin: run_child(argv, root))
    except model_sync.SyncError as error:
        return f"[WARN] model-parity · {peer} 모델 설정을 {agent} 와 비교하지 못했다 — {error}"
    if plan.in_sync:
        return f"[PASS] model-parity · {peer} 가 {agent} 와 같은 주 모델·폴백을 쓴다"
    return (f"[WARN] model-parity · {peer} 의 주 모델·폴백이 {agent} 와 다르다 — "
            f"cd {root} && python3 -m automation.model_sync --apply 후 게이트웨이 함께 재시작")


def main(argv: Sequence[str] | None = None, *, run_child: ChildRun = _child) -> int:
    args = _Arguments()
    _ = _parser().parse_args(argv, namespace=args)
    config = _config()
    account = _account()
    role = cast("Role | None", args.role) or _role_of(account, config)
    if role is None:
        if args.notify:
            print("DOCTOR-USAGE: --notify 는 agent·peer 계정에서만 쓴다(알람 상태가 그 계정 홈에 있다)", file=sys.stderr)
            return 2
        return _operator(args, config, run_child)
    home = Path.home()
    facts = gather(account=account, role=role, home=home, gateway_unit=_unit(role, config), online=not args.offline)
    findings = evaluate(facts)
    if args.json:
        print(json.dumps(report.to_json(account, role, findings), ensure_ascii=False))
    else:
        print(report.render_text(account, role, findings))
    if args.notify:
        print(alarm.run_alarm(account, findings, home=home, now=datetime.now().astimezone()))
    return _worst(findings)
