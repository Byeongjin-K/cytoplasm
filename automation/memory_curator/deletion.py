"""Fail-closed disk deletion for one owner-approved memory entry."""

from __future__ import annotations

import fcntl
import os
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Final

from .curator import parse_memory_file, serialize_memory_file
from .model import MemoryEntry, MemoryFile, MemoryKind

_FILENAMES: Final[dict[MemoryKind, str]] = {
    "memory": "MEMORY.md",
    "user": "USER.md",
}
_FILE_MODE: Final = 0o600
_OPEN_SAFELY: Final = os.O_CLOEXEC | os.O_NOFOLLOW
#: 같은 초에 같은 파일에서 회수할 수 있는 최대 건수. tick 당 승격 상한(3)보다 넉넉하다.
_BACKUP_NAME_ATTEMPTS: Final = 16


@dataclass(frozen=True, slots=True)
class DeletionOutcome:
    backup_path: Path
    before_chars: int
    after_chars: int


class DeletionError(RuntimeError):
    """Deletion could not satisfy every disk-safety precondition."""


class MemoryCapError(DeletionError):
    """Appending would push the file past its configured cap — Hermes would refuse it too."""


@dataclass(frozen=True, slots=True)
class AppendOutcome:
    appended: bool
    backup_path: Path | None
    before_chars: int
    after_chars: int


def _open_new_backup(path: Path, stamp: str, action: str = "deleted") -> tuple[Path, int]:
    """이름이 비어 있는 백업을 만든다 — 기존 백업은 절대 덮지 않는다.

    한 reconcile tick 은 시각 하나를 공유한다(`ReconcileRequest.now`). 백업 이름은 초
    단위이므로 같은 파일에서 두 항목을 회수하면 두 번째 이름이 **반드시** 충돌했고,
    그 삭제는 매번 `delete_failed` 로 떨어졌다 — 경합이 아니라 결정적 실패다.
    2026-08-03 실측: 소유자가 승인한 USER.md 2건 중 1건만 삭제되고 나머지는 다음
    tick 으로 밀렸다.

    O_EXCL 은 그대로 둔다. 이름이 이미 있으면 덮어쓰는 대신 다음 이름을 시도한다 —
    충돌은 위험 신호가 아니라 "같은 초에 두 번 회수했다"는 뜻뿐이기 때문이다.
    """
    for suffix in ("", *(f"-{index}" for index in range(2, _BACKUP_NAME_ATTEMPTS + 1))):
        candidate = path.with_name(f"{path.name}.{action}-{stamp}{suffix}")
        try:
            return candidate, os.open(
                candidate,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | _OPEN_SAFELY,
                _FILE_MODE,
            )
        except FileExistsError:
            continue
        except OSError as error:
            raise DeletionError(f"deletion backup could not be created: {candidate}") from error
    raise DeletionError(f"no free deletion backup name for {path.name} at {stamp}")


def _checked_path(memory_dir: Path, kind: MemoryKind) -> Path:
    path = memory_dir / _FILENAMES[kind]
    try:
        path_mode = path.lstat().st_mode
    except OSError as error:
        raise DeletionError(f"memory file is not accessible: {path}") from error
    if stat.S_ISLNK(path_mode):
        raise DeletionError(f"memory file must not be a symlink: {path}")
    if not stat.S_ISREG(path_mode):
        raise DeletionError(f"memory path is not a regular file: {path}")
    return path


class _HermesLock:
    """Hermes' advisory `<file>.lock`, taken non-blocking (a live writer means try later)."""

    def __init__(self, path: Path) -> None:
        self._lock_path = path.with_name(f"{path.name}.lock")
        self._file: BinaryIO | None = None

    def __enter__(self) -> None:
        self._file = _acquire_lock(self._lock_path)

    def __exit__(self, *_exc: object) -> None:
        if self._file is not None:
            self._file.close()


def _acquire_lock(lock_path: Path) -> BinaryIO:
    lock_flags = os.O_RDWR | _OPEN_SAFELY
    lock_created = False
    try:
        lock_descriptor = os.open(
            lock_path,
            lock_flags | os.O_CREAT | os.O_EXCL,
            _FILE_MODE,
        )
        lock_created = True
    except FileExistsError:
        try:
            lock_descriptor = os.open(lock_path, lock_flags)
        except OSError as error:
            raise DeletionError(f"memory lock is not accessible: {lock_path}") from error
    except OSError as error:
        raise DeletionError(f"memory lock could not be created: {lock_path}") from error

    lock_file = os.fdopen(lock_descriptor, "r+b")
    try:
        if lock_created:
            os.fchmod(lock_file.fileno(), _FILE_MODE)
        if not stat.S_ISREG(os.fstat(lock_file.fileno()).st_mode):
            raise DeletionError(f"memory lock is not a regular file: {lock_path}")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise DeletionError(f"memory lock is already held: {lock_path}") from error
        except OSError as error:
            raise DeletionError(f"memory lock could not be acquired: {lock_path}") from error
    except BaseException:
        lock_file.close()
        raise
    return lock_file


def _backup(path: Path, data: bytes, now: datetime, action: str) -> Path:
    backup_path, backup_descriptor = _open_new_backup(path, now.strftime("%Y%m%dT%H%M%SZ"), action)
    try:
        with os.fdopen(backup_descriptor, "wb") as backup_file:
            os.fchmod(backup_file.fileno(), _FILE_MODE)
            _ = backup_file.write(data)
            backup_file.flush()
            os.fsync(backup_file.fileno())
    except OSError as error:
        raise DeletionError(f"deletion backup could not be persisted: {backup_path}") from error
    return backup_path


def _replace(path: Path, memory_dir: Path, rewritten_bytes: bytes) -> bytes:
    try:
        temp_descriptor, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.delete-",
            dir=memory_dir,
        )
    except OSError as error:
        raise DeletionError("temporary deletion file could not be created") from error
    temp_path = Path(temp_name)
    try:
        with os.fdopen(temp_descriptor, "wb") as temp_file:
            _ = temp_file.write(rewritten_bytes)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.chmod(temp_path, _FILE_MODE, follow_symlinks=False)
        os.replace(temp_path, path)
    except OSError as error:
        temp_path.unlink(missing_ok=True)
        raise DeletionError("memory file could not be replaced atomically") from error
    return _read_regular_file(path)


def delete_entry(
    memory_dir: Path,
    kind: MemoryKind,
    index: int,
    *,
    expected_bytes: bytes,
    now: datetime,
) -> DeletionOutcome:
    """Delete exactly one indexed entry while holding Hermes' advisory lock."""
    path = _checked_path(memory_dir, kind)
    with _HermesLock(path):
        data = _read_regular_file(path)
        if data != expected_bytes:
            raise DeletionError("memory snapshot changed before deletion")
        try:
            original = parse_memory_file(data.decode("utf-8"), kind=kind)
        except UnicodeDecodeError as error:
            raise DeletionError("memory file is not valid UTF-8") from error
        if not 0 <= index < len(original.entries):
            raise DeletionError(f"memory entry index is out of range: {index}")
        removed: MemoryEntry = original.entries[index]

        backup_path = _backup(path, data, now, "deleted")

        remaining = original.entries[:index] + original.entries[index + 1 :]
        rewritten = MemoryFile(kind=kind, entries=remaining)
        rewritten_bytes = serialize_memory_file(rewritten).encode("utf-8")
        read_back = _replace(path, memory_dir, rewritten_bytes)
        try:
            verified = parse_memory_file(read_back.decode("utf-8"), kind=kind)
        except UnicodeDecodeError as error:
            raise DeletionError("rewritten memory file is not valid UTF-8") from error
        has_expected_count = len(verified.entries) == len(original.entries) - 1
        removed_is_absent = all(entry.text != removed.text for entry in verified.entries)
        if read_back != rewritten_bytes or not has_expected_count or not removed_is_absent:
            raise DeletionError("rewritten memory file failed deletion verification")

        return DeletionOutcome(
            backup_path=backup_path,
            before_chars=original.char_count,
            after_chars=rewritten.char_count,
        )


def append_entry(
    memory_dir: Path,
    kind: MemoryKind,
    text: str,
    *,
    cap: int,
    now: datetime,
) -> AppendOutcome:
    """Append one entry under the same lock/backup/verify rules; an existing equal entry is a no-op."""
    path = _checked_path(memory_dir, kind)
    with _HermesLock(path):
        data = _read_regular_file(path)
        try:
            original = parse_memory_file(data.decode("utf-8"), kind=kind)
        except UnicodeDecodeError as error:
            raise DeletionError("memory file is not valid UTF-8") from error
        if any(entry.text == text for entry in original.entries):
            return AppendOutcome(False, None, original.char_count, original.char_count)
        rewritten = MemoryFile(kind=kind, entries=(*original.entries, MemoryEntry(text)))
        if rewritten.char_count > cap:
            raise MemoryCapError(f"{path.name} would hold {rewritten.char_count} > {cap} chars")
        backup_path = _backup(path, data, now, "appended")
        rewritten_bytes = serialize_memory_file(rewritten).encode("utf-8")
        read_back = _replace(path, memory_dir, rewritten_bytes)
        if read_back != rewritten_bytes:
            raise DeletionError("rewritten memory file failed append verification")
        return AppendOutcome(True, backup_path, original.char_count, rewritten.char_count)


def _read_regular_file(path: Path) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | _OPEN_SAFELY)
    except OSError as error:
        raise DeletionError(f"memory file could not be opened safely: {path}") from error
    try:
        with os.fdopen(descriptor, "rb") as memory_file:
            if not stat.S_ISREG(os.fstat(memory_file.fileno()).st_mode):
                raise DeletionError(f"memory file is no longer regular: {path}")
            return memory_file.read()
    except OSError as error:
        raise DeletionError(f"memory file could not be read: {path}") from error
