from __future__ import annotations

import subprocess
import tarfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROVENANCE = ROOT / "automation" / "deploy_provenance.sh"


def test_archive_manifest_excludes_ignored_residue_and_keeps_reviewable_files(
    tmp_path: Path,
) -> None:
    # Given: a Git source tree containing tracked, untracked-reviewable, and ignored files.
    repo = tmp_path / "repo"
    package = repo / "package"
    package.mkdir(parents=True)
    (repo / ".gitignore").write_text(".env\n.venv/\n", encoding="utf-8")
    (package / "tracked.py").write_text("TRACKED = True\n", encoding="utf-8")
    (package / "reviewable.txt").write_text("review me\n", encoding="utf-8")
    (package / ".env").write_text("ignored=true\n", encoding="utf-8")
    (package / ".venv").mkdir()
    (package / ".venv" / "residue.py").write_text("ignored = True\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "add", ".gitignore", "package/tracked.py"],
        check=True,
    )
    archive = tmp_path / "package.tar"

    # When: the shared deploy archive helper builds the tar stream.
    command = (
        f'source "{PROVENANCE}"; '
        f'deploy_archive_stream "{repo}" "{repo}" package > "{archive}"'
    )
    result = subprocess.run(
        ["bash", "-c", command],
        capture_output=True,
        text=True,
        check=False,
    )

    # Then: only tracked and untracked-nonignored paths are packaged.
    assert result.returncode == 0, result.stderr
    with tarfile.open(archive) as built:
        names = set(built.getnames())
    assert "package/tracked.py" in names
    assert "package/reviewable.txt" in names
    assert "package/.env" not in names
    assert not any(name.startswith("package/.venv") for name in names)
    # And: the ROOT DIRECTORY entry survives. `git ls-files` names only files, so
    # driving tar from it dropped `package/` — and skill_store.py refuses an archive
    # without it ("archive lacks the skill root directory"), which killed every
    # privileged mount at stage 4/4 on 2026-08-04.
    assert "package" in names or "package/" in names, "skill root directory entry is required"


def test_scoped_deploy_tar_streams_use_the_shared_git_manifest() -> None:
    # Given: every deploy script in G1's archive-hardening scope.
    scripts = (
        ROOT / "automation" / "deploy-skill.sh",
        ROOT / "automation" / "hermes_compat" / "deploy.sh",
        ROOT / "automation" / "regression_bank" / "deploy.sh",
        ROOT / "automation" / "skill_generation" / "deploy.sh",
        ROOT / "skills" / "mail" / "deploy.sh",
    )

    # When/Then: each tar-producing script delegates file selection to Git.
    for script in scripts:
        text = script.read_text(encoding="utf-8")
        assert "deploy_archive_stream" in text or "deploy_tree_swap" in text, (
            f"archive manifest bypass: {script}"
        )



def test_archive_root_entry_does_not_smuggle_ignored_files(tmp_path: Path) -> None:
    # Given: the root entry is added so skill_store.py accepts the archive. Emitting a
    # DIRECTORY to tar normally makes it WALK that directory, which would re-admit every
    # ignored file the git manifest exists to keep out. `--no-recursion` must prevent it.
    repo = tmp_path / "repo"
    package = repo / "package"
    (package / "__pycache__").mkdir(parents=True)
    (repo / ".gitignore").write_text("__pycache__/\n.env\n", encoding="utf-8")
    (package / "tracked.py").write_text("TRACKED = True\n", encoding="utf-8")
    (package / "__pycache__" / "residue.pyc").write_text("x", encoding="utf-8")
    (package / ".env").write_text("SECRET=1\n", encoding="utf-8")
    _ = subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _ = subprocess.run(
        ["git", "-C", str(repo), "add", ".gitignore", "package/tracked.py"], check=True
    )
    archive = tmp_path / "package.tar"

    # When: the archive is built with the root entry present.
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{PROVENANCE}"; deploy_archive_stream "{repo}" "{repo}" package > "{archive}"',
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    # Then: the root entry is there AND no ignored path rode in with it.
    assert result.returncode == 0, result.stderr
    with tarfile.open(archive) as built:
        names = set(built.getnames())
    assert "package" in names or "package/" in names
    assert "package/tracked.py" in names
    assert not any("__pycache__" in name for name in names), "root entry recursed into ignored paths"
    assert "package/.env" not in names


def test_tree_swap_delegation_still_selects_files_through_git() -> None:
    # Given: a deployer may delegate its tar stream to deploy_tree_swap (RCB todo 9).
    text = (ROOT / "automation" / "deploy_tree.sh").read_text(encoding="utf-8")

    # Then: the helper itself streams through the shared Git manifest, in code, not prose.
    calls = [
        line for line in text.splitlines()
        if not line.lstrip().startswith("#") and 'deploy_archive_stream "$repo_root"' in line
    ]
    assert calls, "deploy_tree_swap bypasses the shared git archive manifest"
