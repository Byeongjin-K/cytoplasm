"""배포 스크립트 **전수**가 호스트 미지정을 자기 이름으로 거부하는가.

`tests/unit/test_deploy_host_fail_closed.py` 는 mail·wiki 두 스크립트만 고정한다. 그
가드가 나머지에는 없어서, 변수를 빠뜨리면 ssh 가 리터럴 플레이스홀더로 접속을 시도하고
나오는 오류는 DNS 를 가리킨다 — 진짜 원인("변수를 안 줬다")은 어디에도 없다
(follow-ups 2026-08-18).

목록을 손으로 적지 않고 추적 중인 `deploy.sh` 를 훑어 만든다: 새 배포 스크립트가
등록을 잊혀 빠지는 것이 이 부채가 처음 생긴 방식이기 때문이다.

**왜 별도 파일인가**: 원본 파일의 출력 해시는 FS3 정산 레코드 `4fd4b808…`
(`.omo/evidence/fs3/completions/task-6.json`)에 고정돼 있고 `test_fs3_replay_gate.py` 가
매번 재생을 대조한다. 원본의 파라미터 목록을 넓히면 그 과거 증적이 재현되지 않는다 —
원장을 고쳐 맞추는 것은 증적 위조이므로 전수 검사는 이 파일이 소유한다.

검증은 스크립트를 **실행**해서 한다 — 텍스트 grep 은 fail-closed 를 증명하지 못한다.
호스트가 비어 있으면 ssh 이전 단계에서 멈추므로 외부효과는 없고, PATH 앞단에 stub 을
두어 어떤 경로로도 실제 ssh 에 닿지 않음을 보장한다.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_HOST_VARIABLES = (
    "DEPLOY_SSH_HOST", "NODE_DEPLOY_SSH_HOST", "RAG_STACK_SSH_HOST", "NODE_RAG_NODE_NAME",
)


def _tracked_deploy_scripts() -> tuple[str, ...]:
    listed = subprocess.run(
        ["git", "ls-files", "*deploy.sh"],
        cwd=str(_REPO), capture_output=True, text=True, check=True,
    )
    return tuple(
        sorted(
            line for line in listed.stdout.splitlines()
            if line.strip()
        )
    )


_DEPLOY_SCRIPTS = _tracked_deploy_scripts()


def _sandbox(tmp_path: Path) -> dict[str, str]:
    # A script may also read its host from the node config, so the stub makes that
    # source empty too — otherwise "no host configured" is only half true.
    python = tmp_path / "python3"
    _ = python.write_text(
        "\n".join(
            (
                "#!/usr/bin/env bash",
                'if [ "${1##*/}" = "node_config_sh.py" ]; then',
                "  printf '%s\\n' 'NODE_DEPLOY_SSH_HOST=' 'NODE_RAG_NODE_NAME='",
                "  exit 0",
                "fi",
                'exec /usr/bin/python3 "$@"',
                "",
            )
        ),
        encoding="utf-8",
    )
    python.chmod(0o755)
    ssh = tmp_path / "ssh"
    _ = ssh.write_text("#!/usr/bin/env bash\necho 'STUB-SSH REACHED' >&2\nexit 99\n", encoding="utf-8")
    ssh.chmod(0o755)
    environment = {
        key: value for key, value in os.environ.items() if key not in _HOST_VARIABLES
    }
    environment["PATH"] = f"{tmp_path}:/usr/bin:/bin"
    # A deployer that is not on origin/main yet is stopped by the provenance guard, which
    # prints its own DEPLOY-BLOCK and hides a missing host check until after the merge
    # (PR #97 and #93 both turned main red that way). Skip provenance so every deployer
    # reaches its own host check on its branch; ssh stays the stub.
    environment["DEPLOY_ALLOW_UNPUSHED"] = "1"
    return environment


def test_the_repository_has_deploy_scripts_to_check() -> None:
    assert _DEPLOY_SCRIPTS, "git ls-files found no deploy.sh — the enumeration is broken"


@pytest.mark.parametrize("relative", _DEPLOY_SCRIPTS)
def test_missing_host_is_refused_before_any_ssh(relative: str, tmp_path: Path) -> None:
    # Given: a deploy script with no deployment host configured anywhere.
    environment = _sandbox(tmp_path)

    # When: it is run.
    result = subprocess.run(
        ["bash", str(_REPO / relative)],
        capture_output=True, text=True, check=False, timeout=60,
        cwd=str(_REPO), env=environment,
    )

    # Then: it stops with its own diagnosis, and never hands a host to ssh.
    assert "DEPLOY-BLOCK" in result.stderr, result.stdout + result.stderr
    assert "STUB-SSH REACHED" not in result.stderr, "the script reached ssh anyway"


@pytest.mark.parametrize("relative", _DEPLOY_SCRIPTS)
def test_the_placeholder_hostname_is_gone(relative: str) -> None:
    # Then: no path through the script can still reach ssh with the literal placeholder.
    assert "<primary-node>" not in (_REPO / relative).read_text(encoding="utf-8")
