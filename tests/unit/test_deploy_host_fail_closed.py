"""배포 스크립트가 호스트 미지정을 자기 이름으로 거부하는가.

`skills/mail/deploy.sh` 의 폴백은 리터럴 플레이스홀더 `<primary-node>` 였다. 환경변수를
빠뜨리면 ssh 가 해석되지 않는 이름으로 접속을 시도하고, 나오는 오류는 DNS 를 가리킨다 —
실제 원인("변수를 안 줬다")은 어디에도 없다. 엉뚱한 노드로 배포되지는 않으므로 안전 문제는
아니지만, 배포 중에 한 번 더 막히는 마찰이고 원인 추적이 길어진다.

검증은 스크립트를 **실행**해서 한다 — 텍스트 grep 은 fail-closed 를 증명하지 못한다.

**호스트가 없다**는 전제는 환경변수만 비워서는 완성되지 않는다(2026-08-27 보강). 스크립트가
`~/.hermes/node.toml` 도 읽으므로, 운영자 워크스테이션에서는 변수를 지워도 호스트가 해석돼
스크립트가 끝까지 진행하고 **실제 노드에 배포한다** — 이 파일이 그때까지 외부효과를 내지
않은 것은 우연히 호스트가 비어 있었기 때문이지 격리 때문이 아니었다. 그래서 노드 설정
출처를 stub 으로 비우고, PATH 앞단의 stub `ssh` 로 어떤 경로로도 실제 ssh 에 닿지 않게 한다
(`test_deploy_host_fail_closed_all.py` 와 같은 격리). 단언 자체는 그대로다.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_FAIL_CLOSED = ("skills/mail/deploy.sh", "skills/wiki/deploy.sh")


def _stub_bin(tmp_path: Path) -> Path:
    """노드 설정 출처를 비우고, 실제 ssh 로 나가는 길을 막는 PATH 앞단."""
    python = tmp_path / "python3"
    _ = python.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "${1##*/}" = "node_config_sh.py" ]; then\n'
        "  printf '%s\\n' 'NODE_DEPLOY_SSH_HOST=' 'NODE_RAG_NODE_NAME='\n"
        "  exit 0\n"
        "fi\n"
        'exec /usr/bin/python3 "$@"\n',
        encoding="utf-8",
    )
    python.chmod(0o755)
    ssh = tmp_path / "ssh"
    _ = ssh.write_text("#!/usr/bin/env bash\necho 'STUB-SSH REACHED' >&2\nexit 99\n", encoding="utf-8")
    ssh.chmod(0o755)
    return tmp_path

@pytest.mark.parametrize("relative", _FAIL_CLOSED)
def test_missing_host_is_refused_before_any_ssh(relative: str, tmp_path: Path) -> None:
    # Given: the deploy script with no host configured in ANY source it consults.
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in ("DEPLOY_SSH_HOST", "NODE_DEPLOY_SSH_HOST")
    }
    environment["PATH"] = f"{_stub_bin(tmp_path)}:/usr/bin:/bin"

    # When: it is run.
    result = subprocess.run(
        ["bash", str(_REPO / relative)],
        capture_output=True, text=True, check=False, timeout=60,
        cwd=str(_REPO), env=environment,
    )

    # Then: it stops with its own diagnosis rather than handing an unresolvable
    # placeholder to ssh, and it names the fix.
    if relative == "skills/wiki/deploy.sh":
        assert result.returncode == 4, result.stdout + result.stderr
        assert result.stderr.startswith("WIKI-RETIRED:")
    else:
        assert result.returncode == 3, result.stdout + result.stderr
        assert "DEPLOY-BLOCK" in result.stderr
        assert "DEPLOY_SSH_HOST" in result.stderr
    assert "STUB-SSH REACHED" not in result.stderr


@pytest.mark.parametrize("relative", _FAIL_CLOSED)
def test_the_placeholder_hostname_is_gone(relative: str) -> None:
    # Then: no path through the script can still reach ssh with the literal placeholder.
    assert "<primary-node>" not in (_REPO / relative).read_text(encoding="utf-8")
