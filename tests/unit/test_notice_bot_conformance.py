"""Every deployed Discord credential read names the resolver or an explicit exemption."""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# Every direct read has a reviewed reason; exception handlers are scanned too.
EXEMPT = {
    "skills/meeting/plugin/__init__.py": "Standalone legacy ACK only when automation and the override are both absent; any override without its resolver blocks sending.",
    "automation/install/discord_check.py": "Read-only installer GET probe; it checks the credential explicitly supplied by the installer.",
    "automation/install/wizard.py": "Preserves the installer credential across sudo; no Discord sender.",
    "automation/report_hub/collector.py": "Read-only Discord report ingestion; never posts messages.",
}
SHELL_EXEMPT = {
    "automation/install/quickstart.sh": "Installer presence test and sudo environment preservation, no Discord HTTP.",
    "automation/owner_notice_probe.sh": "Read-only presence probe; active override replaces the legacy token requirement.",
    "automation/provision-agent.sh": "First-install secret presence check, no Discord HTTP.",
    "automation/provision-supply-chain-watch.sh": "First-install grep for a credential key, no Discord HTTP.",
}


def direct_reads(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    aliases = {"DISCORD_BOT_TOKEN"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if isinstance(value, ast.Constant) and value.value == "DISCORD_BOT_TOKEN":
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                aliases.update(target.id for target in targets if isinstance(target, ast.Name))
    def key(node):
        return (isinstance(node, ast.Constant) and node.value in aliases
                or isinstance(node, ast.Name) and node.id in aliases
                or isinstance(node, ast.Attribute) and node.attr == "token_env")
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.args and key(node.args[0]):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if name in {"get", "getenv", "__getitem__", "_require_env", "startswith"}:
                found.append(node.lineno)
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load) and key(node.slice):
            found.append(node.lineno)
    return found


def test_no_direct_credential_reads_outside_resolver_and_explicit_ledger():
    hits = {
        str(path.relative_to(REPO)): direct_reads(path)
        for folder in ("automation", "skills")
        for path in (REPO / folder).rglob("*.py")
        if "vendor" not in path.parts and path.name != "notice_bot.py"
    }
    actual = {path: lines for path, lines in hits.items() if lines}
    assert set(actual) == set(EXEMPT), actual
    assert all(EXEMPT.values())


def test_shell_token_references_are_only_declared_presence_or_environment_plumbing():
    actual = {
        str(path.relative_to(REPO))
        for path in (REPO / "automation").rglob("*.sh")
        if "DISCORD_BOT_TOKEN" in path.read_text()
    }
    assert actual == set(SHELL_EXEMPT)
    assert all(SHELL_EXEMPT.values())


def test_import_error_handlers_do_not_hide_direct_credential_reads(tmp_path):
    source = tmp_path / "sender.py"
    source.write_text(
        "try:\n    import missing_resolver\nexcept ImportError:\n"
        "    token = os.environ.get('DISCORD_BOT_TOKEN', '')\n",
        encoding="utf-8",
    )
    assert direct_reads(source) == [4]
