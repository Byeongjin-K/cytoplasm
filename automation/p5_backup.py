"""Private, byte-verified account backups retained across interrupted P5 retries."""
from __future__ import annotations

import hashlib
import json
import os
import tarfile
from collections.abc import Sequence
from pathlib import Path


def fingerprint(path: Path) -> str:
    if path.is_symlink():
        return "link:" + os.readlink(path)
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    if path.is_dir():
        return "directory"
    raise ValueError(f"P5-BACKUP-REFUSED: unsupported file {path.name}")


def inventory(home: Path, paths: Sequence[Path]) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in paths:
        entries = [path]
        if path.is_dir() and not path.is_symlink():
            entries.extend(path.rglob("*"))
        for entry in entries:
            # Never traverse an external directory via a symlink.
            if any((home / parent).is_symlink()
                   for parent in entry.relative_to(home).parents if parent != Path(".")):
                raise ValueError("P5-BACKUP-REFUSED: symlink ancestor")
            result[entry.relative_to(home).as_posix()] = fingerprint(entry)
    return result


def verify(archive: Path, expected: dict[str, str]) -> None:
    actual: dict[str, str] = {}
    with tarfile.open(archive, "r") as handle:
        for member in handle.getmembers():
            if member.issym():
                value = "link:" + member.linkname
            elif member.isdir():
                value = "directory"
            elif member.isfile():
                stream = handle.extractfile(member)
                if stream is None:
                    raise ValueError("P5-BACKUP-REFUSED: missing tar data")
                with stream:
                    value = hashlib.sha256(stream.read()).hexdigest()
            else:
                raise ValueError("P5-BACKUP-REFUSED: unsupported tar member")
            if member.name in actual:
                raise ValueError("P5-BACKUP-REFUSED: duplicate tar member")
            actual[member.name] = value
    if actual != expected:
        raise ValueError("P5-BACKUP-REFUSED: archive read-back differs")


def backup(home: Path, folder: Path, paths: Sequence[Path]) -> dict[str, str]:
    """Create once, verify on every use; never overwrite the pre-deletion snapshot."""
    folder.mkdir(parents=True, mode=0o700, exist_ok=True)
    folder.chmod(0o700)
    archive, index = folder / "home.tar", folder / "inventory.json"
    if index.exists():
        expected = json.loads(index.read_text(encoding="utf-8"))
        verify(archive, expected)
        return expected
    expected = inventory(home, paths)
    temporary = folder / "home.tar.tmp"
    with temporary.open("wb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        with tarfile.open(fileobj=stream, mode="w", dereference=False) as handle:
            for relative in sorted(expected):
                # Hardlinks are stored as full bytes to verify every file independently.
                handle.inodes.clear()
                handle.add(home / relative, arcname=relative, recursive=False)
        stream.flush()
        os.fsync(stream.fileno())
    verify(temporary, expected)
    temporary.replace(archive)
    index.write_text(json.dumps(expected, sort_keys=True), encoding="utf-8")
    index.chmod(0o600)
    with index.open("rb") as stream:
        os.fsync(stream.fileno())
    descriptor = os.open(folder, os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return expected
