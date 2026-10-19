"""노드의 healthcheck SSH 경로를 하나의 설치 자산으로 수렴시킨다."""
from __future__ import annotations

import os
import pwd
import re
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Literal

from automation.install.allowed_signers import PublicKey, TrustKeyError, parse_public_key
from automation.install.peer_attest_key import CommandRunner
from automation.install.plan import ProvisionHealthcheckProbe

def wrapper_path(action: ProvisionHealthcheckProbe) -> Path:
    return action.operator_home / '.local/libexec/autophagy-healthcheck-probe'


def generator_command(action: ProvisionHealthcheckProbe, mode: Literal['--install', '--inputs-digest']) -> tuple[str, ...]:
    # 설치 전에는 릴리스 링크가 없으므로 생성기와 설정을 배포 체크아웃에 고정한다. 그 체크아웃은
    # ops 소유 2750 이라 운영자로 강등해 부르면 읽지 못하고(2026-09-08 실호스트 실측 rc=126),
    # 판정도 같은 argv 를 쓰는데 runuser 는 root 전용이라 ops 로 도는 계획에서 언제나 실패했다.
    # 권한은 그대로 두고 운영자의 것은 HOME 과 래퍼 경로로만 넘긴다 — 래퍼 내용은 프로브
    # 카탈로그에서 나오지 실행 사용자에서 나오지 않는다.
    return (
        'env', '-i', 'PATH=/usr/local/bin:/usr/bin:/bin',
        f'HOME={action.operator_home}', 'HEALTHCHECK_NODE_CONFIG_PATH=/etc/autophagy/node.toml',
        f'HEALTHCHECK_RELEASE_SOURCE_ROOT={action.source_dir}',
        f'HEALTHCHECK_WRAPPER_PATH={wrapper_path(action)}',
        'bash', str(action.source_dir / 'automation/healthcheck_probe_wrapper.sh'), mode, action.node_name,
    )


def _carries_key(line: str, public: PublicKey) -> bool:
    fields = line.split()
    return not line.lstrip().startswith('#') and any(
        algorithm == public.algorithm and material == public.material
        for algorithm, material in zip(fields, fields[1:])
    )


def append_binding(existing: str, public_key: str, wrapper: Path) -> str:
    """다른 운영자의 줄과 기존 키의 주석을 보존한다."""
    public = parse_public_key(public_key)
    if any(_carries_key(line, public) for line in existing.splitlines()):
        return existing
    separator = '' if not existing or existing.endswith('\n') else '\n'
    return existing + separator + f'restrict,command="{wrapper}" {public.line()}\n'


def inspect_probe(action: ProvisionHealthcheckProbe) -> bool:
    if not action.source_dir.is_dir():
        return False
    try:
        result = subprocess.run(generator_command(action, '--inputs-digest'), cwd=action.source_dir,
                                check=False, capture_output=True, text=True)
    except PermissionError:  # the operator's dry run on a node cannot enter the ops checkout
        return False
    expected = result.stdout.strip() if result.returncode == 0 else None
    if expected is None or re.fullmatch('[0-9a-f]{64}', expected) is None:
        return False
    wrapper = wrapper_path(action)
    authorized = action.operator_home / '.ssh/authorized_keys'
    public_path = action.private_path.with_suffix('.pub')
    try:
        operator = pwd.getpwnam(action.operator_account)
        ops = pwd.getpwnam(action.ops_account)
        header = f'# wrapper-inputs: {expected}'
        if header not in wrapper.read_text(encoding='utf-8').splitlines():
            return False
        public = parse_public_key(public_path.read_text(encoding='utf-8'))
        binding = f'restrict,command="{wrapper}" '
        if not any(line.startswith(binding) and _carries_key(line, public)
                   for line in authorized.read_text(encoding='utf-8').splitlines()):
            return False
        for path, mode, owner in (
            (wrapper, 0o755, operator), (authorized, 0o600, operator),
            (action.private_path, 0o600, ops), (public_path, 0o644, ops),
            (authorized.parent, 0o700, operator), (action.private_path.parent, 0o700, ops),
        ):
            metadata = path.lstat()
            if (stat.S_IMODE(metadata.st_mode) != mode or stat.S_ISLNK(metadata.st_mode)
                    or metadata.st_uid != owner.pw_uid or metadata.st_gid != owner.pw_gid):
                return False
    except (OSError, KeyError, UnicodeError, TrustKeyError):
        return False
    return True


def provision_probe(action: ProvisionHealthcheckProbe, run: CommandRunner) -> None:
    public_path = action.private_path.with_suffix('.pub')
    for directory, owner in ((action.private_path.parent, action.ops_account),
                             (action.operator_home / '.ssh', action.operator_account)):
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        shutil.chown(directory, user=owner, group=owner)
    if os.path.lexists(action.private_path) != os.path.lexists(public_path):
        raise OSError('HEALTHCHECK-KEY-PARTIAL: 키 쌍이 불완전하여 재생성을 거부한다')
    if not action.private_path.exists():
        _ = run(('runuser', '-u', action.ops_account, '--', 'ssh-keygen', '-q', '-t', 'ed25519',
                 '-N', '', '-f', str(action.private_path), '-C', f'{action.ops_account}@{action.node_name}-healthcheck'))
    for path, mode in ((action.private_path, 0o600), (public_path, 0o644)):
        if not stat.S_ISREG(path.lstat().st_mode):
            raise OSError('HEALTHCHECK-KEY-TYPE: 키가 일반 파일이 아니다')
        os.chmod(path, mode)
        shutil.chown(path, user=action.ops_account, group=action.ops_account)
    authorized = action.operator_home / '.ssh/authorized_keys'
    # append 모드는 기존 줄을 재작성하지 않고 새 키의 바인딩만 덧붙인다.
    with authorized.open('a+', encoding='utf-8') as stream:
        os.fchmod(stream.fileno(), 0o600)
        shutil.chown(authorized, user=action.operator_account, group=action.operator_account)
        _ = stream.seek(0)
        existing = stream.read()
        updated = append_binding(existing, public_path.read_text(encoding='utf-8'), wrapper_path(action))
        _ = stream.write(updated[len(existing):])
    result = run(generator_command(action, '--install'), cwd=action.source_dir)
    if not any(line.startswith(('WRAPPER-INSTALLED ', 'WRAPPER-UNCHANGED ')) for line in result.stdout.splitlines()):
        raise OSError('HEALTHCHECK-WRAPPER-UNCONFIRMED: 생성기가 설치 완료 표식을 반환하지 않았다')
    # 생성기가 root 로 돌았으므로 운영자 홈에 남은 것은 root 소유다. 판정은 운영자 소유
    # 0755 를 요구하므로 여기서 넘기지 않으면 적용은 성공하고 다음 계획이 또 올린다.
    wrapper = wrapper_path(action)
    for path in (action.operator_home / '.local', wrapper.parent, wrapper):
        if not path.exists():
            continue
        os.chmod(path, 0o755)
        shutil.chown(path, user=action.operator_account, group=action.operator_account)
