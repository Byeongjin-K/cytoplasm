"""Deploy-checkout drift must reach the repair-ticket path, carrying its own recovery rule.

``probe_checkout_mirrors_origin`` only reports; ``main`` is what turns a report into work.
This drives the real ``healthcheck.sh`` end to end - real registry, real dispatch, real
``report_repair`` - and asserts a drifted checkout produces exactly one repair ticket.

The ticket body matters as much as its existence: the documented recovery for this fault is
``git format-patch`` -> ``git am`` on the workstation checkout, because the commits stranded
in prod exist nowhere else. A ticket that says only "check failed" invites the one repair
that destroys them (``git reset --hard``), so the guidance travels with the ticket.

Hermetic: an ``ssh`` stand-in first on PATH answers every probe locally - canned responses
for the service probes, the throwaway ``tmp_path`` checkout for the mirror probe, and a
journal file for the repair CLI invocation. No node, no network, no real repair ticket.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from tests.unit.test_healthcheck_peer_gateway_probe import write_peer_files

_REPO = Path(__file__).resolve().parents[2]
_HEALTHCHECK = _REPO / "automation" / "healthcheck.sh"
_CHECK_NAME = "example-primary-node ops checkout mirrors origin/main"
_TICKET_ID = "t_testticket"
#: 래퍼 프로브를 조용히 통과시키기 위한 고정 다이제스트 — 생성기 스털과 가짜 ssh 가 같은 값을 낸다.
_FAKE_WRAPPER_DIGEST = "ab" * 32

_FAKE_SSH = r"""#!/usr/bin/env bash
set -uo pipefail
cmd="${*: -1}"
cmd="${cmd//\/srv\/autophagy-agents/$FAKE_CHECKOUT}"
case "$cmd" in
  *repair_cli.py*detect*)
    { printf 'DETECT %s\n' "$cmd"; cat; } >> "$TICKET_JOURNAL"
    echo "t_testticket"
    ;;
  *health/liveliness*) echo 200 ;;
  *chat/completions*) printf 'PASS\t200\tnone\tnone\n' ;;
  *:8800/*) echo 401 ;;
  *:8001/health*) echo '{"status":"ok","model":"BAAI/bge-m3","dimensions":1024}' ;;
  *:6333/healthz*) echo 'healthz check passed' ;;
  *:8765/health*) echo '{"status":"ok","collection":"personal_cha"}' ;;
  *synthetic-does-not-exist*) echo inactive; exit 3 ;;
  *systemctl*is-active*) echo active ;;
  *sha256sum*.hermes/scripts/*) exec bash -c "$cmd" ;;
  *SNAPSHOT-V1*) exec bash -c "$cmd" ;;
  *wrapper-inputs*) echo "$FAKE_WRAPPER_DIGEST" ;;
  *merge-base*--is-ancestor*) exec bash -c "$cmd" ;;
  *) printf 'unexpected remote command: %s\n' "$cmd" >&2; exit 97 ;;
esac
"""

_FAKE_SUDO = """#!/usr/bin/env bash
set -euo pipefail
while [[ $# -gt 0 ]]; do
  case "$1" in
    -n|-H) shift ;;
    -u) shift 2 ;;
    *) break ;;
  esac
done
exec env "$@"
"""


def _git(repo: Path, *args: str) -> None:
    _ = subprocess.run(
        ("git", "-C", str(repo), *args), check=True, capture_output=True, text=True
    )


def _mirror_checkout(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _ = subprocess.run(
        ("git", "init", "--bare", "--initial-branch=main", str(origin)),
        check=True,
        capture_output=True,
        text=True,
    )
    checkout = tmp_path / "autophagy-agents"
    checkout.mkdir()
    _git(checkout, "init", "--initial-branch=main")
    _git(checkout, "config", "user.email", "ops@test.local")
    _git(checkout, "config", "user.name", "ops")
    _git(checkout, "config", "commit.gpgsign", "false")
    (checkout / "SKILL.md").write_text("version: 1.5.3\n", encoding="utf-8")
    _git(checkout, "add", "SKILL.md")
    _git(checkout, "commit", "-m", "deployed state")
    _git(checkout, "remote", "add", "origin", str(origin))
    _git(checkout, "push", "-u", "origin", "main")
    return checkout


def _drifted_checkout(tmp_path: Path) -> Path:
    """The exact 2026-07-27 shape: prod holds a commit origin/main has never seen."""
    checkout = _mirror_checkout(tmp_path)
    (checkout / "SKILL.md").write_text("version: 1.5.5\n", encoding="utf-8")
    _git(checkout, "commit", "-am", "learned in prod, never pushed")
    return checkout


def _behind_checkout(tmp_path: Path) -> Path:
    """Origin moved one commit ahead; prod is stale. Invisible to the old probe."""
    checkout = _mirror_checkout(tmp_path)
    mover = tmp_path / "mover"
    _ = subprocess.run(
        ("git", "clone", str(tmp_path / "origin.git"), str(mover)),
        check=True, capture_output=True, text=True,
    )
    _git(mover, "config", "user.email", "mover@test.local")
    _git(mover, "config", "user.name", "mover")
    _git(mover, "config", "commit.gpgsign", "false")
    (mover / "SKILL.md").write_text("version: 1.6.0\n", encoding="utf-8")
    _git(mover, "commit", "-am", "a commit only origin has")
    _git(mover, "push", "origin", "main")
    return checkout


def _fake_bin(tmp_path: Path, *, ssh_down: bool = False) -> Path:
    fake_bin = tmp_path / ("bin_down" if ssh_down else "bin")
    fake_bin.mkdir(exist_ok=True)
    ssh_body = "#!/usr/bin/env bash\nexit 255\n" if ssh_down else _FAKE_SSH
    for name, body in (("ssh", ssh_body), ("sudo", _FAKE_SUDO)):
        stub = fake_bin / name
        stub.write_text(body, encoding="utf-8")
        stub.chmod(0o755)
    return fake_bin


@dataclass(frozen=True, slots=True)
class Sweep:
    """One healthcheck run plus the repair tickets it produced."""

    returncode: int
    output: str
    tickets: str


def _quiet_skill_mounts(tmp_path: Path) -> tuple[Path, Path]:
    """A release/live pair that agrees with itself: no skills, no mounts, no drift."""
    runtime, live = tmp_path / "skill-runtime", tmp_path / "skill-live"
    (runtime / "automation").mkdir(parents=True, exist_ok=True)
    (runtime / "skills").mkdir(parents=True, exist_ok=True)
    live.mkdir(parents=True, exist_ok=True)
    (runtime / "automation" / "__init__.py").touch()
    for module in ("skill_mount_drift.py", "skill_review.py"):
        source = _REPO / "automation" / module
        _ = (runtime / "automation" / module).write_bytes(source.read_bytes())
    return runtime, live


def _quiet_release_probes(tmp_path: Path, checkout: Path) -> tuple[Path, Path, Path]:
    sha = subprocess.run(
        ("git", "-C", str(checkout), "rev-parse", "HEAD"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    generation = tmp_path / "releases" / sha
    source = generation / "automation"
    installed = tmp_path / "installed-helper"
    source.mkdir(parents=True)
    installed.mkdir()
    for source_name, installed_name in (
        ("release_store.py", "autophagy-install-release"),
        ("release_provenance.py", "release_provenance.py"),
    ):
        payload = (_REPO / "automation" / source_name).read_bytes()
        _ = (source / source_name).write_bytes(payload)
        _ = (installed / installed_name).write_bytes(payload)
    # The probe now also compares the reconcile unit and the gateway helper — both are
    # installed outside the release by a root provisioner, and the unit going unwatched
    # is what let a three-day production freeze pass as healthy (2026-08-19).
    _ = (source / "node_asset_renderer.py").write_text(
        "import shutil, sys\nshutil.copyfile(sys.argv[1], sys.argv[2])\n", encoding="utf-8"
    )
    # 래퍼 생성기도 렌더러처럼 스털이다 — 이 테스트의 주제는 체크아웃 드리프트
    # 티켓팅이지 allowlist 생성이 아니다(그쪽은 자기 테스트가 따로 있다).
    _ = (source / "healthcheck_probe_wrapper.sh").write_text(
        f"#!/usr/bin/env bash\necho {_FAKE_WRAPPER_DIGEST}\n", encoding="utf-8"
    )
    units = tmp_path / "units"
    units.mkdir()
    (source / "systemd").mkdir(parents=True, exist_ok=True)
    converge = installed / "autophagy-converge.d"
    converge.mkdir()
    (source / "libexec").mkdir(parents=True, exist_ok=True)
    for installed_path, source_path, payload in (
        (installed / "autophagy-gateway-pair", source / "gateway_pair.py", "pair\n"),
        (
            units / "autophagy-deploy-reconcile.service",
            source / "systemd" / "autophagy-deploy-reconcile.service",
            "service\n",
        ),
        (
            units / "autophagy-deploy-reconcile.timer",
            source / "systemd" / "autophagy-deploy-reconcile.timer",
            "timer\n",
        ),
        (installed / "autophagy-install-skill", source / "skill_store.py", "skill\n"),
        (
            installed / "autophagy-converge-origin-main",
            source / "converge_origin_main.sh",
            "converge\n",
        ),
        (
            installed / "autophagy-resume-deploy",
            source / "libexec" / "autophagy-resume-deploy",
            "resume\n",
        ),
        (converge / "origin_snapshot.sh", source / "origin_snapshot.sh", "snapshot\n"),
    ):
        _ = installed_path.write_text(payload, encoding="utf-8")
        _ = source_path.write_text(payload, encoding="utf-8")
    # 리컨실러가 실제로 실행하는 converge.d 사본 — libexec 루트와 같은 소스를 공유한다.
    for name in ("release_store.py", "release_provenance.py"):
        _ = (converge / name).write_bytes((source / name).read_bytes())
    current = tmp_path / "current-release"
    current.symlink_to(generation, target_is_directory=True)
    # 리컨실러가 import 하는 패키지 사본 — libexec 루트에는 없는 파일들이라 위 목록과
    # 겹치지 않는다. 프로브가 이것들을 보기 시작한 뒤로(2026-09-07) 완비된 설치를
    # 모델하려면 여기에도 있어야 한다.
    package = converge / "automation"
    package.mkdir()
    for relative, name in (
        ("automation/__init__.py", "__init__.py"),
        ("automation/git_tag_signature.py", "git_tag_signature.py"),
        ("automation/typing_compat.py", "typing_compat.py"),
        ("automation/update_trust.py", "update_trust.py"),
        ("automation/update_trust_state.py", "update_trust_state.py"),
        ("automation/node_config.py", "node_config.py"),
        ("configs/node.example.toml", "node.example.toml"),
    ):
        payload = f"{name}-v1\n".encode()
        origin = source.parent / relative
        origin.parent.mkdir(parents=True, exist_ok=True)
        _ = origin.write_bytes(payload)
        _ = (package / name).write_bytes(payload)

    return current, installed / "autophagy-install-release", installed / "release_provenance.py"


def _sweep(
    tmp_path: Path, checkout: Path, *args: str, ssh_down: bool = False, no_repair: bool = False
) -> Sweep:
    journal = tmp_path / "tickets.txt"
    env = dict(os.environ)
    env["HOME"] = str(tmp_path / "isolated-home")
    env["PATH"] = f"{_fake_bin(tmp_path, ssh_down=ssh_down)}{os.pathsep}{env['PATH']}"
    env["HEALTHCHECK_LOG_DIR"] = str(tmp_path / "logs")
    env["HEALTHCHECK_SSH_USER"] = ""
    env["HEALTHCHECK_SSH_IDENTITY"] = ""
    peer_config, peer_directory = write_peer_files(tmp_path)
    env["HEALTHCHECK_PEER_GATEWAY_CONFIG"] = str(peer_config)
    env["HEALTHCHECK_PEER_CHANNEL_DIRECTORY"] = str(peer_directory)
    # 이 하네스가 모델하는 것은 **완비된 노드**이고, 그런 노드는 소유자 통지를 받을지
    # 이미 결정했다. 결정하지 않은 상태를 여기서 재현하면 모든 sweep 단언이 그 한 줄
    # 때문에 흔들린다 — 그 상태의 판정은 자기 테스트가 따로 갖고 있다.
    credential = tmp_path / "repair-approval.env"
    _ = credential.write_text(
        "DISCORD_BOT_TOKEN=fixture-not-a-real-credential\nAUTOPHAGY_OWNER_ID=1\n",
        encoding="utf-8",
    )
    env["HEALTHCHECK_OWNER_NOTICE_CREDENTIAL"] = str(credential)
    env["FAKE_CHECKOUT"] = str(checkout)
    env["FAKE_WRAPPER_DIGEST"] = _FAKE_WRAPPER_DIGEST
    # Once the checkout probe runs locally (no ssh) the FAKE_CHECKOUT rewrite no
    # longer reaches it; the local probe reads its target from this env instead.
    env["HEALTHCHECK_OPS_CHECKOUT"] = str(checkout)
    current, helper, provenance = _quiet_release_probes(tmp_path, checkout)
    env["RUNTIME_RELEASE_CURRENT"] = str(current)
    env["HEALTHCHECK_RELEASE_SOURCE_ROOT"] = str(current)
    receipt = tmp_path / "deploy-all-receipt.json"
    _ = receipt.write_text(
        f'{{"release_sha":"{current.resolve().name}","version":2}}\n', encoding="utf-8"
    )
    env["HEALTHCHECK_DEPLOY_ALL_RECEIPT"] = str(receipt)
    env["HEALTHCHECK_RELEASE_HELPER"] = str(helper)
    env["HEALTHCHECK_RELEASE_PROVENANCE"] = str(provenance)
    env["HEALTHCHECK_LIBEXEC_DIR"] = str(helper.parent)
    env["HEALTHCHECK_UNIT_DIR"] = str(tmp_path / "units")
    # 워처 래퍼 프로브에는 **실제로 맞는 행 하나**를 물린다. 빈 매니페스트로 조용히
    # 통과시키면 SSH 전면 장애에서도 이 프로브만 PASS 가 되어, 아래 INFRA_FAILURE 붕괴
    # 가드(원격 전건 실패일 때만 티켓 1건)를 깨뜨린다.
    home = Path(env["HOME"])
    (home / ".hermes" / "scripts").mkdir(parents=True, exist_ok=True)
    wrapper_source = current.resolve() / "automation" / "release_provenance.py"
    _ = (home / ".hermes" / "scripts" / "w.py").write_bytes(wrapper_source.read_bytes())
    watcher_manifest = tmp_path / "watcher-manifest.txt"
    _ = watcher_manifest.write_text(
        "agent|automation/release_provenance.py|.hermes/scripts/w.py|required\n", encoding="utf-8"
    )
    env["HEALTHCHECK_WATCHER_MANIFEST"] = str(watcher_manifest)
    # 런타임 패키지 프로브도 원격 명령을 실제로 한 번 내게 해 SSH 장애 tally를
    # 보존한다. 릴리스와 계정 홈에 같은 한 파일을 두어 평상시에는 clean이다.
    package_source = current.resolve() / "automation" / "runtime_fixture"
    package_runtime = home / ".hermes" / "runtime_fixture"
    package_source.mkdir(parents=True)
    package_runtime.mkdir(parents=True)
    _ = (package_source / "item.py").write_text("VALUE = 1\n", encoding="utf-8")
    _ = (package_runtime / "item.py").write_text("VALUE = 1\n", encoding="utf-8")
    runtime_manifest = tmp_path / "runtime-package-manifest.txt"
    _ = runtime_manifest.write_text(
        "\n".join((
            "agent|automation/runtime_fixture|.hermes/runtime_fixture|required",
            "agent|automation/runtime_fixture|.hermes/runtime_fixture|required|rag|python",
            "",
        )),
        encoding="utf-8",
    )
    env["HEALTHCHECK_RUNTIME_PACKAGE_MANIFEST"] = str(runtime_manifest)
    update_trust = tmp_path / "update_trust_probe.py"
    update_trust.write_text("raise SystemExit(0)\n", encoding="utf-8")
    env["UPDATE_TRUST_SCRIPT"] = str(update_trust)
    env["TICKET_JOURNAL"] = str(journal)
    if no_repair:
        env["HEALTHCHECK_NO_REPAIR"] = "1"
    # The skill-mount probe also runs locally, so a real sweep would judge THIS host's
    # /srv and fail for reasons unrelated to the checkout. Point it at a matching
    # (empty) pair so it passes and these assertions stay about the checkout ticket.
    runtime, live = _quiet_skill_mounts(tmp_path)
    env["AUTOPHAGY_RUNTIME_ROOT"] = str(runtime)
    env["HEALTHCHECK_SKILL_LIVE_ROOT"] = str(live)
    result = subprocess.run(
        ("bash", str(_HEALTHCHECK), *args),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    return Sweep(
        returncode=result.returncode,
        output=result.stdout + result.stderr,
        tickets=journal.read_text(encoding="utf-8") if journal.exists() else "",
    )


def test_a_mirrored_checkout_sweeps_clean(tmp_path: Path) -> None:
    # Given a checkout that still mirrors origin/main
    sweep = _sweep(tmp_path, _mirror_checkout(tmp_path))

    # Then the sweep is healthy and nothing is ticketed
    assert sweep.returncode == 0, sweep.output
    assert "ALL_HEALTHY" in sweep.output
    assert sweep.tickets == ""


def test_drift_fails_the_sweep_and_raises_a_repair_ticket(tmp_path: Path) -> None:
    # Given a checkout holding a commit origin/main has never seen
    sweep = _sweep(tmp_path, _drifted_checkout(tmp_path))

    # Then the sweep fails and the drift reaches the existing repair-ticket path
    assert sweep.returncode == 1, sweep.output
    assert f"FAIL {_CHECK_NAME}" in sweep.output
    assert f"REPAIR_TICKET {_TICKET_ID}" in sweep.output
    assert "--source healthcheck" in sweep.tickets
    assert f"--location '{_CHECK_NAME}'" in sweep.tickets


def test_manual_diagnostic_opt_out_does_not_file_repair_occurrences(tmp_path: Path) -> None:
    sweep = _sweep(tmp_path, _drifted_checkout(tmp_path), no_repair=True)

    assert sweep.returncode == 1, sweep.output
    assert f"FAIL {_CHECK_NAME}" in sweep.output
    assert "REPAIR_TICKET" not in sweep.output
    assert sweep.tickets == ""
    assert "HEALTHCHECK_FAILED" in sweep.output


def test_only_the_drifted_check_is_ticketed(tmp_path: Path) -> None:
    """The healthy services must not ride along as ticket noise."""
    sweep = _sweep(tmp_path, _drifted_checkout(tmp_path))

    assert sweep.tickets.count("DETECT ") == 1


def test_the_drift_ticket_names_the_non_destructive_recovery(tmp_path: Path) -> None:
    """Stranded commits exist nowhere else - the ticket must not invite a discard."""
    sweep = _sweep(tmp_path, _drifted_checkout(tmp_path))

    body = sweep.tickets
    assert "git format-patch" in body
    assert "git am" in body
    assert "reset --hard" in body
    # The two drift shapes must not merge: an AHEAD checkout has stranded commits
    # and needs format-patch, NOT a pull. land.sh here would be the wrong advice.
    assert "land.sh" not in body


def test_a_healthy_service_probe_is_never_given_drift_guidance(tmp_path: Path) -> None:
    """Guidance is per-probe: an unrelated failure must not inherit the checkout advice."""
    sweep = _sweep(tmp_path, _mirror_checkout(tmp_path), "--synthetic-failure")

    assert sweep.returncode == 1, sweep.output
    assert "DETECT " in sweep.tickets


def test_a_behind_checkout_is_ticketed_with_pull_not_patch_guidance(tmp_path: Path) -> None:
    """Origin moved ahead; prod just needs to catch up — land.sh, never format-patch."""
    sweep = _sweep(tmp_path, _behind_checkout(tmp_path))

    assert sweep.returncode == 1, sweep.output
    assert f"FAIL {_CHECK_NAME}" in sweep.output
    assert sweep.tickets.count("DETECT ") == 1
    assert "RELEASE-STALE-SUPPRESSED-WARN" in sweep.output
    assert "land.sh" in sweep.tickets
    assert "format-patch" not in sweep.tickets


def test_a_total_ssh_outage_reports_infra_failure_not_nine_tickets(tmp_path: Path) -> None:
    """Regression lock for finding γ / d7ed0ad.

    Once the checkout probe runs locally it passes during a fleet-wide SSH outage,
    so the all-fail guard would see 9-of-10 and emit nine tickets — back to the
    'one credential problem looks like nine outages' failure. A total remote outage
    must still collapse to a single INFRA_FAILURE, never per-service tickets.
    """
    sweep = _sweep(tmp_path, _mirror_checkout(tmp_path), ssh_down=True)

    assert sweep.returncode == 1, sweep.output
    assert "INFRA_FAILURE" in sweep.output
    assert sweep.tickets == ""
    assert "format-patch" not in sweep.tickets
