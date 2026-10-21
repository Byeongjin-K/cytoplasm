r"""배포 push 는 착지를 확인해야 한다 — ssh 의 exit 0 은 파일이 갔다는 뜻이 아니다.

2026-08-20 실측: `skills/wiki/deploy.sh` 가 rc=0 으로 끝났는데 노드의 파일은 7월 22일자
그대로였다. 11개 deploy.sh 의 `push_file` 이 전부 바이트 동일했고, 전부
`run_agent "... cat > \$HOME/<dest> ..." < "$source"` 한 줄로 끝났다 — 원격 `cat` 은
stdin 이 비어 있어도 0을 돌려주므로, 아무것도 안 써도 성공으로 보인다. 그 실행에서는
ssh 가 로컬 포워딩 실패를 경고했고(`Could not request local forwarding`), `bash -lc`
로그인 셸이 stdin 을 먼저 소비할 수 있는 구조였다.

`set -euo pipefail` 은 이미 다 붙어 있었다 — 그래서 이건 종료코드 전파 문제가 아니라
**확인하지 않은 쓰기** 문제다. 원격 read-back 해시 대조는 이 리포가 이미 쓰는 방식이다
(obsidian_write 의 push 후 원격 해시 검증, deploy-skill.sh 의 `readlink` 판정).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_HELPER = _REPO / "automation" / "deploy_push.sh"
_DEPLOY_SCRIPTS = (
    "automation/doctor/deploy.sh",
    "automation/managed_sync/deploy.sh",
    "automation/memory_curator/deploy.sh",
    "automation/memory_relocate/deploy.sh",
    "automation/notes_organize/deploy.sh",
    "automation/rag_ingest/deploy.sh",
    "automation/repair/deploy.sh",
    "automation/selfskill_audit/deploy.sh",
    "automation/skill_generation/deploy.sh",
    "automation/state_backup/deploy.sh",
    "automation/stt_eval/deploy.sh",
    "automation/voice_catalog/deploy.sh",
    "skills/budget/deploy.sh",
    "skills/calendar/deploy.sh",
    "skills/coordination/deploy.sh",
    "skills/mail/deploy.sh",
    "skills/todo/deploy.sh",
)


def _run(tmp_path: Path, *, land: bool, corrupt: bool = False) -> subprocess.CompletedProcess[str]:
    """`run_agent` 를 가짜 노드로 갈아끼워 push→read-back 경로만 돌린다."""
    home = tmp_path / "node-home"
    home.mkdir()
    source = tmp_path / "payload.py"
    _ = source.write_text("print('v2')\n", encoding="utf-8")

    # 가짜 run_agent: 원격 셸 대신 로컬 bash 로 같은 스크립트를 돌린다.
    # land=False 는 stdin 을 버려 "ssh 는 0인데 아무것도 안 쓴" 실측 상황을 만든다.
    stdin_source = '< /dev/null' if not land else ""
    corruptor = "printf 'tampered\\n' > \"$HOME/$1\";" if corrupt else ""
    stub = tmp_path / "stub.sh"
    _ = stub.write_text(
        "run_agent() {\n"
        f'  HOME="{home}" bash -c "$1" {stdin_source}\n'
        "}\n",
        encoding="utf-8",
    )
    script = (
        f'source "{stub}"; source "{_HELPER}"; '
        + (f'( {corruptor} true ) ' if corrupt else "")
        + f'push_file "{source}" "scripts/payload.py"'
    )
    return subprocess.run(
        ("bash", "-c", script), capture_output=True, text=True, check=False
    )


def test_a_push_that_lands_succeeds(tmp_path: Path) -> None:
    result = _run(tmp_path, land=True)

    assert result.returncode == 0, result.stdout + result.stderr


def test_a_push_that_writes_nothing_is_not_a_success(tmp_path: Path) -> None:
    """실측된 그 상황 — 원격 명령은 0을 냈지만 파일은 그대로였다."""
    result = _run(tmp_path, land=False)

    assert result.returncode != 0, "쓰이지 않았는데 성공으로 보고했다"
    assert "DEPLOY-BLOCK" in result.stderr
    assert "did not land" in result.stderr


def test_the_failure_names_the_destination(tmp_path: Path) -> None:
    """어느 파일이 안 갔는지 말하지 않으면 11개 배포 중 무엇인지 다시 찾아야 한다."""
    result = _run(tmp_path, land=False)

    assert "scripts/payload.py" in result.stderr


def test_the_destination_is_replaced_by_rename(tmp_path: Path) -> None:
    """원자적 rename으로 목적지가 교체되고 temp 파일이 남지 않는다."""
    home = tmp_path / "node-home"
    home.mkdir()
    dest = home / "scripts" / "payload.py"
    dest.parent.mkdir()
    old_content = "old version\n"
    dest.write_text(old_content, encoding="utf-8")
    old_inode = dest.stat().st_ino
    source = tmp_path / "new_payload.py"
    source.write_text("print('v2')\n", encoding="utf-8")

    stub = tmp_path / "stub.sh"
    stub.write_text(
        f'''run_agent() {{
  HOME="{home}" bash -c "$1"
}}
''',
        encoding="utf-8",
    )
    script = f'source "{stub}"; source "{_HELPER}"; push_file "{source}" "scripts/payload.py"'
    result = subprocess.run(("bash", "-c", script), capture_output=True, text=True, check=False)

    assert result.returncode == 0
    assert dest.read_text(encoding="utf-8") == "print('v2')\n"
    assert dest.stat().st_ino != old_inode, "목적지가 제자리에서 덮어써졌다 — rename 으로 교체돼야 한다"
    assert dest.stat().st_mode & 0o777 == 0o600
    assert not any(p.name.startswith("payload.py.deploy-tmp.") for p in home.glob("**/*"))


def test_a_push_that_writes_nothing_leaves_the_old_file_intact(tmp_path: Path) -> None:
    """stdin이 버려지면 rc != 0, 목적지의 기존 내용이 그대로 남고 temp 파일이 없다."""
    home = tmp_path / "node-home"
    home.mkdir()
    dest = home / "scripts" / "payload.py"
    dest.parent.mkdir()
    old_content = "old version\n"
    dest.write_text(old_content, encoding="utf-8")
    source = tmp_path / "new_payload.py"
    source.write_text("print('v2')\n", encoding="utf-8")

    stub = tmp_path / "stub.sh"
    stub.write_text(
        f'''run_agent() {{
  HOME="{home}" bash -c "$1" < /dev/null
}}
''',
        encoding="utf-8",
    )
    script = f'source "{stub}"; source "{_HELPER}"; push_file "{source}" "scripts/payload.py"'
    result = subprocess.run(("bash", "-c", script), capture_output=True, text=True, check=False)

    assert result.returncode != 0
    assert "DEPLOY-BLOCK" in result.stderr
    assert dest.read_text(encoding="utf-8") == old_content
    assert not any(p.name.startswith("payload.py.deploy-tmp.") for p in home.glob("**/*"))


def test_a_truncated_push_leaves_the_old_file_intact(tmp_path: Path) -> None:
    """전송이 중간에 끊겨 앞 절반만 도착해도 목적지는 옛 바이트 그대로이고 임시 파일이 없다."""
    home = tmp_path / "node-home"
    home.mkdir()
    dest = home / "scripts" / "payload.py"
    dest.parent.mkdir()
    _ = dest.write_text("old version\n", encoding="utf-8")
    source = tmp_path / "payload_new.py"
    _ = source.write_text("print('v2')\n" * 50, encoding="utf-8")

    stub = tmp_path / "stub.sh"
    _ = stub.write_text(
        "run_agent() {\n"
        f'  head -c 100 | HOME="{home}" bash -c "$1"\n'
        "}\n",
        encoding="utf-8",
    )
    script = f'source "{stub}"; source "{_HELPER}"; push_file "{source}" "scripts/payload.py"'
    result = subprocess.run(("bash", "-c", script), capture_output=True, text=True, check=False)

    assert result.returncode != 0
    assert dest.read_text(encoding="utf-8") == "old version\n"
    assert not any(".deploy-tmp." in p.name for p in home.glob("**/*"))


def _push(tmp_path: Path, destination: str, *, bin_dir: Path | None = None) -> tuple[
    subprocess.CompletedProcess[str], Path, Path
]:
    """가짜 노드(HOME)로 push_file 을 돌리고 run_agent 호출 흔적 파일을 함께 돌려준다."""
    home = tmp_path / "node-home"
    (home / "scripts").mkdir(parents=True, exist_ok=True)
    calls = tmp_path / "calls.log"
    source = tmp_path / "new_payload.py"
    _ = source.write_text("print('v2')\n", encoding="utf-8")
    path = f'PATH="{bin_dir}:$PATH" ' if bin_dir else ""
    stub = tmp_path / "stub.sh"
    _ = stub.write_text(
        "run_agent() {\n"
        f'  echo called >> "{calls}"\n'
        f'  {path}HOME="{home}" bash -c "$1"\n'
        "}\n",
        encoding="utf-8",
    )
    script = f'source "{stub}"; source "{_HELPER}"; push_file "{source}" "$1"'
    result = subprocess.run(
        ("bash", "-c", script, "bash", destination), capture_output=True, text=True, check=False
    )
    return result, home, calls


def test_a_destination_with_command_substitution_is_refused(tmp_path: Path) -> None:
    marker = tmp_path / "PWNED"
    result, _home, calls = _push(tmp_path, f"scripts/$(touch {marker})payload.py")

    assert result.returncode != 0
    assert "DEPLOY-BLOCK: unsafe destination" in result.stderr
    assert not marker.exists(), "목적지의 명령 치환이 원격 셸에서 실행됐다"
    assert not calls.exists(), "거부된 목적지로 run_agent 가 호출됐다"


def test_unsafe_destination_shapes_are_refused(tmp_path: Path) -> None:
    for index, destination in enumerate(
        ("../escape.py", "/abs/payload.py", "scripts/../x.py", "./x.py", "scripts/`id`.py", 'a"b.py', "a b.py")
    ):
        result, _home, calls = _push(tmp_path / f"case{index}", destination)

        assert result.returncode != 0, destination
        assert "unsafe destination" in result.stderr, destination
        assert not calls.exists(), destination


def test_a_failed_rename_removes_the_temp_file_and_keeps_old_bytes(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_mv = bin_dir / "mv"
    _ = fake_mv.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    fake_mv.chmod(0o755)
    dest = tmp_path / "node-home" / "scripts" / "payload.py"
    dest.parent.mkdir(parents=True)
    _ = dest.write_text("old version\n", encoding="utf-8")

    result, home, _calls = _push(tmp_path, "scripts/payload.py", bin_dir=bin_dir)

    assert result.returncode != 0
    assert dest.read_text(encoding="utf-8") == "old version\n"
    assert not any(".deploy-tmp." in p.name for p in home.glob("**/*"))


# --- 모든 deploy.sh 가 검증된 구현을 쓰는가 -------------------------------------------


def test_every_deploy_script_uses_the_verified_helper() -> None:
    """사본이 11개였기 때문에 이 결함이 11곳에 있었다 — 다시 갈라지지 않게 고정한다."""
    offenders: list[str] = []
    for relative in _DEPLOY_SCRIPTS:
        text = (_REPO / relative).read_text(encoding="utf-8")
        if "deploy_push.sh" not in text or "push_file() {" in text:
            offenders.append(relative)

    assert not offenders, (
        "이 배포 스크립트들이 검증되지 않은 자체 push_file 을 쓴다 — "
        f"automation/deploy_push.sh 를 source 하도록 바꾼다: {offenders}"
    )


def test_the_helper_reads_back_with_its_own_stdin() -> None:
    """read-back 이 호출자의 stdin 을 먹으면 그 다음 push 가 빈 파일을 쓴다."""
    text = _HELPER.read_text(encoding="utf-8")

    assert "/dev/null" in text, "read-back 은 stdin 을 /dev/null 로 막아야 한다"
    assert "sha256sum" in text
