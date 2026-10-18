#!/usr/bin/env python3
"""Owner-only Discord approval gate for skill deployment (W1-8).

Runs as the ``agent`` account on the production node. The production approval path
is a manual reaction by the guild owner (cha) on the surface this flow DECLARES —
``SKILL_DEPLOY`` / ``MANAGED_ACTIVATE``, resolved through the shared directory by
:mod:`automation.skill_gate_surface` and pinned there forever by SI-6.
For unattended regression only, a signed injected approval is accepted under
``E2E_TEST_MODE=1`` by reusing the W1-6 interop injection adapter (HMAC).
The production agent gateway independently refuses E2E_TEST_MODE at boot.

Exit codes: 0 approved / request ok; 1 approval absent or invalid (and a
refused/failed record retirement); 2 usage/config error; 3 weekly auto-proposal
rate limit exceeded; 6 approval-lifecycle refusal (an existing live request is
preserved).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import secrets
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Literal, TypeAlias, assert_never
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

INTEROP_RUNTIME = Path(os.environ.get("INTEROP_RUNTIME", "~/.hermes/interop_runtime")).expanduser()
sys.path.insert(0, str(INTEROP_RUNTIME))

from automation.peer_attestation import AttestationExpectation, SshSignedAttestationVerifier, load_bot_ids, parse_timestamp as _parse_timestamp, valid_peer_attestation, valid_signed_attestation  # noqa: E402
from automation.skill_gate_e2e import GateBindings, check_injected, sign  # noqa: E402
from automation.skill_gate_review import review_status_line  # noqa: E402
from automation import skill_gate_approval, skill_gate_request, skill_gate_retire, skill_gate_specs, skill_gate_surface  # noqa: E402
from automation.interop.approval_lifecycle import ApprovalRecordsError, ApprovalRequest, ApprovalSurfaceError as LifecycleSurfaceError, Probe  # noqa: E402
from automation.interop.approval_surface import ApprovalKind, ApprovalSurfaceError  # noqa: E402

API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://github.com/orientpine/autophagy-agents, 0)"
APPROVE_EMOJI = skill_gate_specs.APPROVE_EMOJI  # ✅ WHITE HEAVY CHECK MARK
CANCEL_EMOJI = skill_gate_specs.CANCEL_EMOJI  # ⛔ NO ENTRY — takes precedence over ✅
GATE_DIR = Path("~/.hermes/skill-gate").expanduser()
INTEROP_CONFIG = Path("~/.hermes/interop/config.json").expanduser()
APPROVAL_LOG = Path(os.environ.get("APPROVAL_LOG_PATH", "/srv/autophagy-agents/logs/approvals.jsonl"))
OPS_PEERS_CONFIG = Path("/etc/autophagy/peers.yaml")
WEEKLY_AUTO_LIMIT = 3
# 첫 줄은 두 모양을 모두 받는다. 신형(`[skill-deploy] wiki 배포 승인 요청`)은 스레드 제목이
# 읽히게 하고, 구형(`[skill-deploy] 승인 요청`)은 **이미 게시된 펌딩 요청**이 그대로
# 해소되게 한다 — 바꾸는 순간 16건이 공중에 떠 있었다.
_REQUEST_BINDING = re.compile(
    r"\A\[skill-deploy\] (?:[a-z0-9][a-z0-9-]{1,40} 배포 )?승인 요청\n"
    r"- skill: `(?P<skill>[a-z0-9][a-z0-9-]{1,40})`\n"
    r"- sha256: `(?P<digest>[0-9a-f]{64})`\n- deploy_nonce: `(?P<nonce>[0-9a-f]{32})`\n"
)
PeerAttestMode: TypeAlias = Literal["discord", "signed"]
SIGNED_PEER_PENDING: Final = "- peer verdict: PENDING (awaiting signed peer record)"

_mask = skill_gate_specs.mask
provenance_lines = skill_gate_specs.provenance_lines  # deploy 요청 CLI 표면 재노출


def _token() -> str:
    from automation.notice_bot import resolve_token

    token = resolve_token()
    if not token:
        print("FATAL: DISCORD_BOT_TOKEN missing", file=sys.stderr)
        raise SystemExit(2)
    return token


#: Discord closes a per-route bucket for a second or two at a time. Asking the owner's
#: decision costs TWO reaction reads per record (⛔ then ✅), so a watcher tick over a
#: handful of pending approvals exhausts that bucket as a matter of course — it is normal
#: traffic, not a fault, and the answer is to wait and ask again. Bounded, though: a tick
#: that retries forever hangs without emitting anything, which is its own silent failure.
_RATE_LIMIT_ATTEMPTS: Final = 5
_RATE_LIMIT_FALLBACK_SECONDS: Final = 1.0


def _retry_after(error: HTTPError) -> float:
    """How long Discord asked us to wait; a missing or unreadable value still backs off."""
    value = error.headers.get("Retry-After") if error.headers is not None else None
    try:
        return max(float(value), 0.0) if value is not None else _RATE_LIMIT_FALLBACK_SECONDS
    except (TypeError, ValueError):
        return _RATE_LIMIT_FALLBACK_SECONDS


def _send(request: Request) -> Any:
    with urlopen(request, timeout=30) as response:  # noqa: S310
        body = response.read().decode("utf-8")
    return json.loads(body) if body else None


def _api(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    """One Discord call, honouring rate limits — and ONLY rate limits.

    A 429 is the server scheduling us, so it is retried. Nothing else is: retrying a 404
    would make a deleted approval message look like a transient blip, and this is the path
    that decides whether the owner approved a deploy.
    """
    request = Request(
        f"{API}{path}",
        data=None if payload is None else json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bot {_token()}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method=method,
    )
    for _ in range(_RATE_LIMIT_ATTEMPTS - 1):
        try:
            return _send(request)
        except HTTPError as error:
            if error.code != 429:
                raise
            time.sleep(_retry_after(error))
    return _send(request)  # 마지막 시도는 실패해도 그대로 올린다


def _owner_id() -> str:
    try:
        owner = json.loads(INTEROP_CONFIG.read_text(encoding="utf-8")).get("owner_id")
    except OSError:
        print(f"FATAL: interop config unreadable: {INTEROP_CONFIG}", file=sys.stderr)
        raise SystemExit(2) from None
    if not isinstance(owner, str) or not owner:
        print("FATAL: owner_id missing from interop config", file=sys.stderr)
        raise SystemExit(2)
    return owner


def _identity() -> skill_gate_surface.GateIdentity:
    """This process's bot identity — the shared directory resolves the surface from it."""
    return skill_gate_surface.GateIdentity(_token(), _api, GATE_DIR, INTEROP_CONFIG)


def _deploy_bindings(skill: str) -> skill_gate_surface.ApprovalBindings:
    """Declare which supply-chain kind this run authorizes; the directory answers where."""
    return skill_gate_surface.surface_for(skill_gate_surface.deploy_kind(skill), _identity())


def _approval_text(skill: str, digest: str, message_id: str) -> str:
    return f"APPROVE skill:{skill} sha256:{digest} msg:{message_id}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    path.chmod(0o600)


def _log_approval(
    args: argparse.Namespace,
    method: str,
    execution: skill_gate_approval.ApprovalExecution | None = None,
) -> None:
    payload = {
        "action": "skill.deploy",
        "approval": {"channel": "approvals", "message_id": args.message_id, "method": method},
        "payload": {"skill_sha256": args.hash},
        "target_id": f"skill:{args.skill}",
    }
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    record = {
        "action": "skill.deploy",
        "approval": payload["approval"],
        "hash": f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}",
        "result": {"status": "approved"},
        "target_id": f"skill:{args.skill}",
        "timestamp": _utc_now(),
    }
    if execution is not None:
        record["binding"] = {
            "action": execution.action,
            "action_hash": execution.request.action_hash,
            "deploy_nonce": execution.nonce,
            "destination": execution.destination,
            "message_id": execution.request.message_id,
        }
    _append_jsonl(APPROVAL_LOG, record)


def _deploy_gate(
    args: argparse.Namespace,
    *,
    peer_status: str = "",
    peer_mode: PeerAttestMode = "discord",
    deploy_nonce: str = "",
) -> skill_gate_approval.SkillApprovalGate:
    """This run's deploy gate: a fresh nonce, plus one action hash over what ✅ authorizes."""
    spec = skill_gate_specs.DeploySpec(
        skill=args.skill,
        digest=args.hash,
        deploy_nonce=deploy_nonce or str(getattr(args, "deploy_nonce", "")) or secrets.token_hex(16),
        review_status=review_status_line(GATE_DIR / "review-verdicts.jsonl", args.skill, args.hash),
        provenance=skill_gate_specs.provenance_of(str(getattr(args, "provenance_file", ""))),
        binding=_REQUEST_BINDING,
        peer_attest_mode=peer_mode,
        peer_status=peer_status,
    )
    surface = skill_gate_approval.GateSurface(
        _api, GATE_DIR, _owner_id, lambda: _deploy_bindings(args.skill)
    )
    return skill_gate_approval.SkillApprovalGate(surface, spec)


def _stored_deploy_nonce(args: argparse.Namespace) -> str:
    path = GATE_DIR / "pending" / f"{args.skill}.json"
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return ""
    if not isinstance(decoded, dict):
        return ""
    nonce = decoded.get("deploy_nonce")
    return nonce if isinstance(nonce, str) and re.fullmatch(r"[0-9a-f]{32}", nonce) else ""


def _approved_resume_waiting(gate: skill_gate_approval.SkillApprovalGate) -> bool:
    try:
        outstanding = gate.outstanding(gate.spec.key())
        return len(outstanding) == 1 and gate.probe(outstanding[0]) is Probe.APPROVED
    except (ApprovalRecordsError, LifecycleSurfaceError):
        return False


def _approval_execution(
    gate: skill_gate_approval.SkillApprovalGate, args: argparse.Namespace
) -> skill_gate_approval.ApprovalExecution:
    request = ApprovalRequest(
        key=gate.spec.key(),
        action_hash=gate.spec.action_hash(),
        message_id=args.message_id,
        channel_id=gate.channel_id(),
        created_at="",
    )
    return skill_gate_approval.ApprovalExecution(
        request=request,
        nonce=args.deploy_nonce,
        action="skill.deploy",
        destination=f"skill:{args.skill}",
    )


def _auto_proposals_exhausted(proposals: Path, week: str) -> bool:
    if not proposals.exists():
        return False
    rows = [json.loads(line) for line in proposals.read_text(encoding="utf-8").splitlines() if line]
    used = sum(1 for row in rows if row.get("week") == week and row.get("source") == "auto")
    if used < WEEKLY_AUTO_LIMIT:
        return False
    print(f"RATE-LIMIT: {used} auto proposals already this week (max {WEEKLY_AUTO_LIMIT})", file=sys.stderr)
    return True


def cmd_request(args: argparse.Namespace) -> int:
    """One live request per skill: reuse it, supersede it with --fresh, or refuse."""
    json_output = bool(getattr(args, "json", False))
    mode = _peer_attest_mode(args) or "discord"
    supplied_nonce = str(getattr(args, "deploy_nonce", ""))
    checks_resume = hasattr(args, "peer_attest_mode") and not supplied_nonce
    stored_nonce = _stored_deploy_nonce(args) if checks_resume else ""
    if stored_nonce and _approved_resume_waiting(
        _deploy_gate(
            args,
            peer_status=SIGNED_PEER_PENDING if mode == "signed" else "",
            peer_mode=mode,
            deploy_nonce=stored_nonce,
        )
    ):
        refused = skill_gate_request.Requested(
            None,
            skill_gate_request.LIFECYCLE_REFUSAL_EXIT,
            "REFUSED: approved request is awaiting the resume watcher reason=approved-awaiting-resume",
        )
        return skill_gate_request.emit(refused, json_output=json_output)
    gate = _deploy_gate(
        args,
        peer_status=SIGNED_PEER_PENDING if mode == "signed" else "",
        peer_mode=mode,
    )
    reused = skill_gate_request.reuse(gate)
    if reused is not None and not args.fresh:
        return skill_gate_request.emit(reused, json_output=json_output)
    source = os.environ.get("SKILL_PROPOSAL_SOURCE", "manual")
    proposals = GATE_DIR / "proposals.jsonl"
    week = datetime.now(timezone.utc).strftime("%G-W%V")
    if source == "auto" and _auto_proposals_exhausted(proposals, week):
        return 3
    requested = skill_gate_request.post_request(gate, fresh=bool(args.fresh))
    record = requested.record
    if requested.posted and record is not None:
        _append_jsonl(proposals, {"hash": args.hash, "message_id": record["message_id"],
                                  "skill": args.skill, "source": source,
                                  "timestamp": _utc_now(), "week": week})
    return skill_gate_request.emit(requested, json_output=json_output)


@dataclass(frozen=True, slots=True)
class PeerAttestationEvidence:
    request_content: str
    key_fingerprint: str = ""


class _PeerTrustRootUnavailable(Exception):
    pass


def _peer_attest_mode(args: argparse.Namespace) -> PeerAttestMode | None:
    match getattr(args, "peer_attest_mode", ""):
        case "discord":
            return "discord"
        case "signed":
            return "signed"
        case _:
            return None


def _signed_blob(args: argparse.Namespace) -> str:
    direct = getattr(args, "peer_attestation_blob", None)
    if isinstance(direct, str):
        return direct
    if bool(getattr(args, "peer_attestation_stdin", False)):
        return sys.stdin.read()
    return ""


def _signed_verifier(args: argparse.Namespace) -> SshSignedAttestationVerifier | None:
    value = getattr(args, "peer_attest_public_key", "")
    if not isinstance(value, str) or not value:
        return None
    return SshSignedAttestationVerifier(Path(value))


def _signed_peer_status(args: argparse.Namespace) -> str | None:
    verifier = _signed_verifier(args)
    if verifier is None:
        return None
    fingerprint = verifier.fingerprint()
    if fingerprint is None:
        return None
    return f"- peer verdict: PASS (key fp {fingerprint})"


def _peer_attestation_evidence(
    args: argparse.Namespace,
    channel_id: str,
    mode: PeerAttestMode,
) -> PeerAttestationEvidence | None:
    request = _api("GET", f"/channels/{channel_id}/messages/{args.message_id}")
    author = request.get("author") if isinstance(request, dict) else None
    content = request.get("content") if isinstance(request, dict) else None
    timestamp = request.get("timestamp") if isinstance(request, dict) else None
    matched = _REQUEST_BINDING.match(content) if isinstance(content, str) else None
    requested_at = _parse_timestamp(timestamp) if isinstance(timestamp, str) else None
    if not isinstance(author, dict) or matched is None or requested_at is None or not isinstance(content, str):
        return None
    if matched.group("skill") != args.skill or matched.group("digest") != args.hash or matched.group("nonce") != args.deploy_nonce:
        return None
    expectation = AttestationExpectation(channel_id, args.message_id, args.deploy_nonce, args.skill, args.hash, requested_at)
    match mode:
        case "discord":
            bot_ids = _load_ops_bot_ids()
            if bot_ids is None:
                raise _PeerTrustRootUnavailable
            if author.get("id") != bot_ids.agent_bot_id or author.get("bot") is not True:
                return None
            messages = _api("GET", f"/channels/{channel_id}/messages?after={args.message_id}&limit=100")
            if not isinstance(messages, list):
                return None
            if not valid_peer_attestation(messages, expectation, bot_ids, _now()):
                return None
            return PeerAttestationEvidence(content)
        case "signed":
            identity = _api("GET", "/users/@me")
            if (
                not isinstance(identity, dict)
                or identity.get("id") != author.get("id")
                or identity.get("bot") is not True
                or author.get("bot") is not True
            ):
                return None
            verifier = _signed_verifier(args)
            if verifier is None or not valid_signed_attestation(
                _signed_blob(args), expectation, verifier, _now()
            ):
                return None
            fingerprint = verifier.fingerprint()
            if fingerprint is None:
                return None
            return PeerAttestationEvidence(content, fingerprint)
        case unreachable:
            assert_never(unreachable)


def _peer_attestation_present(args: argparse.Namespace, channel_id: str) -> bool:
    mode = _peer_attest_mode(args)
    return mode is not None and _peer_attestation_evidence(args, channel_id, mode) is not None


class _ApprovalMessageGone(Exception):
    """The message this request is bound to no longer exists on the surface."""


def _owner_reacted(
    args: argparse.Namespace, owner_id: str, channel_id: str, emoji: str
) -> bool:
    """Did the OWNER (not a bot, not anyone else) put ``emoji`` on the request?"""
    try:
        users = _api(
            "GET",
            f"/channels/{channel_id}/messages/{args.message_id}/reactions/"
            f"{quote(emoji)}?limit=100",
        )
    except HTTPError as error:
        # 404 is `10008 Unknown Message` — NOT "nobody used that emoji". Discord answers
        # 200 [] for that (2026-08-03 실측). This comment used to claim the opposite, and
        # the claim cost two requests: their messages had been deleted, every query 404'd,
        # and the watcher reported them as "not answered yet" forever while the owner had
        # nothing to press. Worse, a 404 on the ⛔ query alone could still read ✅ and
        # return approved — fail-open on the highest-privilege path in the system.
        # "could not see" is never "was permitted", and it is never "was not answered".
        if error.code != 404:
            raise
        raise _ApprovalMessageGone(str(args.message_id)) from error
    if not isinstance(users, list):
        users = []
    for user in users:
        if not isinstance(user, dict):
            continue
        user_id, is_bot = str(user.get("id", "")), bool(user.get("bot", False))
        if user_id == owner_id and not is_bot:
            return True
        print(
            f"IGNORED non-owner reaction: user={_mask(user_id)} bot={is_bot}",
            file=sys.stderr,
        )
    return False


def _owner_decision(args: argparse.Namespace, owner_id: str, channel_id: str) -> str:
    """``approved`` | ``denied`` | ``missing`` | ``absent`` — ⛔ takes precedence over ✅.

    ``missing`` is kept apart from ``absent`` for the same reason ``denied`` is: the two
    need different handling. An unanswered request is waiting for the owner. A request
    whose message is gone is waiting for someone to POST a new one — nobody can react to
    a message that does not exist, so it never moves on its own. Collapsing them makes a
    stuck request report as healthy forever (2026-08-03 실측: 두 건이 그 상태였다).

    AGENTS.md makes ⛔ precedence a repo-wide invariant for every owner-confirm flow
    ("✅와 ⛔가 함께 있으면 취소로 처리한다 — 외부효과 fail-safe"), and every other
    surface honours it. This gate did not: it queried the ✅ endpoint and nothing
    else, so an owner who approved and then changed their mind was overruled by their
    own earlier ✅ — on the highest-privilege path in the system.

    ⛔ is therefore asked FIRST. Denial short-circuits, so a refusal cannot be lost
    to a transport failure on the approval query that follows it.

    ``denied`` and ``absent`` are kept apart because a reaction watcher (FA-3) must
    retain a request nobody has answered and retire one that was refused; collapsing
    both into False would make it retry a decision the owner already made.
    """
    try:
        return skill_gate_approval.owner_decision(
            lambda emoji: _owner_reacted(args, owner_id, channel_id, emoji)
        )
    except _ApprovalMessageGone:
        return "missing"


#: The owner refused. Distinct from 1 ("nobody has answered yet") because
#: deploy-skill.sh reads 1 as "retry with a peer-attestation refresh" — right for an
#: unanswered request, wrong for a cancelled one, which would spend a Discord
#: round-trip re-attesting a deployment that is not happening and then report it as
#: "ABSENT or INVALID". 7 is already taken (peer attestation expired).
DENIED_EXIT = 8


def decision_exit_code(decision: str) -> int:
    """Map an owner decision to the exit code deploy-skill.sh acts on.

    FA-2 needs the split as much as FA-1 did: a resume path must retain the reviewed
    artifact for a request still awaiting an answer and discard it for a refused one,
    and one exit code cannot drive both.

    ``missing`` deliberately shares exit 1 with ``absent``: both keep the reviewed
    artifact, and for a gone message the "retry" that exit 1 triggers is exactly the
    recovery — the re-run posts a fresh request. Only a refusal discards.
    """
    if decision == "approved":
        return 0
    return DENIED_EXIT if decision == "denied" else 1


def _report_rejection(decision: str, args: argparse.Namespace) -> None:
    if decision == "denied":
        print(
            f"REJECTED: owner {CANCEL_EMOJI} on message {_mask(args.message_id)}"
            " — deployment cancelled",
            file=sys.stderr,
        )
        return
    if decision == "missing":
        print(
            f"REJECTED: approval message {_mask(args.message_id)} no longer exists"
            " — nobody can react to it; re-run to post a new request",
            file=sys.stderr,
        )
        return
    print(
        f"REJECTED: no owner {APPROVE_EMOJI} reaction on message {_mask(args.message_id)}",
        file=sys.stderr,
    )


def _owner_approval_present(args: argparse.Namespace, owner_id: str, channel_id: str) -> bool:
    decision = _owner_decision(args, owner_id, channel_id)
    if decision == "approved":
        return True
    _report_rejection(decision, args)
    return False


def cmd_check(args: argparse.Namespace) -> int:
    mode = _peer_attest_mode(args)
    if mode is None:
        print("FATAL: peer_attest_mode must be explicitly set to discord or signed", file=sys.stderr)
        return 2
    owner_id = _owner_id()
    gate = _deploy_gate(
        args,
        peer_status=SIGNED_PEER_PENDING if mode == "signed" else "",
        peer_mode=mode,
    )
    execution = _approval_execution(gate, args)
    channel_id = execution.request.channel_id
    try:
        evidence = _peer_attestation_evidence(args, channel_id, mode)
    except _PeerTrustRootUnavailable:
        print(f"FATAL: Discord peer trust root unavailable: {OPS_PEERS_CONFIG}", file=sys.stderr)
        return 2
    if evidence is None:
        print("REJECTED: valid peer attestation absent", file=sys.stderr)
        return 1
    if mode == "signed" and not {"render_version", "content_sha256"}.intersection(gate.stored() or {}):
        # 판본 없는 승격 카드만 재생한다. digest가 있는 fallback v1도 저장 본문으로 검증한다.
        peer_status = f"- peer verdict: PASS (key fp {evidence.key_fingerprint})"
        verified_gate = _deploy_gate(args, peer_status=peer_status, peer_mode=mode)
        if evidence.request_content == verified_gate.spec.render():
            gate = verified_gate
        elif evidence.request_content != gate.spec.render():
            print("REJECTED: signed peer verdict message binding invalid", file=sys.stderr)
            return 1
        execution = _approval_execution(gate, args)
    bindings = GateBindings(
        owner_id,
        channel_id,
        _approval_text,
        lambda _skill, _digest, _message_id, method: _log_approval(args, method, execution),
        _mask,
    )
    if args.injection_file:
        if os.environ.get("E2E_TEST_MODE") != "1":
            print("REJECTED: injected approval requires E2E_TEST_MODE=1", file=sys.stderr)
            return 1
        if not gate.valid_binding(execution, APPROVAL_LOG):
            print("REJECTED: approval execution binding invalid", file=sys.stderr)
            return 1
        return check_injected(args, bindings)
    if os.environ.get("E2E_TEST_MODE"):
        print("FATAL: E2E_TEST_MODE set but no --injection-file; refusing ambiguous mode", file=sys.stderr)
        return 2
    decision = _owner_decision(args, owner_id, channel_id)
    if decision != "approved":
        _report_rejection(decision, args)
        return decision_exit_code(decision)
    approval = gate.approval_outcome(execution, APPROVAL_LOG)
    if not approval.approved():
        _ = skill_gate_approval.preserve_rejected(gate, approval.cause)
        print("REJECTED: owner approval binding invalid", file=sys.stderr)
        return 1
    _log_approval(args, "manual_reaction", execution)
    print(f"APPROVED method=manual_reaction owner={_mask(owner_id)}")
    return 0


def cmd_sign(args: argparse.Namespace) -> int:
    bindings = GateBindings(
        _owner_id(),
        _deploy_gate(args).channel_id(),
        _approval_text,
        lambda _skill, _digest, _message_id, method: _log_approval(args, method),
        _mask,
    )
    return sign(args, bindings)


#: The live release approval does not cover this exact (skill, digest). Fail-closed and
#: distinct from 1/8: a skill changed after the release cut must not ride an adjacent ✅.
RELEASE_NOT_COVERED_EXIT = 4


def _load_ops_bot_ids() -> object:
    """The ONE configured attestation trust root — hygiene guard counts this call site."""
    return load_bot_ids(OPS_PEERS_CONFIG)


def _release_attestation_present(
    args: argparse.Namespace,
    channel_id: str,
    message_id: str,
    nonce: str,
    mode: PeerAttestMode,
) -> bool:
    """Peer attestation bound to the RELEASE approval message, not a per-skill request.

    The expectation carries this skill's own digest — the peer attests each skill
    beside the one release message — and the release nonce the owner's ✅ displayed.
    """
    message = _api("GET", f"/channels/{channel_id}/messages/{message_id}")
    timestamp = message.get("timestamp") if isinstance(message, dict) else None
    requested_at = _parse_timestamp(timestamp) if isinstance(timestamp, str) else None
    if requested_at is None:
        return False
    expectation = AttestationExpectation(
        channel_id, message_id, nonce, args.skill, args.hash, requested_at
    )
    match mode:
        case "discord":
            bot_ids = _load_ops_bot_ids()
            if bot_ids is None:
                raise _PeerTrustRootUnavailable
            messages = _api(
                "GET", f"/channels/{channel_id}/messages?after={message_id}&limit=100"
            )
            return isinstance(messages, list) and valid_peer_attestation(
                messages, expectation, bot_ids, _now()
            )
        case "signed":
            verifier = _signed_verifier(args)
            return verifier is not None and valid_signed_attestation(
                _signed_blob(args), expectation, verifier, _now()
            )
        case unreachable:
            assert_never(unreachable)


def cmd_release_authorize(args: argparse.Namespace) -> int:
    """VA-2: one owner ✅ on a release authorizes exactly the digests it displayed.

    Exit: 0 authorized (stdout ``<message_id>:<release_nonce>``) · 8 owner ⛔ ·
    1 pending/absent/unverifiable · 4 digest not covered · 2 no usable release record.
    Membership is checked BEFORE any Discord round-trip — a request that can never be
    authorized must not spend one.
    """
    from automation.release_spec import ReleaseSpecError, spec_from_record

    record_path = GATE_DIR / "pending" / "release.json"
    try:
        decoded = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print("RELEASE-AUTHORIZE: no live release approval record", file=sys.stderr)
        return 2
    if not isinstance(decoded, dict):
        print("RELEASE-AUTHORIZE: release record is not an object", file=sys.stderr)
        return 2
    record = {str(name): str(value) for name, value in decoded.items()}
    try:
        spec = spec_from_record(record)
    except (ReleaseSpecError, ValueError):
        print("RELEASE-AUTHORIZE: release record is malformed", file=sys.stderr)
        return 2
    surface_name = getattr(args, "surface", None) or f"skill:{args.skill}"
    head = getattr(args, "head", None) or ""
    if head and spec.head_sha != head:
        # 예전 릴리스의 ✅ 가 지금 배포된 릴리스의 표면을 인가하지 못하게 한다.
        print(
            f"RELEASE-AUTHORIZE-BLOCK: release record is for {spec.head_sha[:12]},"
            f" not {head[:12]}",
            file=sys.stderr,
        )
        return RELEASE_NOT_COVERED_EXIT
    if (surface_name, args.hash) not in spec.surface_digests:
        print(
            f"RELEASE-AUTHORIZE-BLOCK: release {spec.version} does not cover"
            f" {surface_name}@{args.hash[:12]} — 릴리스 이후 바뀐 표면은 옆 승인으로 못 탄다",
            file=sys.stderr,
        )
        return RELEASE_NOT_COVERED_EXIT
    surface = skill_gate_approval.GateSurface(
        _api,
        GATE_DIR,
        _owner_id,
        lambda: skill_gate_surface.surface_for(ApprovalKind.RELEASE, _identity()),
    )
    gate = skill_gate_approval.SkillApprovalGate(surface, spec)
    try:
        outstanding = gate.outstanding("release")
        decision = gate.probe(outstanding[0]) if outstanding else None
    except (ApprovalRecordsError, LifecycleSurfaceError):
        decision = None
    if decision is Probe.APPROVED:
        mode = _peer_attest_mode(args)
        if mode is not None:
            try:
                attested = _release_attestation_present(
                    args,
                    gate.channel_id(),
                    record.get("message_id", ""),
                    record.get("release_nonce", ""),
                    mode,
                )
            except _PeerTrustRootUnavailable:
                print(
                    f"FATAL: Discord peer trust root unavailable: {OPS_PEERS_CONFIG}",
                    file=sys.stderr,
                )
                return 2
            if not attested:
                print(
                    "RELEASE-AUTHORIZE: peer attestation absent or expired — refresh it",
                    file=sys.stderr,
                )
                return 7
        print(f"RELEASE-AUTHORIZED: {spec.version} covers {surface_name}", file=sys.stderr)
        print(f"{record.get('message_id', '')}:{record.get('release_nonce', '')}")
        return 0
    if decision is Probe.CANCELLED:
        print(
            f"RELEASE-AUTHORIZE-BLOCK: owner {CANCEL_EMOJI} on release {spec.version}",
            file=sys.stderr,
        )
        return DENIED_EXIT
    print(
        "RELEASE-AUTHORIZE: release approval is pending or unverifiable — NOT authorized",
        file=sys.stderr,
    )
    return 1


def cmd_consume(args: argparse.Namespace) -> int:
    """Retire the decision this deploy's MOUNT consumed — CAS on (skill, hash, message id)."""
    return skill_gate_retire.emit(skill_gate_retire.consume(_deploy_gate(args), args.message_id))


def cmd_abandon(args: argparse.Namespace) -> int:
    """Operator override: audited retirement of a decision whose effect can never run."""
    order = skill_gate_retire.AbandonOrder(
        args.message_id,
        args.reason,
        skill_gate_retire.actor(),
        bool(getattr(args, "legacy_only", False)),
    )
    audit = skill_gate_retire.abandon_log(APPROVAL_LOG)
    return skill_gate_retire.emit(skill_gate_retire.abandon(_deploy_gate(args), order, audit))


def main() -> int:
    where = skill_gate_surface.where_to_look(ApprovalKind.SKILL_DEPLOY)
    parser = argparse.ArgumentParser(description=f"{__doc__}\n{where}")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func in (("request", cmd_request), ("check", cmd_check), ("sign", cmd_sign),
                       ("consume", cmd_consume), ("abandon", cmd_abandon)):
        cmd = sub.add_parser(name)
        cmd.add_argument("--skill", required=True)
        cmd.add_argument("--hash", required=True)
        cmd.set_defaults(func=func)
        if name == "request":
            cmd.add_argument("--fresh", action="store_true")
            cmd.add_argument("--json", action="store_true")
            cmd.add_argument("--provenance-file", default="")
            cmd.add_argument("--peer-attest-mode", choices=("discord", "signed"), default="discord")
        else:
            cmd.add_argument("--message-id", required=True)
        if name == "check":
            cmd.add_argument("--injection-file", default="")
            cmd.add_argument("--deploy-nonce", required=True)
            cmd.add_argument("--provenance-file", default="")
            cmd.add_argument("--peer-attest-mode", choices=("discord", "signed"), default="")
            cmd.add_argument("--peer-attest-public-key", default="")
            cmd.add_argument("--peer-attestation-stdin", action="store_true")
        if name == "abandon":
            cmd.add_argument("--reason", required=True)
            cmd.add_argument("--legacy-only", action="store_true")
        if name == "sign":
            cmd.add_argument("--out", required=True)
            cmd.add_argument("--user-id", default="")
            cmd.add_argument("--forge-signature", action="store_true")
    release_cmd = sub.add_parser("release-authorize")
    release_target = release_cmd.add_mutually_exclusive_group(required=True)
    release_target.add_argument("--skill")
    release_target.add_argument("--surface", help="스킬 밖 표면(예: healthcheck-wrapper:<node>)")
    release_cmd.add_argument("--hash", required=True)
    release_cmd.add_argument("--head", default="", help="이 릴리스 HEAD 의 레코드만 인가한다")
    release_cmd.add_argument("--peer-attest-mode", choices=("discord", "signed"), default="")
    release_cmd.add_argument("--peer-attest-public-key", default="")
    release_cmd.add_argument("--peer-attestation-stdin", action="store_true")
    release_cmd.set_defaults(func=cmd_release_authorize)
    try:  # publish subcommands only resolve on the workstation (full repo); the staged agent gate omits skill_gate_publish
        publish_module = importlib.import_module("automation.skill_gate_publish")
    except ModuleNotFoundError:
        pass
    else:
        cmd_publish_request = publish_module.cmd_publish_request
        cmd_publish_check = publish_module.cmd_publish_check
        for name, func in (("publish-request", cmd_publish_request), ("publish-check", cmd_publish_check)):
            cmd = sub.add_parser(name)
            cmd.add_argument("--skill", required=True)
            cmd.add_argument("--hash", required=True)
            cmd.add_argument("--manifest-hash", required=True)
            cmd.add_argument("--tag", required=True)
            cmd.set_defaults(func=func)
            if name == "publish-request":
                cmd.add_argument("--json", action="store_true")
            else:
                cmd.add_argument("--message-id", required=True)
                cmd.add_argument("--publish-nonce", required=True)
                cmd.add_argument("--injection-file", default="")
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except ApprovalSurfaceError as error:
        print(f"FATAL: approval surface unresolved ({error}); pin deploy_approvals_channel_id in interop config", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
