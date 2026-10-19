"""v2 배포 선언의 노드 관측 — 트리·빌드·cron·roster, 그리고 릴리스가 올리지 못하는 표면.

줄 문법(`OBS|artifact|…`·`artifact-detail`·`pending`·`pending-detail`·`pending-unknown`, 마지막
`OBS|artifacts|<n>`)은 계획 todo 4 가 정한다. want/have 는 신원(모르면 `?`, 없으면 `-`)이고 읽지
못한 것은 `unknown` 이지 부재가 아니다. 노드에서 온 문자열은 데이터다 — 경로는 퍼센트 인코딩,
프로브 줄은 제어 문자와 `|` 를 지운다.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypeAlias
from urllib.parse import quote

from automation import deploy_all_config, deploy_all_gateway
from automation.cron_timer import Job, identity
from automation.deploy_all_cron_listing import parse_cron_listing
from automation.deploy_declarations import Declaration, all_declarations
from automation.node_config import NodeConfigError, load_node_config
from automation.peer_retired import retired_destination

#: 계정·경로는 원격 고정 스크립트의 위치 인자다 — 안전하게 다룰 수 없는 값은 보내지 않는다.
SAFE: Final = re.compile(r"^[A-Za-z0-9_./-]+$")
Runner: TypeAlias = Callable[[str, tuple[str, ...]], tuple[int, str]]
Details: TypeAlias = tuple[tuple[str, str], ...]
UNAVAILABLE: Final = "OBS|pending-unknown|delegated-probes-unavailable"
DIGEST_HELPER: Final = "skills/mail/scripts/mailon_vendor_digest.sh"
_TIMEOUT: Final = 330
_CACHE_DIRS: Final = frozenset({"__pycache__", ".venv", ".ruff_cache", ".pytest_cache"})
_SHA: Final = re.compile(r"^[0-9a-f]{64}$")
_SHORT: Final = re.compile(r"^[0-9a-f]{16}$")
_UNSAFE_TEXT: Final = re.compile(r"[\x00-\x1f\x7f|]")
_CRON_FIELDS: Final = ("schedule", "script", "deliver", "mode")
_TIMER_CHECK: Final = re.compile(r"^TIMER-CHECK state=(ok|absent|stale|unknown) detail=([a-z-]+) ", re.M)
#: 검증 명령 자체가 돌지 못한 종료코드(홈 진입 실패·인자 거부·시간 초과·실행 불가·명령 없음).
_ROSTER_NOT_RUN: Final = frozenset({44, 64, 124, 126, 127})

#: 계정으로 실행되는 유일한 스크립트. 부재(exit 3)는 가장 가까운 기존 조상이 탐색 가능할
#: 때만 단언한다 — 막힌 디렉터리 밑을 "없음"으로 읽으면 권한 오류가 부재로 둔갑한다.
#: 해시는 stdin 으로 잰다 — `sha256sum FILE` 은 개행·역슬래시가 든 이름에서 줄 앞에 `\` 를 붙인다.
REMOTE_SCRIPT: Final = r"""cd "$HOME" || exit 44
gone() { local d; d="$(dirname "$1")"; while [[ ! -e "$d" && "$d" != / ]]; do d="$(dirname "$d")"; done
  [[ -x "$d" ]] && exit 3; exit 44; }
set -o pipefail
case "$1" in
  sha) p="$HOME/$2"; [[ -e "$p" ]] || gone "$p"; sha256sum < "$p" | cut -d" " -f1 ;;
  snap) r="$HOME/$2"; [[ -e "$r" ]] || gone "$r"; [[ -d "$r" && -r "$r" && -x "$r" ]] || exit 44
    find "$r/" -type d \( -name __pycache__ -o -name .venv -o -name .ruff_cache -o -name .pytest_cache \) \
      -prune -o -type f -print0 | LC_ALL=C sort -z | while IFS= read -r -d "" f; do
        rel="${f#"$r"/}"; rel="${rel#/}"; [[ "$rel" == .env.secrets ]] && continue
        [[ "$3" == python && "$rel" != *.py ]] && continue
        h="$(sha256sum < "$f" | cut -d" " -f1)" && [[ -n "$h" ]] || exit 44; printf "%s|%s\0" "$h" "$rel"; done ;;
  derived) d="$HOME/$3"; [[ -e "$d" ]] || gone "$d"; . "$2" || exit 44
    if [[ -d "$d/mailon" ]]; then mailon_vendor_digest "$d/mailon" || exit 44; else echo -; fi
    [[ ! -e "$d/runtime-manifest.json" ]] || cat -- "$d/runtime-manifest.json" || exit 44 ;;
  legacy-service) PYTHONPATH="$2" python3 -B -m automation.legacy_services check --name "$3" --mode "$4" ;;
  config) PYTHONPATH="$2" python3 -B -m "$3" --show "${@:4}" ;;
  roster) PYTHONPATH="$2" python3 -B -m automation.group_roster validate "$HOME/.hermes/roster.yaml" >/dev/null 2>&1 ;;
  cron) PATH="$HOME/.local/bin:$PATH" hermes cron list --all ;;
  timer) PATH="$HOME/.local/bin:$PATH" PYTHONPATH="$2" python3 -B -m automation.cron_timer check-declared --role "$3" --name "$4" ;;
  p5-retired) cmp -s "$2/peer-retired" <(printf retired) ;;
  delegated) timeout 300 bash "$2/automation/healthcheck_delegated.sh" ;;
  gateway) r="$2"; shift 2; PYTHONPATH="$r" python3 -B -m automation.gateway_generation --check --runtime-root "$r" "$@" ;;
  gateway-service) export XDG_RUNTIME_DIR="/run/user/$(id -u)"
    systemctl --user is-active hermes-gateway.service; rc=$?
    if cmp -s "$HOME/.hermes/gateway-stopped-by-release" <(printf stopped); then echo marked; else echo unmarked; fi
    exit "$rc" ;;
  *) exit 64 ;;
esac"""


def _safe(*values: str) -> bool:
    return all(SAFE.fullmatch(value) for value in values)


def default_runner(account: str, args: tuple[str, ...]) -> tuple[int, str]:
    """`sudo -n -u <account> -H bash -c <REMOTE_SCRIPT> _ <args…>` — 값 검사와 시간 상한이 있다."""
    if not _safe(account, *args):
        return 64, ""
    try:
        proc = subprocess.run(("sudo", "-n", "-u", account, "-H", "bash", "-c", REMOTE_SCRIPT, "_", *args),
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              check=False, timeout=_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return 124, ""
    return proc.returncode, proc.stdout


@dataclass(frozen=True, slots=True)
class _Seen:
    want: str
    have: str
    state: str  # ok | stale | extra | absent | unknown
    details: Details = ()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _state(want: str, have: str) -> str:
    return "unknown" if "?" in (want, have) else "absent" if have == "-" else "ok" if want == have else "stale"


def tree_identity(snapshot: dict[str, str]) -> str:
    """`SNAPSHOT-V1\\n<sha256>|<상대경로>\\n…`(경로 바이트 순) 전체의 sha256."""
    body = "".join(f"{snapshot[k]}|{k}\n" for k in sorted(snapshot, key=str.encode))
    return _sha_text(f"SNAPSHOT-V1\n{body}")


def _raise(error: OSError) -> None:
    raise error


def release_tree_snapshot(root: Path, decl: Declaration) -> dict[str, str] | None:
    """릴리스 쪽 선택: profile 확장자·최상위 cron/·캐시 제외, files= 면 그 목록만. None = 모름."""
    source, files, prefix = root / decl.source, decl.attr("files"), decl.attr("prefix")
    try:
        if files:
            names = files.split(",")
            if not all((source / name).is_file() for name in names):
                return None
        elif source.is_dir():
            names = []
            for top, dirs, found in os.walk(source, onerror=_raise):
                dirs[:] = [d for d in dirs if d not in _CACHE_DIRS]
                names += [(Path(top) / n).relative_to(source).as_posix() for n in found if not (Path(top) / n).is_symlink()]
            names = [n for n in names if not n.startswith("cron/") and n != ".env.secrets"
                     and (decl.attr("profile") != "python" or n.endswith(".py"))]
        else:
            return None
        return {(f"{prefix}/{n}" if prefix else n): _sha(source / n) for n in names}
    except OSError:
        return None


def _deployed_snapshot(out: str) -> dict[str, str] | None:
    snapshot: dict[str, str] = {}
    for entry in out.split("\0")[:-1] if not out or out.endswith("\0") else ["!"]:
        sha, sep, rel = entry.partition("|")
        if not sep or not _SHA.fullmatch(sha) or not rel or rel in snapshot:
            return None
        snapshot[rel] = sha
    return snapshot


def _tree(decl: Declaration, root: Path, call: Runner, _cache: dict[str, object]) -> _Seen:
    want_snap = release_tree_snapshot(root, decl)
    rc, out = call(decl.account, ("snap", decl.destination, decl.attr("profile")))
    have_snap = _deployed_snapshot(out) if rc == 0 else None
    want = "?" if want_snap is None else tree_identity(want_snap)
    have = "-" if rc == 3 else "?" if have_snap is None else tree_identity(have_snap)
    if want_snap is None or have_snap is None:
        reason: Details = (("missing-source", "-"),) if want_snap is None else ()
        return _Seen(want, have, "absent" if rc == 3 and want_snap is not None else "unknown", reason)
    details = tuple(("missing-file", p) for p in sorted(want_snap) if p not in have_snap) + tuple(
        ("different-file", p) for p in sorted(want_snap) if have_snap.get(p, want_snap[p]) != want_snap[p])
    extras = tuple(("extra-file", p) for p in sorted(have_snap) if p not in want_snap)
    return _Seen(want, have, "stale" if details else "extra" if extras else "ok", details + extras)


def _file(decl: Declaration, root: Path, call: Runner, _cache: dict[str, object]) -> _Seen:
    try:
        want = _sha(root / decl.source)
    except OSError:
        want = "?"
    rc, out = call(decl.account, ("sha", decl.destination))
    have = "-" if rc == 3 else out.strip() if rc == 0 and _SHA.fullmatch(out.strip()) else "?"
    state = _state(want, have)
    reason: Details = (("different-file", "-"),) if state == "stale" else ()
    return _Seen(want, have, state, (("missing-source", "-"),) if want == "?" else reason)


def _derived(decl: Declaration, root: Path, call: Runner, _cache: dict[str, object]) -> _Seen:
    if decl.attr("algorithm") == "mail-html-reply-v1":
        mode = _cache.get("mail_html_reply", "off")
        if mode not in ("on", "off"):
            return _Seen("?", "?", "unknown")
        want = _sha_text(str(mode))
        rc, out = call(decl.account, ("sha", decl.destination))
        have = out.strip() if rc == 0 else "-" if rc == 3 else "?"
        # Before the first release there is no marker; that is the plain-text default.
        state = "ok" if mode == "off" and have == "-" else _state(want, have)
        return _Seen(want, want if state == "ok" else have, state)
    if decl.attr("algorithm") == "legacy-service-v1":
        from automation.legacy_services import UNITS

        name = Path(decl.destination).stem
        mode = _cache.get(name, "on")
        if name not in UNITS or mode not in ("on", "off"):
            return _Seen("?", "?", "unknown")
        want = _sha_text(str(mode))
        rc, out = call(decl.account, ("legacy-service", str(root), name, str(mode)))
        state = out.strip() if rc == 0 and out.strip() in ("ok", "stale") else "unknown"
        return _Seen(want, want if state == "ok" else "?" if state == "unknown" else "-", state)
    helper = str(root / DIGEST_HELPER)
    try:
        proc = subprocess.run(("bash", "-c", '. "$1"; mailon_vendor_digest "$2"', "_", helper, str(root / decl.source)),
                              capture_output=True, text=True, check=False, timeout=_TIMEOUT)
        src16, req16 = proc.stdout.strip() if proc.returncode == 0 else "", _sha(root / decl.attr("requirements"))[:16]
    except (OSError, subprocess.TimeoutExpired):
        src16 = req16 = ""
    want = f"{src16}-{req16}" if decl.attr("algorithm") == "mailon-py-v1" and _SHORT.fullmatch(src16) else "?"
    rc, out = call(decl.account, ("derived", helper, decl.destination))
    if rc != 0:
        return _Seen(want, "-" if rc == 3 else "?", _state(want, "-" if rc == 3 else "?"))
    recomputed, _, manifest = out.partition("\n")
    try:
        loaded = json.loads(manifest)
        src, req = str(loaded["src_digest"]), str(loaded["req_digest"])
    except (ValueError, KeyError, TypeError):
        src = req = ""
    have = f"{src}-{req}" if _SHORT.fullmatch(src) and _SHORT.fullmatch(req) else "?"
    if have == "?" or recomputed.strip() != src:
        return _Seen(want, have, "stale", (("corrupt", "-"),))
    return _Seen(want, have, _state(want, have))


def _timer(decl: Declaration, root: Path, call: Runner) -> _Seen:
    """`runner=systemd` 행 — 사용자 타이머와 같은 이름 Hermes 잡(paused 여야 함)을 계정 안 `cron_timer` 가 판정한다."""
    want = identity(Job(decl.destination, decl.attr("schedule"), decl.attr("script"),
                        tuple(e for e in decl.attr("env").split(",") if e), decl.attr("deliver")))
    rc, out = call(decl.account, ("timer", str(root), decl.account, decl.destination))
    match = _TIMER_CHECK.search(out) if rc == 0 else None
    if match is None:
        return _Seen(want, "?", "unknown", (("timer-check-unavailable", "-"),))
    state, detail = match[1], match[2]
    have = {"ok": want, "absent": "-", "unknown": "?"}.get(state, f"timer-{detail}"[:40])
    return _Seen(want, have, state, () if state in ("ok", "absent") else ((detail, "-"),))


def _cron(decl: Declaration, root: Path, call: Runner, cache: dict[str, object]) -> _Seen:
    if decl.attr("runner") == "systemd":
        return _timer(decl, root, call)
    want_values = tuple(" ".join(decl.attr(k).split()) for k in _CRON_FIELDS)
    want = _sha_text("\n".join(want_values))[:16]
    if decl.account not in cache:
        rc, out = call(decl.account, ("cron",))
        cache[decl.account] = parse_cron_listing(out) if rc == 0 else None
    listing = cache[decl.account]
    if not isinstance(listing, dict):
        return _Seen(want, "?", "unknown", (("unreadable-listing", "-"),))
    found: list[dict[str, str]] = listing.get(decl.destination, [])
    if len(found) != 1:
        return _Seen(want, "?", "unknown", (("duplicate-name", "-"),)) if found else _Seen(want, "-", "absent")
    job = found[0]
    mode = job["Mode"].split()[0] if job.get("Mode") else "agent"
    have_values = (" ".join(job["Schedule"].split()), job.get("Script", "-"), job["Deliver"], mode)
    details = tuple((f"different-{k}", "-") for k, w, h in zip(_CRON_FIELDS, want_values, have_values) if w != h)
    return _Seen(want, _sha_text("\n".join(have_values))[:16], "stale" if details else "ok", details)


#: algorithm → the module that owns the wanted value and runs `--show` inside the account.
_CONFIG_MODULES: Final = deploy_all_config.CONFIG_MODULES


def _config(decl: Declaration, root: Path, call: Runner, _cache: dict[str, object]) -> _Seen:
    """계정 설정 파일의 저장소가 정한 값 — 원하는 값은 이 릴리스 트리의 알고리즘 모듈이 말한다."""
    mode = _cache.get("dori_agent_chat")
    channel = _cache.get("agent_chat_channel", "off")
    return _Seen(*deploy_all_config.observe_config(
        decl, root, call, dori_mode=mode if isinstance(mode, str) else None,
        chat_channel=channel if isinstance(channel, str) else "off",
        omo_relay_mode=str(_cache.get("omo_relay_mode", "off")),
    ))


_OBSERVERS: Final = {"file": _file, "tree": _tree, "derived": _derived, "cron": _cron, "config": _config}


def _verdict(decl: Declaration, seen: _Seen, roster_reason: str) -> tuple[str, Details]:
    if decl.policy == "retired":
        return {"-": "retired-absent", "?": "unknown"}.get(seen.have, "retired-present"), ()
    if roster_reason:
        return "held", ((roster_reason, "-"),)
    if decl.policy == "held" and seen.state != "ok":
        return "held", (("unreadable", "-"),) if seen.have == "?" else seen.details or ((seen.state, "-"),)
    if seen.state == "absent" and decl.policy == "optional":
        return "ok", ()
    return seen.state, seen.details


def _delegated(root: Path, call: Runner, ops_account: str) -> list[str]:
    script = root / "automation/healthcheck_delegated.sh"
    if not (script.is_file() and os.access(script, os.R_OK)):
        return [UNAVAILABLE]  # 이 런타임은 위임 프로브를 돌릴 수 없다 — 계정 실행기를 부르지 않는다
    rc, out = call(ops_account, ("delegated", str(root)))
    lines: list[str] = []
    checks, end = 0, -1
    for raw in out.split("\n") if rc == 0 else ["!"]:
        kind, _, rest = raw.partition("|")
        parts = rest.split("|", 3 if kind == "DELEGATED" else 2)
        if end < 0 and kind == "DELEGATED" and len(parts) == 4 and parts[2] in ("PASS", "FAIL", "UNKNOWN"):
            lines.append(f"OBS|pending|{parts[0]}|{parts[1]}|{parts[2]}|{_UNSAFE_TEXT.sub(' ', parts[3])}")
            checks += 1
        elif end < 0 and kind == "DELEGATED-DETAIL" and len(parts) == 3:
            lines.append(f"OBS|pending-detail|{parts[0]}|{parts[1]}|{_UNSAFE_TEXT.sub(' ', parts[2])}")
        elif end < 0 and kind == "DELEGATED-END" and rest.isdigit():
            end = int(rest)
        elif raw or (lines and not _safe(*lines[-1].split("|")[2:4])):
            return [UNAVAILABLE]
    return lines if end == checks and all(_safe(*line.split("|")[2:4]) for line in lines) else [UNAVAILABLE]


def collect(
    runtime_root: Path, runner: Runner, ops_account: str, *, gateway_only: bool = False,
    dori_agent_chat: str | None = None,
    hermes_gateways: str = "running",
    agent_chat_channel: str = "off",
    omo_relay_mode: str = "off",
    mail_html_reply: str = "off",
    report_hub: str = "on",
    hermes_dashboard: str = "on",
    hermes_memory: str = "on",
    peer_retired: bool = False,
) -> list[str]:
    """v2 선언의 관측 줄 + 게이트웨이 세대 + 위임 프로브 줄 + `OBS|artifacts|<n>`. legacy 행은 다루지 않는다.

    `gateway_only` 는 재시동 뒤 대기용이다 — 드롭인 묶음만 관측해 `OBS|gateway|…` 줄만 돌려준다."""

    def call(account: str, args: tuple[str, ...]) -> tuple[int, str]:
        return runner(account, args) if _safe(account, *args) else (64, "")

    declarations = [d for d in all_declarations(runtime_root) if not d.legacy and d.attr("node") != "rag"]
    if peer_retired:
        declarations = [d for d in declarations
                        if not retired_destination(d.account, d.destination, d.source)
                        and not (d.attr("activation") == "gateway" and d.kind == "config"
                                 and d.attr("algorithm") != "yoodori-channel-v1")]
    if gateway_only:
        declarations = deploy_all_gateway.bundle(declarations)
    gated = [d for d in declarations if d.attr("requires") == "roster"]
    roster_rc = {a: call(a, ("roster", str(runtime_root)))[0] for a in {d.account for d in gated}}
    own = {a: "roster-unverified" if rc in _ROSTER_NOT_RUN else "roster-required" for a, rc in roster_rc.items() if rc}
    held = {d.owner for d in gated if d.account in own}  # 한 계정이라도 실패하면 소유 선언 묶음 전체가 보류된다
    cache: dict[str, object] = {"dori_agent_chat": dori_agent_chat, "agent_chat_channel": agent_chat_channel}
    cache.update({"report-hub": report_hub, "hermes-dashboard": hermes_dashboard, "hermes-memory": hermes_memory})
    cache["omo_relay_mode"] = omo_relay_mode
    cache["mail_html_reply"] = mail_html_reply
    lines: list[str] = []
    statuses: dict[tuple[str, str], tuple[str, Details]] = {}
    for decl in declarations:
        if (hermes_memory == "off" and decl.kind == "cron" and decl.account == "agent"
                and decl.destination in ("memory-curator-watch", "memory-relocate-watch")):
            # The derived declaration verifies both disabled timers instead of re-enabling them.
            continue
        seen = _OBSERVERS[decl.kind](decl, runtime_root, call, cache)
        # 계정의 사유는 그 계정 자신의 검증 결과다. 통과한 계정은 다른 계정 때문에 보류됐다고만 적는다 —
        # 실패한 계정의 사유를 물려받으면 영수증이 멀쩡한 계정의 행까지 init-local 대상으로 센다.
        gate = own.get(decl.account, "roster-sibling") if decl.owner in held else ""
        status, details = _verdict(decl, seen, gate if decl.attr("requires") == "roster" else "")
        statuses[(decl.account, decl.destination)] = (status, details)
        head = f"{decl.kind}|{decl.account}|{decl.destination}"
        lines.append(f"OBS|artifact|{head}|{decl.owner}|{decl.policy}|{decl.attr('activation')}|{status}"
                     f"|{'-' if decl.policy == 'retired' else seen.want}|{seen.have}")
        lines += [f"OBS|artifact-detail|{head}|{reason}|{quote(rel, safe='/._-+,@=~')}" for reason, rel in details]
    accounts = sorted({d.account for d in declarations if d.destination == deploy_all_gateway.DROPIN}
                      | ({"agent", "peer"} if hermes_gateways == "stopped" and not peer_retired else set()))
    services = deploy_all_gateway.service_lines(accounts, hermes_gateways, call)
    service_states = deploy_all_gateway.fold_services((line.split("|") for line in services), set(accounts), hermes_gateways)
    lines += deploy_all_gateway.gateway_lines(declarations, statuses, runtime_root, call,
                                              service_states, hermes_gateways=hermes_gateways)
    if gateway_only:
        return [line for line in lines if line.startswith("OBS|gateway|")] + services
    count = sum(1 for line in lines if line.startswith("OBS|artifact|"))
    return [*lines, f"OBS|gateway-mode|{'retired' if peer_retired else hermes_gateways}", *services,
            *_delegated(runtime_root, call, ops_account), f"OBS|artifacts|{count}"]


def v2_file_destinations(runtime_root: Path) -> frozenset[tuple[str, str]]:
    """v2 file 선언의 (계정, 목적지) — 선언 밖 홈 파일 목록에서 빼야 하는 것들."""
    return frozenset((d.account, d.destination) for d in all_declarations(runtime_root) if not d.legacy and d.kind == "file")


def observe_node(
    runtime_root: Path, *, gateway_only: bool = False, dori_agent_chat: str | None = None,
    hermes_gateways: str = "running",
    agent_chat_channel: str = "off",
    omo_relay_mode: str = "off",
    mail_html_reply: str = "off",
    report_hub: str = "on",
    hermes_dashboard: str = "on",
    hermes_memory: str = "on",
    peer_retired: bool = False,
) -> list[str]:
    """노드의 기본 수집 — 노드 설정의 ops 계정으로 위임 프로브를 돌린다(설정 불가 = 확인 불가)."""
    try:
        ops_account = load_node_config().ops_account
    except (NodeConfigError, OSError):
        ops_account = ""
    return collect(runtime_root, default_runner, ops_account, gateway_only=gateway_only,
                   dori_agent_chat=dori_agent_chat, hermes_gateways=hermes_gateways,
                   report_hub=report_hub, hermes_dashboard=hermes_dashboard,
                    hermes_memory=hermes_memory,
                   agent_chat_channel=agent_chat_channel, peer_retired=peer_retired,
                   omo_relay_mode=omo_relay_mode, mail_html_reply=mail_html_reply)
