"""One owner-approved external effect that no dedicated skill owns: record, hash, store.

Before this package an agent asked to "save this result to Drive" or to run any other
external action without a dedicated approval CLI answered "이 작업은 승인 경로가 없다" and
stopped (2026-10-17: a 41-person result file and a Drive save both ended there). The
owner's answer: "이 경로 만들어 모든 요청에 대한 경로가 있어야 해". A request here freezes
exactly what will run — a file's bytes for a Drive save, an argv for a command — binds the
card to one action hash over that frozen payload, and is the only record the watcher
executes from.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Final

STATE_ROOT_ENV: Final = "EFFECT_REQUEST_ROOT"
DEFAULT_STATE_ROOT: Final = Path.home() / ".hermes" / "effect-requests"
LIVE_STATUSES: Final = frozenset({"proposed", "posted", "approved"})
DRIVE_SAVE: Final = "drive-save"
COMMAND: Final = "command"
EFFECTS: Final = frozenset({DRIVE_SAVE, COMMAND})


class EffectRequestError(RuntimeError):
    """A request that cannot be made or executed; ``retryable`` marks transient failures."""

    def __init__(self, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class EffectRequest:
    request_id: str
    key: str
    effect: str
    summary: str
    payload: dict[str, object]
    action_hash: str
    created_at: str
    status: str = "proposed"
    kind: str = ""
    surface: str = ""
    channel_id: str = ""
    policy_version: int = 0
    message_id: str | None = None
    approval_thread_id: str = ""
    approval_guild_id: str = ""
    origin_channel_id: str = ""
    origin_message_id: str = ""
    render_version: str | None = None
    attempts: int = 0
    last_error: str = ""
    receipt: dict[str, object] | None = None
    finished_at: str = ""


def state_root() -> Path:
    override = os.environ.get(STATE_ROOT_ENV)
    return Path(override).expanduser() if override else DEFAULT_STATE_ROOT


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def action_hash(effect: str, summary: str, payload: dict[str, object]) -> str:
    """The one hash the card carries and the watcher re-derives before it runs anything."""
    body = canonical({"action": f"effect_request.{effect}", "payload": payload, "summary": summary})
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> tuple[str, int]:
    with path.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    return digest, path.stat().st_size


class EffectRequestStore:
    """JSON records + frozen files under one flock; every write is atomic 0600."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or state_root()

    @property
    def _state(self) -> Path:
        return self.root / "requests.json"

    def frozen_dir(self, request_id: str) -> Path:
        return self.root / "frozen" / request_id

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (self.root / "store.lock").open("a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _read(self) -> dict[str, EffectRequest]:
        try:
            payload = json.loads(self._state.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as error:
            raise EffectRequestError("effect request store is unreadable") from error
        names = {field.name for field in fields(EffectRequest)}
        return {
            raw["request_id"]: EffectRequest(**{k: v for k, v in raw.items() if k in names})
            for raw in payload.get("requests", [])
        }

    def _write(self, records: dict[str, EffectRequest]) -> None:
        body = {"version": 1, "requests": [asdict(record) for record in records.values()]}
        _atomic_write(self._state, json.dumps(body, ensure_ascii=False, indent=1, sort_keys=True))

    def all(self) -> tuple[EffectRequest, ...]:
        with self._locked():
            return tuple(self._read().values())

    def get(self, request_id: str) -> EffectRequest | None:
        with self._locked():
            return self._read().get(request_id)

    def freeze_file(self, request_id: str, source: Path) -> Path:
        """Copy ``source`` into the request's private folder; the copy is what gets saved."""
        target = self.frozen_dir(request_id) / source.name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(0o600)
        return target

    def create(self, record: EffectRequest) -> None:
        with self._locked():
            records = self._read()
            records[record.request_id] = record
            self._write(records)

    def update(self, record: EffectRequest) -> None:
        with self._locked():
            records = self._read()
            current = records.get(record.request_id)
            if current is None or current.action_hash != record.action_hash:
                raise EffectRequestError("effect request record is absent or stale")
            if current.message_id not in {None, record.message_id}:
                raise EffectRequestError("effect request message binding is immutable")
            records[record.request_id] = record
            self._write(records)

    def set_message(self, request_id: str, message_id: str, channel_id: str, version: str | None) -> None:
        with self._locked():
            records = self._read()
            current = records.get(request_id)
            if current is None:
                raise EffectRequestError("effect request record is absent")
            if current.message_id is not None:
                if (current.message_id, current.channel_id) == (message_id, channel_id):
                    return
                raise EffectRequestError("effect request is already bound to another card")
            records[request_id] = replace(
                current, message_id=message_id, channel_id=channel_id, status="posted",
                render_version=version or current.render_version,
            )
            self._write(records)

    def clear_message(self, key: str, digest: str, message_id: str) -> None:
        with self._locked():
            records = self._read()
            for request_id, record in records.items():
                if (record.key, record.action_hash, record.message_id) == (key, digest, message_id):
                    records[request_id] = replace(record, message_id=None, status="superseded")
            self._write(records)


def _atomic_write(target: Path, text: str) -> None:
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=target.parent, prefix=".tmp-", delete=False
    ) as handle:
        handle.write(text)
        temporary = Path(handle.name)
    temporary.chmod(0o600)
    temporary.replace(target)
