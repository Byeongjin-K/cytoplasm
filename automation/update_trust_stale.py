"""Notice a newer release this node cannot verify, without touching the root helper.

2026-10-16: after the update key rotated, a third-party node stayed on v1.22.2 for days.
The resolver skips every tag it cannot verify and installs the newest one it can, with
rc 0, so the reconciler and the healthcheck both reported healthy. This module asks the
one question the resolver does not: is there a release NEWER than the installed one that
failed its signature, and which key signed it?

It lives outside ``update_trust.py`` on purpose. That file is copied into the root-owned
converge helper (provision-deploy-converge.sh) and the release-helper drift probe fails
whenever its bytes change, which only a manual sudo re-provision can clear. Detection is
observation, so it belongs on the unprivileged side and never decides what is installed.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import os
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "automation"

from automation.git_tag_signature import GitRunner, RemoteReleaseTag
from automation.update_trust import (
    _HEAD_REF,
    UPDATE_ALLOWED_SIGNERS_PATH,
    TrustedUpdate,
    UpdateTrustError,
    _Git,
    _parse_remote_release,
    _release_candidates,
    _remote_argument,
    _TagVerifier,
    _verify_remote_tag,
    resolve_signed_update,
)
from automation.update_trust_state import ReleaseFloorError, parse_release_version

#: Rejections that mean "this node's trust file does not name the key that signed it".
#: Transport and race failures say nothing about the key and raise no warning.
_SIGNATURE_REJECTIONS: Final = frozenset({"BAD-SIGNATURE", "WRONG-PRINCIPAL"})
_SSH_SIGNATURE: Final = ("-----BEGIN SSH SIGNATURE-----", "-----END SSH SIGNATURE-----")


@dataclass(frozen=True, slots=True)
class StaleTrustKey:
    installed_tag: str
    tag: str
    reason: str
    signer_fingerprint: str


def stale_key_warning(stale: StaleTrustKey | None) -> str:
    """One line an operator can act on: which release is stuck, why, and which key signed it."""
    if stale is None:
        return ""
    signer = stale.signer_fingerprint or "알 수 없음"
    return (
        f"UPDATE-TRUST-STALE-KEY 신뢰 키 갱신 필요: 더 새 릴리스 {stale.tag} 가 이 서버의 "
        f"신뢰 키({UPDATE_ALLOWED_SIGNERS_PATH})로 검증되지 않아 {stale.installed_tag} 에 머물러 있다. "
        f"새 태그 서명 키 지문 {signer} ({stale.reason}). 공개 릴리스 노트·README 의 지문과 "
        "같을 때만 docs/guide/install.md 「업데이트 신뢰키가 바뀌었을 때」 절차로 추가한다."
    )


def ssh_signature_fingerprint(tag_object: str) -> str:
    """SHA256 fingerprint of the key embedded in a tag's SSH signature, or "".

    An SSHSIG blob carries the signer's public key (PROTOCOL.sshsig): magic, uint32
    version, then the key as an SSH string. The fingerprint is ssh-keygen's: unpadded
    base64 of sha256 over that key blob. Reading it is not trusting it.
    """
    begin, end = _SSH_SIGNATURE
    start = tag_object.find(begin)
    stop = tag_object.find(end, start + 1)
    if start < 0 or stop < 0:
        return ""
    armored = "".join(tag_object[start + len(begin):stop].split())
    try:
        blob = base64.b64decode(armored, validate=True)
    except (binascii.Error, ValueError):
        return ""
    if not blob.startswith(b"SSHSIG") or len(blob) < 14:
        return ""
    length = int.from_bytes(blob[10:14], "big")
    key = blob[14:14 + length]
    if length == 0 or len(key) != length:
        return ""
    digest = base64.b64encode(hashlib.sha256(key).digest()).decode("ascii").rstrip("=")
    return f"SHA256:{digest}"


def _signer(git: _Git, remote_url: str, tag: RemoteReleaseTag) -> str:
    with tempfile.TemporaryDirectory(prefix="autophagy-update-signer-") as temporary:
        repository = Path(temporary) / "repository.git"
        tag_ref = f"refs/tags/{tag.name}"
        try:
            _ = git.run(("git", "init", "--bare", str(repository)), "TAG-SIGNER")
            _ = git.run(
                ("git", "-C", str(repository), "-c", f"remote.origin.url={remote_url}",
                 "fetch", "--force", "--no-tags", "--depth=1", "origin", f"+{tag_ref}:{tag_ref}"),
                "TAG-SIGNER",
            )
            shown = git.run(("git", "-C", str(repository), "cat-file", "tag", tag_ref), "TAG-SIGNER")
        except UpdateTrustError:
            return ""
    return ssh_signature_fingerprint(shown.stdout)


def find_stale_trust_key(
    mirror: Path,
    update: TrustedUpdate,
    allowed_signers: Path = UPDATE_ALLOWED_SIGNERS_PATH,
    runner: GitRunner = subprocess.run,
    *,
    remote_url: str | None = None,
) -> StaleTrustKey | None:
    """The newest release above ``update`` that fails its signature, or None.

    Raises UpdateTrustError only when the remote cannot be read at all; callers treat
    that as "unknown", never as a reason to change what is installed.
    """
    git = _Git(runner=runner, environment=dict(os.environ))
    remote = _remote_argument(remote_url)
    refs = git.run(("git", "-C", str(mirror), "ls-remote", remote, _HEAD_REF, "refs/tags/*"), "REMOTE-REFS")
    release = _parse_remote_release(refs.stdout)
    try:
        installed = parse_release_version(update.tag)
    except ReleaseFloorError:
        return None
    newer = [tag for tag in _release_candidates(release) if parse_release_version(tag.name) > installed]
    if not newer:
        return None
    url = remote if remote_url is not None else git.run(
        ("git", "-C", str(mirror), "remote", "get-url", "origin"), "REMOTE-URL",
    ).stdout.strip()
    verifier = _TagVerifier(git=git, remote_url=url, allowed_signers=allowed_signers)
    try:
        _ = _verify_remote_tag(verifier, newer[0])
    except UpdateTrustError as error:
        if error.prefix not in _SIGNATURE_REJECTIONS:
            return None
        return StaleTrustKey(update.tag, newer[0].name, error.prefix, _signer(git, url, newer[0]))
    return None


def main(argv: Sequence[str] | None = None) -> int:
    """``check``: print the warning and exit 3 when stale, 0 when not, 1 when unknown."""
    parser = argparse.ArgumentParser(prog="update-trust-stale")
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("check")
    _ = check.add_argument("--mirror", type=Path, required=True)
    _ = check.add_argument("--allowed-signers", type=Path, default=UPDATE_ALLOWED_SIGNERS_PATH)
    _ = check.add_argument("--floor-path", type=Path, required=True)
    args = parser.parse_args(argv)
    mirror: Path = args.mirror
    signers: Path = args.allowed_signers
    try:
        update = resolve_signed_update(mirror, signers, floor_path=args.floor_path)
        stale = find_stale_trust_key(mirror, update, signers)
    except UpdateTrustError as error:
        print(f"UPDATE-TRUST-STALE-UNKNOWN {error}", file=sys.stderr)
        return 1
    if stale is None:
        return 0
    print(stale_key_warning(stale), file=sys.stderr)
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
