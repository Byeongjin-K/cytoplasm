"""RC-1: 계정 홈 배포물 선언(<package>/deploy-manifest.txt)과 파생 중앙 표의 conformance.

새 파일인 이유: `test_watcher_drift_probe.py` 는 프로브의 판정과 중앙 표를 검사한다 —
여기는 그 표의 **원천**(패키지별 선언)과 유도를 검사한다. 검사 대상이 다르고, 기존
파일에 케이스를 더하지 않는 것이 이 리포의 테스트 규약이다(tests/AGENTS.md).

이 파일이 막는 실패 모드는 하나다: **등록 누락**. 배포기를 만들고 중앙 표 등록을 잊으면
배포되지 않아도 탐지되지 않았다(2026-08-28 meeting 플러그인 5일 침묵). 이제 선언은
배포기 옆에 있어야 하고, 중앙 표는 그 선언의 파생물이며, 어느 쪽을 잊어도 여기서 깨진다.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final

import pytest

if TYPE_CHECKING:
    from automation.deploy_declarations import Declaration

_REPO: Final = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
from automation.watcher_manifest import (  # noqa: E402
    CENTRAL_MANIFEST,
    HOME_DEPLOYED_PATTERN,
    ManifestError,
    declaration_files,
    derive_manifest,
    parse_rows,
)


def _declared_rows() -> list[tuple[Path, Declaration]]:
    """legacy 행과 v2 행을 모두 — v2 file 선언도 선언이다(DEC-30)."""
    from automation.deploy_declarations import parse_declaration_file

    return [
        (declaration, row)
        for declaration in declaration_files(_REPO)
        for row in parse_declaration_file(
            declaration.relative_to(_REPO).as_posix(), declaration.read_text(encoding="utf-8")
        )
    ]


def test_the_central_manifest_is_exactly_the_derivation() -> None:
    """중앙 표는 파생물이다 — 손 편집도, 선언만 고치고 emit 을 잊는 것도 RED."""
    central = (_REPO / CENTRAL_MANIFEST).read_text(encoding="utf-8")
    assert central == derive_manifest(_REPO), (
        f"{CENTRAL_MANIFEST} 가 선언과 어긋난다 — "
        "`python3 -m automation.watcher_manifest emit` 으로 재생성하라"
    )


def test_every_home_write_in_a_deploy_script_is_declared() -> None:
    """deploy.sh 가 홈에 쓰는 모든 목적지는 어느 패키지 선언에든 있어야 한다."""
    declared = {row.destination for _, row in _declared_rows() if row.kind == "file"}
    missing: list[str] = []
    scripts = sorted(_REPO.glob("skills/*/deploy.sh")) + sorted(
        _REPO.glob("automation/*/deploy.sh")
    )
    for script in scripts:
        package = script.relative_to(_REPO).parent.as_posix()
        for written in HOME_DEPLOYED_PATTERN.findall(script.read_text(encoding="utf-8")):
            if written not in declared:
                missing.append(
                    f"{script.relative_to(_REPO)} -> {written}"
                    f" ({package}/deploy-manifest.txt 에 선언하라)"
                )
    assert not missing, "선언 없는 배포 대상: " + ", ".join(missing)


def test_every_row_lives_with_its_owning_package() -> None:
    """행은 소스를 소유한 패키지의 선언에 있어야 한다 — 재배포 명령 유도와 같은 규칙."""
    for declaration, row in _declared_rows():
        package = declaration.relative_to(_REPO).parent.as_posix()
        assert row.owner == package
        if row.legacy:
            assert "/".join(row.source.split("/")[:2]) == package, (
                f"{declaration.relative_to(_REPO)} 의 행이 남의 소스를 선언한다: {row.source}"
            )


def test_every_declared_source_exists() -> None:
    """file·cron·config 는 파일을, tree·derived 는 디렉터리를 가리킨다."""
    for declaration, row in _declared_rows():
        source = _REPO / row.source
        exists = source.is_file() if row.kind in ("file", "cron", "config") else source.is_dir()
        assert exists, f"{declaration.relative_to(_REPO)} 가 없는 소스를 가리킨다: {row.source}"


def test_destinations_are_unique_per_account() -> None:
    """(account, destination) 이 두 선언에 있으면 어느 배포기가 권위인지 모호해진다."""
    seen: dict[tuple[str, str], Path] = {}
    for declaration, row in _declared_rows():
        key = (row.account, row.destination)
        assert key not in seen, (
            f"중복 선언: {key} — {seen[key].relative_to(_REPO)} 와 "
            f"{declaration.relative_to(_REPO)}"
        )
        seen[key] = declaration


def test_derivation_is_deterministic(tmp_path: Path) -> None:
    (tmp_path / "automation" / "pkg").mkdir(parents=True)
    (tmp_path / "skills").mkdir()
    declaration = tmp_path / "automation" / "pkg" / "deploy-manifest.txt"
    _ = declaration.write_text(
        "# comment\nagent|automation/pkg/w.py|.hermes/scripts/w.py|required\n",
        encoding="utf-8",
    )
    first = derive_manifest(tmp_path)
    assert first == derive_manifest(tmp_path)
    assert "agent|automation/pkg/w.py|.hermes/scripts/w.py|required" in first
    assert "# --- automation/pkg/deploy-manifest.txt ---" in first


def test_an_empty_declaration_is_refused(tmp_path: Path) -> None:
    """빈 선언은 '선언 없음'과 구분되지 않는다 — 배포물이 사라졌으면 파일을 지운다."""
    (tmp_path / "automation" / "pkg").mkdir(parents=True)
    (tmp_path / "skills").mkdir()
    _ = (tmp_path / "automation" / "pkg" / "deploy-manifest.txt").write_text(
        "# rows were removed\n", encoding="utf-8"
    )
    with pytest.raises(ManifestError):
        _ = derive_manifest(tmp_path)


def test_a_malformed_row_is_refused() -> None:
    """파싱할 수 없는 선언은 탐지할 수 없는 배포물이다 — 조용히 건너뛰지 않는다."""
    with pytest.raises(ManifestError):
        _ = parse_rows("agent|only|three-fields")


def test_check_command_passes_on_a_synced_tree() -> None:
    """CLI 는 CI 밖(노드·훅)에서도 같은 판정을 내린다."""
    result = subprocess.run(
        (sys.executable, "-m", "automation.watcher_manifest", "check"),
        capture_output=True,
        text=True,
        check=False,
        cwd=_REPO,
    )
    assert result.returncode == 0, result.stdout + result.stderr
