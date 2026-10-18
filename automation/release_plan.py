"""Deterministic release surfaces and bounded owner-facing patch notes (VA-1).

The release command runs only for a clean checkout whose ``HEAD`` equals ``origin/main``.
That makes the checkout an exact materialisation of the commit being approved, so skill
digests can reuse :func:`skill_review.skill_digest` byte-for-byte.  Home-artifact ownership
reuses :class:`watcher_manifest.Row`; no second deployment registry exists here.
"""
from __future__ import annotations

import ast
import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from automation.release_notes import COMMIT_FORMAT, BundleMap, ReleaseCommit, parse_commits
from automation.skill_review import skill_digest
from automation.watcher_manifest import CENTRAL_MANIFEST, Row, parse_rows


class ReleasePlanError(RuntimeError):
    """The requested git range cannot produce a trustworthy release plan."""


@dataclass(frozen=True, slots=True)
class ReleasePlan:
    version: str
    base: str
    head: str
    tree_digest: str
    changed_paths: tuple[str, ...]
    surface_digests: tuple[tuple[str, str], ...]
    commit_titles: tuple[str, ...]
    major_signals: tuple[str, ...]
    commits: tuple[ReleaseCommit, ...]


def _git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(repo), *arguments),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ReleasePlanError(
            f"git {' '.join(arguments[:2])} failed with exit {result.returncode}"
        )
    return result.stdout


def _changed_paths(repo: Path, base: str, head: str) -> tuple[str, ...]:
    raw = subprocess.run(
        ("git", "-C", str(repo), "diff", "--name-only", "-z", f"{base}..{head}"),
        capture_output=True,
        check=False,
    )
    if raw.returncode != 0:
        raise ReleasePlanError("could not enumerate the release diff")
    return tuple(sorted(part.decode("utf-8") for part in raw.stdout.split(b"\0") if part))


def _revision_text(repo: Path, revision: str, path: str) -> str | None:
    result = subprocess.run(
        ("git", "-C", str(repo), "show", f"{revision}:{path}"),
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout if result.returncode == 0 else None


def _assignment_value(source: str | None, name: str) -> str | None:
    if source is None:
        return None
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        raise ReleasePlanError(f"cannot parse release signal source for {name}") from error
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == name and node.value is not None:
                return ast.dump(node.value, include_attributes=False)
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
                return ast.dump(node.value, include_attributes=False)
    return None


def _schema_values(repo: Path, revision: str) -> dict[str, str]:
    result = subprocess.run(
        (
            "git", "-C", str(repo), "grep", "-l", "-E",
            r"SCHEMA_VERSION[[:space:]]*[:=]", revision, "--", "automation",
        ),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        raise ReleasePlanError(f"could not inspect schema versions at {revision}")
    values: dict[str, str] = {}
    prefix = f"{revision}:"
    pattern = re.compile(
        r"(?:readonly\s+)?([A-Z_][A-Z0-9_]*SCHEMA_VERSION)\s*(?::[^=]+)?=\s*([^#]+)"
    )
    for row in result.stdout.splitlines():
        path = row.removeprefix(prefix)
        source = _revision_text(repo, revision, path) or ""
        for text in source.splitlines():
            match = pattern.search(text)
            if match and (match[1] == "SCHEMA_VERSION" or match[1].endswith("_SCHEMA_VERSION")):
                values[f"{path}:{match[1]}"] = match[2].strip()
    return values


def _directory_keys(source: str | None) -> tuple[str, ...]:
    if source is None:
        return ()
    return tuple(sorted(set(re.findall(r'_interop_config_string\(["\']([^"\']+)', source))))


def major_signals(repo: Path, *, base: str, head: str) -> tuple[str, ...]:
    """Return machine-contract values changed by the release range."""
    watched = (
        ("automation/interop/approval_surface.py", "POLICY_VERSION"),
        ("automation/node_config.py", "_FIELD_NAMES"),
        ("automation/install/profiles.py", "DEFAULT_PROFILE"),
    )
    signals: list[str] = []
    for path, name in watched:
        before = _assignment_value(_revision_text(repo, base, path), name)
        after = _assignment_value(_revision_text(repo, head, path), name)
        if before != after:
            signals.append(f"{path}:{name}")
    before_schemas = _schema_values(repo, base)
    after_schemas = _schema_values(repo, head)
    signals.extend(
        name
        for name in sorted(before_schemas.keys() | after_schemas.keys())
        if before_schemas.get(name) != after_schemas.get(name)
    )
    directory = "automation/interop/approval_directory.py"
    if _directory_keys(_revision_text(repo, base, directory)) != _directory_keys(
        _revision_text(repo, head, directory)
    ):
        signals.append(f"{directory}:required config keys")
    return tuple(signals)


def _sha256_lines(rows: tuple[str, ...]) -> str:
    return hashlib.sha256("".join(f"{row}\n" for row in rows).encode("utf-8")).hexdigest()


def _home_package_digest(repo: Path, rows: tuple[Row, ...]) -> str:
    payload: list[str] = []
    for row in sorted(rows, key=lambda item: (item.destination, item.source)):
        try:
            source_digest = hashlib.sha256((repo / row.source).read_bytes()).hexdigest()
        except OSError as error:
            raise ReleasePlanError(f"declared release source is unreadable: {row.source}") from error
        payload.append(f"{source_digest}  {row.destination}")
    return _sha256_lines(tuple(payload))


def _git_paths_digest(repo: Path, head: str, paths: tuple[str, ...]) -> str:
    listing = _git(repo, "ls-tree", "-r", head, "--", *paths)
    return hashlib.sha256(listing.encode("utf-8")).hexdigest()


def _tree_digest(repo: Path, head: str) -> str:
    tree = _git(repo, "rev-parse", f"{head}^{{tree}}").strip()
    if not tree:
        raise ReleasePlanError("release tree is empty")
    return hashlib.sha256(tree.encode("ascii")).hexdigest()


def build_plan(
    repo: Path,
    *,
    base: str,
    head: str,
    version: str,
    bump: str = "patch",
) -> ReleasePlan:
    """Build one immutable plan from an exact clean checkout of ``head``."""
    actual = _git(repo, "rev-parse", "HEAD").strip()
    if actual != head:
        raise ReleasePlanError(f"checkout HEAD {actual} differs from release HEAD {head}")
    changed = _changed_paths(repo, base, head)
    signals = major_signals(repo, base=base, head=head)
    if signals and bump != "major":
        raise ReleasePlanError(f"MAJOR bump required: {', '.join(signals)}")
    manifest_path = repo / CENTRAL_MANIFEST
    try:
        rows = parse_rows(manifest_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ReleasePlanError(f"deployment manifest is unreadable: {manifest_path}") from error

    surfaces: dict[str, str] = {}
    skills_root = repo / "skills"
    skills = sorted(
        path.name
        for path in skills_root.iterdir()
        if path.is_dir() and (path / "SKILL.md").is_file()
    )
    for skill in skills:
        surfaces[f"skill:{skill}"] = skill_digest(repo / "skills" / skill)

    packages = sorted({row.owning_package for row in rows})
    for package in packages:
        package_rows = tuple(row for row in rows if row.owning_package == package)
        surfaces[f"home:{package}"] = _home_package_digest(repo, package_rows)

    bundle_map = BundleMap(
        skills=tuple(skills),
        home_sources=tuple((row.source, f"home:{row.owning_package}") for row in rows),
    )
    grouped: dict[str, list[str]] = {}
    for path in changed:
        for name in bundle_map.bundles(path):
            grouped.setdefault(name, []).append(path)
    tree_digest = _tree_digest(repo, head)
    for name in ("rag", "root", "runtime"):
        located = grouped.get(name)
        if located:
            surfaces[name] = _git_paths_digest(repo, head, tuple(located))
    if grouped.get("repo"):
        surfaces["repo"] = tree_digest

    commits = parse_commits(
        _git(
            repo,
            "-c",
            "core.quotePath=false",
            "log",
            "--reverse",
            f"--format={COMMIT_FORMAT}",
            "--name-only",
            f"{base}..{head}",
        ),
        bundle_map,
    )
    return ReleasePlan(
        version=version,
        base=base,
        head=head,
        tree_digest=tree_digest,
        changed_paths=changed,
        surface_digests=tuple(sorted(surfaces.items())),
        commit_titles=tuple(commit.subject for commit in commits),
        major_signals=signals,
        commits=commits,
    )
