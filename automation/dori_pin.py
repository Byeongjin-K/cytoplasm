"""도리 포크 고정 커밋(`dori/pin.toml`)의 단일 정의 — 읽기·검증·그 커밋으로 받기.

설치기(`automation.install.dori`)와 배포 영수증(`deploy_receipt.render_receipt` 의 `dori_pin`)이
같은 파서를 쓴다. 갈라지면 설치한 커밋과 영수증이 말하는 커밋이 달라진다.

받기는 빈 임시 디렉터리에서 그 커밋 하나만 fetch 하고, `git rev-parse HEAD` 가 고정값과 같을
때만 목적지로 rename 한다. 실패하면 임시 디렉터리를 지워 반쯤 받은 트리를 남기지 않는다.
포크의 `install.sh` 는 main 을 그대로 pull 하므로(고정 없음) 여기서 그 단계를 대신한다.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

REPO_ROOT: Final = Path(__file__).resolve().parents[1]
PIN_RELPATH: Final = Path("dori") / "pin.toml"
SCHEMA: Final = 1
_SHA: Final = re.compile(r"[0-9a-f]{40}")


class PinError(RuntimeError):
    """고정값이 틀렸거나 그 커밋을 받을 수 없다 — 메시지 첫 낱말이 판정 표식이다."""


@dataclass(frozen=True, slots=True)
class DoriPin:
    repo: str
    commit: str

    def as_receipt(self) -> dict[str, str]:
        return {"repo": self.repo, "commit": self.commit}


def parse_pin(text: str) -> DoriPin:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise PinError(f"DORI-PIN-INVALID: not TOML ({error})") from error
    if data.get("schema") != SCHEMA:
        raise PinError(f"DORI-PIN-INVALID: schema must be {SCHEMA}")
    repo, commit = data.get("repo"), data.get("commit")
    if not isinstance(repo, str) or not (repo.startswith("https://") or Path(repo).is_absolute()):
        raise PinError("DORI-PIN-INVALID: repo must be an https URL")
    if not isinstance(commit, str) or _SHA.fullmatch(commit) is None:
        raise PinError("DORI-PIN-INVALID: commit must be a full 40-hex SHA")
    return DoriPin(repo, commit)


def load_pin(root: Path = REPO_ROOT) -> DoriPin:
    path = root / PIN_RELPATH
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise PinError(f"DORI-PIN-UNREADABLE: {path} ({error.__class__.__name__})") from error
    return parse_pin(text)


def checkout_head(path: Path) -> str | None:
    if not (path / ".git").exists():
        return None
    result = subprocess.run(
        ("git", "-C", str(path), "rev-parse", "HEAD"), capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _git(cwd: Path, *args: str) -> None:
    result = subprocess.run(("git", *args), cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        tail = (result.stderr.strip().splitlines() or ["no output"])[-1]
        raise PinError(f"DORI-PIN-FETCH-FAIL: git {args[0]} failed ({tail})")


def fetch_at(pin: DoriPin, dest: Path) -> None:
    """`dest` 에 고정 커밋 하나만 받는다. `dest` 가 이미 있으면 건드리지 않는다."""
    if dest.exists():
        raise PinError(f"DORI-PIN-DEST-EXISTS: {dest}")
    created = [parent for parent in dest.parents if not parent.exists()]
    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{dest.name}-pin-", dir=dest.parent))
    try:
        _git(staging, "init", "--quiet")
        _git(staging, "remote", "add", "origin", pin.repo)
        _git(staging, "fetch", "--quiet", "--depth", "1", "origin", pin.commit)
        _git(staging, "checkout", "--quiet", "--detach", "FETCH_HEAD")
        head = checkout_head(staging)
        if head != pin.commit:
            raise PinError(f"DORI-PIN-MISMATCH: want {pin.commit} got {head}")
        staging.rename(dest)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        for parent in created:
            try:
                parent.rmdir()
            except OSError:
                break
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m automation.dori_pin")
    sub = parser.add_subparsers(dest="command", required=True)
    _ = sub.add_parser("show", help="print the pinned fork commit")
    verify = sub.add_parser("verify", help="does a checkout sit exactly on the pinned commit")
    _ = verify.add_argument("--checkout", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        pin = load_pin()
    except PinError as error:
        print(error, file=sys.stderr)
        return 2
    if args.command == "show":
        print(f"DORI-PIN repo={pin.repo} commit={pin.commit}")
        return 0
    head = checkout_head(args.checkout)
    if head != pin.commit:
        print(f"DORI-PIN-MISMATCH: {args.checkout} is at {head or 'no checkout'}, pin is {pin.commit}")
        return 1
    print(f"DORI-PIN-OK {args.checkout} {pin.commit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
