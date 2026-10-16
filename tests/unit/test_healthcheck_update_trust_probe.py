from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HEALTHCHECK = ROOT / "automation" / "healthcheck.sh"
PROBE = ROOT / "automation" / "update_trust_probe.sh"


def test_healthcheck_runs_update_trust_before_release_drift_probes() -> None:
    # Given: the healthcheck's ordered probe inventory.
    body = HEALTHCHECK.read_text(encoding="utf-8")

    # When: the signed-update and release checks are located.
    trust_index = body.index("|update_trust|")
    mirror_index = body.index("|checkout_mirrors_origin|")
    release_index = body.index("|release_matches_origin|")

    # Then: trust owns an unsigned-head incident before generic drift grading runs.
    assert trust_index < mirror_index < release_index


def test_update_trust_probe_when_signature_is_missing_surfaces_specific_reason(
    tmp_path: Path,
) -> None:
    # Given: the verifier reports the precise unsigned-head block category.
    fake_python = tmp_path / "python3"
    script = "\n".join(
        (
            "#!/usr/bin/env bash",
            "printf 'UNSIGNED-HEAD: origin/main lacks a trusted signed release tag\\n' >&2",
            "exit 1",
            "",
        )
    )
    _ = fake_python.write_text(script, encoding="utf-8")
    fake_python.chmod(0o755)
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    command = (
        f'source "{PROBE}"; '
        f'UPDATE_TRUST_PYTHON="{fake_python}" '
        f'UPDATE_TRUST_SCRIPT="{tmp_path / "update_trust.py"}" '
        f'probe_update_trust ignored ignored "{mirror}"'
    )

    # When: healthcheck executes its local update-trust probe.
    completed = subprocess.run(
        ("bash", "-c", command),
        # The probe now carries the anti-rollback floor the converger anchors to, and
        # fails closed without one — so the stub needs a root to derive it from.
        env={**os.environ, "NODE_PRIVATE_ROOT": str(tmp_path / "private")},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    # Then: the existing healthcheck failure stream carries the verifier category.
    assert completed.returncode == 1
    assert "UPDATE-TRUST-BLOCK UNSIGNED-HEAD" in completed.stderr


def _record_probe_argv(tmp_path: Path, private_root: Path) -> list[str]:
    """Run the probe against a python stub that records the verifier's argv."""
    recorder = tmp_path / "argv.txt"
    fake_python = tmp_path / "python3"
    _ = fake_python.write_text(
        "\n".join(
            (
                "#!/usr/bin/env bash",
                f'printf "%s\\n" "$@" > "{recorder}"',
                "exit 0",
                "",
            )
        ),
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    mirror = tmp_path / "mirror"
    mirror.mkdir(exist_ok=True)
    command = (
        f'source "{PROBE}"; '
        f'UPDATE_TRUST_PYTHON="{fake_python}" '
        f'UPDATE_TRUST_SCRIPT="{tmp_path / "update_trust.py"}" '
        f'probe_update_trust ignored ignored "{mirror}"'
    )
    completed = subprocess.run(
        ("bash", "-c", command),
        env={
            **os.environ,
            "HEALTHCHECK_RELEASE_FLOOR": str(private_root / "release-floor.json"),
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return recorder.read_text(encoding="utf-8").split()


def test_update_trust_probe_asks_the_same_question_convergence_asks(tmp_path: Path) -> None:
    """A node that opts out of signed updates must not earn a PASS the converger refuses.

    Convergence became signature-only on 2026-08-21, but this probe still ran `resolve`,
    which honours `require_signed_updates`. On a node carrying that opt-out the probe
    would report PASS while every convergence tick was blocked — the same split-brain the
    reconciler had just been cured of, one layer up. So the probe asks the signature-only
    verb, and carries the anti-rollback floor the converger anchors to.
    """
    argv = _record_probe_argv(tmp_path, tmp_path / "private")

    assert "resolve-signed" in argv
    assert "--node-config" not in argv
    floor = argv[argv.index("--floor-path") + 1]
    assert floor == str(tmp_path / "private" / "release-floor.json")


def test_update_trust_probe_fails_closed_when_the_floor_cannot_be_resolved(
    tmp_path: Path,
) -> None:
    """No floor means no anti-rollback anchor, so the probe must not answer PASS."""
    fake_python = tmp_path / "python3"
    _ = fake_python.write_text(
        "#!/usr/bin/env bash\nprintf 'RELEASE-FLOOR: release floor is absent\\n' >&2\nexit 1\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    mirror = tmp_path / "mirror"
    mirror.mkdir(exist_ok=True)
    command = (
        f'source "{PROBE}"; '
        f'UPDATE_TRUST_PYTHON="{fake_python}" '
        f'UPDATE_TRUST_SCRIPT="{tmp_path / "update_trust.py"}" '
        f'probe_update_trust ignored ignored "{mirror}"'
    )
    environment = dict(os.environ)

    completed = subprocess.run(
        ("bash", "-c", command),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert completed.returncode == 1
    assert "UPDATE-TRUST-BLOCK" in completed.stderr


def test_update_trust_probe_when_a_newer_release_fails_the_trust_key_then_fails_with_the_reason(
    tmp_path: Path,
) -> None:
    # Given: the verifier resolves an older release (rc 0) but names a stale trust key.
    fake_python = tmp_path / "python3"
    script = "\n".join(
        (
            "#!/usr/bin/env bash",
            f"printf '{'a' * 40}\\n'",
            "printf 'UPDATE-TRUST-STALE-KEY 신뢰 키 갱신 필요: v1.40.0 SHA256:new\\n' >&2",
            "exit 0",
            "",
        )
    )
    _ = fake_python.write_text(script, encoding="utf-8")
    fake_python.chmod(0o755)
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    command = (
        f'source "{PROBE}"; '
        f'UPDATE_TRUST_PYTHON="{fake_python}" '
        f'UPDATE_TRUST_SCRIPT="{tmp_path / "update_trust.py"}" '
        f'probe_update_trust ignored ignored "{mirror}"'
    )

    # When: healthcheck executes its local update-trust probe.
    completed = subprocess.run(
        ("bash", "-c", command),
        env={**os.environ, "NODE_PRIVATE_ROOT": str(tmp_path / "private")},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    # Then: a frozen node is not reported healthy, and the operator sees why.
    assert completed.returncode == 1
    assert "[update-trust] UPDATE-TRUST-STALE-KEY 신뢰 키 갱신 필요" in completed.stderr
