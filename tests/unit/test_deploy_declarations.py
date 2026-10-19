"""배포 선언 v2 문법(`automation.deploy_declarations`)의 파싱과 legacy 투영의 바이트 보존.

새 파일인 이유: `test_watcher_manifest_declarations.py` 는 실제 저장소의 선언과 중앙 표의
conformance 를 검사한다 — 여기는 임시 저장소에서 문법 자체(종류·정책·키·경로 규칙)와
"v2 행은 중앙 표에 나오지 않는다"는 투영 규칙을 검사한다. 중앙 표의 바이트는 헬스체크
허용 목록 지문에 들어가므로 v2 행이 더해져도 한 바이트도 바뀌면 안 된다.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

from automation.deploy_declarations import (
    Declaration,
    all_declarations,
    parse_declaration_file,
)
from automation.watcher_manifest import (
    CENTRAL_MANIFEST,
    ManifestError,
    declaration_files,
    derive_manifest,
)

_REPO: Final = Path(__file__).resolve().parents[2]
_DECL: Final = "automation/pkg/deploy-manifest.txt"


def _copy_declarations(target: Path) -> None:
    for declaration in declaration_files(_REPO):
        relative = declaration.relative_to(_REPO)
        (target / relative).parent.mkdir(parents=True, exist_ok=True)
        _ = shutil.copyfile(declaration, target / relative)
    (target / "skills").mkdir(exist_ok=True)


def _only(text: str, relative: str = _DECL) -> Declaration:
    declarations = parse_declaration_file(relative, text)
    assert len(declarations) == 1
    return declarations[0]


def test_legacy_projection_is_byte_identical(tmp_path: Path) -> None:
    _copy_declarations(tmp_path)
    existing = tmp_path / "automation" / "effect_request" / "deploy-manifest.txt"
    _ = existing.write_text(
        existing.read_text(encoding="utf-8")
        + "agent|automation/effect_request|.hermes/example-runtime|v2:tree;files=a.py,b.py\n",
        encoding="utf-8",
    )
    only_v2 = tmp_path / "automation" / "zz_v2only" / "deploy-manifest.txt"
    only_v2.parent.mkdir(parents=True)
    _ = only_v2.write_text(
        "# v2 only\npeer|configs/x.yaml|.hermes/interop/x.yaml|v2:file;requires=roster\n",
        encoding="utf-8",
    )
    central = (_REPO / CENTRAL_MANIFEST).read_text(encoding="utf-8")
    assert derive_manifest(tmp_path) == central
    v2 = [d for d in all_declarations(tmp_path) if not d.legacy]
    assert {
        ("tree", "automation/effect_request"),
        ("file", "automation/zz_v2only"),
    } <= {(d.kind, d.owner) for d in v2}


def test_real_repository_declarations_keep_the_projection_rules() -> None:
    declarations = all_declarations(_REPO)
    assert declarations
    assert all(d.kind == "file" for d in declarations if d.legacy)
    assert derive_manifest(_REPO) == (_REPO / CENTRAL_MANIFEST).read_text(encoding="utf-8")


def test_legacy_rows_keep_their_meaning() -> None:
    required, optional = parse_declaration_file(
        _DECL,
        "agent|automation/pkg/w.py|.hermes/scripts/w.py|required\n"
        "peer|automation/pkg/o.py|.hermes/scripts/o.py|optional:opt-in only\n",
    )
    assert (required.legacy, required.kind, required.policy, required.reason) == (
        True,
        "file",
        "required",
        "",
    )
    assert (optional.policy, optional.reason, optional.owner) == (
        "optional",
        "opt-in only",
        "automation/pkg",
    )


def test_v2_owner_is_the_declaration_directory() -> None:
    declaration = _only(
        "agent|configs/external-effect-tools.yaml|.hermes/interop/external-effect-tools.yaml"
        "|v2:file;activation=gateway\n",
        "automation/interop/deploy-manifest.txt",
    )
    assert declaration.owner == "automation/interop"
    assert declaration.legacy is False
    assert declaration.policy == "required"
    assert declaration.attr("activation") == "gateway"


def test_bare_v2_file_is_a_required_file() -> None:
    declaration = _only("agent|automation/pkg/w.py|.hermes/scripts/w.py|v2:file\n")
    assert (declaration.kind, declaration.policy, declaration.reason, declaration.attrs) == (
        "file",
        "required",
        "",
        (),
    )


def test_cron_destination_is_the_job_name() -> None:
    declaration = _only(
        "agent|automation/pkg/w.py|pkg-watch"
        "|v2:cron;schedule=*/10 * * * *;script=w.py;deliver=discord;mode=no-agent\n"
    )
    assert declaration.kind == "cron"
    assert declaration.destination == "pkg-watch"
    assert declaration.attr("schedule") == "*/10 * * * *"
    assert declaration.attr("mode") == "no-agent"


def test_tree_attributes_and_defaults() -> None:
    declaration = _only(
        "agent|automation/pkg|.hermes/pkg-runtime"
        "|v2:tree;files=a.py,b.py;prefix=automation/pkg;lock=.hermes/pkg.lock"
        ";policy=held;reason=waits for roster\n"
    )
    assert declaration.policy == "held"
    assert declaration.reason == "waits for roster"
    assert declaration.attr("files") == "a.py,b.py"
    assert declaration.attr("prefix") == "automation/pkg"
    assert declaration.attr("profile") == "python"
    assert declaration.attr("node") == "default"
    assert declaration.attr("activation") == "none"
    assert declaration.attr("missing", "x") == "x"
    assert ("policy", "held") not in declaration.attrs


def test_derived_attributes() -> None:
    declaration = _only(
        "agent|skills/mail/vendor/mailon|.hermes/mailon-runtime/current"
        "|v2:derived;algorithm=mailon-py-v1;requirements=skills/mail/vendor/requirements.txt\n",
        "skills/mail/deploy-manifest.txt",
    )
    assert declaration.owner == "skills/mail"
    assert declaration.attr("algorithm") == "mailon-py-v1"


_INVALID: Final = (
    ("unknown kind", "v2:blob"),
    ("unsupported key", "v2:file;colour=red"),
    ("key of another kind", "v2:file;schedule=* * * * *"),
    ("duplicate key", "v2:file;node=rag;node=rag"),
    ("empty value", "v2:file;activation="),
    ("empty piece", "v2:file;"),
    ("no equals", "v2:file;activation"),
    ("unknown policy", "v2:file;policy=maybe;reason=x"),
    ("held without reason", "v2:file;policy=held"),
    ("optional without reason", "v2:file;policy=optional"),
    ("bad enum", "v2:file;activation=always"),
    ("bad profile", "v2:tree;profile=java"),
    ("pipe in value", "v2:file;reason=a|b;policy=held"),
    ("cron missing mode", "v2:cron;schedule=* * * * *;script=w.py;deliver=discord"),
    ("dot-dot prefix", "v2:tree;prefix=../x"),
    ("absolute lock", "v2:tree;lock=/tmp/x.lock"),
    ("legacy optional without reason", "optional:"),
    ("legacy unknown policy", "maybe"),
)


@pytest.mark.parametrize(("label", "policy"), _INVALID, ids=[label for label, _ in _INVALID])
def test_invalid_typed_rows_are_rejected(label: str, policy: str) -> None:
    del label
    with pytest.raises(ManifestError):
        _ = parse_declaration_file(_DECL, f"agent|automation/pkg|.hermes/pkg|{policy}\n")


@pytest.mark.parametrize(
    ("source", "destination"),
    (
        ("/abs/w.py", ".hermes/scripts/w.py"),
        ("automation/pkg/w.py", "/home/x/.hermes/scripts/w.py"),
        ("automation/../w.py", ".hermes/scripts/w.py"),
        ("automation/pkg/w.py", ".hermes/./scripts/w.py"),
        ("automation//w.py", ".hermes/scripts/w.py"),
    ),
)
def test_paths_must_be_plain_relative(source: str, destination: str) -> None:
    for policy in ("required", "v2:file"):
        with pytest.raises(ManifestError):
            _ = parse_declaration_file(_DECL, f"agent|{source}|{destination}|{policy}\n")


def test_declaration_without_any_row_is_still_rejected(tmp_path: Path) -> None:
    with pytest.raises(ManifestError):
        _ = parse_declaration_file(_DECL, "# nothing left\n")
    declaration = tmp_path / _DECL
    declaration.parent.mkdir(parents=True)
    (tmp_path / "skills").mkdir()
    _ = declaration.write_text("# nothing left\n", encoding="utf-8")
    with pytest.raises(ManifestError):
        _ = derive_manifest(tmp_path)
    with pytest.raises(ManifestError):
        _ = all_declarations(tmp_path)


def test_v2_only_declaration_emits_no_section(tmp_path: Path) -> None:
    declaration = tmp_path / _DECL
    declaration.parent.mkdir(parents=True)
    (tmp_path / "skills").mkdir()
    _ = declaration.write_text("agent|automation/pkg|.hermes/pkg|v2:tree\n", encoding="utf-8")
    derived = derive_manifest(tmp_path)
    assert "automation/pkg/deploy-manifest.txt" not in derived
    assert "v2:" not in derived


def test_invalid_v2_row_fails_the_check_command(tmp_path: Path) -> None:
    from automation import watcher_manifest

    declaration = tmp_path / _DECL
    declaration.parent.mkdir(parents=True)
    (tmp_path / "skills").mkdir()
    _ = declaration.write_text("agent|automation/pkg|.hermes/pkg|v2:blob\n", encoding="utf-8")
    (tmp_path / "configs").mkdir()
    _ = (tmp_path / CENTRAL_MANIFEST).write_text(derive_manifest(tmp_path), encoding="utf-8")
    assert watcher_manifest.check(tmp_path) == 1


def _run_cli(copy: Path, command: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(copy)}
    return subprocess.run(
        [sys.executable, "-m", "automation.watcher_manifest", command],
        cwd=copy, env=env, capture_output=True, text=True, check=False, timeout=60,
    )


def _repo_copy(target: Path, extra_rows: str) -> None:
    _copy_declarations(target)
    (target / "automation").mkdir(exist_ok=True)
    for name in ("__init__.py", "watcher_manifest.py", "deploy_declarations.py"):
        _ = shutil.copyfile(_REPO / "automation" / name, target / "automation" / name)
    (target / "configs").mkdir(exist_ok=True)
    central = target / CENTRAL_MANIFEST
    _ = shutil.copyfile(_REPO / CENTRAL_MANIFEST, central)
    bad = target / "automation/zz_v2only/deploy-manifest.txt"
    bad.parent.mkdir(parents=True)
    _ = bad.write_text(extra_rows, encoding="utf-8")


@pytest.mark.parametrize(
    "rows",
    (
        "agent|automation/zz_v2only/a|.hermes/a|v2:blob\n",
        "agent|automation/zz_v2only/a|.hermes/a|v2:file;bogus=1\n",
    ),
)
def test_cli_run_as_module_reports_invalid_v2_row(tmp_path: Path, rows: str) -> None:
    _repo_copy(tmp_path, rows)
    result = _run_cli(tmp_path, "check")
    output = result.stdout + result.stderr
    assert result.returncode == 1
    assert "INVALID" in output
    assert "Traceback" not in output


def test_cli_run_as_module_still_reports_drift(tmp_path: Path) -> None:
    _repo_copy(tmp_path, "agent|automation/zz_v2only/a|.hermes/a|v2:file\n")
    with (tmp_path / CENTRAL_MANIFEST).open("a", encoding="utf-8") as handle:
        _ = handle.write("agent|automation/x/y|.hermes/y|required\n")
    result = _run_cli(tmp_path, "check")
    assert result.returncode == 1
    assert "DRIFT" in result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "row",
    ("agent|automation/zz_v2only/a|.hermes/a\n", "agent||.hermes/a|required\n"),
)
def test_cli_emit_reports_malformed_legacy_row_as_invalid(tmp_path: Path, row: str) -> None:
    _repo_copy(tmp_path, row)
    before = (tmp_path / CENTRAL_MANIFEST).read_bytes()
    result = _run_cli(tmp_path, "emit")
    output = result.stdout + result.stderr
    assert result.returncode == 1
    assert "INVALID" in output
    assert "Traceback" not in output
    assert (tmp_path / CENTRAL_MANIFEST).read_bytes() == before


def test_cli_emit_on_valid_declarations_is_byte_identical(tmp_path: Path) -> None:
    _repo_copy(tmp_path, "agent|automation/zz_v2only/a|.hermes/a|v2:file\n")
    result = _run_cli(tmp_path, "emit")
    assert result.returncode == 0
    assert (tmp_path / CENTRAL_MANIFEST).read_bytes() == (_REPO / CENTRAL_MANIFEST).read_bytes()


_REJECTED_DECLARATIONS: Final = (
    b"agent|automation/zz_v2only/a|.hermes/a|required|extra\n",
    b"agent|automation/zz_v2only/a|.hermes/a|v2:blob\n",
    b"agent|automation/zz_v2only/a|.hermes/\xff\xfe|required\n",
)


@pytest.mark.parametrize("command", ("emit", "check"))
@pytest.mark.parametrize("payload", _REJECTED_DECLARATIONS)
def test_cli_rejects_invalid_declaration_and_leaves_the_central_table(
    tmp_path: Path, payload: bytes, command: str
) -> None:
    _repo_copy(tmp_path, "agent|automation/zz_v2only/a|.hermes/a|v2:file\n")
    _ = (tmp_path / "automation/zz_v2only/deploy-manifest.txt").write_bytes(payload)
    central = tmp_path / CENTRAL_MANIFEST
    before = hashlib.sha256(central.read_bytes()).hexdigest()
    result = _run_cli(tmp_path, command)
    output = result.stdout + result.stderr
    assert result.returncode == 1
    assert "INVALID" in output
    assert "Traceback" not in output
    assert hashlib.sha256(central.read_bytes()).hexdigest() == before
