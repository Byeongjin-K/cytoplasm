"""Release-bound opt-in for document OCR, and the file-relay client it points the extractor at.

Owner Q335 (2026-10-21 03:27 KST "머지하고 다음 릴리스 때 켜기"): `document_text` reads scanned
pdf, legacy hwp and photos only when an OCR command is configured. On the node that command is the
operator's GPU queue reached through files: this client drops the file in `~/outbox/ocr/`, the
operator account's `dori-ocr-relay` runs OCR inside its sandbox and answers in `~/inbox/ocr/`.
No gate, firewall or sudoers line is involved. The host switch `release-flags/document-ocr` must
be exactly `on`, joins the same-HEAD release approval, and lands as the agent marker below; an
absent or malformed marker keeps OCR off. Top-level imports stay stdlib so `relay` runs as a file.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import secrets
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Final

SURFACE: Final = "document-ocr:on"
CARD_LINE: Final = "메일 첨부·스캔 PDF·구형 HWP·사진 글자 읽기(OCR) 켜기"
MARKER_NAME: Final = "document-ocr.mode"
MARKER: Final = f".hermes/{MARKER_NAME}"
ALGORITHM: Final = "document-ocr-v1"
# The relay accepts exactly `<digits>-<digits>-<digits>.<ext>` in lower case.
RELAY_SUFFIXES: Final = frozenset({"pdf", "hwp", "png", "jpg", "jpeg"})
RELAY_TIMEOUT_SECONDS: Final = 1800
# Leave the extractor's own deadline room so this client, not a SIGKILL, withdraws the request.
RELAY_MARGIN_SECONDS: Final = 15


def surface() -> tuple[str, str]:
    return SURFACE, hashlib.sha256(b"on").hexdigest()


def release_mode(home: Path) -> str:
    """Only exact host bytes opt in; unreadable or malformed files refuse."""
    try:
        value = (home / ".hermes/release-flags/document-ocr").read_bytes()
    except FileNotFoundError:
        return "off"
    if value != b"on":
        raise ValueError("DOCUMENT-OCR-REFUSED: expected literal on")
    return "on"


def released_on(environ: dict[str, str] | None = None) -> bool:
    """The deployed marker; anything but exact `on` (missing, `off`, unreadable) keeps OCR off."""
    env = os.environ if environ is None else environ
    hermes = Path(env["HERMES_HOME"]) if env.get("HERMES_HOME") else Path.home() / ".hermes"
    try:
        return (hermes / MARKER_NAME).read_bytes() == b"on"
    except OSError:
        return False


def relay_command() -> tuple[str, ...]:
    """The argv `document_text` runs as `<argv> <file>` once the release marker is on."""
    return (sys.executable or "/usr/bin/python3", "-I", str(Path(__file__).resolve()), "relay")


def _relay_timeout() -> float:
    configured = os.environ.get("DOCUMENT_OCR_TIMEOUT_SECONDS", "").strip()
    total = int(configured) if configured.isdigit() and int(configured) > 0 else RELAY_TIMEOUT_SECONDS
    return float(max(total - RELAY_MARGIN_SECONDS, 1))


def relay(source: Path, *, home: Path | None = None, timeout: float | None = None,
          poll: float = 1.0) -> int:
    """Hand one file to the operator relay and print its markdown; 2 input, 4 OCR failed, 5 no answer."""
    extension = source.suffix.lower().removeprefix(".")
    if extension not in RELAY_SUFFIXES or source.is_symlink() or not source.is_file():
        print(f"document-ocr: not a readable {'/'.join(sorted(RELAY_SUFFIXES))} file", file=sys.stderr)
        return 2
    root = home or Path.home()
    outbox, inbox = root / "outbox/ocr", root / "inbox/ocr"
    for folder in (outbox, inbox):
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    ident = f"{int(time.time())}-{os.getpid()}-{secrets.randbelow(1 << 30)}"
    request = outbox / f"{ident}.{extension}"
    partial = outbox / f".{ident}.{extension}.part"
    answer, failure = inbox / f"{ident}.md", inbox / f"{ident}.err"
    deadline = time.monotonic() + (_relay_timeout() if timeout is None else timeout)
    try:
        shutil.copyfile(source, partial)
        partial.chmod(0o600)
        os.replace(partial, request)
        while True:
            if answer.is_file():
                sys.stdout.write(answer.read_text(encoding="utf-8", errors="replace"))
                return 0
            if failure.is_file():
                sys.stderr.write(failure.read_text(encoding="utf-8", errors="replace")[-2000:])
                return 4
            if time.monotonic() >= deadline:
                print("document-ocr: no answer from the OCR relay in time", file=sys.stderr)
                return 5
            time.sleep(poll)
    finally:
        # Withdrawing the request first lets the relay drop an answer that arrives late.
        for leftover in (partial, request, answer, failure):
            leftover.unlink(missing_ok=True)


def prepare_deploy(repo: Path, host: str, source: Path) -> tuple[str, str]:
    """Verify the active HEAD and existing release authority before preparing marker bytes."""
    from automation.node_config import NodeConfigError, load_node_config

    mode = release_mode(Path.home())
    try:
        config = load_node_config()
    except NodeConfigError as error:
        raise ValueError("노드 설정을 읽을 수 없습니다") from error
    host = host or config.deploy_ssh_host
    if not host or host.startswith("-"):
        raise ValueError("배포 SSH 별칭이 필요합니다")
    head = subprocess.run(
        ("git", "-C", str(repo), "rev-parse", "HEAD"),
        capture_output=True, text=True, check=True, timeout=30,
    ).stdout.strip()
    pointer = subprocess.run(
        ("ssh", host, shlex.join(("readlink", str(config.release_current)))),
        capture_output=True, text=True, check=True, timeout=30,
    ).stdout.strip()
    if Path(pointer).name != head:
        raise ValueError("노드와 배포 HEAD가 다릅니다")
    if mode == "on":
        approved = subprocess.run(
            (str(repo / "automation/release_approval_remote.sh"),
             "authorize-document-ocr", "--head", head),
            capture_output=True, text=True, check=False, timeout=120,
        )
        if approved.returncode:
            raise ValueError("같은 HEAD의 릴리스 승인이 필요합니다")
    source.write_bytes(mode.encode("ascii"))
    return host, config.agent_account


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("switch", "prepare", "relay"))
    parser.add_argument("file", nargs="?", type=Path)
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--host", default="")
    parser.add_argument("--source", type=Path)
    args = parser.parse_args(argv)
    if args.command == "relay":
        if args.file is None:
            parser.error("relay requires a file")
        return relay(args.file)
    try:
        if args.command == "switch":
            print(release_mode(Path.home()))
        else:
            if args.repo is None or args.source is None:
                parser.error("prepare requires repo and source")
            host, account = prepare_deploy(args.repo, args.host, args.source)
            print(f"{host}\n{account}")
        return 0
    except (OSError, ValueError, subprocess.SubprocessError):
        print("DOCUMENT-OCR-REFUSED: 스위치·노드 HEAD·릴리스 승인을 확인하세요", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
