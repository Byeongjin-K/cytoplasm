"""Resolve a public update only through its trusted signed release tag."""

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
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Final, TypeVar

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "automation"

from automation.git_tag_signature import (
    GitRunner,
    HEAD_REF as _HEAD_REF,
    OBJECT_ID as _OBJECT_ID,
    RemoteRefError,
    RemoteRelease,
    RemoteReleaseTag as _ReleaseTag,
    SignatureInvocation,
    TagSignatureError,
    TagSignatureRequest,
    parse_remote_release_refs,
    verify_tag_signature,
)
from automation.node_config import NodeConfigError, load_node_config
from automation.update_trust_state import (
    ReleaseFloorError,
    advance_release_floor,
    parse_release_version,
    release_floor_path,
)


if TYPE_CHECKING:
    from typing import override
else:
    try:
        from typing import override
    except ImportError:
        # provision-deploy-converge.sh 가 이 파일을 루트 libexec 트리에 소수 동반 모듈과 함께
        # 단독 설치한다 — 거기에는 automation.typing_compat 이 없으므로 같은 폴백을 여기서 든다
        # (skills/mail/scripts/mail_runtime.py 와 같은 이유·같은 모양).
        _Method = TypeVar("_Method")

        def override(method: _Method, /) -> _Method:
            return method

UPDATE_ALLOWED_SIGNERS_PATH: Final = Path("/etc/autophagy/update-allowed-signers")
UPDATE_TRUST_PRINCIPAL: Final = "update-trust@autophagy"
_GIT_TIMEOUT_SECONDS: Final = 120.0


#: Rejections that mean "this node's trust file does not name the key that signed it".
#: Transport and race failures are not here: they say nothing about the key.
_SIGNATURE_REJECTIONS: Final = frozenset({"BAD-SIGNATURE", "WRONG-PRINCIPAL"})
_SSH_SIGNATURE: Final = ("-----BEGIN SSH SIGNATURE-----", "-----END SSH SIGNATURE-----")


@dataclass(frozen=True, slots=True)
class UpdateTrustError(Exception):
    prefix: str
    detail: str
    signer_fingerprint: str = ""

    @override
    def __str__(self) -> str:
        return f"{self.prefix}: {self.detail}"


@dataclass(frozen=True, slots=True)
class StaleTrustKey:
    """A release newer than the one installed whose signature this node cannot verify.

    2026-10-16: a third-party node sat on v1.22.2 for days after the update key rotated,
    because the resolver skipped every newer tag and returned the last old-key tag with
    rc 0 — the reconciler, the healthcheck and the owner all saw "healthy".
    """

    tag: str
    reason: str
    signer_fingerprint: str


@dataclass(frozen=True, slots=True)
class TrustedUpdate:
    tag: str
    commit_sha: str
    stale_key: StaleTrustKey | None = None


def stale_key_warning(update: TrustedUpdate) -> str:
    """One line an operator can act on: which release is stuck, why, and which key signed it."""
    stale = update.stale_key
    if stale is None:
        return ""
    signer = stale.signer_fingerprint or "알 수 없음"
    return (
        f"UPDATE-TRUST-STALE-KEY 신뢰 키 갱신 필요: 더 새 릴리스 {stale.tag} 가 이 서버의 "
        f"신뢰 키({UPDATE_ALLOWED_SIGNERS_PATH})로 검증되지 않아 {update.tag} 에 머물러 있다. "
        f"새 태그 서명 키 지문 {signer} ({stale.reason}). 공개 릴리스 노트·README 의 지문과 "
        "같을 때만 docs/guide/install.md 「업데이트 신뢰키가 바뀌었을 때」 절차로 추가한다."
    )


def ssh_signature_fingerprint(tag_object: str) -> str:
    """SHA256 fingerprint of the public key embedded in a tag's SSH signature, or "".

    An SSHSIG blob carries the signer's public key (PROTOCOL.sshsig): magic, uint32
    version, then the key as an SSH string. The fingerprint is ssh-keygen's: unpadded
    base64 of sha256 over that key blob. Reading it is not trusting it — the value only
    names which key to compare against the published fingerprint.
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


class _Arguments(argparse.Namespace):
    command: str
    mirror: Path
    allowed_signers: Path
    node_config: Path | None
    floor_path: Path | None

    def __init__(self) -> None:
        super().__init__()
        self.command = ""
        self.mirror = Path()
        self.allowed_signers = UPDATE_ALLOWED_SIGNERS_PATH
        self.node_config = None
        self.floor_path = None


@dataclass(frozen=True, slots=True)
class _Git:
    runner: GitRunner
    environment: dict[str, str]

    def run(self, args: tuple[str, ...], prefix: str) -> subprocess.CompletedProcess[str]:
        try:
            result = self.runner(
                list(args),
                env=self.environment,
                capture_output=True,
                text=True,
                timeout=_GIT_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise UpdateTrustError(prefix, f"git invocation failed: {error}") from error
        if result.returncode != 0:
            raise UpdateTrustError(prefix, f"git returned {result.returncode}: {result.stderr.strip()}")
        return result


def _parse_remote_release(text: str) -> RemoteRelease:
    try:
        return parse_remote_release_refs(text)
    except RemoteRefError as error:
        raise UpdateTrustError(error.prefix, error.detail) from error


@dataclass(frozen=True, slots=True)
class _TagVerifier:
    git: _Git
    remote_url: str
    allowed_signers: Path


def _verify_remote_tag(
    verifier: _TagVerifier,
    tag: _ReleaseTag,
) -> TrustedUpdate:
    try:
        temporary = tempfile.TemporaryDirectory(prefix="autophagy-update-trust-")
    except OSError as error:
        raise UpdateTrustError("VERIFY-WORKSPACE", f"cannot create verification repository: {error}") from error
    with temporary:
        repository = Path(temporary.name) / "repository.git"
        _ = verifier.git.run(("git", "init", "--bare", str(repository)), "VERIFY-WORKSPACE")
        tag_ref = f"refs/tags/{tag.name}"
        _ = verifier.git.run(
            (
                "git",
                "-C",
                str(repository),
                "-c",
                f"remote.origin.url={verifier.remote_url}",
                "fetch",
                "--force",
                "--no-tags",
                "--depth=1",
                "origin",
                f"+{tag_ref}:{tag_ref}",
            ),
            "TAG-FETCH",
        )
        fetched_object = verifier.git.run(
            ("git", "-C", str(repository), "rev-parse", tag_ref),
            "TAG-BINDING",
        ).stdout.strip()
        if fetched_object != tag.object_sha:
            raise UpdateTrustError("TAG-RACE", f"release tag changed while verifying: {tag.name}")
        try:
            verify_tag_signature(
                SignatureInvocation(
                    verifier.git.runner,
                    verifier.git.environment,
                    _GIT_TIMEOUT_SECONDS,
                ),
                TagSignatureRequest(
                    repository=repository,
                    tag=tag_ref,
                    allowed_signers=verifier.allowed_signers,
                    expected_principal=UPDATE_TRUST_PRINCIPAL,
                ),
            )
        except TagSignatureError as error:
            try:
                shown = verifier.git.run(
                    ("git", "-C", str(repository), "cat-file", "tag", tag_ref), "TAG-SIGNER",
                ).stdout
            except UpdateTrustError:
                shown = ""  # the rejection stands; only the signer's name is unknown
            signer = ssh_signature_fingerprint(shown)
            raise UpdateTrustError(error.prefix, error.detail, signer) from error
        commit_sha = verifier.git.run(
            ("git", "-C", str(repository), "rev-parse", f"{tag_ref}^{{commit}}"),
            "TAG-BINDING",
        ).stdout.strip()
        if commit_sha != tag.commit_sha:
            raise UpdateTrustError("TAG-RACE", f"release tag target changed while verifying: {tag.name}")
        return TrustedUpdate(tag=tag.name, commit_sha=commit_sha)


def _release_candidates(release: RemoteRelease) -> tuple[_ReleaseTag, ...]:
    """Newest release first — 이름 사전순은 v1.10.0 을 v1.9.0 보다 낮게 본다.

    릴리스 절차가 낼 수 없는 이름은 릴리스가 아니므로 후보에서 빠진다. 그 판정은
    parse_release_version 하나가 갖고 여기서 사본을 만들지 않는다. 이 필터가 파서가 아니라
    여기 있는 이유는 git_tag_signature 가 automation 의존을 질 수 없기 때문이다 — 스킬 배포
    스테이징과 peer 증명 단독 체크아웃이 그 파일만 싣는다.
    """
    ordered: list[tuple[tuple[int, int, int], _ReleaseTag]] = []
    for tag in release.tags:
        try:
            ordering = parse_release_version(tag.name)
        except ReleaseFloorError:
            continue
        ordered.append((ordering, tag))
    ordered.sort(key=lambda item: item[0], reverse=True)
    return tuple(tag for _, tag in ordered)


def _remote_argument(remote_url: str | None) -> str:
    if remote_url is None:
        return "origin"
    if not remote_url or remote_url.startswith("-"):
        raise UpdateTrustError("REMOTE-URL", "update channel is empty or option-shaped")
    return remote_url


#: ``floor_path`` has no default on purpose. Both independent verification paths must
#: anchor to the SAME durable floor — the ops pre-gate in ``deploy_reconcile_cli`` and
#: the root helper's re-verification exist as a pair to close a TOCTOU window
#: (decisions.md, W-F1-D), so a caller that silently inherited "no floor" would leave
#: that window open on one side while the other believed it shut. A missing argument is
#: a TypeError here, which is the loudest place for it to be found.
def resolve_signed_update(
    mirror: Path,
    allowed_signers: Path = UPDATE_ALLOWED_SIGNERS_PATH,
    runner: GitRunner = subprocess.run,
    *,
    remote_url: str | None = None,
    floor_path: Path,
) -> TrustedUpdate:
    """Resolve the trusted release, refusing one older than any already verified."""
    git = _Git(runner=runner, environment=dict(os.environ))
    remote = _remote_argument(remote_url)
    refs = git.run(
        ("git", "-C", str(mirror), "ls-remote", remote, _HEAD_REF, "refs/tags/*"),
        "REMOTE-REFS",
    )
    release = _parse_remote_release(refs.stdout)
    verification_url = (
        remote
        if remote_url is not None
        else git.run(
            ("git", "-C", str(mirror), "remote", "get-url", "origin"),
            "REMOTE-URL",
        ).stdout.strip()
    )
    if not verification_url or verification_url.startswith("-"):
        raise UpdateTrustError("REMOTE-URL", "origin URL is empty or option-shaped")
    verifier = _TagVerifier(
        git=git,
        remote_url=verification_url,
        allowed_signers=allowed_signers,
    )
    last_error: UpdateTrustError | None = None
    stale: StaleTrustKey | None = None
    for tag in _release_candidates(release):
        try:
            update = _verify_remote_tag(verifier, tag)
            # Freshness AFTER authorship: an unverified tag name must never be
            # able to move the floor, or an attacker who can push `v99.0.0`
            # without signing it could pin the channel shut forever.
            advance_release_floor(floor_path, update.tag, update.commit_sha)
        except UpdateTrustError as error:
            last_error = error
            # Candidates run newest first, so the first signature rejection is the
            # newest release this node cannot verify. Keep it: falling back to an older
            # verified tag is correct, doing so silently is what froze a node for days.
            if stale is None and error.prefix in _SIGNATURE_REJECTIONS:
                stale = StaleTrustKey(tag.name, error.prefix, error.signer_fingerprint)
        except ReleaseFloorError as error:
            last_error = UpdateTrustError(error.prefix, error.detail)
        else:
            return update if stale is None else replace(update, stale_key=stale)
    if last_error is not None:
        raise last_error
    raise UpdateTrustError("UNSIGNED-HEAD", "origin has no trusted signed release tag")


def resolve_update_target(
    mirror: Path,
    require_signed_updates: bool,
    allowed_signers: Path = UPDATE_ALLOWED_SIGNERS_PATH,
    *,
    remote_url: str | None = None,
    floor_path: Path,
) -> str:
    if require_signed_updates:
        # Passing ``remote_url=None`` through is exactly what omitting it did: the
        # resolver keys both the ls-remote target and the verification URL off
        # ``remote_url is not None``. One call site is one place to keep the floor.
        return resolve_signed_update(
            mirror,
            allowed_signers,
            remote_url=remote_url,
            floor_path=floor_path,
        ).commit_sha
    git = _Git(runner=subprocess.run, environment=dict(os.environ))
    remote = _remote_argument(remote_url)
    result = git.run(
        ("git", "-C", str(mirror), "ls-remote", remote, _HEAD_REF),
        "REMOTE-UNRESOLVED",
    )
    fields = result.stdout.split()
    if len(fields) != 2 or _OBJECT_ID.fullmatch(fields[0]) is None or fields[1] != _HEAD_REF:
        raise UpdateTrustError("REMOTE-UNRESOLVED", "origin/main is absent or malformed")
    return fields[0]


#: ``resolve`` honours ``require_signed_updates``; ``resolve-signed`` reads no
#: configuration at all, and that difference is the whole point. The privileged helper
#: named ``~ops/.hermes/node.toml`` as the file that answer came from, and ops holds
#: NOPASSWD sudo for that helper — one file in its own home was enough to make root
#: install an unsigned ``origin/main`` (2026-08-21). So the signature-only verb takes the
#: floor and the trust root from its caller: both are root-owned paths the provisioner
#: bakes into the helper, and neither is anything an unprivileged account can write.
def _resolve(args: _Arguments) -> tuple[str, bool]:
    if args.command == "resolve-signed":
        if args.floor_path is None:
            raise UpdateTrustError("FLOOR-PATH", "--floor-path is required")
        signed = resolve_signed_update(args.mirror, args.allowed_signers, floor_path=args.floor_path)
        warning = stale_key_warning(signed)
        if warning:
            # stderr, never stdout: the root helper reads stdout as the install target.
            print(warning, file=sys.stderr)
        return signed.commit_sha, False
    config = load_node_config(args.node_config)
    target = resolve_update_target(
        args.mirror, config.require_signed_updates, args.allowed_signers,
        floor_path=release_floor_path(config),
    )
    return target, not config.require_signed_updates


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="update-trust")
    subparsers = parser.add_subparsers(dest="command", required=True)
    resolve = subparsers.add_parser("resolve")
    _ = resolve.add_argument("--mirror", type=Path, required=True)
    _ = resolve.add_argument("--allowed-signers", type=Path, default=UPDATE_ALLOWED_SIGNERS_PATH)
    _ = resolve.add_argument("--node-config", type=Path, default=None)
    signed = subparsers.add_parser("resolve-signed")
    _ = signed.add_argument("--mirror", type=Path, required=True)
    _ = signed.add_argument("--allowed-signers", type=Path, default=UPDATE_ALLOWED_SIGNERS_PATH)
    _ = signed.add_argument("--floor-path", type=Path, required=True)
    args = _Arguments()
    _ = parser.parse_args(argv, namespace=args)
    try:
        target, waived = _resolve(args)
    except (UpdateTrustError, NodeConfigError) as error:
        print(f"UPDATE-TRUST-BLOCK {error}", file=sys.stderr)
        return 1
    if waived:
        print(
            "UPDATE-TRUST-OPTOUT mutable origin/main accepted by explicit node policy",
            file=sys.stderr,
        )
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
