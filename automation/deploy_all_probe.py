"""노드에서 배포 전량을 관측·판정한다 (RC-3) — 릴리스 트리에 실려 그 자리에서 돈다.

관측과 판정이 **같은 세대의 코드**여야 한다: 워크스테이션 체크아웃이 노드 릴리스보다
앞서거나 뒤처져 있을 수 있으므로, 이 프로브는 `/srv/autophagy-agent-current` 안의
자기 사본으로 실행되어 그 릴리스가 스스로를 판정하게 한다(`deploy_all.sh` 가 ssh 로
부른다). 판정 로직은 `automation.deploy_all`(순수)이, 마운트 대조는 기존
`skill_mount_drift.inspect_mounts` 가, 홈 배포물 목록은 `watcher_manifest` 의 파생
매니페스트가 소유한다 — 여기는 관측 수집과 배선만 남는다(사본 0).

exit: 0 clean · 1 drift · 4 관측 불가(fail-closed — 보지 못한 것은 깨끗한 것이 아니다)
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

if __package__ in (None, ""):  # pragma: no cover - 스크립트로 직접 실행될 때만
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from automation import deploy_all, deploy_all_gateway, deploy_all_observe  # noqa: E402
from automation.deploy_declarations import all_declarations  # noqa: E402
from automation.dori_pin import PIN_RELPATH, PinError, load_pin  # noqa: E402
from automation.skill_mount_drift import DriftError, inspect_mounts  # noqa: E402
from automation.watcher_manifest import (  # noqa: E402
    CENTRAL_MANIFEST,
    HOME_DEPLOYED_PATTERN,
    ManifestError,
    parse_rows,
)

#: 계정·목적지는 sudo 명령에 박히므로 안전하게 인용할 수 없는 값은 관측하지 않는다 —
#: watcher_drift_probe 와 같은 fail-closed(관측 불가 = "?" = 판정 실패).
_SAFE: Final = deploy_all_observe.SAFE

HomeReader = Callable[[str, str], str]
HomeLister = Callable[[str], tuple[str, ...] | str]
Observer = Callable[[Path], list[str]]


def _read_home(account: str, destination: str) -> str:
    """계정 홈 배포본의 sha256. "" = 없음 · "?" = 읽지 못함(sudo 거부·불통)."""
    if not _SAFE.match(account) or not _SAFE.match(destination):
        return "?"
    proc = subprocess.run(
        (
            "sudo", "-n", "-u", account, "-H", "bash", "-c",
            f'path="$HOME/{destination}"; '
            '[[ -e "$path" ]] || exit 3; '
            'set -o pipefail; sha256sum -- "$path" 2>/dev/null | cut -d" " -f1',
        ),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 3:
        return ""
    if proc.returncode != 0:
        return "?"
    return proc.stdout.strip()


#: 홈 배포물 열거 스크립트. 첫 줄의 ``cd "$HOME"`` 이 하중을 진다 — sudo 로 계정을 바꿔도
#: cwd 는 호출자의 것(운영자 홈, 0700)이 그대로 남고, find(1) 는 끝날 때 시작 디렉터리로
#: 돌아가려다 EACCES 로 exit 1 을 낸다. 목록은 다 뽑혔는데 rc 만 1 이라 "?" 로 읽혀
#: v1.0.154 배포 전량이 UNVERIFIABLE 로 멈췄다(2026-09-03).
_LIST_HOME_SCRIPT: Final = (
    'cd "$HOME" || exit 1; '
    'status=0; for root in "$HOME/.hermes/scripts" '
    '"$HOME/.hermes/plugins"; do [[ -d "$root" ]] || continue; '
    'if [[ "$root" == */scripts ]]; then '
    "find \"$root\" -mindepth 1 -maxdepth 1 -type f -printf "
    "'.hermes/scripts/%f\\n' || status=$?; "
    "else find \"$root\" -mindepth 2 -maxdepth 2 -type f -printf "
    "'.hermes/plugins/%P\\n' || status=$?; fi; done; exit \"$status\""
)


def _list_home(account: str) -> tuple[str, ...] | str:
    """선언 계정의 배포 가능 모양만 열거한다. ``?`` 는 sudo·find 관측 실패다."""
    if not _SAFE.fullmatch(account):
        return "?"
    proc = subprocess.run(
        ("sudo", "-n", "-u", account, "-H", "bash", "-c", _LIST_HOME_SCRIPT),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return "?"
    paths = tuple(sorted(set(proc.stdout.splitlines())))
    if any(
        not _SAFE.fullmatch(path) or not HOME_DEPLOYED_PATTERN.fullmatch(path)
        for path in paths
    ):
        return "?"
    return paths


def observations(
    runtime_root: Path,
    live_root: Path,
    home_reader: HomeReader,
    home_lister: HomeLister,
    observer: Observer | None = None,
    notice_mode: tuple[str, str] | None = None,
) -> list[str]:
    """관측 줄을 만든다. 목록을 못 보면 선언 밖 파일 부재를 증명할 수 없어 실패한다."""
    lines: list[str] = [f"OBS|release|{runtime_root.resolve().name}"]
    drift = inspect_mounts(runtime_root, live_root)
    lines.append("OBS|mounts|judged")
    for skill, expected, mounted in drift.stale:
        lines.append(f"OBS|mount-stale|{skill}|{expected}|{mounted}")
    for skill in drift.unmounted:
        lines.append(f"OBS|mount-unmounted|{skill}")
    for skill in drift.orphaned:
        lines.append(f"OBS|mount-orphaned|{skill}")
    manifest = runtime_root / CENTRAL_MANIFEST
    rows = parse_rows(manifest.read_text(encoding="utf-8"))
    declared: dict[str, set[str]] = {}
    v2_files = deploy_all_observe.v2_file_destinations(runtime_root)
    for row in rows:
        declared.setdefault(row.account, set()).add(row.destination)
        source_sha = hashlib.sha256((runtime_root / row.source).read_bytes()).hexdigest()
        policy_kind = "optional" if row.policy.startswith("optional") else "required"
        deployed = home_reader(row.account, row.destination)
        lines.append(
            f"OBS|home|{row.account}|{row.destination}|{row.source}"
            f"|{policy_kind}|{source_sha}|{deployed}"
        )
    for account, destinations in sorted(declared.items()):
        listed = home_lister(account)
        if listed == "?":
            raise deploy_all.ObservationError(f"home listing unreadable: {account}")
        for destination in listed:
            if destination in destinations or (account, destination) in v2_files:
                continue
            deployed = home_reader(account, destination)
            if not deployed or deployed == "?":
                raise deploy_all.ObservationError(
                    f"listed home artifact unreadable: {account}:{destination}"
                )
            lines.append(
                f"OBS|undeclared|{account}|{destination}|{deployed[:12]}"
            )
    lines += (observer or deploy_all_observe.observe_node)(runtime_root)
    if notice_mode is not None:
        from automation.notice_bot_release import observe as observe_notice_bot

        for account, status in observe_notice_bot(runtime_root, *notice_mode).items():
            lines.append(f"OBS|notice-bot|{account}|{status}")
    lines.append("OBS|end")
    return lines


def _gateways(runtime_root: Path, observer: Observer, wanted: str) -> int:
    """재시동 뒤 대기용 — 게이트웨이 세대 줄만. 0 = 기다릴 것 없음 · 1 = stale/unknown · 4 = 관측 불가."""
    try:
        lines = observer(runtime_root)
        states = deploy_all_gateway.fold((line.split("|") for line in lines if line.startswith("OBS|gateway|")), None)
        services = [line.split("|") for line in lines if line.startswith("OBS|gateway-service|")]
        accounts = {d.account for d in all_declarations(runtime_root) if d.destination == deploy_all_gateway.DROPIN}
        if wanted == "stopped":
            accounts |= {"agent", "peer"}
        states += deploy_all_gateway.fold_services(services, accounts, wanted)
    except (ManifestError, OSError, deploy_all.ObservationError) as error:
        print(f"DEPLOY-ALL-UNVERIFIABLE: {error}", file=sys.stderr)
        return 4
    for line in lines:
        print(line)
    return 1 if deploy_all_gateway.blocking(states) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="배포 전량 관측·판정(읽기 전용)")
    parser.add_argument(
        "--runtime-root", type=Path, default=Path("/srv/autophagy-agent-current")
    )
    parser.add_argument(
        "--live-root", type=Path, default=Path("/srv/autophagy-skills/live")
    )
    parser.add_argument(
        "--format", choices=("report", "actions", "receipt", "gateways"), default="report"
    )
    parser.add_argument(
        "--wrapper-refresh",
        action="append",
        default=[],
        metavar="NODE|STATUS|TOKEN",
        help="릴리스 ✅ 로 인가된 허용 목록 래퍼 갱신 결과(영수증에 싣는다)",
    )
    parser.add_argument(
        "--strict-undeclared",
        action="store_true",
        help="선언 밖 홈 파일도 drift 로 판정",
    )
    parser.add_argument("--rag-mcp-refresh", default="", help="MCP refresh result for the receipt")
    parser.add_argument("--notice-bot", default="off", help="릴리스 호스트가 승인한 발신 자격증명 지문")
    from automation.yoodori_channel import channel_value

    parser.add_argument("--yoodori-channel", type=channel_value, default="off",
                        help="릴리스 호스트가 선택한 대화·알림 채널 또는 off")
    parser.add_argument(
        "--dori-agent-chat", choices=("on", "off"), required=True,
        help="릴리스 호스트가 읽은 도리 전환(노드는 그 파일을 갖지 않는다)",
    )
    parser.add_argument("--hermes-gateways", choices=("stopped", "running"), required=True,
                        help="릴리스 호스트가 읽은 게이트웨이 정지/복구 모드")
    args = parser.parse_args(argv)
    observer = functools.partial(deploy_all_observe.observe_node, dori_agent_chat=args.dori_agent_chat,
                                 hermes_gateways=args.hermes_gateways,
                                 agent_chat_channel=args.yoodori_channel)
    if args.format == "gateways":
        return _gateways(args.runtime_root, functools.partial(observer, gateway_only=True), args.hermes_gateways)
    try:
        lines = observations(args.runtime_root, args.live_root, _read_home, _list_home, observer,
                             (args.notice_bot, args.yoodori_channel))
        notice_states = {parts[2]: parts[3] for line in lines
                         if (parts := line.split("|"))[:2] == ["OBS", "notice-bot"]}
        if args.notice_bot != "off" and not notice_states:
            raise deploy_all.ObservationError("notice-bot observation is missing")
        notice_clean = all(state == "ok" for state in notice_states.values())
        plan = deploy_all.parse_observations(
            [line for line in lines if not line.startswith("OBS|notice-bot|")],
            strict_undeclared=args.strict_undeclared
        )
        if plan.hermes_gateways != args.hermes_gateways:
            raise deploy_all.ObservationError("node observation does not match the host gateway mode")
    except (DriftError, ManifestError, OSError, subprocess.SubprocessError, deploy_all.ObservationError) as error:
        print(f"DEPLOY-ALL-UNVERIFIABLE: {error}", file=sys.stderr)
        return 4
    if args.format == "receipt":
        if not plan.clean or not notice_clean:
            print(deploy_all.render_plan(plan), file=sys.stderr)
            return 1
        verified_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        reasons = {
            (d.kind, d.account, d.destination): d.reason
            for d in all_declarations(args.runtime_root)
            if not d.legacy
        }
        try:
            dori_pin = (
                load_pin(args.runtime_root).as_receipt()
                if (args.runtime_root / PIN_RELPATH).exists() else None
            )
            receipt = deploy_all.render_receipt(
                plan,
                verified_at=verified_at,
                declared_reasons=reasons,
                wrapper_refresh=tuple(args.wrapper_refresh),
                rag_mcp_refresh=args.rag_mcp_refresh,
                dori_pin=dori_pin,
            )
        except (deploy_all.ObservationError, PinError) as error:
            print(f"DEPLOY-ALL-UNVERIFIABLE: {error}", file=sys.stderr)
            return 4
        import json

        document = json.loads(receipt)
        document["notice_bot"] = {"digest": args.notice_bot, "accounts": notice_states}
        print(json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    if args.format == "actions":
        actions = deploy_all.render_actions(plan)
        if actions:
            print(actions)
        if not notice_clean:
            print("ACT|notice-bot|converge")
        return 0 if plan.clean and notice_clean else 1
    print(deploy_all.render_plan(plan))
    if not notice_clean:
        print("NOTICE-BOT-DRIFT: account overrides differ from the release")
    return 0 if plan.clean and notice_clean else 1


if __name__ == "__main__":  # pragma: no cover - CLI 진입점
    raise SystemExit(main())
