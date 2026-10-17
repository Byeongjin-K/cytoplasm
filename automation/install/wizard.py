"""Guided install: ask the few things only the operator knows, then drive the installer.

The order is the one quickstart.sh proved safe — plan without root, show it, take an
explicit `yes`, only then escalate — and the installer stays the single decision maker:
this module assembles `python3 -m automation.install …` exactly as docs/guide/install.md
shows and never carries install logic of its own. All terminal, process and environment
access goes through one injectable console so the flow is provable without a host.
"""
from __future__ import annotations

import argparse
import getpass
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Final, Protocol

from automation.install.profiles import UnknownProfileError, resolve_profile
from automation.install.wizard_screens import (
    DEFAULT_ORIGIN_URL,
    DOCTOR_ARGV,
    DORI_PROFILE,
    PROFILE_MENU,
    Answers,
    approval_preview,
    doctor_settled,
    dori_argv,
    installer_argv,
    render_node_toml,
    summarize_plan,
    summarize_verdict,
)

_REPO: Final = Path(__file__).resolve().parents[2]
_DEFAULT_CONFIG: Final = Path.home() / ".config" / "autophagy" / "node.toml"
_TOOLS: Final = ("git", "curl", "ssh-keygen", "useradd", "sudo", "systemctl")


class Console(Protocol):
    tty: bool
    euid: int
    environ: dict[str, str]
    log_dir: Path | None

    def read_line(self, prompt: str) -> str: ...
    def run(self, argv: tuple[str, ...]) -> tuple[int, str]: ...
    def write(self, text: str) -> None: ...


class TerminalConsole:
    def __init__(self) -> None:
        self.tty = sys.stdin.isatty()
        self.euid = os.geteuid()
        self.environ = dict(os.environ)
        self.log_dir: Path | None = Path(tempfile.mkdtemp(prefix="autophagy-wizard-"))

    def read_line(self, prompt: str) -> str:
        return input(prompt)

    def run(self, argv: tuple[str, ...]) -> tuple[int, str]:
        process = subprocess.Popen(
            argv, cwd=_REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        collected: list[str] = []
        assert process.stdout is not None
        for line in process.stdout:
            self.write(line)
            collected.append(line)
        return process.wait(), "".join(collected)

    def write(self, text: str) -> None:
        _ = sys.stdout.write(text)
        sys.stdout.flush()


class _Arguments(argparse.Namespace):
    profile: str | None = None
    origin_url: str | None = None
    node_name: str | None = None
    operator: str | None = None
    config: Path = _DEFAULT_CONFIG
    update_trust_key: Path | None = None
    expect_update_trust_fingerprint: str | None = None
    with_component: list[str] = []
    dori_role: list[str] = []
    owner_call: str | None = None
    dry_run_only: bool = False
    yes: bool = False


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m automation.install.wizard", add_help=True)
    _ = parser.add_argument("--profile", default=None, help="core | rag | report-hub | full | dori")
    _ = parser.add_argument("--origin-url", default=None)
    _ = parser.add_argument("--node-name", default=None)
    _ = parser.add_argument("--operator", default=None)
    _ = parser.add_argument("--config", type=Path, default=_DEFAULT_CONFIG)
    _ = parser.add_argument("--update-trust-key", type=Path, default=None)
    _ = parser.add_argument("--expect-update-trust-fingerprint", default=None)
    _ = parser.add_argument("--with-component", action="append", default=[], metavar="NAME")
    _ = parser.add_argument("--dori-role", action="append", default=[], metavar="KEY",
                            help="dori 프로필의 선택 역할(okdori, moodori)")
    _ = parser.add_argument("--owner-call", default=None, help="dori 프로필: 도리가 소유자를 부르는 말")
    _ = parser.add_argument("--dry-run-only", action="store_true")
    _ = parser.add_argument("--yes", action="store_true", help="비대화 실행 전용: 확인을 건너뛴다")
    return parser


def _section(io: Console, title: str) -> None:
    io.write(f"\n== {title} ==\n")


def _prerequisites(io: Console, key: Path | None) -> bool:
    _section(io, "① 전제 확인")
    rows = [
        (sys.version_info >= (3, 11), f"Python {sys.version.split()[0]}", "Python 3.11 이상이 필요하다"),
        (Path("/run/systemd/system").is_dir(), "Linux + systemd",
         "systemd 가 없으면 계획 확인까지만 가능하다(리컨실러·워처가 systemd 타이머다)"),
        *((shutil.which(tool) is not None, tool, f"{tool} 이 PATH 에 없다") for tool in _TOOLS),
        (key is not None and os.access(key, os.R_OK), f"업데이트 신뢰키 {key or '(미지정)'}",
         "--update-trust-key <bundle>/update-trust.pub 로 릴리스 번들의 공개키를 준다"),
    ]
    for ok, label, hint in rows:
        io.write(f"  [{'통과' if ok else '필요'}] {label}{'' if ok else ' — ' + hint}\n")
    io.write(approval_preview() + "\n")
    return rows[-1][0]


def _ask(io: Console, prompt: str, default: str) -> str:
    answer = io.read_line(f"  {prompt} [{default}]: ").strip() if default else io.read_line(f"  {prompt}: ").strip()
    return answer or default


def _ask_profile(io: Console) -> str:
    io.write("  이 노드의 용도를 고른다:\n")
    for index, (name, meaning) in enumerate(PROFILE_MENU, 1):
        io.write(f"    {index}. {name:<11} {meaning}\n")
    while True:
        answer = io.read_line("  선택 (번호 또는 이름) [1]: ").strip() or "1"
        if answer.isdigit() and 1 <= int(answer) <= len(PROFILE_MENU):
            return PROFILE_MENU[int(answer) - 1][0]
        if answer == DORI_PROFILE:
            return answer
        try:
            return resolve_profile(answer).name
        except UnknownProfileError as error:
            io.write(f"  {error} — 다시 고른다.\n")


def _collect(io: Console, args: _Arguments) -> Answers:
    _section(io, "② 이 노드에 대한 질문")
    profile = resolve_profile(args.profile).name if args.profile else _ask_profile(io)
    origin, node, operator = args.origin_url or "", args.node_name or "", args.operator or ""
    asked = False
    if args.config.is_file():
        io.write(f"  설정 파일이 이미 있어 그대로 쓴다: {args.config}\n")
    elif not (origin and node and operator):
        origin = origin or _ask(io, "업데이트를 받아올 공개 저장소 URL", DEFAULT_ORIGIN_URL)
        node = node or _ask(io, "이 호스트 이름", socket.gethostname())
        operator = operator or _ask(io, "운영자 로그인 계정", getpass.getuser())
        asked = True
    fingerprint = args.expect_update_trust_fingerprint
    if fingerprint is None:
        io.write("  신뢰키 지문은 설치기가 아닌 경로(README·릴리스 노트)에서 받은 값을 넣는다. 비우면 기계 대조가 빠지고 종료 게이트가 WARN 이 된다.\n")
        fingerprint = (_ask(io, "공지된 지문 SHA256:…", "") or None) if asked else None
    assert args.update_trust_key is not None
    return Answers(profile, origin, node, operator, args.config, args.update_trust_key,
                   fingerprint, tuple(args.with_component))


def main(argv: Sequence[str] | None = None, *, io: Console | None = None) -> int:
    io = io or TerminalConsole()
    args = _Arguments()
    _ = _parser().parse_args(argv, namespace=args)
    io.write("Autophagy 설치 마법사 — 설치 로직은 전부 python3 -m automation.install 에 있고, 여기서는 묻고 보여주고 확인만 받는다.\n")
    if args.profile is None and io.tty:
        _section(io, "⓪ 설치 방식")
        args.profile = _ask_profile(io)
    if args.profile == DORI_PROFILE:
        return _dori(io, args)
    try:
        if args.profile is not None:
            _ = resolve_profile(args.profile)
    except UnknownProfileError as error:
        io.write(f"WIZARD-UNKNOWN-PROFILE: {error}\n")
        return 2
    if not _prerequisites(io, args.update_trust_key):
        io.write(f"WIZARD-TRUST-KEY-UNREADABLE: {args.update_trust_key or '--update-trust-key 미지정'}\n")
        return 2
    must_ask = not args.config.is_file() and not (args.origin_url and args.node_name and args.operator)
    if not io.tty and (must_ask or args.profile is None or not (args.yes or args.dry_run_only)):
        io.write("WIZARD-NO-TTY: 질문에 답하거나 확인할 터미널이 없다. 비대화 실행이라면 모든 값을 플래그로 주고 --yes 를 명시한다.\n")
        return 2
    answers = _collect(io, args)
    if not answers.config_path.is_file():
        answers.config_path.parent.mkdir(parents=True, exist_ok=True)
        _ = answers.config_path.write_text(render_node_toml(answers), encoding="utf-8")
        io.write(f"  설정을 썼다: {answers.config_path}\n")

    _section(io, "③ 계획 확인 — 쓰기 없음, root 불필요")
    dry_argv = installer_argv(answers, dry_run=True, as_root=io.euid == 0)
    io.write("  $ " + " ".join(dry_argv) + "\n")
    rc, output = io.run(dry_argv)
    _keep_log(io, "01-dry-run.log", output)
    if rc != 0:
        io.write(summarize_verdict(output, returncode=rc) + "\n")
        return rc
    io.write(summarize_plan(output) + "\n")
    if args.dry_run_only:
        io.write("  --dry-run-only 이므로 여기서 끝낸다. 실제 설치는 이 옵션 없이 다시 실행한다.\n")
        return 0

    _section(io, "④ 확인")
    if not args.yes:
        if io.read_line("  위 계획대로 실제 설치를 진행한다. 진행하려면 yes 를 입력한다: ").strip() != "yes":
            io.write("  취소했다. 아무것도 실행하지 않았다.\n")
            return 3
    apply_argv = installer_argv(answers, dry_run=False, as_root=io.euid == 0)
    if apply_argv[0] == "sudo" and io.environ.get("DISCORD_BOT_TOKEN"):
        probe, _ = io.run(("sudo", "--preserve-env=DISCORD_BOT_TOKEN", "true"))
        if probe == 0:
            apply_argv = tuple(["sudo", "--preserve-env=DISCORD_BOT_TOKEN", *apply_argv[1:]])
        else:
            io.write("  sudoers 가 환경 보존을 거부해 DISCORD_BOT_TOKEN 이 설치기에 닿지 않는다 — discord-readiness 는 WARN 으로 남고 설치는 계속한다(끝나면 소유자 확인 절차가 출력된다).\n")

    _section(io, "⑤ 실제 설치 — 멱등: 막히면 원인을 고치고 같은 명령을 다시 실행한다")
    rc, output = io.run(apply_argv)
    _keep_log(io, "02-install.log", output)
    _section(io, "⑥ 결과")
    io.write(summarize_verdict(output, returncode=rc) + "\n")
    if rc == 0:
        _connect(io, interactive=io.tty and not args.yes)
    return rc


def _dori(io: Console, args: _Arguments) -> int:
    """도리 프로필 — Hermes 경로와 같은 순서(계획 → yes → 실행)를 `automation.install.dori` 에 건다."""
    if not io.tty and not (args.yes or args.dry_run_only):
        io.write("WIZARD-NO-TTY: 확인할 터미널이 없다. 비대화 실행이라면 --yes 또는 --dry-run-only 를 명시한다.\n")
        return 2
    roles, owner_call = tuple(args.dori_role), args.owner_call
    _section(io, "① 도리 설치 계획 — 쓰기·네트워크 없음, root 불필요")
    dry_argv = dori_argv(roles, owner_call, dry_run=True)
    io.write("  $ " + " ".join(dry_argv) + "\n")
    rc, output = io.run(dry_argv)
    _keep_log(io, "01-dori-dry-run.log", output)
    if rc != 0:
        io.write(f"  도리 계획에서 멈춘다(rc={rc}) — 위의 DORI- 줄이 원인이다. 고친 뒤 같은 명령을 다시 실행한다.\n")
        return rc
    if args.dry_run_only:
        io.write("  --dry-run-only 이므로 여기서 끝낸다. 실제 설치는 이 옵션 없이 다시 실행한다.\n")
        return 0
    _section(io, "② 확인")
    if not args.yes:
        if io.read_line("  위 계획대로 도리를 설치한다. 진행하려면 yes 를 입력한다: ").strip() != "yes":
            io.write("  취소했다. 아무것도 실행하지 않았다.\n")
            return 3
    _section(io, "③ 도리 설치 — 멱등: 이미 있는 파일은 덮지 않는다")
    rc, output = io.run(dori_argv(roles, owner_call, dry_run=False))
    _keep_log(io, "02-dori-install.log", output)
    io.write("  도리 설치 완료.\n" if rc == 0 else f"  도리 설치 미완(rc={rc}) — 위의 DORI- 줄이 원인이다.\n")
    return rc


def _connect(io: Console, *, interactive: bool) -> None:
    _section(io, "⑦ 연결·승인 — 사람이 승인해야 하는 것을 doctor 가 하나씩 확인·안내한다")
    while True:
        io.write("  $ " + " ".join(DOCTOR_ARGV) + "\n")
        _, output = io.run(DOCTOR_ARGV)
        _keep_log(io, "03-doctor.log", output)
        if doctor_settled(output):
            io.write("  필수 연결이 모두 통과했다. 남은 [WARN] 은 선택 기능이다 — 필요할 때 위 절차대로 켠다.\n")
            return
        if not interactive:
            io.write("  남은 항목은 위 절차대로 승인한 뒤 python3 -m automation.doctor 로 다시 확인한다.\n")
            return
        answer = io.read_line("  위 절차대로 승인·연결한 뒤 Enter 를 누르면 다시 점검한다 (나중에 하려면 q): ")
        if answer.strip().lower() == "q":
            io.write("  나중에 python3 -m automation.doctor 로 다시 확인한다. 설치 자체는 끝났다.\n")
            return


def _keep_log(io: Console, name: str, output: str) -> None:
    if io.log_dir is None:
        return
    path = io.log_dir / name
    _ = path.write_text(output, encoding="utf-8")
    io.write(f"  전문: {path}\n")


if __name__ == "__main__":
    raise SystemExit(main())
