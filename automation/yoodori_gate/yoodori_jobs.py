#!/usr/bin/python3 -I
"""Owner-command jobs for the YooDori gate: owner-message check, detached runs, receipts.

Owner 2026-10-18 (Q233 "6개 다 카드 없이 바로 + 하루 요약에 남기기", and for patent
export 18:29 KST "5번도 카드 없이 해"): writes inside the owner's own account run without
a card, only when a recent owner Discord message asked for them, and each successful
write lands in the shared auto-write ledger with that message's link for the 21:30 summary.
Outbound paths (mail, sharing) are not jobs; they stay approval cards.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from automation.owner_kakao import KakaoReference

DISCORD_EPOCH_MS = 1420070400000
ORIGIN_MAX_AGE_S = 24 * 3600
TAIL_OUT = 12000
TAIL_ERR = 2000
MAX_FILE = 64 * 1024 * 1024
_JOB_ID = re.compile(r"[0-9a-f]{12}")
_PATH_IN_OUTPUT = re.compile(r"(/[^\s'\"`,;:()<>\[\]]+)")


class JobRefused(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Origin:
    channel_id: str
    message_id: str
    link: str
    kakao: tuple[KakaoReference, ...] = ()
    sent_at: float | None = None


Fetch = Callable[[str, str], dict]


def _discord_get(path: str, token: str) -> dict:
    request = urllib.request.Request(
        f"https://discord.com/api/v10{path}",
        headers={"Authorization": f"Bot {token}", "User-Agent": "yoodori-gate (cytoplasm)"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("unexpected Discord response")
    return value


def verify_owner_origin(
    channel_id: str, message_id: str, owner_id: str, token: str, *,
    now: float | None = None, fetch: Fetch = _discord_get, home: Path | None = None,
) -> Origin:
    """The origin must be the owner's own (non-bot) message, or his tap on a question card, from the last 24 hours
    (owner 2026-11-06 17:04 KST "카드 버튼 답을 노드가 근거로 받게 바꿔.")."""
    from automation.owner_origin import OriginRefused, verify_owner_origin as verify
    from automation.owner_kakao import load_kakao_relay

    try:
        relay = load_kakao_relay((home or Path.home()) / ".hermes/interop/config.json")
        found = verify(channel_id, message_id, owner_id, token, now=now, fetch=fetch,
                       card_answers=True, kakao_relay=relay)
    except OriginRefused as error:
        raise JobRefused(str(error)) from None
    return Origin(found.channel_id, found.message_id, found.link, found.kakao, found.sent_at)


def _discord_json(path: str, token: str) -> object:
    """Any Discord JSON value (a reaction list is an array)."""
    request = urllib.request.Request(
        f"https://discord.com/api/v10{path}",
        headers={"Authorization": f"Bot {token}", "User-Agent": "yoodori-gate (cytoplasm)"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def dashboard_address() -> tuple[str, str]:
    """The node's dashboard on loopback and the gate key it keeps in its private root (``orders-gate.token``)."""
    from automation.project_board.config import load_config, state_root
    from automation.project_board.models import ProjectBoardError

    try:
        port = load_config().port
    except ProjectBoardError as error:
        raise ValueError(str(error)) from None
    return f"http://127.0.0.1:{port}", (state_root() / "orders-gate.token").read_text(encoding="utf-8").strip()


def dashboard_ask(method: str, path: str) -> object:
    """One gate API call to the dashboard, never through a proxy; any failure is an OSError or ValueError."""
    base, key = dashboard_address()
    if not key:
        raise ValueError("dashboard gate key is empty")
    request = urllib.request.Request(
        base + path, method=method, data=b"" if method == "POST" else None,
        headers={"X-PB-Gate-Token": key, "Accept": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def verify_owner_todo(todo_id: str, owner_id: str, token: str, *, now: float | None = None):
    """A card-approved dashboard todo as the owner's basis (``automation.owner_origin.verify_owner_todo``)."""
    from automation.owner_origin import OriginRefused, verify_owner_todo as verify

    try:
        return verify(todo_id, owner_id, token, ask=dashboard_ask, fetch=_discord_json, now=now)
    except OriginRefused as error:
        raise JobRefused(str(error)) from None


def record_todo_use(todo_id: str) -> int:
    from automation.owner_origin import OriginRefused, record_todo_use as record

    try:
        return record(todo_id, ask=dashboard_ask)
    except OriginRefused as error:
        raise JobRefused(str(error)) from None


def jobs_root(home: Path) -> Path:
    return home / ".local/state/yoodori-jobs"


def job_dir(home: Path, job_id: str) -> Path:
    if not _JOB_ID.fullmatch(job_id):
        raise JobRefused("job id must be 12 hex characters")
    path = jobs_root(home) / job_id
    if path.is_symlink() or not path.is_dir():
        raise JobRefused("no such job")
    return path


def new_job(home: Path, spec: Mapping[str, object]) -> Path:
    root = jobs_root(home)
    if root.is_symlink():
        raise JobRefused("job directory must not be a symlink")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    job_id = secrets.token_hex(6)
    path = root / job_id
    path.mkdir(mode=0o700)
    (path / "out").mkdir(mode=0o700)
    _atomic(path / "spec.json", json.dumps({**spec, "id": job_id}, ensure_ascii=False).encode())
    return path


def _atomic(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def launch(path: Path, env: Mapping[str, str], python: str = "/usr/bin/python3") -> None:
    """Detach from the SSH session so a long run outlives the gate's own deadline."""
    subprocess.Popen(
        [python, "-I", str(Path(__file__).resolve()), "run", str(path)],
        cwd=str(path / "out"), env=dict(env), stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )


def wait(path: Path, seconds: float, *, step: float = 1.0) -> bool:
    deadline = time.monotonic() + seconds
    while not (path / "rc").exists():
        if time.monotonic() >= deadline:
            return False
        time.sleep(step)
    return True


def _collect_outputs(path: Path, home: Path, started: float, stdout: str) -> None:
    """Copy files this run printed and created under the account home into out/."""
    out = path / "out"
    for raw in dict.fromkeys(_PATH_IN_OUTPUT.findall(stdout)):
        candidate = Path(raw.rstrip("."))
        try:
            real = candidate.resolve()
            if (not real.is_relative_to(home.resolve()) or real.is_relative_to(path.resolve())
                    or candidate.is_symlink() or not real.is_file()
                    or real.stat().st_size > MAX_FILE or real.stat().st_mtime < started - 1
                    or any(part.startswith(".") for part in real.relative_to(home.resolve()).parts)):
                continue
            target = out / real.name
            if not target.exists():
                _atomic(target, real.read_bytes())
        except OSError:
            continue


def _record(spec: Mapping[str, object], path: Path, release: Path) -> None:
    sys.path.insert(0, str(release))
    from automation import auto_write

    now = datetime.now(UTC).isoformat()
    origin = spec["origin"]
    assert isinstance(origin, dict)
    auto_write.record(str(spec.get("kind", "yoodori")), auto_write.Entry(
        source_id=f"discord:{origin['channel_id']}/{origin['message_id']}",
        path=f"{spec['label']} · {origin['link']}" if origin.get("link") else str(spec["label"]),
        revision=path.name, recorded_at=now, written_at=now,
    ))


def run(path: Path, *, home: Path | None = None, release: Path = Path("/srv/autophagy-agent-current")) -> int:
    home = home or Path.home()
    spec = json.loads((path / "spec.json").read_text(encoding="utf-8"))
    argv = [str(word).replace("{OUT}", str(path / "out")) for word in spec["argv"]]
    started = time.time()
    try:
        with (path / "stdout").open("wb") as out, (path / "stderr").open("wb") as err:
            rc = subprocess.run(argv, cwd=str(path / "out"), stdin=subprocess.DEVNULL, stdout=out,
                                stderr=err, timeout=int(spec["timeout"]), check=False).returncode
    except subprocess.TimeoutExpired:
        rc = 124
    except OSError:
        rc = 127
    stdout = (path / "stdout").read_text(encoding="utf-8", errors="replace")
    _collect_outputs(path, home, started, stdout)
    recorded = "no"
    marker = spec.get("marker")
    marked = not marker or any(line.startswith(f"{marker} ") for line in stdout.splitlines())
    if rc == 0 and spec.get("write") and marked:
        try:
            _record(spec, path, release)
            recorded = "yes"
        except (OSError, ValueError, ImportError, KeyError):
            recorded = "failed"
    _atomic(path / "rc", json.dumps({"rc": rc, "ledger": recorded,
                                     "finished": datetime.now(UTC).isoformat()}).encode())
    return rc


def report(path: Path) -> tuple[int, str]:
    spec = json.loads((path / "spec.json").read_text(encoding="utf-8"))
    if not (path / "rc").exists():
        return 0, f"JOB-RUNNING id={spec['id']} name={spec['name']} — check again with: job {spec['id']}\n"
    done = json.loads((path / "rc").read_text(encoding="utf-8"))

    def tail(name: str, limit: int) -> str:
        try:
            text = (path / name).read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return ""
        return text if len(text) <= limit else "…(앞부분 생략)\n" + text[-limit:]

    files = sorted(p.name for p in (path / "out").iterdir() if p.is_file() and not p.name.startswith("."))
    lines = [f"JOB-DONE id={spec['id']} name={spec['name']} rc={done['rc']} ledger={done['ledger']}"]
    if spec.get("write") and done["ledger"] == "yes":
        lines.append(f"근거: {spec['origin']['link']}")
    lines += ["--- stdout ---", tail("stdout", TAIL_OUT), "--- stderr ---", tail("stderr", TAIL_ERR)]
    lines.append("JOB-FILES " + (" ".join(files) if files else "(없음)") + f"  (받기: job-file {spec['id']} <이름>)")
    return int(done["rc"]), "\n".join(lines) + "\n"


def output_file(path: Path, name: str) -> Path:
    if not re.fullmatch(r"(?!\.)[^/\x00-\x1f]{1,200}", name):
        raise JobRefused("plain output file name required")
    target = path / "out" / name
    if target.is_symlink() or not target.is_file() or target.stat().st_size > MAX_FILE:
        raise JobRefused("no such output file")
    return target


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "run":
        sys.exit(2)
    sys.exit(run(Path(sys.argv[2])))
