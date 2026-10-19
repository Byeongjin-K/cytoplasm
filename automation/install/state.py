from __future__ import annotations

import grp
import hashlib
import os
import pwd
import stat
import subprocess
from dataclasses import replace
from pathlib import Path

from automation.install.components import EnableUserUnit, EnsureSymlink
from automation.install.healthcheck_probe_asset import inspect_probe
from automation.install.plan import (
    DirectoryState,
    EnsureDirectory,
    FileState,
    InstallInputs,
    ProvisionHealthcheckProbe,
    SystemState,
    build_plan,
)


def _owner(path: Path) -> tuple[str, str] | None:
    try:
        metadata = path.lstat()
        return pwd.getpwuid(metadata.st_uid).pw_name, grp.getgrgid(metadata.st_gid).gr_name
    except (KeyError, OSError):
        return None


def inspect_state(inputs: InstallInputs) -> SystemState:
    config = inputs.config
    account_names = (config.agent_account, config.peer_account, config.ops_account)
    accounts = frozenset(name for name in account_names if _account_exists(name))
    ready_accounts = frozenset(
        name
        for name, home in (
            (config.agent_account, config.agent_home),
            (config.peer_account, config.peer_home),
            (config.ops_account, config.ops_home),
        )
        if _account_ready(name, home)
    )
    members = frozenset(name for name in (config.agent_account, config.peer_account) if _in_group(name, config.service_group))
    groups = {config.service_group: members} if _group_exists(config.service_group) else {}

    desired = build_plan(inputs, SystemState.empty())
    directory_paths = [
        action.spec.path for action in desired.actions if isinstance(action, EnsureDirectory)
    ]
    directories: dict[Path, DirectoryState] = {}
    for path in directory_paths:
        ownership = _owner(path)
        if ownership is not None and path.is_dir() and not path.is_symlink():
            directories[path] = DirectoryState(stat.S_IMODE(path.stat().st_mode), *ownership)

    files: dict[Path, FileState] = {}
    for spec in inputs.files:
        ownership = _owner(spec.path)
        if ownership is not None and spec.path.is_file() and not spec.path.is_symlink():
            try:
                digest = hashlib.sha256(spec.path.read_bytes()).hexdigest()
                mode = stat.S_IMODE(spec.path.stat().st_mode)
            except OSError:
                continue
            files[spec.path] = FileState(digest, mode, ownership[0], ownership[1])

    private_key = config.ops_home / ".ssh" / "id_ed25519"
    private_keys: frozenset[Path] = (
        frozenset({private_key})
        if _key_ready(private_key, config.ops_account)
        else frozenset()
    )
    peer_private_key = config.peer_home / ".ssh" / "peer_attest_ed25519"
    peer_public_key = Path(f"/etc/autophagy/peer-attest-{config.peer_account}.pub")
    peer_attest_keys: frozenset[Path] = (
        frozenset({peer_private_key})
        if _peer_attest_key_ready(peer_private_key, peer_public_key, config.peer_account)
        else frozenset()
    )
    repositories = {
        path: origin
        for path in (config.deploy_checkout, config.repair_work)
        if (origin := _repository_origin(path)) is not None
    }
    timers = frozenset(timer for timer in inputs.timers if _timer_enabled(timer))
    try:
        operator_home = Path(pwd.getpwnam(config.operator_account).pw_dir)
    except KeyError:
        operator_home = None
    probe = next(action for action in desired.actions if isinstance(action, ProvisionHealthcheckProbe))
    return SystemState(
        accounts=accounts,
        ready_accounts=ready_accounts,
        groups=groups,
        directories=directories,
        files=files,
        private_keys=private_keys,
        peer_attest_keys=peer_attest_keys,
        repositories=repositories,
        enabled_timers=timers,
        enabled_user_units=frozenset(unit for unit in inputs.components.user_units if _user_unit_enabled(unit)),
        symlinks=frozenset(link for link in inputs.components.symlinks if _symlink_ready(link)),
        gitleaks_version=_gitleaks_version(),
        operator_home=operator_home,
        healthcheck_probe_ready=inspect_probe(replace(probe, operator_home=operator_home)) if operator_home is not None else False,
    )


def _symlink_ready(link: EnsureSymlink) -> bool:
    try:
        return (
            link.path.is_symlink()
            and link.path.readlink() == link.target
            and _owner(link.path) == (link.owner, link.owner)
        )
    except OSError:
        # 판독 불가는 수렴으로 간주하지 않는다. dry-run은 작업을 계획하고 실제 적용은
        # 같은 권한 문제가 계속되면 실패하므로 변경 경로의 fail-closed 성질도 유지된다.
        return False


def _account_exists(name: str) -> bool:
    try:
        _ = pwd.getpwnam(name)
    except KeyError:
        return False
    return True


def _account_ready(name: str, home: Path) -> bool:
    try:
        account = pwd.getpwnam(name)
        home_stat = home.stat()
        secrets_stat = (home / ".env.secrets").stat()
    except (KeyError, OSError):
        return False
    linger = Path("/var/lib/systemd/linger") / name
    return (
        Path(account.pw_dir) == home
        and stat.S_IMODE(home_stat.st_mode) == 0o700
        and stat.S_IMODE(secrets_stat.st_mode) == 0o600
        and home_stat.st_uid == account.pw_uid
        and secrets_stat.st_uid == account.pw_uid
        and linger.exists()
    )


def _key_ready(private_key: Path, account_name: str) -> bool:
    try:
        account = pwd.getpwnam(account_name)
        private_stat = private_key.stat()
        public_stat = private_key.with_suffix(".pub").stat()
    except (KeyError, OSError):
        return False
    return (
        stat.S_IMODE(private_stat.st_mode) == 0o600
        and private_stat.st_uid == account.pw_uid
        and public_stat.st_uid == account.pw_uid
    )


def _peer_attest_key_ready(
    private_key: Path,
    published_key: Path,
    account_name: str,
) -> bool:
    generated_public = private_key.with_suffix(".pub")
    try:
        account = pwd.getpwnam(account_name)
        private_stat = private_key.lstat()
        generated_stat = generated_public.lstat()
        published_stat = published_key.lstat()
        private_parent_stat = private_key.parent.lstat()
        published_parent_stat = published_key.parent.lstat()
        same_public_key = generated_public.read_bytes() == published_key.read_bytes()
    except (KeyError, OSError):
        return False
    return (
        stat.S_ISREG(private_stat.st_mode)
        and stat.S_ISREG(generated_stat.st_mode)
        and stat.S_ISREG(published_stat.st_mode)
        and stat.S_ISDIR(private_parent_stat.st_mode)
        and stat.S_ISDIR(published_parent_stat.st_mode)
        and stat.S_IMODE(private_stat.st_mode) == 0o600
        and stat.S_IMODE(generated_stat.st_mode) == 0o644
        and stat.S_IMODE(published_stat.st_mode) == 0o644
        and stat.S_IMODE(private_parent_stat.st_mode) == 0o700
        and private_stat.st_uid == account.pw_uid
        and private_stat.st_gid == account.pw_gid
        and generated_stat.st_uid == account.pw_uid
        and generated_stat.st_gid == account.pw_gid
        and private_parent_stat.st_uid == account.pw_uid
        and private_parent_stat.st_gid == account.pw_gid
        and published_stat.st_uid == 0
        and published_stat.st_gid == 0
        and published_parent_stat.st_uid == 0
        and not published_parent_stat.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        and same_public_key
    )


def _repository_origin(path: Path) -> str | None:
    # A dry run by the operator on an installed node cannot read the ops-owned checkout;
    # unreadable is "not converged" like any other path the planner cannot inspect,
    # not an INSTALL-BLOCK (2026-10-19, the release host became the primary node).
    try:
        if not (path / ".git").is_dir():
            return None
    except PermissionError:
        return None
    # Scoped to this one path, never `--system`: the installer runs as root over
    # checkouts owned by the ops account, and git's dubious-ownership refusal made this
    # return None. The planner reads None as "no repository" and drives a bare clone into
    # a populated directory, so every re-run failed (2026-09-07). Trusting one known path
    # answers that without making root trust every repository on the host.
    result = subprocess.run(
        ("git", "-c", f"safe.directory={path}", "-C", str(path), "remote", "get-url", "origin"),
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _group_exists(name: str) -> bool:
    try:
        _ = grp.getgrnam(name)
    except KeyError:
        return False
    return True


def _in_group(account: str, group: str) -> bool:
    try:
        account_entry = pwd.getpwnam(account)
        group_entry = grp.getgrnam(group)
    except KeyError:
        return False
    return group_entry.gr_gid in set(os.getgrouplist(account, account_entry.pw_gid))


def _timer_enabled(name: str) -> bool:
    try:
        result = subprocess.run(
            ("systemctl", "is-enabled", "--quiet", name),
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return False
    return result.returncode == 0


def _user_unit_enabled(unit: EnableUserUnit) -> bool:
    try:
        result = subprocess.run((*unit.command(), "is-enabled", "--quiet", unit.name),
                                check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (KeyError, FileNotFoundError):
        return False
    return result.returncode == 0


def _gitleaks_version() -> str | None:
    try:
        result = subprocess.run(
            ("gitleaks", "version"),
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None
