from __future__ import annotations

import hashlib
import stat
from dataclasses import replace
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from urllib.error import HTTPError

import pytest

from automation.credential_rotation import effects
from automation.credential_rotation.effects import RotationSeams
from automation.credential_rotation.files import atomic_write
from automation.credential_rotation.registry import (
    HttpProbe, ProviderContext, ROTATION_TARGETS, RotationTarget,
    hash_with_dashboard_provider, make_password_login_probe,
)
from automation.credential_rotation.rotate import main

NEW_PASSWORD = "NewFixturePasswordValue2"
OLD_PASSWORD = "OldFixturePasswordValue2"
NEW_SESSION_SECRET = "c" * 64
FIXED_NOW = datetime(2026, 7, 27, 4, 5, tzinfo=UTC)


def _target(tmp_path: Path, target_name: str) -> RotationTarget:
    # Kanban is no longer shipped as a rotation target. Keep testing the generic
    # provider/session-secret behavior with an explicit synthetic target.
    base = (RotationTarget(
        account="agent", verifier_path=tmp_path / "unused.env", note_path=tmp_path / "unused.txt",
        username_key="FIXTURE_USERNAME", password_hash_key="FIXTURE_PASSWORD_HASH",
        session_secret_key="FIXTURE_SESSION_SECRET", password_hasher=hash_with_dashboard_provider,
        provider=ProviderContext(tmp_path / "python", tmp_path), unit_name="fixture.service",
        probe_url="http://fixture.invalid/auth/password-login", probe_builder=make_password_login_probe,
        probe_success_status=200,
    ) if target_name == "kanban" else ROTATION_TARGETS[target_name])
    target = replace(
        base,
        verifier_path=tmp_path / f"{target_name}.env",
        note_path=tmp_path / f"{target_name}.txt",
    )
    session_line = (
        f"{target.session_secret_key}={'a' * 64}\n" if target.session_secret_key is not None else ""
    )
    target.verifier_path.write_text(
        f"{target.username_key}=fixture-user\n"
        f"{target.password_hash_key}=old-verifier\n"
        f"{session_line}UNCHANGED_ENV=preserve-me\n",
        encoding="utf-8",
    )
    target.note_path.write_text(
        "Fixture dashboard login\n"
        "URL: http://fixture.invalid/\n"
        f"{_identity_label(target_name)}: fixture-user\n"
        f"password: {OLD_PASSWORD}\n"
        "rotated: 2026-01-01T00:00Z\n",
        encoding="utf-8",
    )
    return target


def _identity_label(target_name: str) -> str:
    return "username" if target_name == "kanban" else "user"


def _seams(target: RotationTarget, statuses: tuple[int, ...] = (200,)) -> tuple[RotationSeams, list[str]]:
    pending_statuses = iter(statuses)
    restarts: list[str] = []

    def restart(received: RotationTarget) -> None:
        restarts.append(received.unit_name)

    def send_probe(_probe: HttpProbe) -> int:
        return next(pending_statuses)

    return (
        RotationSeams(
            current_user=lambda: target.account,
            generate_password=lambda: NEW_PASSWORD,
            generate_session_secret=lambda: NEW_SESSION_SECRET,
            provider_hash=lambda _provider, _password: "fixture-provider-hash",
            restart_unit=restart,
            send_probe=send_probe,
            atomic_write=atomic_write,
            secure_delete=lambda path: path.unlink(),
            now=lambda: FIXED_NOW,
        ),
        restarts,
    )


def _run(target: RotationTarget, seams: RotationSeams) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    status = main(("fixture",), seams, {"fixture": target}, stdout, stderr)
    return status, stdout.getvalue(), stderr.getvalue()


def _non_rotated_non_password_lines(content: str) -> list[str]:
    return [
        line
        for line in content.splitlines(keepends=True)
        if not line.startswith("password:") and not line.startswith("rotated:")
    ]


def test_rotation_when_verifier_key_missing_then_both_files_remain_byte_identical(tmp_path: Path) -> None:
    # Given
    target = _target(tmp_path, "kanban")
    target.verifier_path.write_text(
        f"{target.username_key}=fixture-user\n{target.session_secret_key}={'a' * 64}\n",
        encoding="utf-8",
    )
    original_verifier = target.verifier_path.read_bytes()
    original_note = target.note_path.read_bytes()
    seams, restarts = _seams(target)

    # When
    status, _stdout, _stderr = _run(target, seams)

    # Then
    assert status == 1
    assert target.verifier_path.read_bytes() == original_verifier
    assert target.note_path.read_bytes() == original_note
    assert restarts == []


def test_rotation_when_note_lacks_password_line_then_both_files_remain_untouched(tmp_path: Path) -> None:
    # Given
    target = _target(tmp_path, "report-hub")
    target.note_path.write_text("Fixture dashboard\nuser: fixture-user\n", encoding="utf-8")
    original_verifier = target.verifier_path.read_bytes()
    original_note = target.note_path.read_bytes()
    seams, restarts = _seams(target)

    # When
    status, _stdout, _stderr = _run(target, seams)

    # Then
    assert status == 1
    assert target.verifier_path.read_bytes() == original_verifier
    assert target.note_path.read_bytes() == original_note
    assert restarts == []


@pytest.mark.parametrize("target_name", ("kanban", "report-hub"))
def test_rotation_when_probe_succeeds_then_preserves_note_and_writes_mode_600(
    tmp_path: Path, target_name: str
) -> None:
    # Given
    target = _target(tmp_path, target_name)
    original_note = target.note_path.read_text(encoding="utf-8")
    seams, restarts = _seams(target)

    # When
    status, stdout, stderr = _run(target, seams)

    # Then
    verifier = target.verifier_path.read_text(encoding="utf-8")
    note = target.note_path.read_text(encoding="utf-8")
    expected_hash = (
        "fixture-provider-hash"
        if target_name == "kanban"
        else hashlib.sha256(NEW_PASSWORD.encode("utf-8")).hexdigest()
    )
    assert status == 0
    assert f"{target.username_key}=fixture-user" in verifier
    assert f"{target.password_hash_key}={expected_hash}" in verifier
    if target.session_secret_key is not None:
        assert f"{target.session_secret_key}={NEW_SESSION_SECRET}" in verifier
    assert f"{_identity_label(target_name)}: fixture-user\n" in note
    assert f"password: {NEW_PASSWORD}\n" in note
    assert _non_rotated_non_password_lines(note) == _non_rotated_non_password_lines(original_note)
    assert note.count("rotated:") == 1
    assert note.endswith("rotated: 2026-07-27T04:05Z\n")
    assert stat.S_IMODE(target.verifier_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.note_path.stat().st_mode) == 0o600
    assert NEW_PASSWORD not in stdout
    assert NEW_PASSWORD not in stderr
    assert restarts == [target.unit_name]


def test_rotation_when_probe_fails_then_restores_both_files_and_exits_nonzero(tmp_path: Path) -> None:
    # Given
    target = _target(tmp_path, "kanban")
    original_verifier = target.verifier_path.read_bytes()
    original_note = target.note_path.read_bytes()
    seams, restarts = _seams(target, statuses=(401, 200))

    # When
    status, stdout, stderr = _run(target, seams)

    # Then
    assert status == 1
    assert target.verifier_path.read_bytes() == original_verifier
    assert target.note_path.read_bytes() == original_note
    assert restarts == [target.unit_name, target.unit_name]
    assert NEW_PASSWORD not in stdout
    assert NEW_PASSWORD not in stderr


def test_rotation_when_run_as_another_account_then_refuses_before_writing(tmp_path: Path) -> None:
    # Given
    target = _target(tmp_path, "report-hub")
    original_verifier = target.verifier_path.read_bytes()
    seams, _restarts = _seams(target)
    blocked = replace(seams, current_user=lambda: "wrong-account")

    # When
    status, _stdout, _stderr = _run(target, blocked)

    # Then
    assert status == 1
    assert target.verifier_path.read_bytes() == original_verifier


def _probe() -> HttpProbe:
    return HttpProbe(url="http://127.0.0.1:1/", body=b"{}", headers={}, method="POST")


def test_send_probe_when_unit_is_still_binding_then_retries_until_it_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a unit that refuses connections until it finishes binding
    attempts = 0

    def urlopen(_request: object, timeout: float) -> object:  # noqa: ARG001
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ConnectionRefusedError("not bound yet")
        raise HTTPError("http://127.0.0.1:1/", 200, "OK", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(effects, "urlopen", urlopen)
    monkeypatch.setattr(effects.time, "sleep", lambda _seconds: None)

    # When
    status = effects.send_probe(_probe())

    # Then the restart window is waited out instead of being read as a bad password
    assert status == 200
    assert attempts == 3


def test_send_probe_when_the_password_is_rejected_then_answers_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a running unit that rejects the credential
    attempts = 0

    def urlopen(_request: object, timeout: float) -> object:  # noqa: ARG001
        nonlocal attempts
        attempts += 1
        raise HTTPError("http://127.0.0.1:1/", 401, "Unauthorized", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(effects, "urlopen", urlopen)
    monkeypatch.setattr(effects.time, "sleep", lambda _seconds: pytest.fail("must not retry a real answer"))

    # When
    status = effects.send_probe(_probe())

    # Then a 401 is an answer, so it is never retried into the rate limiter
    assert status == 401
    assert attempts == 1


def test_send_probe_when_the_unit_never_comes_back_then_gives_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a unit that never binds
    clock = iter([0.0, 0.0, 99.0])

    def urlopen(_request: object, timeout: float) -> object:  # noqa: ARG001
        raise ConnectionRefusedError("never bound")

    monkeypatch.setattr(effects, "urlopen", urlopen)
    monkeypatch.setattr(effects.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(effects.time, "sleep", lambda _seconds: None)

    # When
    status = effects.send_probe(_probe())

    # Then it reports a transport failure rather than hanging forever
    assert status == 0
