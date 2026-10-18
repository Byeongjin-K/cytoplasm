"""선언 밖 홈 파일을 숨기지 않되 자동 복구 가능 드리프트와 구분한다."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Final

import pytest

_REPO: Final = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from automation import deploy_all, deploy_all_probe  # noqa: E402


def _runtime(tmp_path: Path) -> tuple[Path, Path, str]:
    runtime = tmp_path / "runtime"
    source = runtime / "automation" / "pkg" / "declared.py"
    source.parent.mkdir(parents=True)
    source.write_text("print('declared')\n", encoding="utf-8")
    manifest = runtime / "configs" / "watcher-deploy-manifest.txt"
    manifest.parent.mkdir()
    manifest.write_text(
        "agent|automation/pkg/declared.py|.hermes/scripts/declared.py|required\n",
        encoding="utf-8",
    )
    live = tmp_path / "live"
    live.mkdir()
    return runtime, live, hashlib.sha256(source.read_bytes()).hexdigest()


def test_probe_reports_only_undeclared_matching_home_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, live, declared_sha = _runtime(tmp_path)
    monkeypatch.setattr(
        deploy_all_probe,
        "inspect_mounts",
        lambda _runtime, _live: SimpleNamespace(stale=(), unmounted=(), orphaned=()),
    )
    hashes = {
        ".hermes/scripts/declared.py": declared_sha,
        ".hermes/scripts/hand-copy.py": "b" * 64,
    }

    lines = deploy_all_probe.observations(
        runtime,
        live,
        lambda _account, destination: hashes[destination],
        lambda _account: tuple(hashes),
    )
    plan = deploy_all.parse_observations(lines)

    assert [(item.account, item.destination, item.sha256_prefix) for item in plan.undeclared] == [
        ("agent", ".hermes/scripts/hand-copy.py", "b" * 12)
    ]
    assert plan.clean
    assert "declared.py" not in "\n".join(
        line for line in lines if line.startswith("OBS|undeclared|")
    )


def test_unreadable_home_listing_is_an_observation_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime, live, declared_sha = _runtime(tmp_path)
    monkeypatch.setattr(
        deploy_all_probe,
        "inspect_mounts",
        lambda _runtime, _live: SimpleNamespace(stale=(), unmounted=(), orphaned=()),
    )

    with pytest.raises(deploy_all.ObservationError, match="listing"):
        deploy_all_probe.observations(
            runtime,
            live,
            lambda _account, _destination: declared_sha,
            lambda _account: "?",
        )

    monkeypatch.setattr(
        deploy_all_probe,
        "_read_home",
        lambda _account, _destination: declared_sha,
    )
    monkeypatch.setattr(deploy_all_probe, "_list_home", lambda _account: "?")
    assert deploy_all_probe.main(["--dori-agent-chat", "off", "--hermes-gateways", "running",
                                  "--runtime-root", str(runtime), "--live-root", str(live)]
    ) == 4
    assert "DEPLOY-ALL-UNVERIFIABLE" in capsys.readouterr().err


def test_undeclared_warning_is_rendered_and_recorded_without_blocking_receipt() -> None:
    plan = deploy_all.parse_observations(
        [
            "OBS|release|abc123",
            "OBS|mounts|judged",
            "OBS|home|agent|.hermes/scripts/d.py|automation/pkg/d.py|required|aaa|aaa",
            "OBS|undeclared|agent|.hermes/scripts/hand.py|bbbbbbbbbbbb",
            "OBS|end",
        ]
    )

    assert plan.clean
    assert "UNDECLARED HOME WARNINGS" in deploy_all.render_plan(plan)
    receipt = json.loads(
        deploy_all.render_receipt(plan, verified_at="2026-09-02T00:00:00+00:00")
    )
    assert receipt["undeclared"] == [
        {
            "account": "agent",
            "destination": ".hermes/scripts/hand.py",
            "sha256_prefix": "bbbbbbbbbbbb",
        }
    ]


def test_strict_undeclared_flag_escalates_warning_to_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines = [
        "OBS|release|abc123",
        "OBS|mounts|judged",
        "OBS|home|agent|.hermes/scripts/d.py|automation/pkg/d.py|required|aaa|aaa",
        "OBS|undeclared|agent|.hermes/scripts/hand.py|bbbbbbbbbbbb",
        "OBS|end",
    ]
    monkeypatch.setattr(deploy_all_probe, "observations", lambda *_args, peer_retired=False: lines)
    monkeypatch.setattr(deploy_all_probe, "_read_home", lambda *_args: "")

    assert deploy_all_probe.main(["--dori-agent-chat", "off", "--hermes-gateways", "running"]) == 0
    assert deploy_all_probe.main(["--dori-agent-chat", "off", "--hermes-gateways", "running", "--strict-undeclared"]) == 1


def test_home_listing_survives_a_caller_cwd_the_account_cannot_reenter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """v1.0.154 (2026-09-03): the probe ran ``sudo -u agent find`` from the operator's
    0700 home, find(1) could not restore that cwd on exit and returned 1 with the
    listing complete — read as "?" and the whole release became UNVERIFIABLE."""
    home = tmp_path / "home"
    (home / ".hermes" / "scripts").mkdir(parents=True)
    (home / ".hermes" / "scripts" / "declared.py").write_text("x", encoding="utf-8")
    (home / ".hermes" / "plugins" / "00-gate").mkdir(parents=True)
    (home / ".hermes" / "plugins" / "00-gate" / "plugin.py").write_text("x", encoding="utf-8")
    locked = tmp_path / "operator-home"
    locked.mkdir()
    monkeypatch.chdir(locked)
    locked.chmod(0o000)  # the listing account may not re-enter the caller's cwd
    try:
        proc = subprocess.run(
            ("bash", "-c", deploy_all_probe._LIST_HOME_SCRIPT),
            env={"HOME": str(home), "PATH": os.environ["PATH"]},
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        locked.chmod(0o700)

    assert proc.returncode == 0, proc.stderr
    assert sorted(proc.stdout.split()) == [
        ".hermes/plugins/00-gate/plugin.py",
        ".hermes/scripts/declared.py",
    ]
