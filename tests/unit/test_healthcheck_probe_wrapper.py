"""healthcheck 강제명령 래퍼의 생성기와 드리프트 프로브.

래퍼는 2026-08-20까지 손으로 유지됐고, 그 대가는 조용했다 — 그 시점 healthcheck 가
보내는 43개 명령 중 23개가 목록 밖이라 exit 126 을 받고 있었고, 반대로 아무도 쓰지 않는
해시 14개가 허용 범위만 넓히고 있었다. 거부당한 프로브는 자기 하나만 실패하고, 그 실패가
수리 티켓을 내려 해도 티켓 명령까지 같은 이유로 거부되므로 밖으로 나가는 신호가 없다.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_GENERATOR = _REPO / "automation" / "healthcheck_probe_wrapper.sh"
_PROBE = _REPO / "automation" / "healthcheck_wrapper_probe.sh"
_HEALTHCHECK = _REPO / "automation" / "healthcheck.sh"


def _generate(*args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "HEALTHCHECK_SSH_USER": "", "HEALTHCHECK_SSH_IDENTITY": ""}
    return subprocess.run(
        ("bash", str(_GENERATOR), *args),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _digest_with_recorded_unit_command(
    remote_command: str, *, unrelated_marker: str = ""
) -> str:
    """원격 명령을 기록만 하도록 활성 단위 프로브 하나를 대체한다(ssh 없음)."""
    script = f'''source "{_GENERATOR}"
source "{_HEALTHCHECK}"
probe_owner_notice_credentials() {{
  : {shlex.quote(unrelated_marker)}
  capture_on_node "$1" "$RECORDED_UNIT_COMMAND"
}}
wrapper_inputs_digest "$PRIMARY_NODE"
'''
    result = subprocess.run(
        ("bash", "-c", script),
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "HEALTHCHECK_SSH_USER": "",
            "HEALTHCHECK_SSH_IDENTITY": "",
            "RECORDED_UNIT_COMMAND": remote_command,
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()



def _run_probe(
    tmp_path: Path, *, header: str | None, expected: str, rejected: bool = False
) -> subprocess.CompletedProcess[str]:
    """생성기와 원격 읽기를 스텁으로 갈아끼워 프로브의 판정만 돌린다."""
    release = tmp_path / "release" / "automation"
    release.mkdir(parents=True)
    _ = (release / "healthcheck_probe_wrapper.sh").write_text(
        f"#!/usr/bin/env bash\necho {expected}\n", encoding="utf-8"
    )
    stub = tmp_path / "stub.sh"
    if rejected:
        body = "capture_on_node() { return 1; }"
    else:
        body = f"capture_on_node() {{ printf %s '{header or ''}'; return 0; }}"
    _ = stub.write_text(body + "\n", encoding="utf-8")
    return subprocess.run(
        (
            "bash",
            "-c",
            f'source "{stub}"; source "{_PROBE}"; '
            f"probe_healthcheck_wrapper_current node ops ignored",
        ),
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "HEALTHCHECK_RELEASE_SOURCE_ROOT": str(tmp_path / "release")},
    )


# --- 생성기 ---------------------------------------------------------------------


def test_the_generated_wrapper_is_valid_bash_with_provenance() -> None:
    result = _generate("--print")

    assert result.returncode == 0, result.stderr
    assert re.search(r"^# wrapper-inputs: [0-9a-f]{64}$", result.stdout, re.M)
    assert "exit 126" in result.stdout, "허용 목록 밖은 여전히 거부해야 한다"
    check = subprocess.run(
        ("bash", "-n"), input=result.stdout, capture_output=True, text=True, check=False
    )
    assert check.returncode == 0, check.stderr


def test_the_allowlist_is_observed_from_the_checks_not_hand_listed() -> None:
    """프로브가 무슨 명령을 내는지 생성기가 따로 알지 않는다 — 실행 없이 관측한다."""
    result = _generate("--print")

    hashes = re.findall(r"^  ([0-9a-f]{64})", result.stdout, re.M)
    # 정확한 개수는 노드마다 다르다 — 프로브가 로컬 사유로 조기 반환하면 그 뒤 명령이
    # 기록되지 않기 때문이다(그래서 생성은 노드에서 한다). 여기서는 "관측이 실제로
    # 일어났는가"만 본다.
    assert len(hashes) >= 10, f"관측이 거의 아무것도 잡지 못했다: {len(hashes)}"
    assert len(hashes) == len(set(hashes)), "중복 해시는 목록만 부풀린다"


def test_the_inputs_digest_moves_when_only_a_recorded_probe_command_moves() -> None:
    before = _digest_with_recorded_unit_command("systemctl --user is-active one.service")
    after = _digest_with_recorded_unit_command("systemctl --user is-active two.service")

    assert re.fullmatch(r"[0-9a-f]{64}", before)
    assert before != after


def test_the_inputs_digest_ignores_unrelated_probe_edits() -> None:
    before = _digest_with_recorded_unit_command(
        "systemctl --user is-active unchanged.service", unrelated_marker="before"
    )
    after = _digest_with_recorded_unit_command(
        "systemctl --user is-active unchanged.service", unrelated_marker="after"
    )

    assert before == after


def test_the_inputs_digest_is_stable_for_identical_recorded_commands() -> None:
    first = _digest_with_recorded_unit_command("systemctl --user is-active stable.service")
    second = _digest_with_recorded_unit_command("systemctl --user is-active stable.service")

    assert first == second


def test_the_inputs_digest_moves_when_the_check_list_moves(tmp_path: Path) -> None:
    """체크·워처 매니페스트가 바뀌면 지문이 바뀌어야 드리프트를 알아챌 수 있다."""
    before = _generate("--inputs-digest").stdout.strip()
    manifest = tmp_path / "manifest.txt"
    _ = manifest.write_text(
        "agent|automation/healthcheck.sh|.hermes/scripts/x.py|required\n", encoding="utf-8"
    )
    env = {**os.environ, "HEALTHCHECK_WATCHER_MANIFEST": str(manifest)}
    after = subprocess.run(
        ("bash", str(_GENERATOR), "--inputs-digest"),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    ).stdout.strip()

    assert re.fullmatch(r"[0-9a-f]{64}", before)
    assert before != after


# --- 드리프트 프로브 -------------------------------------------------------------


def test_a_current_wrapper_passes(tmp_path: Path) -> None:
    digest = "a" * 64
    result = _run_probe(tmp_path, header=digest, expected=digest)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "WRAPPER-PASS" in result.stderr


def test_a_wrapper_built_from_an_older_check_list_fails(tmp_path: Path) -> None:
    result = _run_probe(tmp_path, header="b" * 64, expected="a" * 64)

    assert result.returncode != 0
    assert "WRAPPER-DRIFT" in result.stderr
    assert "--install" in result.stderr, "재생성 명령이 함께 나와야 한다"


def test_a_hand_maintained_wrapper_without_provenance_fails(tmp_path: Path) -> None:
    """생성기 이전의 손 유지보수본은 헤더가 없다 — 그것이 곧 신호다."""
    result = _run_probe(tmp_path, header="", expected="a" * 64)

    assert result.returncode != 0
    assert "no provenance header" in result.stderr
    assert "--install" in result.stderr


def test_a_wrapper_that_rejects_the_probe_is_itself_the_answer(tmp_path: Path) -> None:
    """래퍼가 이 프로브를 거부하면 재생성되지 않았다는 뜻이다 — 조용히 넘기지 않는다."""
    result = _run_probe(tmp_path, header=None, expected="a" * 64, rejected=True)

    assert result.returncode != 0
    assert "WRAPPER-DRIFT" in result.stderr
    assert "--install" in result.stderr


# --- 배선 -----------------------------------------------------------------------


def test_healthcheck_wires_the_wrapper_probe() -> None:
    text = _HEALTHCHECK.read_text(encoding="utf-8")

    assert "healthcheck_wrapper_probe.sh" in text
    assert "healthcheck_wrapper_current" in text
    assert "/chat/completions" not in text
    assert "Authorization: Bearer" not in text
    local = next(
        line for line in text.splitlines() if line.startswith("readonly LOCAL_PROBES=")
    )
    assert "healthcheck_wrapper_current" not in local, (
        "래퍼는 운영자 홈에 있어 cron 계정(ops)이 읽지 못한다 — 원격 프로브여야 한다"
    )
