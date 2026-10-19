"""레거시 import 루트 진단(`automation/interop/pin_import_root.sh`, RCB todo 22).

자동 시작 드롭인은 은퇴했다. 명시적으로 실행하는 진단 유틸리티는 현재 릴리스 세대의
항목별 심링크 묶음을 만든다. 여기서 증명하는 불변식:
  P1 순서 — 링크는 묶음이 다 만들어진 뒤에만, 한 번의 rename 으로 바뀐다.
  P2 실패 복귀 — 게이트 코드 닫힘이 하나라도 없으면 exit 78 이고 기존 링크·묶음은 그대로다.
  P3 가림 — 묶음은 `hermes_compat`·`__pycache__` 를 싣지 않는다. 호환 패치 캐리어의
     `automation.hermes_compat` 은 계속 캐리어에서 읽힌다.
  P4 보존 — 같은 세대로 다시 고정하면 아무것도 바뀌지 않고, 새 세대로 고정해도 앞선 묶음은 남는다.
  P5 같은 규칙 — 배포기의 사전 import 시험이 만드는 묶음과 필수 파일 목록이 이 스크립트와 같다.
"""
from __future__ import annotations

import fcntl
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

from automation.interop import gateway_preflight

_REPO: Final = Path(__file__).resolve().parents[2]
_PIN: Final = _REPO / "automation/interop/pin_import_root.sh"
_DROPIN: Final = _REPO / "automation/interop/gateway-dropin.conf"
_ROOT_VARIABLES: Final = ("AUTOPHAGY_RUNTIME_ROOT", "PIN_RELEASE_POINTER", "PIN_MIRROR_ROOT", "PYTHONPATH")


def _env(home: Path, **extra: str) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in _ROOT_VARIABLES}
    env.update(HOME=str(home), PIN_RELEASE_POINTER=str(home / "absent-current"),
               PIN_MIRROR_ROOT=str(home / "absent-mirror"))
    env.update(extra)
    return env


def _pin(home: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(_PIN)], env=_env(home, **extra), capture_output=True,
                          text=True, check=False, timeout=60)


def _fake_generation(root: Path, name: str, *, skip: str = "") -> Path:
    gen = root / name
    for relative in (*gateway_preflight.REQUIRED, "automation/owner_notice.py",
                     "automation/hermes_compat/__init__.py", "automation/__pycache__/x.pyc"):
        if relative == skip:
            continue
        target = gen / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        _ = target.write_text(f"# {name}\n", encoding="utf-8")
    return gen


def _state(home: Path) -> tuple[str, tuple[str, ...]]:
    link = home / ".hermes/autophagy-import"
    store = home / ".hermes/autophagy-import.d"
    target = os.readlink(link) if link.is_symlink() else "-"
    entries = tuple(sorted(str(p.relative_to(store)) for p in store.rglob("*"))) if store.exists() else ()
    return target, entries


def test_the_farm_exposes_automation_without_the_compat_package(tmp_path: Path) -> None:
    (tmp_path / "current").symlink_to(_REPO)
    result = _pin(tmp_path, PIN_RELEASE_POINTER=str(tmp_path / "current"))
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["PIN-IMPORT-ROOT-OK", _REPO.name]
    farm = tmp_path / ".hermes/autophagy-import"
    assert os.path.realpath(farm) == str(tmp_path / ".hermes/autophagy-import.d" / _REPO.name)
    env = {**_env(tmp_path), "PYTHONPATH": str(farm)}
    ok = subprocess.run([sys.executable, "-B", "-c", "import automation.interop.approval_types"],
                        env=env, cwd=tmp_path, capture_output=True, text=True, check=False)
    assert ok.returncode == 0, ok.stderr
    shadow = subprocess.run([sys.executable, "-B", "-c", "import automation.hermes_compat"],
                            env=env, cwd=tmp_path, capture_output=True, text=True, check=False)
    assert shadow.returncode == 1 and "ModuleNotFoundError" in shadow.stderr
    names = sorted(p.name for p in (farm / "automation").iterdir())
    assert "hermes_compat" not in names and "__pycache__" not in names
    assert all((farm / "automation" / n).is_symlink() for n in names)


def test_with_the_compat_carrier_hermes_compat_still_comes_from_the_carrier(tmp_path: Path) -> None:
    gen = _fake_generation(tmp_path / "releases", "gen-a")
    carrier = tmp_path / ".hermes/hermes-compat"
    (carrier / "automation/hermes_compat").mkdir(parents=True)
    _ = (carrier / "automation/hermes_compat/__init__.py").write_text("WHERE = 'carrier'\n", encoding="utf-8")
    _ = shutil.copyfile(_REPO / "automation/hermes_compat/hermes_compat_boot.py", carrier / "hermes_compat_boot.py")
    assert _pin(tmp_path, AUTOPHAGY_RUNTIME_ROOT=str(gen)).returncode == 0
    probe = ("import hermes_compat_boot, automation, automation.hermes_compat as c, os;"
             "print(c.WHERE, os.path.realpath(automation.__file__))")
    env = {**_env(tmp_path), "PYTHONPATH": f"{tmp_path / '.hermes/autophagy-import'}:{carrier}"}
    out = subprocess.run([sys.executable, "-B", "-c", probe], env=env, cwd=tmp_path,
                         capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["carrier", str(gen / "automation/__init__.py")]


@pytest.mark.parametrize("missing", gateway_preflight.REQUIRED)
def test_missing_gate_code_blocks_the_pin_and_keeps_the_old_link(tmp_path: Path, missing: str) -> None:
    old = _fake_generation(tmp_path / "releases", "gen-old")
    assert _pin(tmp_path, AUTOPHAGY_RUNTIME_ROOT=str(old)).returncode == 0
    before = _state(tmp_path)
    broken = _fake_generation(tmp_path / "releases", "gen-new", skip=missing)
    result = _pin(tmp_path, AUTOPHAGY_RUNTIME_ROOT=str(broken))
    assert result.returncode == 78
    assert result.stderr.startswith("PIN-IMPORT-ROOT-BLOCK: ")
    assert str(broken / missing) in result.stderr
    assert _state(tmp_path) == before


def test_a_missing_release_pointer_pins_the_mirror_with_a_warning(tmp_path: Path) -> None:
    mirror = _fake_generation(tmp_path, "mirror")
    result = _pin(tmp_path, PIN_MIRROR_ROOT=str(mirror))
    assert result.returncode == 0, result.stderr
    assert "PIN-IMPORT-ROOT-WARN" in result.stderr
    assert os.path.realpath(tmp_path / ".hermes/autophagy-import/automation/__init__.py") == str(
        mirror / "automation/__init__.py")


def test_a_present_release_pointer_wins_over_the_mirror(tmp_path: Path) -> None:
    release = _fake_generation(tmp_path / "releases", "gen-r")
    (tmp_path / "current").symlink_to(release)
    mirror = _fake_generation(tmp_path, "mirror")
    result = _pin(tmp_path, PIN_RELEASE_POINTER=str(tmp_path / "current"), PIN_MIRROR_ROOT=str(mirror))
    assert result.returncode == 0 and "PIN-IMPORT-ROOT-WARN" not in result.stderr
    assert os.readlink(tmp_path / ".hermes/autophagy-import") == "autophagy-import.d/gen-r"


def test_repinning_is_idempotent_and_keeps_earlier_farms(tmp_path: Path) -> None:
    first = _fake_generation(tmp_path / "releases", "gen-a")
    assert _pin(tmp_path, AUTOPHAGY_RUNTIME_ROOT=str(first)).returncode == 0
    link = tmp_path / ".hermes/autophagy-import"
    inode = link.lstat().st_ino
    before = _state(tmp_path)
    again = _pin(tmp_path, AUTOPHAGY_RUNTIME_ROOT=str(first))
    assert again.returncode == 0 and _state(tmp_path) == before
    assert link.lstat().st_ino == inode
    second = _fake_generation(tmp_path / "releases", "gen-b")
    assert _pin(tmp_path, AUTOPHAGY_RUNTIME_ROOT=str(second)).returncode == 0
    target, entries = _state(tmp_path)
    assert target == "autophagy-import.d/gen-b"
    assert set(before[1]) <= set(entries)
    assert [e for e in entries if e.startswith(".")] == [".lock"]


def test_a_changed_tree_under_the_same_name_gets_a_new_farm(tmp_path: Path) -> None:
    mirror = _fake_generation(tmp_path, "mirror")
    assert _pin(tmp_path, PIN_MIRROR_ROOT=str(mirror)).returncode == 0
    old_farm = sorted(p.name for p in (tmp_path / ".hermes/autophagy-import.d/mirror/automation").iterdir())
    _ = (mirror / "automation/added.py").write_text("x = 1\n", encoding="utf-8")
    result = _pin(tmp_path, PIN_MIRROR_ROOT=str(mirror))
    assert result.returncode == 0, result.stderr
    target = os.readlink(tmp_path / ".hermes/autophagy-import")
    assert target != "autophagy-import.d/mirror" and target.startswith("autophagy-import.d/mirror~")
    assert (tmp_path / ".hermes" / target / "automation/added.py").is_symlink()
    assert sorted(p.name for p in (tmp_path / ".hermes/autophagy-import.d/mirror/automation").iterdir()) == old_farm


def test_a_real_directory_at_the_import_root_is_never_replaced(tmp_path: Path) -> None:
    gen = _fake_generation(tmp_path / "releases", "gen-a")
    real = tmp_path / ".hermes/autophagy-import"
    real.mkdir(parents=True)
    _ = (real / "keep").write_text("mine\n", encoding="utf-8")
    result = _pin(tmp_path, AUTOPHAGY_RUNTIME_ROOT=str(gen))
    assert result.returncode == 78 and result.stderr.startswith("PIN-IMPORT-ROOT-BLOCK: ")
    assert real.is_dir() and not real.is_symlink() and (real / "keep").read_text() == "mine\n"


def test_a_held_lock_blocks_without_touching_the_link(tmp_path: Path) -> None:
    old = _fake_generation(tmp_path / "releases", "gen-old")
    assert _pin(tmp_path, AUTOPHAGY_RUNTIME_ROOT=str(old)).returncode == 0
    before = _state(tmp_path)
    new = _fake_generation(tmp_path / "releases", "gen-new")
    with (tmp_path / ".hermes/autophagy-import.d/.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        result = _pin(tmp_path, AUTOPHAGY_RUNTIME_ROOT=str(new), PIN_LOCK_WAIT="0")
    assert result.returncode == 78 and result.stderr.startswith("PIN-IMPORT-ROOT-BLOCK: ")
    assert _state(tmp_path) == before


def test_the_preflight_builds_the_same_farm(tmp_path: Path) -> None:
    gen = _fake_generation(tmp_path / "releases", "gen-a")
    assert _pin(tmp_path, AUTOPHAGY_RUNTIME_ROOT=str(gen)).returncode == 0
    pinned = tmp_path / ".hermes/autophagy-import/automation"
    built = tmp_path / "preflight-farm"
    gateway_preflight.build_farm(gen, built)
    def listing(root: Path) -> list[tuple[str, str]]:
        return sorted((p.name, os.readlink(p)) for p in root.iterdir())
    assert listing(built / "automation") == listing(pinned)
    assert gateway_preflight.missing_closure(gen) == []
    assert gateway_preflight.resolve_root({"AUTOPHAGY_RUNTIME_ROOT": str(gen)}) == gen


def test_the_hermes_startup_dropin_is_retired() -> None:
    # Given the retired Hermes gateway hook package.
    # When enumerating startup hooks that a new install could copy.
    # Then no interop startup drop-in remains installable.
    assert not _DROPIN.exists()
