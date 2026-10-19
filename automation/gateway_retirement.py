"""Release-approved LiteLLM removal, using only the current non-root account."""
from __future__ import annotations

import argparse
import fcntl
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Final

from automation.gateway_retirement_policy import CARD_LINES as CARD_LINES, surface as surface, config_without_gateway

HEAD: Final = re.compile(r"[0-9a-f]{40}\Z")
MARKER: Final = ".local/state/litellm-retired/completed"
BACKUP: Final = ".local/state/litellm-retired/20261019"
BACKUP_NAMES: Final = (
    "litellm-db.sql.gz", "volume-autophagy_litellm_postgres_data.tar.gz", "config.yaml",
    "docker-compose.yml", "alert_dispatcher.py", "custom_callbacks.py",
)
IMAGES: Final = ("docker.litellm.ai/berriai/litellm:main-stable", "postgres:16-alpine", "python:3.13-alpine")
VOLUMES: Final = ("autophagy-litellm_litellm_postgres_data", "autophagy_litellm_postgres_data")
Runner = Callable[[tuple[str, ...]], subprocess.CompletedProcess[str]]


def run(argv: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    # deploy_all reaches us through `sudo -n -u <acct> -H env …`, which carries no
    # XDG_RUNTIME_DIR; `systemctl --user` then prints nothing and every unit check refused
    # (v2.6.1 on the node, 2026-10-19). An existing value is kept.
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return subprocess.run(argv, capture_output=True, text=True, check=False, timeout=120, env=env)


def checked(argv: tuple[str, ...], runner: Runner) -> str:
    result = runner(argv)
    if result.returncode:
        raise ValueError("GATEWAY-RETIRE-REFUSED: 명령 실패")
    return result.stdout


def safe_path(home: Path, relative: str) -> Path:
    path = home / relative
    for part in (path, *path.parents):
        if part == home:
            break
        if part.is_symlink() or os.path.ismount(part):
            raise ValueError("GATEWAY-RETIRE-REFUSED: 링크 또는 마운트 경로")
    return path


def release_switch(home: Path) -> str:
    flag = safe_path(home, ".hermes/release-flags/litellm")
    try:
        value = flag.read_bytes()
    except FileNotFoundError:
        return "active"
    if value != b"retired":
        raise ValueError("GATEWAY-RETIRE-REFUSED: 스위치는 줄바꿈 없는 retired만 허용")
    return "retired"


def atomic_write(path: Path, data: bytes) -> None:
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".retire-")
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        if path.read_bytes() != data:
            raise ValueError("GATEWAY-RETIRE-REFUSED: 저장 후 재조회 불일치")
    finally:
        Path(temporary).unlink(missing_ok=True)


def stopped(unit: str, runner: Runner) -> None:
    for verb, allowed in (("is-active", ("inactive", "failed", "unknown")),
                          ("is-enabled", ("disabled", "not-found", "masked", "static"))):
        result = runner(("systemctl", "--user", verb, unit))
        if (result.returncode not in (0, 1, 3, 4) or result.stdout.strip() not in allowed
                or (result.returncode == 0 and result.stdout.strip() != "static")):
            raise ValueError("GATEWAY-RETIRE-REFUSED: 유닛 정지·비활성 확인 불가")


def docker(args: tuple[str, ...], runner: Runner) -> str:
    return checked(("sg", "docker", "-c", shlex.join(("docker", *args))), runner)


def remove_ops(home: Path, runner: Runner, *, check: bool = False) -> None:
    folder = safe_path(home, BACKUP)
    index = safe_path(home, BACKUP + "/SHA256SUMS")
    entries = index.read_text(encoding="utf-8").splitlines()
    expected = set(BACKUP_NAMES)
    parsed = [re.fullmatch(r"([0-9a-f]{64}) [ *](?:\./)?([^/]+)", line) for line in entries]
    if any(row is None for row in parsed) or {row[2] for row in parsed if row} != expected or len(entries) != len(expected):
        raise ValueError("GATEWAY-RETIRE-REFUSED: 백업 색인 오류")
    for name in BACKUP_NAMES:
        if not safe_path(home, BACKUP + "/" + name).is_file():
            raise ValueError("GATEWAY-RETIRE-REFUSED: 백업 파일 없음")
    checked(("sh", "-c", 'cd "$1" && sha256sum -c SHA256SUMS', "_", str(folder)), runner)
    print("GATEWAY-RETIRE: 백업 검증 완료")
    stopped("litellm-gateway.service", runner)
    if checked(("ss", "-H", "-lntu", "sport = :4000"), runner).strip():
        raise ValueError("GATEWAY-RETIRE-REFUSED: 포트 사용 중")
    for image in IMAGES:
        if docker(("ps", "-q", "--filter", "ancestor=" + image), runner).strip():
            raise ValueError("GATEWAY-RETIRE-REFUSED: 컨테이너 실행 중")
    unit = safe_path(home, ".config/systemd/user/litellm-gateway.service")
    compose = safe_path(home, "litellm-gateway")
    print("GATEWAY-RETIRE: 정지 상태 확인 완료")
    if check:
        # Rehearsal: prove docker is reachable as this account, then stop before any change.
        docker(("volume", "ls", "--format", "{{.Name}}"), runner)
        docker(("image", "ls", "--format", "{{.Repository}}:{{.Tag}}"), runner)
        print("GATEWAY-RETIRE-CHECK: ops 통과(변경 없음)")
        return
    unit.unlink(missing_ok=True)
    checked(("systemctl", "--user", "daemon-reload"), runner)
    print("GATEWAY-RETIRE: ops 유닛 제거 완료")
    volumes = set(docker(("volume", "ls", "--format", "{{.Name}}"), runner).splitlines())
    for volume in VOLUMES:
        if volume in volumes:
            docker(("volume", "rm", volume), runner)
        print("GATEWAY-RETIRE: 지정 볼륨 처리 완료")
    available = set(docker(("image", "ls", "--format", "{{.Repository}}:{{.Tag}}"), runner).splitlines())
    for image in IMAGES:
        if image in available:
            if docker(("ps", "-a", "-q", "--filter", "ancestor=" + image), runner).strip():
                print("GATEWAY-RETIRE: 다른 컨테이너가 쓰는 이미지 보존")
                continue
            docker(("image", "rm", image), runner)
        print("GATEWAY-RETIRE: 지정 이미지 처리 완료")
    if compose.exists():
        shutil.rmtree(compose)
    print("GATEWAY-RETIRE: 비밀 설정 폴더 제거 완료")


def remove_agent(home: Path, runner: Runner, *, check: bool = False) -> None:
    config = safe_path(home, ".hermes/config.yaml")
    secrets = safe_path(home, ".env.secrets")
    config_data = config.read_bytes()
    updated_config = config_without_gateway(config_data)
    secret_data = secrets.read_bytes() if secrets.exists() else b""
    updated_secrets = b"".join(line for line in secret_data.splitlines(keepends=True)
                               if not re.match(rb"[ \t]*(?:export[ \t]+)?LITELLM_[A-Za-z0-9_]+[ \t]*=", line))
    units = [safe_path(home, ".config/systemd/user/autophagy-cron-daily-cost-report." + suffix)
             for suffix in ("timer", "service")]
    script = safe_path(home, ".hermes/scripts/send_cost_report.py")
    for unit in units:
        stopped(unit.name, runner)
    print("GATEWAY-RETIRE: agent 설정·유닛 검사 완료")
    if check:
        print("GATEWAY-RETIRE-CHECK: agent 통과(변경 없음)")
        return
    for path, before, after in ((config, config_data, updated_config), (secrets, secret_data, updated_secrets)):
        if before != after:
            if path.read_bytes() != before:
                raise ValueError("GATEWAY-RETIRE-REFUSED: 검사 뒤 설정 변경")
            atomic_write(path, after)
        print("GATEWAY-RETIRE: agent 설정 처리 완료")
    for path in (script, *units):
        path.unlink(missing_ok=True)
        print("GATEWAY-RETIRE: 비용 보고 파일 처리 완료")
    checked(("systemctl", "--user", "daemon-reload"), runner)
    print("GATEWAY-RETIRE: agent 유닛 재조회 준비 완료")


def remove(home: Path, role: str, head: str, *, runner: Runner = run, check: bool = False) -> None:
    if HEAD.fullmatch(head) is None or role not in ("ops", "agent"):
        raise ValueError("GATEWAY-RETIRE-REFUSED: 제거 인자 오류")
    marker = safe_path(home, MARKER)
    if marker.exists():
        if HEAD.fullmatch(marker.read_text(encoding="ascii")) is None:
            raise ValueError("GATEWAY-RETIRE-REFUSED: 완료 표식 오류")
        print("GATEWAY-RETIRE: 이미 완료")
        return
    match role:
        case "ops":
            remove_ops(home, runner, check=check)
        case "agent":
            remove_agent(home, runner, check=check)
    if check:
        return
    marker.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    atomic_write(marker, head.encode("ascii"))
    print("GATEWAY-RETIRE: 제거 완료")


def main() -> int:
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--release-switch", action="store_true")
    modes.add_argument("--remove", action="store_true")
    parser.add_argument("--role", choices=("ops", "agent"))
    parser.add_argument("--head")
    parser.add_argument("--check", action="store_true", help="run every guard, change nothing")
    args = parser.parse_args()
    if args.remove and (args.role is None or args.head is None):
        parser.error("--remove에는 --role과 --head 필요")
    try:
        home = Path.home()
        if args.release_switch:
            print(release_switch(home))
            return 0
        if args.check:
            # Rehearsal writes nothing, not even the lock file.
            remove(home, args.role, args.head, check=True)
            return 0
        lock = safe_path(home, ".local/state/gateway-retirement.lock")
        lock.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        with lock.open("a") as handle:
            os.fchmod(handle.fileno(), 0o600)
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            remove(home, args.role, args.head, check=args.check)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 4
    except (OSError, UnicodeError, subprocess.SubprocessError) as error:
        print(f"GATEWAY-RETIRE-REFUSED: 파일·명령 처리 불가({type(error).__name__})", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
