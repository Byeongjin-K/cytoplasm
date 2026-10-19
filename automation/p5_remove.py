"""Run P5 non-root cleanup as the affected account, after release authorization."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Final

from automation.deploy_all_cron_listing import parse_cron_listing
from automation.p5_backup import backup, inventory
from automation.peer_retired import MARKER, retired

AGENT_PATHS: Final = (
    ".config/systemd/user/hermes-gateway.service",
    ".config/systemd/user/hermes-gateway.service.d",
    ".hermes/plugins", ".hermes/skills", ".hermes/interop_runtime",
    ".hermes/autophagy-import", ".hermes/autophagy-import.d",
    ".hermes/skill-generation", ".hermes/selfskill-audit",
    ".hermes/scripts/selfskill_audit_watch.py", ".hermes/SOUL.md",
    ".hermes/interop/production_guard.sh", ".hermes/interop/pin_import_root.sh",
    ".hermes/interop/gate_driver.py",
    ".config/systemd/user/autophagy-cron-selfskill-audit-watch.service",
    ".config/systemd/user/autophagy-cron-selfskill-audit-watch.timer",
)
PEER_PATHS: Final = (
    ".config/systemd/user/hermes-gateway.service",
    ".config/systemd/user/hermes-gateway.service.d",
)


def call(argv: tuple[str, ...]) -> str:
    """Command failure stops at the named operation without exposing its output."""
    result = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=120)
    if result.returncode:
        raise ValueError(f"P5-REMOVE-FAIL: {' '.join(argv[:3])} rc={result.returncode}")
    return result.stdout


def jobs(hermes: str) -> tuple[str, dict[str, str]]:
    output = call((hermes, "cron", "list", "--all"))
    parsed = parse_cron_listing(output)
    if parsed is None:
        raise ValueError("P5-REMOVE-REFUSED: unreadable cron listing")
    rows = [row for group in parsed.values() for row in group]
    if any(row["state"] != "paused" for row in rows):
        raise ValueError("P5-REMOVE-REFUSED: non-paused Hermes job")
    return output, {row["id"]: row["Name"] for row in rows}


def check(home: Path, role: str, hermes: str) -> str:
    """Rehearsal: the refusals remove() raises before its first write, with no write."""
    _, current = jobs(hermes)
    if retired(home):
        if not sorted((home / ".hermes/retired/p5").glob("*/complete")):
            raise ValueError("P5-REMOVE-REFUSED: retired marker without completed backup")
        return f"P5-CHECK: role={role} 이미 제거됨(남은 일시정지 잡 {len(current)}개, 변경 없음)"
    if len(current) != (30 if role == "agent" else 3):
        raise ValueError("P5-REMOVE-REFUSED: expected 30 agent / 3 peer paused jobs")
    payload = json.loads((home / ".hermes/cron/jobs.json").read_text(encoding="utf-8"))
    raw = payload.get("jobs") if isinstance(payload, dict) else payload
    if not isinstance(raw, list) or {row.get("id") for row in raw} != set(current):
        raise ValueError("P5-REMOVE-REFUSED: jobs file and listing differ")
    return f"P5-CHECK: role={role} 통과(잡 {len(current)}개, 변경 없음)"


def remove(home: Path, role: str, head: str, hermes: str, systemctl: str) -> Path:
    """Backup first; fail on unexpected jobs or changed files; preserve backup on retry."""
    listing, current = jobs(hermes)
    if retired(home):
        completed = sorted((home / ".hermes/retired/p5").glob("*/complete"))
        if not completed:
            raise ValueError("P5-REMOVE-REFUSED: retired marker without completed backup")
        previous = completed[-1].parent
        if current:
            # A deployer re-registered paused Hermes jobs after retirement (2026-10-18: the mail
            # deployer's legacy cron lines). jobs() already refused anything not paused; back up
            # the listing and jobs file before removing, exactly like the first pass.
            late = home / ".hermes/retired/p5" / f"{head}-late"
            late.mkdir(parents=True, mode=0o700, exist_ok=True)
            jobs_file = home / ".hermes/cron/jobs.json"
            for name, data in (("cron-list.txt", listing.encode()), ("job-ids.json", json.dumps(current).encode()),
                               ("jobs.json", jobs_file.read_bytes() if jobs_file.exists() else b"")):
                (late / name).write_bytes(data)
                (late / name).chmod(0o600)
                with (late / name).open("rb") as stream:
                    os.fsync(stream.fileno())
            for job_id in sorted(current):
                call((hermes, "cron", "remove", job_id))
            if jobs(hermes)[1]:
                raise ValueError("P5-REMOVE-FAIL: cron jobs remain")
        backup(home, previous, ())
        return previous
    folder = home / ".hermes/retired/p5" / head
    folder.mkdir(parents=True, mode=0o700, exist_ok=True)
    job_index = folder / "exports.json"
    jobs_file = home / ".hermes/cron/jobs.json"
    if not job_index.exists():
        if len(current) != (30 if role == "agent" else 3):
            raise ValueError("P5-REMOVE-REFUSED: expected 30 agent / 3 peer paused jobs")
        payload = json.loads(jobs_file.read_text(encoding="utf-8"))
        raw = payload.get("jobs") if isinstance(payload, dict) else payload
        if not isinstance(raw, list) or {row.get("id") for row in raw} != set(current):
            raise ValueError("P5-REMOVE-REFUSED: jobs file and listing differ")
        (folder / "jobs.json").write_bytes(jobs_file.read_bytes())
        (folder / "jobs.json").chmod(0o600)
        (folder / "cron-list.txt").write_text(listing, encoding="utf-8")
        (folder / "cron-list.txt").chmod(0o600)
        (folder / "job-ids.json").write_text(json.dumps(current), encoding="utf-8")
        (folder / "job-ids.json").chmod(0o600)
        checksums = {name: hashlib.sha256((folder / name).read_bytes()).hexdigest()
                     for name in ("jobs.json", "job-ids.json", "cron-list.txt")}
        job_index.write_text(json.dumps(checksums), encoding="utf-8")
        job_index.chmod(0o600)
    checksums = json.loads(job_index.read_text(encoding="utf-8"))
    if any(hashlib.sha256((folder / name).read_bytes()).hexdigest() != digest
           for name, digest in checksums.items()):
        raise ValueError("P5-REMOVE-REFUSED: cron export changed")
    for name in (*checksums, "exports.json"):
        with (folder / name).open("rb") as stream:
            os.fsync(stream.fileno())
    original = json.loads((folder / "job-ids.json").read_text(encoding="utf-8"))
    if not current.items() <= original.items():
        raise ValueError("P5-REMOVE-REFUSED: new or changed job since backup")
    selected = [home / p for p in (AGENT_PATHS if role == "agent" else PEER_PATHS)]
    selected += list((home / ".config/systemd/user").glob("*.wants/hermes-gateway.service"))
    if role == "agent":
        selected += list((home / ".hermes").glob("config.yaml.bak*"))
        selected += list((home / ".hermes").glob("SOUL.md.*"))
        selected += list((home / ".config/systemd/user").glob(
            "*.wants/autophagy-cron-selfskill-audit-watch.*"))
    selected = [p for p in selected if p.exists() or p.is_symlink()]
    expected = backup(home, folder, selected)
    # Snapshot bytes must still match before ANY service/file/job deletion.
    if any(expected.get(name) != value for name, value in inventory(home, selected).items()):
        raise ValueError("P5-REMOVE-REFUSED: home changed since backup")
    units = ["hermes-gateway.service"]
    if role == "agent":
        units += ["autophagy-cron-selfskill-audit-watch.timer",
                  "autophagy-cron-selfskill-audit-watch.service"]
    for unit in units:
        # Query LoadState first: retries must not fail on already-removed units.
        loaded = call((systemctl, "--user", "show", unit, "--property=LoadState", "--value")).strip()
        if loaded != "not-found":
            call((systemctl, "--user", "disable", "--now", unit))
            state = call((systemctl, "--user", "show", unit, "--property=ActiveState", "--value")).strip()
            if state not in ("inactive", "failed"):
                raise ValueError(f"P5-REMOVE-FAIL: unit still active {unit}")
    remaining_paths = [p for p in selected if p.exists() or p.is_symlink()]
    if any(expected.get(name) != value for name, value in inventory(home, remaining_paths).items()):
        raise ValueError("P5-REMOVE-REFUSED: home changed while stopping units")
    for job_id in sorted(current):
        call((hermes, "cron", "remove", job_id))
        _, remaining = jobs(hermes)
        if job_id in remaining or not remaining.items() <= original.items():
            raise ValueError("P5-REMOVE-FAIL: cron remove read-back")
    for path in selected:
        if not path.exists() and not path.is_symlink():
            continue  # systemctl disable removed a backed-up enablement symlink.
        relative = path.relative_to(home).as_posix()
        subtree = {name: value for name, value in expected.items()
                   if name == relative or name.startswith(relative + "/")}
        if inventory(home, (path,)) != subtree:
            raise ValueError("P5-REMOVE-REFUSED: target changed")
        if path.is_dir() and not path.is_symlink():
            for directory in (path, *path.rglob("*")):
                if directory.is_dir() and not directory.is_symlink():
                    directory.chmod(directory.stat().st_mode | 0o700)
            shutil.rmtree(path)
        else:
            path.unlink()
        if path.exists() or path.is_symlink():
            raise ValueError("P5-REMOVE-FAIL: target remains")
    call((systemctl, "--user", "daemon-reload"))
    if jobs(hermes)[1]:
        raise ValueError("P5-REMOVE-FAIL: cron jobs remain")
    marker = home / MARKER
    marker.write_bytes(b"retired")
    marker.chmod(0o600)
    (folder / "complete").write_bytes(b"retired")
    return folder


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=("agent", "peer"), required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--check", action="store_true", help="run the refusals, change nothing")
    args = parser.parse_args()
    if re.fullmatch(r"[0-9a-f]{40}", args.head) is None:
        parser.error("invalid release HEAD")
    home = Path.home()
    skills = home / ".hermes/skills"
    if args.role == "agent" and (skills.exists() or skills.is_symlink()):
        mounted = subprocess.run(("mountpoint", "-q", str(home / ".hermes/skills")),
                                 check=False, capture_output=True)
        if mounted.returncode != 32:
            raise ValueError("P5-REMOVE-REFUSED: governed bind or unreadable skills root")
    os.environ["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"
    if args.check:
        print(check(home, args.role, str(home / ".local/bin/hermes")))
        return 0
    lock = home / ".hermes/retired/p5.lock"
    lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock.open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        folder = remove(home, args.role, args.head, str(home / ".local/bin/hermes"), "systemctl")
    print(f"P5-REMOVED role={args.role} backup={folder}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
