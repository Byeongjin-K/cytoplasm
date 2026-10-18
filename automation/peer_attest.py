#!/usr/bin/env python3
"""Produce an independent peer-bot attestation from the peer sandbox copy.

SI-6: the peer replies BESIDE the deploy request, so its attestation must land on
the same declared supply-chain surface. The channel is either handed in by the
pipeline (``--channel-id``) or resolved once in :func:`main` through the shared
directory under the kind ``SKILL_ATTEST`` — this module resolves nothing itself.
"""

from __future__ import annotations

import importlib
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TypeAlias, assert_never
from urllib.error import HTTPError

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from automation.peer_attestation import PEER_ATTESTATION_TTL, format_attestation, format_signed_attestation, parse_attestation, parse_timestamp  # noqa: E402
from automation.peer_signed_attestation import SignedAttestationPayload, sign_signed_attestation  # noqa: E402
from automation.scenario_runner import scenario_passes  # noqa: E402
from automation.skill_review import _frontmatter_passes, _secret_scan_passes, skill_digest  # noqa: E402

_runtime = importlib.import_module("automation.peer_attest_runtime")
AttestationMode = _runtime.AttestationMode
AttestRequest = _runtime.AttestRequest
DiscordRestTransport = _runtime.DiscordRestTransport
DiscordTransport = _runtime.DiscordTransport
_runtime_attest_channel_id = _runtime.attest_channel_id
_parse_request = _runtime.parse_request

OPS_REPO_ROOT = Path("/srv/autophagy-agents")
RELEASES_ROOT = Path("/srv/autophagy-agent-releases")
RELEASE_CURRENT = Path("/srv/autophagy-agent-current")
GATE_DIR = Path("~/.hermes/skill-gate").expanduser()
INTEROP_CONFIG = Path("~/.hermes/interop/config.json").expanduser()
_VERIFIER_FILES = (
    "automation/git_tag_signature.py",
    "automation/peer_attest.py",
    "automation/peer_attest_runtime.py",
    "automation/peer_attestation.py",
    "automation/peer_signed_attestation.py",
    "automation/scenario_runner.py",
    "automation/skill_review.py",
)
Verdict: TypeAlias = Literal["PASS", "FAIL"]


@dataclass(frozen=True, slots=True)
class AttestResult:
    exit_code: int
    digest: str
    verdict: Verdict
    signed_record: str = ""


@dataclass(frozen=True, slots=True)
class AttestationAttempt:
    request: AttestRequest
    digest: str
    verdict: Verdict
    now: datetime


@dataclass(frozen=True, slots=True)
class SignedAttestContext:
    private_key: Path
    transport: DiscordTransport | None
    now: datetime | None = None


def _find_tamperable_path(repo_root: Path) -> Path | None:
    """Return the first checkout path a non-owner could tamper with (fail-closed on stat errors)."""
    for path in (repo_root, *(repo_root / rel for rel in _VERIFIER_FILES)):
        try:
            mode = path.stat().st_mode
        except OSError:
            return path
        if mode & 0o022:
            return path
    return None


def _is_trusted_attestor_root(
    repo_root: Path,
    *,
    ops_repo_root: Path = OPS_REPO_ROOT,
    releases_root: Path = RELEASES_ROOT,
    release_current: Path = RELEASE_CURRENT,
) -> bool:
    """True only for the mirror, a DIRECT release child, or realpath(current).

    ``Path(__file__).resolve()`` follows the ``current`` symlink, so a runtime
    launched from ``/srv/autophagy-agent-current`` sees REPO_ROOT as
    ``/srv/autophagy-agent-releases/<sha>``. Trust exactly those three shapes and
    nothing deeper: the releases parent itself and any grandchild are refused."""
    if repo_root == ops_repo_root:
        return True
    try:
        resolved_current = release_current.resolve()
    except OSError:
        resolved_current = release_current
    if repo_root == resolved_current and repo_root.parent == releases_root:
        return True
    return repo_root.parent == releases_root and repo_root != releases_root


def _already_attested(transport: DiscordTransport, attempt: AttestationAttempt) -> bool:
    """Return true if a reusable peer reply already carries this exact binding."""
    request = attempt.request
    for message in transport.replies_after(request.channel_id, request.request_message_id):
        reference = message.get("message_reference")
        content = message.get("content")
        if not isinstance(reference, Mapping) or not isinstance(content, str):
            continue
        if reference.get("message_id") != request.request_message_id:
            continue
        attestation = parse_attestation(content)
        if attestation is None:
            continue
        if not (
            attestation.request == request.deploy_nonce
            and attestation.skill == request.skill
            and attestation.digest == attempt.digest
        ):
            continue
        if not request.refresh:
            return True
        timestamp = message.get("timestamp")
        attested_at = parse_timestamp(timestamp) if isinstance(timestamp, str) else None
        if (
            attested_at is not None
            and attestation.verdict == attempt.verdict
            and attested_at <= attempt.now <= attested_at + PEER_ATTESTATION_TTL
        ):
            return True
    return False


def _review_attempt(request: AttestRequest, now: datetime | None) -> AttestationAttempt | None:
    try:
        digest = skill_digest(request.staged_dir)
    except OSError:
        return None
    checks = (
        _frontmatter_passes(request.staged_dir, request.skill),
        scenario_passes(request.staged_dir, None),
        _secret_scan_passes(request.staged_dir),
        digest == request.expected_digest,
    )
    verdict: Verdict = "PASS" if all(checks) else "FAIL"
    return AttestationAttempt(request, digest, verdict, now or datetime.now(UTC))


def _post_discord(transport: DiscordTransport, attempt: AttestationAttempt) -> bool:
    request = attempt.request
    body = format_attestation(request.deploy_nonce, request.skill, attempt.digest, attempt.verdict)
    try:
        if _already_attested(transport, attempt):
            return True
        transport.post_reply(request.channel_id, request.request_message_id, body)
    except (HTTPError, OSError, json.JSONDecodeError):
        return False
    return True


def attest(
    request: AttestRequest,
    transport: DiscordTransport,
    now: datetime | None = None,
) -> AttestResult:
    """Review the peer sandbox bytes and publish exactly one bound verdict reply."""
    attempt = _review_attempt(request, now)
    if attempt is None:
        return AttestResult(1, "unavailable", "FAIL")
    if not _post_discord(transport, attempt):
        return AttestResult(1, attempt.digest, "FAIL")
    return AttestResult(
        0 if attempt.verdict == "PASS" else 1,
        attempt.digest,
        attempt.verdict,
    )


def attest_signed(
    request: AttestRequest,
    context: SignedAttestContext,
) -> AttestResult:
    attempt = _review_attempt(request, context.now)
    if attempt is None:
        return AttestResult(1, "unavailable", "FAIL")
    payload = SignedAttestationPayload(
        request=request.deploy_nonce,
        skill=request.skill,
        digest=attempt.digest,
        verdict=attempt.verdict,
        attested_at=attempt.now,
        approval_channel=request.channel_id,
        approval_message=request.request_message_id,
    )
    signature = sign_signed_attestation(context.private_key, payload)
    if signature is None:
        return AttestResult(1, attempt.digest, "FAIL")
    record = format_signed_attestation(payload, signature)
    if context.transport is not None and not _post_discord(context.transport, attempt):
        return AttestResult(1, attempt.digest, "FAIL", record)
    return AttestResult(
        0 if attempt.verdict == "PASS" else 1,
        attempt.digest,
        attempt.verdict,
        record,
    )


def _attest_channel_id(transport: DiscordRestTransport) -> str:
    """SI-6: ask the shared directory where a ``SKILL_ATTEST`` reply belongs — never a DM.

    The resolver is imported HERE, not at module scope, so the attestor still boots from
    the stdlib-only module set its tamper guard covers; a resolver it cannot reach refuses
    the run instead of guessing a channel.
    """
    return _runtime_attest_channel_id(transport, GATE_DIR, INTEROP_CONFIG)


def _peer_signing_key() -> Path:
    return Path.home() / ".ssh" / "peer_attest_ed25519"


def main(argv: Sequence[str] | None = None) -> int:
    """Run only from the protected runtime as the peer account."""
    from automation.notice_bot import resolve_token

    tamperable = _find_tamperable_path(REPO_ROOT)
    if tamperable is not None:
        print(f"FATAL: peer attestor checkout is group/other-writable: {tamperable}", file=sys.stderr)
        return 2
    if not _is_trusted_attestor_root(REPO_ROOT):
        print("FATAL: peer attestor must run from the release runtime or /srv/autophagy-agents", file=sys.stderr)
        return 2
    request = _parse_request(sys.argv[1:] if argv is None else argv)
    token = resolve_token()
    if request is None:
        print("FATAL: invalid attestation arguments", file=sys.stderr)
        return 2
    match request.mode:
        case AttestationMode.DISCORD:
            if not token:
                print("FATAL: Discord attestation mode requires a peer bot token", file=sys.stderr)
                return 2
            transport = DiscordRestTransport(token)
            try:
                bound = replace(request, channel_id=request.channel_id or _attest_channel_id(transport))
            except OSError as error:
                print(f"FATAL: {error}", file=sys.stderr)
                return 2
            result = attest(bound, transport)
            print(f"PEER-ATTEST-{result.verdict} skill={request.skill} sha256={result.digest}")
            return result.exit_code
        case AttestationMode.SIGNED:
            if not request.channel_id:
                print("FATAL: signed attestation mode requires --channel-id", file=sys.stderr)
                return 2
            # The gate reads only the signed stdout record in this mode, so a peer token
            # (kept for the peer gateway) must not add a [skill-attest] reply the owner
            # decided against on 2026-10-06.
            result = attest_signed(request, SignedAttestContext(_peer_signing_key(), None))
            if result.signed_record:
                print(result.signed_record, end="")
            print(
                f"PEER-ATTEST-{result.verdict} skill={request.skill} sha256={result.digest}",
                file=sys.stderr,
            )
            return result.exit_code
        case unreachable:
            assert_never(unreachable)


if __name__ == "__main__":
    raise SystemExit(main())
