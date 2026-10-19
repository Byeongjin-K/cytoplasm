"""Retirement effects run only in temporary homes with injected system commands."""
from __future__ import annotations

import hashlib
import shlex
import subprocess
from pathlib import Path

import pytest

from automation import gateway_retirement as retirement

HEAD = "a" * 40
CONFIG = (
    b"model:\n  provider: openai-codex\n  default: main-model\n"
    b"providers:\n  keep: {}\n  litellm:\n    base_url: http://127.0.0.1:4000\n    key_env: LITELLM_AGENT_KEY\n"
    b"custom_providers:\n  - name: litellm\n    base_url: http://127.0.0.1:4000/v1\n    key_env: LITELLM_AGENT_KEY\n"
    b"  - name: keep\n    base_url: http://example.invalid\n"
    b"fallback_providers:\n  - provider: xai-oauth\n# keep trailing bytes"
)
SECRET = b"# keep\r\nKEEP='private-value'\r\nexport LITELLM_AGENT_KEY=private-secret\r\nLITELLM_OTHER=x\nTAIL=yes"


def _home(tmp_path: Path) -> Path:
    for relative, content in (
        (".hermes/config.yaml", CONFIG), (".env.secrets", SECRET),
        (".hermes/scripts/send_cost_report.py", b"old"),
        (".config/systemd/user/litellm-gateway.service", b"old"),
        (".config/systemd/user/autophagy-cron-daily-cost-report.timer", b"old"),
        (".config/systemd/user/autophagy-cron-daily-cost-report.service", b"old"),
        ("litellm-gateway/.env", b"private-secret"),
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod(0o640 if relative == ".env.secrets" else 0o600)
    folder = tmp_path / retirement.BACKUP
    folder.mkdir(parents=True, mode=0o700)
    entries = []
    for name in retirement.BACKUP_NAMES:
        content = name.encode()
        (folder / name).write_bytes(content)
        entries.append(f"{hashlib.sha256(content).hexdigest()}  {name}\n")
    (folder / "SHA256SUMS").write_text("".join(entries))
    return tmp_path


def _runner(calls: list[tuple[str, ...]], fault: str = "") -> retirement.Runner:
    def execute(argv: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        output, code = "", 0
        if argv[0] == "sh":
            return subprocess.run(argv, capture_output=True, text=True, check=False, timeout=5)
        if argv[:2] == ("systemctl", "--user"):
            if argv[2] == "is-active":
                output, code = ("active", 0) if fault == "active" else ("inactive", 3)
            elif argv[2] == "is-enabled":
                output, code = ("enabled", 0) if fault == "enabled" else ("disabled", 1)
            elif fault == "reload":
                code = 1
        elif argv[0] == "ss":
            output = "listener" if fault == "listener" else ""
        elif argv[:3] == ("sg", "docker", "-c"):
            args = shlex.split(argv[3])
            if args[:2] == ["docker", "ps"]:
                output = "container" if fault == "running" or (fault == "used" and "-a" in args) else ""
            elif args[:3] == ["docker", "volume", "ls"]:
                output = "\n".join((*retirement.VOLUMES, "unrelated"))
            elif args[:3] == ["docker", "image", "ls"]:
                output = "\n".join((*retirement.IMAGES, "unrelated:image"))
            elif fault == "docker":
                code = 1
        else:
            pytest.fail(f"unexpected command: {argv}")
        return subprocess.CompletedProcess(argv, code, stdout=output, stderr="private-error")
    return execute


def test_ops_removes_only_listed_resources_and_marks_last(tmp_path: Path) -> None:
    home = _home(tmp_path)
    calls: list[tuple[str, ...]] = []
    retirement.remove(home, "ops", HEAD, runner=_runner(calls))
    removed = [shlex.split(argv[3]) for argv in calls if argv[0] == "sg" and " rm " in argv[3]]
    assert removed == [
        *[["docker", "volume", "rm", name] for name in retirement.VOLUMES],
        *[["docker", "image", "rm", name] for name in retirement.IMAGES],
    ]
    assert not (home / "litellm-gateway").exists()
    assert not (home / ".config/systemd/user/litellm-gateway.service").exists()
    assert (home / ".env.secrets").read_bytes() == SECRET
    assert all((home / retirement.BACKUP / name).exists() for name in retirement.BACKUP_NAMES)
    assert (home / retirement.MARKER).read_text() == HEAD


@pytest.mark.parametrize("fault", ["active", "enabled", "listener", "running"])
def test_live_usage_refuses_before_first_mutation(tmp_path: Path, fault: str) -> None:
    home = _home(tmp_path)
    calls: list[tuple[str, ...]] = []
    with pytest.raises(ValueError, match="GATEWAY-RETIRE-REFUSED"):
        retirement.remove(home, "ops", HEAD, runner=_runner(calls, fault))
    assert (home / "litellm-gateway/.env").read_bytes() == b"private-secret"
    assert (home / ".config/systemd/user/litellm-gateway.service").exists()
    assert not (home / retirement.MARKER).exists()
    assert not any(" rm " in " ".join(call) for call in calls)


@pytest.mark.parametrize("fault", ["missing", "corrupt", "incomplete", "traversal"])
def test_backup_must_exist_cover_all_files_and_verify(tmp_path: Path, fault: str) -> None:
    home = _home(tmp_path)
    folder = home / retirement.BACKUP
    if fault == "missing":
        (folder / retirement.BACKUP_NAMES[0]).unlink()
    elif fault == "corrupt":
        (folder / retirement.BACKUP_NAMES[0]).write_bytes(b"corrupt")
    elif fault == "incomplete":
        (folder / "SHA256SUMS").write_text("")
    else:
        (folder / "SHA256SUMS").write_text("0" * 64 + "  ../outside\n")
    calls: list[tuple[str, ...]] = []
    with pytest.raises((ValueError, OSError)):
        retirement.remove(home, "ops", HEAD, runner=_runner(calls))
    assert (home / "litellm-gateway").exists()
    assert not (home / retirement.MARKER).exists()
    assert all(call[0] == "sh" for call in calls)


def test_any_state_container_keeps_shared_images(tmp_path: Path) -> None:
    home = _home(tmp_path)
    calls: list[tuple[str, ...]] = []
    retirement.remove(home, "ops", HEAD, runner=_runner(calls, "used"))
    assert not any("docker image rm" in call[-1] for call in calls)
    assert (home / retirement.MARKER).exists()


def test_agent_preserves_other_bytes_permissions_and_accounts(tmp_path: Path) -> None:
    home = _home(tmp_path)
    calls: list[tuple[str, ...]] = []
    retirement.remove(home, "agent", HEAD, runner=_runner(calls))
    expected = CONFIG.replace(
        b"  litellm:\n    base_url: http://127.0.0.1:4000\n    key_env: LITELLM_AGENT_KEY\n", b"",
    ).replace(b"  - name: litellm\n    base_url: http://127.0.0.1:4000/v1\n    key_env: LITELLM_AGENT_KEY\n", b"")
    assert (home / ".hermes/config.yaml").read_bytes() == expected
    assert (home / ".env.secrets").read_bytes() == b"# keep\r\nKEEP='private-value'\r\nTAIL=yes"
    assert (home / ".env.secrets").stat().st_mode & 0o777 == 0o640
    assert not (home / ".hermes/scripts/send_cost_report.py").exists()
    assert not list((home / ".config/systemd/user").glob("*daily-cost-report*"))
    assert (home / "litellm-gateway/.env").read_bytes() == b"private-secret"
    assert calls[-1] == ("systemctl", "--user", "daemon-reload")


@pytest.mark.parametrize("replacement", [
    b"    key_env: OTHER_KEY", b"    key_env: LITELLM_AGENT_KEY\n    extra: x",
    b"    key_env: LITELLM_AGENT_KEY\n  litellm: {}", b"  bad: structure",
])
def test_unexpected_config_refuses_without_changing_any_file(tmp_path: Path, replacement: bytes) -> None:
    home = _home(tmp_path)
    path = home / ".hermes/config.yaml"
    data = CONFIG.replace(b"    key_env: LITELLM_AGENT_KEY", replacement, 1)
    path.write_bytes(data)
    calls: list[tuple[str, ...]] = []
    with pytest.raises(ValueError, match="GATEWAY-RETIRE-REFUSED"):
        retirement.remove(home, "agent", HEAD, runner=_runner(calls))
    assert path.read_bytes() == data
    assert (home / ".env.secrets").read_bytes() == SECRET
    assert (home / ".hermes/scripts/send_cost_report.py").exists()
    assert calls == []


def test_indentless_list_and_crlf_keep_surrounding_config(tmp_path: Path) -> None:
    home = _home(tmp_path)
    original = CONFIG.replace(b"  - name:", b"- name:")
    # Only sequence children change indentation; provider mapping stays as declared.
    mapping, sequence = original.split(b"custom_providers:\n")
    original = mapping + b"custom_providers:\n" + sequence.replace(b"    ", b"  ")
    original = original.replace(b"\n", b"\r\n")
    path = home / ".hermes/config.yaml"
    path.write_bytes(original)
    retirement.remove(home, "agent", HEAD, runner=_runner([]))
    result = path.read_bytes()
    assert b"litellm" not in result
    assert b"\n" not in result.replace(b"\r\n", b"")
    assert result.endswith(b"# keep trailing bytes")


@pytest.mark.parametrize("role", ["ops", "agent"])
def test_completion_marker_makes_next_release_a_noop(tmp_path: Path, role: str) -> None:
    home = _home(tmp_path)
    retirement.remove(home, role, HEAD, runner=_runner([]))
    calls: list[tuple[str, ...]] = []
    retirement.remove(home, role, "b" * 40, runner=_runner(calls))
    assert calls == []
    assert (home / retirement.MARKER).read_text() == HEAD


def test_partial_agent_failure_resumes_after_atomic_edit(tmp_path: Path) -> None:
    home = _home(tmp_path)
    with pytest.raises(ValueError):
        retirement.remove(home, "agent", HEAD, runner=_runner([], "reload"))
    assert not (home / retirement.MARKER).exists()
    retirement.remove(home, "agent", HEAD, runner=_runner([]))
    assert (home / retirement.MARKER).exists()


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_singleton_provider_collections_remain_typed_and_retryable(newline: bytes) -> None:
    data = (
        b"model:\n  provider: openai-codex\n"
        b"providers:\n  litellm:\n    base_url: http://127.0.0.1:4000\n    key_env: LITELLM_AGENT_KEY\n"
        b"custom_providers:\n- name: litellm\n  base_url: http://127.0.0.1:4000/v1\n  key_env: LITELLM_AGENT_KEY\n"
        b"fallback_providers:\n  - provider: xai-oauth\n"
    ).replace(b"\n", newline)
    expected = (
        b"model:\n  provider: openai-codex\nproviders: {}\ncustom_providers: []\n"
        b"fallback_providers:\n  - provider: xai-oauth\n"
    ).replace(b"\n", newline)
    result = retirement.config_without_gateway(data)
    assert result == expected
    assert retirement.config_without_gateway(result) == expected


@pytest.mark.parametrize("relative", [".env.secrets", ".hermes", "litellm-gateway", retirement.BACKUP])
def test_symlinks_are_never_followed(tmp_path: Path, relative: str) -> None:
    home = _home(tmp_path / "home")
    path = home / relative
    original = path.with_name(path.name + "-original")
    path.rename(original)
    path.symlink_to(original)
    role = "agent" if relative in (".env.secrets", ".hermes") else "ops"
    with pytest.raises(ValueError, match="링크"):
        retirement.remove(home, role, HEAD, runner=_runner([]))
    assert original.exists()
    assert not (home / retirement.MARKER).exists()
