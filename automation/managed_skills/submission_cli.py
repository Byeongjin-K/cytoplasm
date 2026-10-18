"""Member CLI for packaging and submitting a personal skill for group review."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import assert_never

from automation import skill_gate
from automation.group_roster import (
    MemberStatus,
    Roster,
    RosterError,
    load_roster,
    roster_path,
)
from automation.interop.approval_lifecycle import Outcome, Verdict
from automation.interop.approval_surface import ApprovalKind
from automation.managed_skills import submission_approval
from automation.managed_skills.principal import PUBLISHER_PRINCIPAL
from automation.managed_skills.release_metadata import (
    ReleaseMetadataError,
    load_release_metadata,
)
from automation.managed_skills.submission_artifact import (
    SubmissionPackageConfig,
    package_personal_skill,
)
from automation.managed_skills.submission_errors import SubmissionArtifactError
from automation.managed_skills.submission_transport import (
    DiscordSubmissionTransport,
    discord_api,
)
from automation.skill_gate_surface import GateIdentity, owner_id, surface_for


@dataclass(frozen=True, slots=True)
class SubmissionCommandConfig:
    personal_repo: Path
    managed_skill: str
    release_metadata: Path
    release_sequence: int
    previous_sha256: str | None
    roster_path: Path
    output_dir: Path
    state_root: Path
    discord_token_file: Path | None = None


class _Arguments(argparse.Namespace):
    personal: str
    skill: str
    release_metadata: Path
    release_sequence: int
    previous_sha256: str | None
    personal_repo: Path | None
    roster: Path | None
    output_dir: Path | None
    state_root: Path | None
    discord_token_file: Path | None

    def __init__(self) -> None:
        super().__init__()
        self.personal = ""
        self.skill = ""
        self.release_metadata = Path()
        self.release_sequence = 1
        self.previous_sha256 = None
        self.personal_repo = None
        self.roster = None
        self.output_dir = None
        self.state_root = None
        self.discord_token_file = None


def _publisher_name(roster: Roster) -> str:
    principal = roster.admin.publisher_principal
    if PUBLISHER_PRINCIPAL.fullmatch(principal) is None:
        raise SubmissionArtifactError("roster admin publisher principal is invalid")
    return principal.removeprefix("publisher-").removesuffix("@autophagy")


def _submitter(roster: Roster, local_owner_id: str) -> str:
    member = next(
        (
            candidate
            for candidate in roster.members
            if candidate.discord_user_id == local_owner_id
            and candidate.status is MemberStatus.ACTIVE
        ),
        None,
    )
    if member is None:
        raise SubmissionArtifactError("local owner is not an active group member")
    return member.node_label


def _identity(token_file: Path | None) -> GateIdentity:
    from automation.notice_bot import resolve_token

    if token_file is None:
        token = resolve_token().strip()
    else:
        try:
            token = token_file.read_text(encoding="utf-8").strip()
        except OSError as error:
            raise SubmissionArtifactError(
                f"discord token file cannot be read: {token_file}"
            ) from error
    if not token:
        raise SubmissionArtifactError("discord token file is empty")
    api = discord_api(token)
    return GateIdentity(token, api, skill_gate.GATE_DIR, skill_gate.INTEROP_CONFIG)


def submit(config: SubmissionCommandConfig) -> Verdict:
    """Package a member commit and request review without importing or publishing it."""
    roster = load_roster(config.roster_path)
    artifact = package_personal_skill(
        SubmissionPackageConfig(
            personal_repo=config.personal_repo,
            managed_skill=config.managed_skill,
            publisher=_publisher_name(roster),
            release_sequence=config.release_sequence,
            previous_sha256=config.previous_sha256,
            metadata=load_release_metadata(config.release_metadata),
            output_dir=config.output_dir,
        )
    )
    identity = _identity(config.discord_token_file)
    transport = DiscordSubmissionTransport(identity.token, identity.api)
    return submission_approval.request_submission_approval(
        submission_approval.SubmissionApprovalConfig(
            artifact=artifact,
            group_id=roster.group_id,
            submitter=_submitter(roster, owner_id(identity.interop_config)),
            reviewer_id=roster.admin.discord_user_id,
            surface=surface_for(ApprovalKind.SKILL_SUBMIT, identity),
            transport=transport,
            state_root=config.state_root,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--personal", required=True)
    _ = parser.add_argument("--skill", required=True)
    _ = parser.add_argument("--release-metadata", type=Path, required=True)
    _ = parser.add_argument("--release-sequence", type=int, default=1)
    _ = parser.add_argument("--previous-sha256")
    _ = parser.add_argument("--personal-repo", type=Path)
    _ = parser.add_argument("--roster", type=Path)
    _ = parser.add_argument("--output-dir", type=Path)
    _ = parser.add_argument("--state-root", type=Path)
    _ = parser.add_argument("--discord-token-file", type=Path)
    args = _Arguments()
    _ = parser.parse_args(namespace=args)
    personal_repo = (
        args.personal_repo
        or Path("~/.hermes/personal-skills").expanduser() / args.personal
    )
    output_dir = (
        args.output_dir
        or Path("~/.hermes/personal-submissions").expanduser() / args.skill
    )
    state_root = args.state_root or Path("~/.hermes/managed-skills/submissions").expanduser()
    try:
        verdict = submit(
            SubmissionCommandConfig(
                personal_repo=personal_repo,
                managed_skill=args.skill,
                release_metadata=args.release_metadata,
                release_sequence=args.release_sequence,
                previous_sha256=args.previous_sha256,
                roster_path=args.roster or roster_path(),
                output_dir=output_dir,
                state_root=state_root,
                discord_token_file=args.discord_token_file,
            )
        )
    except (ReleaseMetadataError, RosterError, SubmissionArtifactError) as error:
        print(f"SUBMISSION-BLOCK: {error}", file=sys.stderr)
        return 1
    match verdict.outcome:
        case Outcome.POSTED | Outcome.PENDING:
            request = verdict.posted or verdict.live
            if request is None:
                print("SUBMISSION-BLOCK: lifecycle returned no request binding", file=sys.stderr)
                return 1
            print(f"SUBMISSION-STAGED message_id={request.message_id}")
            return 0
        case Outcome.DEFERRED | Outcome.REFUSED:
            reason = verdict.reason.value if verdict.reason is not None else "unverifiable"
            print(
                f"SUBMISSION-BLOCK: lifecycle outcome={verdict.outcome.value} reason={reason}",
                file=sys.stderr,
            )
            return 1
        case unreachable:
            assert_never(unreachable)


if __name__ == "__main__":
    raise SystemExit(main())
