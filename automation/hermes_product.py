"""Release-bound Hermes product removal; keep the shared runtime and batch CLI."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Final

from automation.hermes_product_policy import CARD_LINES as CARD_LINES, SURFACE as SURFACE, surface as surface

MARKER: Final = ".hermes/hermes-product-retired"
TARGETS: Final = (
    ".hermes/state.db", ".hermes/state.db-wal", ".hermes/state.db-shm",
    ".hermes/sessions", ".hermes/memories", ".hermes/SOUL.md", ".hermes/skills",
    ".hermes/models_dev_cache.json", ".hermes/models_dev_cache.etag",
    ".hermes/gateway_state.json", ".hermes/gateway-generation.json",
    ".hermes/gateway-starts.log", ".hermes/gateway-stopped-by-release",
)
BACKUP_NAMES: Final = ("SOUL.md", "kanban.db")
HEAD: Final = re.compile(r"[0-9a-f]{40}\Z")


def release_switch(home: Path) -> str:
    try:
        value = (home / ".hermes/release-flags/hermes-product").read_bytes()
    except FileNotFoundError:
        return "active"
    if value != b"retired":
        raise ValueError("HERMES-PRODUCT-REFUSED: 스위치는 줄바꿈 없는 retired만 허용")
    return "retired"


def migration_verified() -> bool:
    """Require the count-verified transaction and its exact, unchanged source export."""
    import sqlite3
    import subprocess

    from automation.ledger import migration
    from automation.ledger.store import LedgerError, Store

    ledger = Store()
    try:
        if not ledger.active():
            return False
        with ledger.connect() as db:
            receipt = json.loads(db.execute("SELECT value FROM metadata WHERE key='migration'").fetchone()[0])
            source = db.execute("SELECT value FROM metadata WHERE key='source'").fetchone()
        if source is None:
            return False
        boards, cards = migration.export_hermes()
        migration.verify_source_counts(boards, cards)
        keys = migration.source_keys(boards)
        fingerprint = hashlib.sha256(json.dumps([boards, cards, keys], sort_keys=True).encode()).hexdigest()
        return receipt == migration.summary(boards, cards) and source[0] == fingerprint
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, LedgerError,
            migration.MigrationError, subprocess.SubprocessError):
        return False


def _write(path: Path, data: bytes) -> None:
    """Exclusive private durable creation; never overwrite a pre-removal backup."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def backup(home: Path, folder: Path) -> dict[str, str]:
    """Only SOUL and the ledger source, verified before deletion and on every retry."""
    if any(path.is_symlink() for path in (folder, *folder.parents) if path != home and home in path.parents):
        raise ValueError("HERMES-PRODUCT-REFUSED: 백업 경로가 심볼릭 링크")
    folder.mkdir(parents=True, mode=0o700, exist_ok=True)
    folder.chmod(0o700)
    index = folder / "sha256.json"
    if index.is_symlink():
        raise ValueError("HERMES-PRODUCT-REFUSED: 백업 색인이 심볼릭 링크")
    if not index.exists():
        checksums = {}
        for name in BACKUP_NAMES:
            source = home / ".hermes" / name
            if source.is_symlink():
                raise ValueError("HERMES-PRODUCT-REFUSED: 백업 원본이 심볼릭 링크")
            if not source.exists():
                continue
            data = source.read_bytes()
            destination = folder / name
            if not destination.exists():
                _write(destination, data)
            if destination.is_symlink() or destination.read_bytes() != data:
                raise ValueError("HERMES-PRODUCT-REFUSED: 중단된 백업과 원본 불일치")
            checksums[name] = hashlib.sha256(data).hexdigest()
        _write(index, json.dumps(checksums, sort_keys=True).encode())
    checksums = json.loads(index.read_text(encoding="utf-8"))
    if not isinstance(checksums, dict) or set(checksums) - set(BACKUP_NAMES):
        raise ValueError("HERMES-PRODUCT-REFUSED: 백업 색인 오류")
    for name, digest in checksums.items():
        copy = folder / name
        if copy.is_symlink() or hashlib.sha256(copy.read_bytes()).hexdigest() != digest:
            raise ValueError("HERMES-PRODUCT-REFUSED: 백업 해시 불일치")
    return checksums


def remove(home: Path, head: str, *, runtime: Path | None = None,
           proc_reader: Callable[[], tuple[Path, ...]] | None = None,
           verify_migration: Callable[[], bool] = migration_verified) -> Path:
    """Snapshot all targets, check all uses, verify Q248 backup, remove, mark last."""
    from automation.hermes_product_live import check, process_paths
    from automation.p5_backup import inventory

    if HEAD.fullmatch(head) is None:
        raise ValueError("HERMES-PRODUCT-REFUSED: 릴리스 HEAD 오류")
    if (home / ".hermes").is_symlink():
        raise ValueError("HERMES-PRODUCT-REFUSED: 런타임 루트가 심볼릭 링크")
    marker = home / MARKER
    if marker.is_symlink():
        raise ValueError("HERMES-PRODUCT-REFUSED: 완료 표식이 심볼릭 링크")
    backup_head = marker.read_text(encoding="utf-8") if marker.exists() else head
    if HEAD.fullmatch(backup_head) is None:
        raise ValueError("HERMES-PRODUCT-REFUSED: 완료 표식 오류")
    folder = home / ".hermes/retired/hermes-product" / backup_head
    kanban = home / ".hermes/kanban.db"
    targets = [home / name for name in TARGETS]
    if kanban.exists():
        if verify_migration() and not any(Path(str(kanban) + suffix).exists() for suffix in ("-wal", "-shm")):
            targets.append(kanban)
        else:
            print("HERMES-PRODUCT-KEPT: kanban.db 장부 이관 검증 불충분 또는 SQLite 보조 파일 존재")
    selected = [path for path in targets if path.exists() or path.is_symlink()]
    if marker.exists() and not selected:
        if not (folder / "sha256.json").is_file():
            raise ValueError("HERMES-PRODUCT-REFUSED: 완료 백업 없음")
        backup(home, folder)
        return folder
    expected = inventory(home, selected)
    if any(path.is_symlink() or os.path.ismount(path) for path in selected):
        raise ValueError("HERMES-PRODUCT-REFUSED: 제품 대상이 링크 또는 마운트")
    check(home, targets, runtime or Path(__file__).resolve().parents[1], proc_reader or process_paths)
    checksums = backup(home, folder)
    for path in selected:
        if path.name in BACKUP_NAMES and checksums.get(path.name) != expected[path.relative_to(home).as_posix()]:
            raise ValueError("HERMES-PRODUCT-REFUSED: 백업 뒤 원본 변경")
    if inventory(home, selected) != expected:
        raise ValueError("HERMES-PRODUCT-REFUSED: 검사 뒤 제품 경로 변경")
    # Re-read live users after backup I/O, before the first deletion.
    check(home, targets, runtime or Path(__file__).resolve().parents[1], proc_reader or process_paths)
    for path in selected:
        relative = path.relative_to(home).as_posix()
        subtree = {name: value for name, value in expected.items()
                   if name == relative or name.startswith(relative + "/")}
        if inventory(home, (path,)) != subtree:
            raise ValueError("HERMES-PRODUCT-REFUSED: 삭제 직전 제품 경로 변경")
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    if any(path.exists() or path.is_symlink() for path in targets):
        raise ValueError("HERMES-PRODUCT-REFUSED: 제품 경로 잔존")
    if not marker.exists():
        _write(marker, backup_head.encode())
    return folder


def main() -> int:
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--release-switch", action="store_true")
    modes.add_argument("--remove", action="store_true")
    parser.add_argument("--head")
    args = parser.parse_args()
    home = Path.home()
    if args.remove and (args.head is None or HEAD.fullmatch(args.head) is None):
        parser.error("--remove에는 40자리 릴리스 --head 필요")
    try:
        if args.release_switch:
            print(release_switch(home))
            return 0
        from automation.ledger.switch import command_lock

        if (home / ".hermes").is_symlink():
            raise ValueError("HERMES-PRODUCT-REFUSED: 런타임 루트가 심볼릭 링크")
        lock = home / ".hermes/retired/hermes-product.lock"
        lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with lock.open("a") as handle, command_lock():
            os.fchmod(handle.fileno(), 0o600)
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            folder = remove(home, args.head)
        print(f"HERMES-PRODUCT-REMOVED: 설정·장부 원본 백업={folder}")
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 4
    except OSError as error:
        print(f"HERMES-PRODUCT-REFUSED: 파일·프로세스 판독 또는 백업 실패({type(error).__name__})",
              file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
