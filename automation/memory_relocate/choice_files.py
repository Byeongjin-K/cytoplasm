"""Account-owned, group-readable mailboxes; names and permissions carry no authority."""
from __future__ import annotations

import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .choices import ChoiceError

_NAME: Final = re.compile(r"[0-9a-f]{64}(?:\.[a-z0-9-]+)*\.json\Z")
_MAX_BYTES: Final = 65536


@dataclass(frozen=True, slots=True)
class Mailbox:
    path: Path
    uid: int
    gid: int

    def prepare(self) -> None:
        """The release provisions the mailboxes (choice_accounts); runtime never repairs permissions."""
        if os.getuid() != self.uid:
            raise ChoiceError("mailbox_writer")
        self.check()

    def check(self) -> None:
        info = self.path.lstat()
        if not stat.S_ISDIR(info.st_mode) or (
            info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)
        ) != (self.uid, self.gid, 0o750):
            raise ChoiceError("mailbox_permissions")

    def read(self, name: str) -> str | None:
        """Open both the mailbox and file without following symlinks, then validate the FD."""
        if not _NAME.fullmatch(name):
            raise ChoiceError("mailbox_name")
        self.check()
        directory = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(directory)
            if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (self.uid, self.gid, 0o750):
                raise ChoiceError("mailbox_changed")
            try:
                descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            except FileNotFoundError:
                return None
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or (
                    info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)
                ) != (self.uid, self.gid, 0o640) or info.st_nlink != 1:
                    raise ChoiceError("mailbox_file_permissions")
                data = stream.read(_MAX_BYTES + 1)
                if len(data) > _MAX_BYTES:
                    raise ChoiceError("mailbox_file_size")
                return data.decode("utf-8")
        finally:
            os.close(directory)

    def publish(self, name: str, text: str) -> None:
        if not _NAME.fullmatch(name) or len(text.encode()) > _MAX_BYTES:
            raise ChoiceError("mailbox_document")
        self.prepare()
        atomic_text(self.path / name, text, group=self.gid)

    def names(self) -> tuple[str, ...]:
        self.check()
        return tuple(sorted(path.name for path in self.path.iterdir() if _NAME.fullmatch(path.name)))


def atomic_text(path: Path, text: str, *, group: int | None = None) -> None:
    """Durable private ledger or mailbox publication, including the parent directory."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, name = tempfile.mkstemp(prefix=".choice-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            if group is not None:
                os.fchown(stream.fileno(), os.getuid(), group)
                os.fchmod(stream.fileno(), 0o640)
            _ = stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        temporary.unlink(missing_ok=True)
