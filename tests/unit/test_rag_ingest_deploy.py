"""The RAG package deployer must preserve provenance, locking, and read-back."""
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_DEPLOY = _REPO / "automation" / "rag_ingest" / "deploy.sh"


def test_rag_ingest_deployer_has_the_production_safety_contract() -> None:
    text = _DEPLOY.read_text(encoding="utf-8")

    assert "deploy_provenance_check" in text
    assert "|| exit 4" in text
    assert 'source "$repo_root/automation/deploy_tree.sh"' in text
    assert (
        'deploy_tree_swap --lock .hermes/rag-ingest/watch.lock '
        '"$repo_root/automation/rag_ingest" .hermes/rag_ingest_runtime/rag_ingest'
    ) in text
    assert ".hermes/scripts/rag_ingest_watch.py" in text
    assert 'source "$repo_root/automation/deploy_timer.sh"' in text
    assert 'converge_timer rag-ingest-watch "every 10m" rag_ingest_watch.py local' in text


def test_remote_payloads_run_from_a_readable_directory() -> None:
    """`sudo -u <acct>` inherits the CALLER's cwd (~oriclaw, unreadable to the target).

    `find` then fails with "Failed to restore initial working directory" and exits
    non-zero, so `set -euo pipefail` aborted the deploy right after the tar extract —
    before the read-back and before the watcher wrapper was pushed. Measured in
    production 2026-08-22: the runtime landed, the wrapper did not, and every ingest
    tick stayed broken while the deployer looked like it had run.

    Scans EVERY shipped script rather than a named pair: the first version of this
    regression hardcoded two files and therefore did not see the RAG-stack deployer
    reintroduce the identical payload one PR later. Only REMOTE payloads are
    constrained; a local find runs in a directory its own caller can read.
    """
    import re
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parents[2]
    markers = ("run_agent ", "run_ops ", "sudo -n -u")
    payload = re.compile(r"(?:run_agent |run_ops |sudo -n -u).*\bfind ")
    readable_cd = re.compile(r'\bcd \\?"\\?\$HOME\\?"')
    inspected: set[tuple[str, int]] = set()
    sources: set[tuple[str, int]] = set()
    for script in sorted((root / "automation").rglob("*.sh")):
        relative = str(script.relative_to(root))
        for number, line in enumerate(script.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            # Independent discovery: any remote marker on a line that also names
            # `find` as a word. The checked set must cover every such line, so a
            # narrowed or broken payload pattern cannot quietly skip one.
            if any(marker in line for marker in markers) and re.search(r"\bfind\b", line):
                sources.add((relative, number))
            if not payload.search(line):
                continue
            inspected.add((relative, number))
            # Without a cd, find inherits the caller's unreadable cwd. The rule asks for
            # the target account's own $HOME: it is guaranteed readable under `sudo -H`
            # and every remote payload uses it. Other readable dirs (e.g. `cd /`) are
            # rejected by convention, not because they would fail; widen this if one is needed.
            assert readable_cd.search(line.split("find ")[0]), (
                f"{relative}: remote payload runs find without first "
                f"entering a readable directory: {stripped[:120]}"
            )
    assert inspected, "found no remote find payload: the scan would pass vacuously"
    assert sources <= inspected, f"remote find payloads not inspected: {sorted(sources - inspected)}"
