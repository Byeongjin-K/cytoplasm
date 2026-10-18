from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC
from pathlib import Path
from typing import Mapping, TextIO

from .effects import DEFAULT_SEAMS, PASSWORD_ALPHABET, RotationSeams
from .files import (
    delete_backups,
    note_password,
    parse_env,
    replace_env_value,
    required_value,
    rewrite_note,
    take_backups,
)
from .registry import ROTATION_TARGETS, RotationError, RotationTarget


def rotate_target(
    target_name: str,
    seams: RotationSeams = DEFAULT_SEAMS,
    registry: Mapping[str, RotationTarget] = ROTATION_TARGETS,
) -> None:
    """Rotate one registered target, with no plaintext status output."""
    target = registry.get(target_name)
    if target is None:
        raise RotationError("unknown dashboard target")
    if target_name == "report-hub":
        from automation.legacy_services import released_off

        if released_off(Path.home(), "report-hub"):
            raise RotationError("report-hub is stopped by release; credential rotation is disabled")
    if seams.current_user() != target.account:
        raise RotationError(f"target {target_name} must run as {target.account}")

    verifier = parse_env(target.verifier_path.read_text(encoding="utf-8"))
    username = required_value(verifier, target.username_key, "verifier username")
    required_value(verifier, target.password_hash_key, "verifier password hash")
    if target.session_secret_key is not None:
        required_value(verifier, target.session_secret_key, "verifier session secret")
    note = target.note_path.read_text(encoding="utf-8")
    old_password = note_password(note)

    password = seams.generate_password()
    _validate_password(password)
    encoded = target.password_hasher(password, target.provider, seams.provider_hash)
    new_verifier = replace_env_value(verifier.content, target.password_hash_key, encoded)
    if target.session_secret_key is not None:
        # Password theft can leave an already-authenticated cookie valid; rotate its signing key too.
        new_verifier = replace_env_value(
            new_verifier,
            target.session_secret_key,
            _validated_session_secret(seams.generate_session_secret()),
        )
    timestamp = seams.now().astimezone(UTC).strftime("%Y-%m-%dT%H:%MZ")
    new_note = rewrite_note(note, password, timestamp)
    backups = take_backups((target.verifier_path, target.note_path), seams.atomic_write, seams.secure_delete)

    try:
        seams.atomic_write(target.verifier_path, new_verifier.encode("utf-8"))
        seams.atomic_write(target.note_path, new_note.encode("utf-8"))
        seams.restart_unit(target)
    except (OSError, subprocess.SubprocessError):
        _rollback(target, backups, username, old_password, seams)
        raise RotationError("rotation failed; original credentials were restored") from None

    status = seams.send_probe(target.probe_builder(target.probe_url, username, password))
    if status != target.probe_success_status:
        _rollback(target, backups, username, old_password, seams)
        raise RotationError("rotation probe failed; original credentials were restored")
    delete_backups(backups, seams.secure_delete)


def _validate_password(password: str) -> None:
    if len(password) != 24 or any(character not in PASSWORD_ALPHABET for character in password):
        raise RotationError("password generator violated the required 24-character alphanumeric contract")


def _validated_session_secret(secret: str) -> str:
    if len(secret) != 64 or any(character not in "0123456789abcdef" for character in secret):
        raise RotationError("session secret generator violated the required 64-hex contract")
    return secret


def _rollback(
    target: RotationTarget,
    backups: tuple[Path, Path],
    username: str,
    old_password: str,
    seams: RotationSeams,
) -> None:
    """Restore, restart, and re-probe before a rotation failure can be reported."""
    restored = True
    for destination, backup in zip((target.verifier_path, target.note_path), backups, strict=True):
        try:
            seams.atomic_write(destination, backup.read_bytes())
        except OSError:
            restored = False
    restarted = True
    try:
        seams.restart_unit(target)
    except subprocess.SubprocessError:
        restarted = False
    recovered = (
        seams.send_probe(target.probe_builder(target.probe_url, username, old_password))
        == target.probe_success_status
        if restored and restarted
        else False
    )
    delete_backups(backups, seams.secure_delete)
    if not recovered:
        raise RotationError("rotation failed and automatic rollback could not be verified")


def main(
    argv: Sequence[str] | None = None,
    seams: RotationSeams = DEFAULT_SEAMS,
    registry: Mapping[str, RotationTarget] = ROTATION_TARGETS,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        print("usage: rotate_cli.py <target>", file=stderr)
        return 2
    target_name = arguments[0]
    print(f"rotating {target_name} dashboard credentials", file=stdout)
    try:
        rotate_target(target_name, seams, registry)
    except (OSError, RotationError, subprocess.SubprocessError) as error:
        print(f"rotation failed: {error}", file=stderr)
        return 1
    print(f"rotation completed for {target_name}", file=stdout)
    return 0
