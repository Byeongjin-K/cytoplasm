"""mailon 런타임이 옛 릴리스에 고정된 것을 알아채는가.

2026-07-29 에 커밋된 vendor 수정이 프로덕션에 도달한 것은 08-18 이다 — **19일**. 그동안
발송은 멀쩡히 동작했으므로 이것은 「중단」이 아니라 **고정**이고, 비정상이라는 신호가 어디에도
없었다. 스킬은 `readlink live/<skill>` 로 판정하고 코드는 리컨실러가 따라오지만 vendor
런타임은 둘 다 아니다. 더 나쁜 것은 마침내 배포한 순간 19일치 미검증 변경이 한꺼번에
올라간다는 점이다 — 실제로 결함 2건이 동시에 올라와 모든 발송이 즉시 실패했다.

여기서 만드는 것은 탐지뿐이다. 배포도 재시동도 하지 않는다.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "skills" / "mail" / "scripts"
_DRIFT = _SCRIPTS / "mailon_runtime_drift.sh"
_DIGEST_HELPER = _SCRIPTS / "mailon_vendor_digest.sh"


def _digest(tree: Path) -> str:
    result = subprocess.run(
        ["bash", "-c", f'. "{_DIGEST_HELPER}"; mailon_vendor_digest "{tree}"'],
        capture_output=True, text=True, check=True, timeout=60,
    )
    return result.stdout.strip()


def _vendor(root: Path, body: str = "x = 1\n") -> Path:
    tree = root / "skills" / "mail" / "vendor" / "mailon"
    tree.mkdir(parents=True)
    (tree / "main.py").write_text(body, encoding="utf-8")
    (tree / "config.py").write_text("PROJECT_ROOT = None\n", encoding="utf-8")
    return tree


def _runtime(root: Path, digest: str) -> Path:
    release = root / "releases" / digest
    release.mkdir(parents=True)
    (root / "current").symlink_to(release)
    return root


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True,
        check=True, timeout=60,
    )
    return result.stdout.strip()


def _commit(root: Path, message: str) -> str:
    _git(root, "add", ".")
    _git(root, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _git_fixture(tmp_path: Path) -> tuple[Path, Path, str, str, str, str]:
    release_root = tmp_path / "release"
    vendor = _vendor(release_root, body="x = 1\n")
    _git(release_root, "init", "-q")
    old_commit = _commit(release_root, "old")
    old_digest = _digest(vendor)
    (vendor / "main.py").write_text("x = 2\n", encoding="utf-8")
    new_commit = _commit(release_root, "new")
    new_digest = _digest(vendor)
    runtime = tmp_path / "home" / ".hermes" / "mailon-runtime"
    return release_root, runtime, old_commit, new_commit, old_digest, new_digest


def _run(release_root: Path, runtime_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(_DRIFT)],
        capture_output=True, text=True, check=False, timeout=60,
        env={
            "PATH": "/usr/bin:/bin", "HOME": str(runtime_root.parent),
            "AUTOPHAGY_REPO_ROOT": str(release_root),
            "MAILON_RUNTIME_ROOT": str(runtime_root),
        },
    )


def test_the_digest_does_not_depend_on_where_the_tree_lives(tmp_path: Path) -> None:
    # Given: byte-identical vendor trees unpacked at two different paths.
    first = _vendor(tmp_path / "a")
    second = _vendor(tmp_path / "b")

    # Then: they digest the same. Otherwise a probe comparing a repo tree against a
    # deployed tree can never agree — the release script used to fold the absolute
    # directory name into the hash, so its "content digest" was not content-addressed.
    assert _digest(first) == _digest(second)
    assert len(_digest(first)) == 16


def test_a_current_runtime_is_quiet(tmp_path: Path) -> None:
    release_root = tmp_path / "release"
    vendor = _vendor(release_root)
    runtime = _runtime(tmp_path / "home" / ".hermes" / "mailon-runtime", _digest(vendor))

    result = _run(release_root, runtime)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_a_pinned_old_runtime_is_reported_with_both_digests(tmp_path: Path) -> None:
    release_root = tmp_path / "release"
    vendor = _vendor(release_root, body="x = 2  # the fix that never shipped\n")
    runtime = _runtime(tmp_path / "home" / ".hermes" / "mailon-runtime", "0123456789abcdef")

    result = _run(release_root, runtime)

    # Then: the 19-day silence becomes one line naming what runs and what should.
    assert result.returncode == 1, result.stdout + result.stderr
    assert "DRIFT" in result.stdout
    assert "0123456789abcdef" in result.stdout
    assert _digest(vendor) in result.stdout


def test_runtime_behind_release_recommends_deploy(tmp_path: Path) -> None:
    release_root, runtime, old_commit, _new_commit, old_digest, _new_digest = _git_fixture(tmp_path)
    _runtime(runtime, old_digest)
    result = _run(release_root, runtime)
    assert result.returncode == 1
    assert "skills/mail/deploy.sh" in result.stdout


def test_runtime_ahead_of_release_recommends_release(tmp_path: Path) -> None:
    release_root, runtime, old_commit, new_commit, old_digest, new_digest = _git_fixture(tmp_path)
    (release_root / "skills/mail/vendor/mailon/main.py").write_text("x = 1\n", encoding="utf-8")
    _runtime(runtime, new_digest)
    result = _run(release_root, runtime)
    assert result.returncode == 1
    assert "automation/release.sh" in result.stdout
    assert "skills/mail/deploy.sh" not in result.stdout


def test_runtime_and_release_with_unknown_direction_show_both(tmp_path: Path) -> None:
    release_root, runtime, _old_commit, _new_commit, _old_digest, _new_digest = _git_fixture(tmp_path)
    (release_root / "skills/mail/vendor/mailon/main.py").write_text("x = 3\n", encoding="utf-8")
    _runtime(runtime, "deadbeefdeadbeef")
    result = _run(release_root, runtime)
    assert result.returncode == 1
    assert "direction=unknown" in result.stdout
    assert "skills/mail/deploy.sh" in result.stdout
    assert "automation/release.sh" in result.stdout


def test_a_missing_runtime_is_unknown_not_a_pass(tmp_path: Path) -> None:
    release_root = tmp_path / "release"
    _vendor(release_root)
    runtime = tmp_path / "home" / ".hermes" / "mailon-runtime"
    runtime.mkdir(parents=True)

    result = _run(release_root, runtime)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout


def test_a_dangling_current_symlink_is_unknown(tmp_path: Path) -> None:
    release_root = tmp_path / "release"
    _vendor(release_root)
    runtime = tmp_path / "home" / ".hermes" / "mailon-runtime"
    runtime.mkdir(parents=True)
    (runtime / "current").symlink_to(runtime / "releases" / "gone")

    result = _run(release_root, runtime)

    # Then: a broken link is damage, not absence, and neither is a pass.
    assert result.returncode == 2, result.stdout + result.stderr


def test_a_missing_vendor_tree_is_unknown(tmp_path: Path) -> None:
    release_root = tmp_path / "release"
    release_root.mkdir()
    runtime = _runtime(tmp_path / "home" / ".hermes" / "mailon-runtime", "deadbeefdeadbeef")

    result = _run(release_root, runtime)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout


@pytest.mark.parametrize(
    "script", ("mailon_runtime_release.sh", "mailon_runtime_drift.sh")
)
def test_both_sides_use_the_one_shared_digest_helper(script: str) -> None:
    source = (_SCRIPTS / script).read_text(encoding="utf-8")

    # Then: neither side keeps its own copy of the formula. A drift detector whose
    # digest can drift from the producer's is worse than none.
    assert "mailon_vendor_digest" in source, f"{script} 가 공용 digest 헬퍼를 쓰지 않는다"


def test_the_deploy_script_carries_the_drift_probe_and_its_helper() -> None:
    deploy = (_REPO / "skills" / "mail" / "deploy.sh").read_text(encoding="utf-8")

    for name in ("mailon_runtime_drift.sh", "mailon_vendor_digest.sh"):
        assert name in deploy, f"{name} 를 어떤 배포 경로도 싣지 않는다 — 「커밋됨 ≠ 배포됨」"



def test_doctor_scheduled_jobs_replaces_digest_drift_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실제 드리프트 프로브를 doctor가 읽고 옛 다이제스트 장부는 호출하지 않는다."""
    import importlib.util
    import sys
    from datetime import UTC, datetime

    from automation.doctor.scheduled import observe
    from automation.install.checks import Status

    spec = importlib.util.spec_from_file_location(
        "mail_digest_watch_drift", _SCRIPTS / "mail_digest_watch.py"
    )
    assert spec is not None and spec.loader is not None
    watch = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = watch
    spec.loader.exec_module(watch)
    monkeypatch.setenv("WATCH_FAILURE_ROOT", str(tmp_path / "streak"))

    release = tmp_path / "release"
    vendor = _vendor(release)
    home = tmp_path / "home"
    runtime = _runtime(home / ".hermes/mailon-runtime", "0123456789abcdef")
    script = release / "skills/mail/scripts/mailon_runtime_drift.sh"
    script.parent.mkdir(parents=True)
    script.symlink_to(_DRIFT)
    (script.parent / "mailon_vendor_digest.sh").symlink_to(_DIGEST_HELPER)

    def run(argv: tuple[str, ...], environment: dict[str, str]) -> tuple[int, str]:
        result = subprocess.run(
            argv, env=environment, capture_output=True, text=True, timeout=20, check=False,
        )
        return result.returncode, result.stdout

    checks = observe(home, release, run, datetime(2026, 10, 18, tzinfo=UTC))
    assert [(check.name, check.status) for check in checks] == [("mailon-runtime-drift", Status.FAIL)]
    (runtime / "current").unlink()
    _runtime(runtime, _digest(vendor))
    assert observe(home, release, run, datetime(2026, 10, 18, tzinfo=UTC)) == ()
    assert not hasattr(watch, "_report_runtime_drift")
    assert not (tmp_path / "streak").exists()
