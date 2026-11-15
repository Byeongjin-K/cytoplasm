"""도리 프로필 설치 — 고정된 도리 포크와 기본 도리 구성(리드·비서, 선택 역할)을 깐다.

Hermes 경로(`python3 -m automation.install`)와 따로 선다: root·업데이트 신뢰키가 필요 없고 노드의
서비스 계정을 만들지 않는다. `dori/pin.toml` 의 커밋으로 포크를 받아 SHA 를 확인하고, 포크 스킬을
링크하고, `dori` CLI 를 `bun link` 하고, 포크의 예시 설정을 한 번만 복사하고, `dori/roles/` 템플릿을
작업 폴더에 렌더한다. 기본 backend herdr 는 그 뒤 `dori_herdr` 로 herdr·ttyd·서버/웹 사용자 유닛을 깔고
리드를 herdr pane 에 띄운다. `--backend aoe` 는 예전처럼 aoe 예시 설정만 복사한다.

이미 있는 파일은 덮지 않는다. 같으면 넘어가고, 다르면 차이만 보여 주고 그대로 둔다 — 소유자가
고쳐 쓴 지침이 재설치로 사라지면 안 된다. 이미 있는 포크 체크아웃이 고정 커밋이 아니면 아무것도
설치하지 않고 멈춘다(`DORI-PIN-SRC-DIFFERS`).
"""
from __future__ import annotations

import argparse
import difflib
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from automation.dori_pin import REPO_ROOT, DoriPin, PinError, checkout_head, fetch_at, load_pin, move_to
from automation.install import dori_herdr

ROLES_RELPATH: Final = Path("dori") / "roles"
TEMPLATE_NAME: Final = "AGENTS.md.tmpl"
AVATAR_NAME: Final = "avatar.md"
_PLACEHOLDER: Final = re.compile(r"\{\{[A-Z_]+\}\}")
DIFF_LINE_LIMIT: Final = 40
_CONFIG_EXAMPLES: Final = {"aoe": "config.aoe.example.json", "herdr": "config.example.json"}
_ENV_EXAMPLE: Final = Path("skills") / "dori-mode" / "setups" / "discord-aoe" / "dori.env.example"
_ENV_KEYS: Final = "DORI_DISCORD_TOKEN·GUILD·CHANNEL·OWNER"
_TOOL_HINTS: Final = {
    "git": "sudo apt install -y git (윈도우: winget install Git.Git)",
    "bun": "curl -fsSL https://bun.sh/install | bash (윈도우: irm https://bun.sh/install.ps1 | iex), 그다음 새 터미널",
}

Runner = Callable[[tuple[str, ...], Path], int]


class DoriInstallError(RuntimeError):
    """설치를 시작하기 전에 거절할 이유 — 메시지 첫 낱말이 판정 표식이다."""


@dataclass(frozen=True, slots=True)
class Role:
    key: str
    name: str
    ko: str
    nick: str
    dir: str
    default: bool
    summary: str


@dataclass(frozen=True, slots=True)
class RoleFile:
    role: Role
    path: Path
    content: str
    state: Literal["new", "same", "differs"]
    current: str | None


def load_roles(root: Path = REPO_ROOT) -> tuple[Role, ...]:
    data = tomllib.loads((root / ROLES_RELPATH / "roles.toml").read_text(encoding="utf-8"))
    return tuple(
        Role(r["key"], r["name"], r["ko"], r["nick"], r["dir"], bool(r["default"]), r["summary"])
        for r in data["role"]
    )


def select_roles(roles: Sequence[Role], chosen: Sequence[str]) -> tuple[Role, ...]:
    known = {role.key for role in roles}
    unknown = [key for key in chosen if key not in known]
    if unknown:
        raise DoriInstallError(
            f"DORI-UNKNOWN-ROLE: {', '.join(unknown)}; known: {', '.join(r.key for r in roles)}"
        )
    return tuple(role for role in roles if role.default or role.key in chosen)


def render(template: str, values: Mapping[str, str]) -> str:
    text = template
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    leftover = sorted(set(_PLACEHOLDER.findall(text)))
    if leftover:
        raise DoriInstallError(f"DORI-TEMPLATE-UNFILLED: {', '.join(leftover)}")
    return text


def _validate_owner_call(value: str) -> str:
    if not value or len(value) > 20 or any(ch in value for ch in "\n\r{}"):
        raise DoriInstallError("DORI-OWNER-CALL-INVALID: 1~20자, 줄바꿈·중괄호 없이")
    return value


@dataclass(frozen=True, slots=True)
class Options:
    src: Path
    skills: Path
    workroot: Path
    home: Path
    owner_call: str
    backend: str
    roles: tuple[str, ...]
    dry_run: bool


def role_files(roles: Sequence[Role], options: Options, root: Path = REPO_ROOT) -> list[RoleFile]:
    files: list[RoleFile] = []
    for role in roles:
        base = root / ROLES_RELPATH / role.key
        values = {
            "NAME": role.name, "KO": role.ko, "NICK": role.nick,
            "OWNER_CALL": options.owner_call, "DORI_SRC": str(options.src),
        }
        rendered = {
            "AGENTS.md": render((base / TEMPLATE_NAME).read_text(encoding="utf-8"), values),
            AVATAR_NAME: (base / AVATAR_NAME).read_text(encoding="utf-8"),
        }
        for name, content in rendered.items():
            path = options.workroot / role.dir / name
            current = path.read_text(encoding="utf-8") if path.is_file() else None
            state = "new" if current is None else ("same" if current == content else "differs")
            files.append(RoleFile(role, path, content, state, current))
    return files


def _diff(entry: RoleFile, limit: int = DIFF_LINE_LIMIT) -> str:
    assert entry.current is not None
    lines = list(difflib.unified_diff(
        entry.current.splitlines(keepends=True), entry.content.splitlines(keepends=True),
        fromfile=f"{entry.path} (지금)", tofile=f"{entry.path} (템플릿)",
    ))
    shown = "".join(line if line.endswith("\n") else line + "\n" for line in lines[:limit])
    if len(lines) > limit:
        shown += f"… 차이 {len(lines) - limit}줄 더 (전문: diff -u {entry.path} <템플릿>)\n"
    return shown


def _is_junction(path: Path) -> bool:
    return bool(getattr(path, "is_junction", lambda: False)())


def _link_skill(link: Path, target: Path) -> None:
    # native Windows: a directory junction, which needs no administrator or developer mode (a symlink does)
    if link.exists() and not link.is_symlink() and not _is_junction(link):
        print(f"DORI-SKILL-KEEP {link} 은 링크가 아니라 그대로 둔다")
        return
    if _is_junction(link):
        link.rmdir()
    elif link.is_symlink():
        link.unlink()
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target)


def env_template(example: str, backend: str) -> str:
    """The fork's dori.env example; a herdr Dori has no aoe session, so its DORI_LEAD_* lines stay commented out."""
    if backend != "herdr":
        return example
    return "".join(f"# (herdr: not used) {line}" if line.startswith("DORI_LEAD_") else line
                   for line in example.splitlines(keepends=True))


def _find_bun(home: Path, which: Callable[[str], str | None]) -> None:
    # bun's installer puts bun in ~/.bun/bin and adds it to PATH only for new terminals; the same terminal still finds it
    folder = home / ".bun" / "bin"
    if which("bun") is None and (folder / ("bun.exe" if sys.platform == "win32" else "bun")).is_file():
        os.environ["PATH"] = f"{folder}{os.pathsep}{os.environ.get('PATH', '')}"
        print(f"DORI-BUN-FOUND {folder} (PATH 에 없어 이 실행에만 더했다)")


def _default_runner(argv: tuple[str, ...], cwd: Path) -> int:
    return subprocess.run(argv, cwd=cwd, check=False).returncode


def _fork_state(pin: DoriPin, src: Path, update: bool = False) -> tuple[str, str]:
    if not src.exists():
        return "fetch", f"{src} 에 {pin.commit[:12]} 을 받고 SHA 를 확인한다"
    head = checkout_head(src)
    if head == pin.commit:
        return "keep", f"{src} 이미 고정 커밋 {pin.commit[:12]}"
    if update and head is not None:
        return "move", f"{src} 를 {head[:12]} 에서 새 고정 커밋 {pin.commit[:12]} 로 옮긴다(설치기가 받은 깨끗한 체크아웃만)"
    return "block", (
        f"DORI-PIN-SRC-DIFFERS: {src} is at {head or 'no git checkout'}, pin is {pin.commit} "
        "— leaving it alone; move it aside or pass --src <빈 경로>"
    )


def main(argv: Sequence[str] | None = None, *, runner: Runner = _default_runner,
         which: Callable[[str], str | None] = shutil.which,
         capture: dori_herdr.Capture = dori_herdr.default_capture,
         fetch: dori_herdr.Fetch = dori_herdr.default_fetch,
         sleep: Callable[[float], None] = time.sleep) -> int:
    home = Path(os.environ.get("HOME", str(Path.home())))
    parser = argparse.ArgumentParser(
        prog="python3 -m automation.install",
        epilog="기존 Hermes 노드 유지보수만 --profile core|rag|report-hub|full 로 명시한다.",
    )
    _ = parser.add_argument("--dry-run", action="store_true", help="계획과 렌더 결과만 보인다(쓰기·네트워크 없음)")
    _ = parser.add_argument("--role", action="append", default=[], metavar="KEY",
                            help="선택 역할을 더한다(okdori, moodori, gongdori). lead·yoodori 는 기본")
    _ = parser.add_argument("--owner-call", default="소유자", help="도리가 소유자를 부르는 말(예: 형)")
    _ = parser.add_argument("--backend", choices=sorted(_CONFIG_EXAMPLES), default="herdr",
                            help="herdr(기본: herdr·ttyd·웹까지 깔고 리드를 herdr pane 에) 또는 aoe(aoe/tmux, 예시 설정만)")
    _ = parser.add_argument("--web-port", type=int, default=None, help="herdr 웹 앞문 루프백 포트(기본 dori/herdr.toml)")
    _ = parser.add_argument("--web-origin", default=None,
                            help="브라우저가 여는 주소(기본 http://127.0.0.1:<포트>, 테일넷이면 https://<호스트>[:포트])")
    _ = parser.add_argument("--update", action="store_true",
                            help="이미 받은 포크(설치기가 받은 깨끗한 체크아웃)를 새 고정 커밋으로 옮긴다")
    _ = parser.add_argument("--src", type=Path, default=home / ".dori" / "src")
    _ = parser.add_argument("--skills", type=Path, default=home / ".agents" / "skills")
    _ = parser.add_argument("--workroot", type=Path, default=home / "Documents")
    args = parser.parse_args(argv)
    try:
        pin = load_pin()
        tools = dori_herdr.load_tools(REPO_ROOT)
        options = Options(args.src, args.skills, args.workroot, home,
                          _validate_owner_call(args.owner_call), args.backend, tuple(args.role), args.dry_run)
        roles = select_roles(load_roles(), options.roles)
        files = role_files(roles, options)
    except (PinError, DoriInstallError) as error:
        print(error)
        return 2

    print(f"DORI-PIN repo={pin.repo} commit={pin.commit}")
    print(f"역할: {', '.join(f'{r.key}({r.nick})' for r in roles)}")
    _find_bun(home, which)
    missing = [tool for tool in ("git", "bun") if which(tool) is None]
    for tool in ("git", "bun"):
        print(f"  [{'필요' if tool in missing else '통과'}] {tool}")
    fork_action, fork_detail = _fork_state(pin, options.src, args.update)
    config = options.home / ".dori" / "config.json"
    env_file = options.home / ".dori" / "dori.env"
    link = options.skills / "dori-mode"
    steps = [
        ("dori-fork", fork_detail),
        ("dori-skill-link", f"{link} -> {options.src / 'skills' / 'dori-mode'}"),
        ("dori-cli", f"bun install && bun link ({options.src / 'skills' / 'dori-mode' / 'scripts'})"),
        ("dori-config", f"{config} {'유지(이미 있음)' if config.exists() else '포크 예시 복사 ' + _CONFIG_EXAMPLES[options.backend]}"),
        ("dori-env", f"{env_file} {'유지(이미 있음)' if env_file.exists() else '포크 예시 복사(0600) — ' + _ENV_KEYS + ' 네 줄만 채우면 된다'}"),
        *((f"dori-role-{e.role.key}", f"{e.path} {({'new': '새로 씀', 'same': '같음 — 넘어감', 'differs': '다름 — 그대로 두고 차이만 보임'})[e.state]}") for e in files),
    ]
    web_port = args.web_port or tools.web_port
    layout = dori_herdr.Layout(options.home, options.src, web_port)
    key = dori_herdr.platform_key()
    if options.backend == "herdr":
        steps += [(f"dori-{kind}", detail) for kind, detail in dori_herdr.plan(layout, tools, key)]
    for index, (kind, detail) in enumerate(steps, 1):
        print(f"{index}. {kind} {detail}")
    for entry in files:
        if entry.state == "differs":
            print(f"DORI-ROLE-KEEP {entry.path}")
            print(_diff(entry), end="")

    if fork_action == "block":
        print(fork_detail)
        return 2
    if options.dry_run:
        print("DORI-PLAN-OK — 계획 확인만 했다. 쓰기·네트워크 없음")
        return 0
    if missing:
        print(f"DORI-INSTALL-BLOCK: {', '.join(missing)} 이 PATH 에 없다")
        for tool in missing:
            print(f"  {tool} 설치: {_TOOL_HINTS[tool]}")
        print("  (한 줄 설치 install.sh·install.ps1 은 이것까지 깐다 — README 「빠른 시작」)")
        return 2

    if fork_action == "fetch":
        try:
            fetch_at(pin, options.src)
        except PinError as error:
            print(error)
            return 3
        print(f"DORI-PIN-OK {options.src} {pin.commit}")
    elif fork_action == "move":
        try:
            old = move_to(pin, options.src)
        except PinError as error:
            print(error)
            return 3
        print(f"DORI-PIN-MOVED {options.src} {old[:12]} -> {pin.commit}")
    link.parent.mkdir(parents=True, exist_ok=True)
    _link_skill(link, options.src / "skills" / "dori-mode")
    scripts = options.src / "skills" / "dori-mode" / "scripts"
    for command in (("bun", "install", "--silent"), ("bun", "link", "--silent")):
        if runner(command, scripts) != 0:
            print(f"DORI-CLI-FAIL: {' '.join(command)} in {scripts}")
            return 4
    if not config.exists():
        config.parent.mkdir(parents=True, exist_ok=True)
        _ = shutil.copyfile(
            options.src / "skills" / "dori-mode" / "references" / _CONFIG_EXAMPLES[options.backend], config
        )
    if env_file.exists():
        print(f"DORI-ENV {env_file} kept")
    elif not (options.src / _ENV_EXAMPLE).is_file():
        print(f"DORI-ENV-NO-EXAMPLE {options.src / _ENV_EXAMPLE} 이 없어 {env_file} 을 만들지 않았다")
    else:
        example = (options.src / _ENV_EXAMPLE).read_text(encoding="utf-8")
        dori_herdr.write_private(env_file, env_template(example, options.backend))
        print(f"DORI-ENV {env_file} new (0600) — {_ENV_KEYS} 네 줄을 자기 봇 값으로 바꾼다")
    for entry in files:
        if entry.state == "new":
            entry.path.parent.mkdir(parents=True, exist_ok=True)
            _ = entry.path.write_text(entry.content, encoding="utf-8")
    kept = sum(1 for e in files if e.state == "differs")
    if options.backend == "herdr":
        lead = next(r for r in roles if r.key == "lead")
        code = dori_herdr.install(
            layout, tools, key, origin=args.web_origin or f"http://127.0.0.1:{web_port}", config=config,
            lead_dir=options.workroot / lead.dir, lead_label=lead.nick, capture=capture, fetch=fetch, which=which,
            sleep=sleep, restart=args.update,
        )
        if code != 0:
            return code
    print(f"DORI-INSTALLED pin={pin.commit[:12]} roles={','.join(r.key for r in roles)} kept={kept} backend={options.backend}")
    if options.backend == "herdr" and layout.windows:
        print(f"다음: {env_file} 의 {_ENV_KEYS} 네 줄을 자기 봇 값으로 바꾸고, `herdr` 를 열어 리드 탭에서 /login 으로 모델에 로그인한 뒤 \"Dori mode\" 라고 한다."
              f" 도리 상시 루프는 작업 스케줄러 '{dori_herdr.SERVE_TASK}' 가 로그온마다 켭니다(토큰을 넣으면 다음 재시도 때 연결).")
    elif options.backend == "herdr":
        print(f"다음: {env_file} 의 {_ENV_KEYS} 네 줄을 자기 봇 값으로 바꾸고, 웹(또는 `herdr`)에서 리드 탭을 열어 /login 으로 모델에 로그인한 뒤 \"Dori mode\" 라고 한다."
              f" 웹 비밀번호는 {layout.web_env} 의 HERDR_WEB_PASSPHRASE(옛 aoe 웹을 쓰던 계정은 그 비밀번호)."
              f" 도리 상시 루프는 사용자 유닛 {dori_herdr.SERVE_UNIT} 가 켭니다(토큰을 넣으면 다음 재시도 때 연결).")
    else:
        print("다음: ~/.dori/dori.env 에 봇 토큰·채널·소유자 id 를 넣고(0600), 리드 작업 폴더에서 에이전트를 열어 \"Dori mode\" 라고 한다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
