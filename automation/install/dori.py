"""도리 프로필 설치 — 고정된 도리 포크와 기본 도리 구성(리드·비서, 선택 역할)을 깐다.

Hermes 경로(`python3 -m automation.install`)와 따로 선다: root·systemd·업데이트 신뢰키가 필요
없고 노드의 서비스 계정을 만들지 않는다. 하는 일은 다섯 가지뿐이다 — `dori/pin.toml` 의 커밋으로
포크를 받아 SHA 를 확인하고, 포크 스킬을 링크하고, `dori` CLI 를 `bun link` 하고, 포크의 예시
설정을 한 번만 복사하고, `dori/roles/` 템플릿을 작업 폴더에 렌더한다.

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
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from automation.dori_pin import REPO_ROOT, DoriPin, PinError, checkout_head, fetch_at, load_pin

ROLES_RELPATH: Final = Path("dori") / "roles"
TEMPLATE_NAME: Final = "AGENTS.md.tmpl"
AVATAR_NAME: Final = "avatar.md"
_PLACEHOLDER: Final = re.compile(r"\{\{[A-Z_]+\}\}")
DIFF_LINE_LIMIT: Final = 40
_CONFIG_EXAMPLES: Final = {"aoe": "config.aoe.example.json", "herdr": "config.example.json"}

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


def _default_runner(argv: tuple[str, ...], cwd: Path) -> int:
    return subprocess.run(argv, cwd=cwd, check=False).returncode


def _fork_state(pin: DoriPin, src: Path) -> tuple[str, str]:
    if not src.exists():
        return "fetch", f"{src} 에 {pin.commit[:12]} 을 받고 SHA 를 확인한다"
    head = checkout_head(src)
    if head == pin.commit:
        return "keep", f"{src} 이미 고정 커밋 {pin.commit[:12]}"
    return "block", (
        f"DORI-PIN-SRC-DIFFERS: {src} is at {head or 'no git checkout'}, pin is {pin.commit} "
        "— leaving it alone; move it aside or pass --src <빈 경로>"
    )


def main(argv: Sequence[str] | None = None, *, runner: Runner = _default_runner,
         which: Callable[[str], str | None] = shutil.which) -> int:
    home = Path(os.environ.get("HOME", str(Path.home())))
    parser = argparse.ArgumentParser(
        prog="python3 -m automation.install",
        epilog="기존 Hermes 노드 유지보수만 --profile core|rag|report-hub|full 로 명시한다.",
    )
    _ = parser.add_argument("--dry-run", action="store_true", help="계획과 렌더 결과만 보인다(쓰기·네트워크 없음)")
    _ = parser.add_argument("--role", action="append", default=[], metavar="KEY",
                            help="선택 역할을 더한다(okdori, moodori). lead·yoodori 는 기본")
    _ = parser.add_argument("--owner-call", default="소유자", help="도리가 소유자를 부르는 말(예: 형)")
    _ = parser.add_argument("--backend", choices=sorted(_CONFIG_EXAMPLES), default="aoe")
    _ = parser.add_argument("--src", type=Path, default=home / ".dori" / "src")
    _ = parser.add_argument("--skills", type=Path, default=home / ".agents" / "skills")
    _ = parser.add_argument("--workroot", type=Path, default=home / "Documents")
    args = parser.parse_args(argv)
    try:
        pin = load_pin()
        options = Options(args.src, args.skills, args.workroot, home,
                          _validate_owner_call(args.owner_call), args.backend, tuple(args.role), args.dry_run)
        roles = select_roles(load_roles(), options.roles)
        files = role_files(roles, options)
    except (PinError, DoriInstallError) as error:
        print(error)
        return 2

    print(f"DORI-PIN repo={pin.repo} commit={pin.commit}")
    print(f"역할: {', '.join(f'{r.key}({r.nick})' for r in roles)}")
    missing = [tool for tool in ("git", "bun") if which(tool) is None]
    for tool in ("git", "bun"):
        print(f"  [{'필요' if tool in missing else '통과'}] {tool}")
    fork_action, fork_detail = _fork_state(pin, options.src)
    config = options.home / ".dori" / "config.json"
    link = options.skills / "dori-mode"
    steps = [
        ("dori-fork", fork_detail),
        ("dori-skill-link", f"{link} -> {options.src / 'skills' / 'dori-mode'}"),
        ("dori-cli", f"bun install && bun link ({options.src / 'skills' / 'dori-mode' / 'scripts'})"),
        ("dori-config", f"{config} {'유지(이미 있음)' if config.exists() else '포크 예시 복사 ' + _CONFIG_EXAMPLES[options.backend]}"),
        *((f"dori-role-{e.role.key}", f"{e.path} {({'new': '새로 씀', 'same': '같음 — 넘어감', 'differs': '다름 — 그대로 두고 차이만 보임'})[e.state]}") for e in files),
    ]
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
        return 2

    if fork_action == "fetch":
        try:
            fetch_at(pin, options.src)
        except PinError as error:
            print(error)
            return 3
        print(f"DORI-PIN-OK {options.src} {pin.commit}")
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.exists() and not link.is_symlink():
        print(f"DORI-SKILL-KEEP {link} 은 링크가 아니라 그대로 둔다")
    else:
        if link.is_symlink():
            link.unlink()
        link.symlink_to(options.src / "skills" / "dori-mode")
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
    for entry in files:
        if entry.state == "new":
            entry.path.parent.mkdir(parents=True, exist_ok=True)
            _ = entry.path.write_text(entry.content, encoding="utf-8")
    kept = sum(1 for e in files if e.state == "differs")
    print(f"DORI-INSTALLED pin={pin.commit[:12]} roles={','.join(r.key for r in roles)} kept={kept}")
    print("다음: ~/.dori/dori.env 에 봇 토큰·채널·소유자 id 를 넣고(0600), 리드 작업 폴더에서 에이전트를 열어 \"Dori mode\" 라고 한다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
