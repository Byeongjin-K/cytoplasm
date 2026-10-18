"""배포기가 provenance 검사가 볼 수 없는 코드를 끌어오지 않는다 — 토큰 수준 닫힌 세계(RCB todo 58, 검증 B58-3·R2·R3).

검사는 고정 공용 헬퍼 묶음과, 검사 순간 셸에 정의된 함수의 파일을 대조한다. 묶음 밖 헬퍼는 검사 **전에**
저장소 루트 형태(`"$repo_root/…"`·`$(dirname …)/…`)로 source 되어 함수를 정의해야 보인다(계획 todo 58 Scope note).
계약(두 번째 Scope note): dispatch 토큰 — `source`·`.`·`eval`·`builtin`·`command`·`exec`·`trap`·`alias`, 셸 실행
(`bash`·`sh`, 경로 표기 포함), 서브셸·명령 치환 밖의 `cd`·`pushd`·`popd`(함수 본문 포함), `BASH_ENV`·`ENV`, 그리고
토큰을 숨길 수 있는 ANSI-C `$'…'`·로케일 `$"…"` 따옴표(`shell_quoting.special_quotes`, 검증 B58-R4-1) — 은
어느 자리에 어떤 옵션·`--`·경로 표기로 나오든 문제다. 예외는 (파일, 정확한 줄) 허용 항목과, 지원하는 루트
형태로 풀리는 단순 source 문 둘뿐이다. 분류하지 못한 dispatch 가 조용히 통과하는 길은 없다.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Final

from tests.unit.shell_quoting import special_quotes
from tests.unit.shell_source_scan import SOURCE_WORDS, Command, plain, scan

_REPO: Final = Path(__file__).resolve().parents[2]
_GUARD: Final = "automation/deploy_provenance.sh"
_CALL: Final = "deploy_provenance_check"
_FUNCTION: Final = re.compile(r"^\s*(?:function\s+)?[\w-]+\s*\(\)\s*\{", re.MULTILINE)
_DISPATCH: Final = frozenset({"source", ".", "eval", "builtin", "command", "exec", "trap", "alias"})
_CWD: Final = frozenset({"cd", "pushd", "popd"})
_SHELLS: Final = frozenset({"bash", "sh"})
_ENV_WORD: Final = re.compile(r"^(?:BASH_ENV|ENV)(?:\+?=|$)")
_HEREDOC: Final = re.compile(
    r"(?:^|[;&|(`]|\bthen|\bdo)[ \t]*(?:source|\.|eval|builtin|command|exec|trap|alias|cd|pushd|popd|(?:\S*/)?(?:ba)?sh)"
    r"(?=[ \t]|$)", re.MULTILINE)
_HELPERS: Final = {
    "automation/deploy_cron.sh", "automation/deploy_push.sh", "automation/deploy_tree.sh",
    "automation/deploy_tree_remote.sh", "automation/deploy_config.sh", "automation/deploy_timer.sh", _GUARD,
}
_ROOT_VARIABLE: Final = re.compile(
    r'^\s*(?:local\s+|readonly\s+)?(\w+)="\$\(cd "\$\(dirname "(?:\$\{BASH_SOURCE\[0\]\}|\$BASH_SOURCE|\$0)"\)'
    r'(/[^"]*)?" && pwd(?: -P)?\)"', re.MULTILINE)
_SCRIPT_DIRECTORY: Final = (
    "$(dirname ${BASH_SOURCE[0]})", "$(dirname $BASH_SOURCE)", "$(dirname $0)",
    "${BASH_SOURCE%/*}", "${BASH_SOURCE[0]%/*}", "${0%/*}",
)
_NODE_ENV_LINE: Final = 'eval "$(python3 "$repo_root/automation/node_config_sh.py" --print-env)"'
_NODE_ENV_DEPLOYERS: Final = (
    "automation/cost-report", "automation/doctor", "automation/effect_request", "automation/interop", "automation/managed_sync",
    "automation/memory_curator", "automation/memory_provider", "automation/memory_relocate",
    "automation/notes_organize", "automation/obsidian_write", "automation/omo_relay",
    "automation/plaud_sync", "automation/project_board",
    "automation/rag_ingest", "automation/rag_stack", "automation/regression_bank", "automation/reminder_poller",
    "automation/repair", "automation/research_trends", "automation/selfskill_audit", "automation/skill_generation",
    "automation/state_backup", "automation/stt_eval", "automation/voice_catalog", "automation/yoodori_gate", "skills/budget", "skills/calendar",
    "skills/coordination", "skills/mail", "skills/meeting", "skills/speechtotext", "skills/todo", "skills/wiki",
)
_NODE_ENV: Final = "evaluates NAME=value exports printed from node.toml by node_config_sh.py; it sources no file"
_NEWLINE: Final = ("the ANSI-C quote is only a `$'\\n'` newline joining, splitting or testing text for a newline; "
                   "no command or shell name is spelled")
_REMOTE_CALL: Final = ("appends a call to a fixed-name `_deploy_tree_remote_*` function (shipped by `declare -f`) to the "
                       "remote script after a `$'\\n'`; no shell name or source target is spelled")
_REMOTE_CLEANUP: Final = ("appends the fixed cleanup command `rm -rf -- \"$staging\"` to the remote script after a `$'\\n'`; "
                          "no shell name or source target is spelled")
_LOCK_FD: Final = "closes the execution lock descriptor this script opened (frozen deploy-skill.sh)"
_ALLOWED: Final[dict[tuple[str, str], str]] = {
    ("automation/memory_curator/deploy.sh",
     'if ! "$repo_root/automation/release_approval_remote.sh" authorize-yoodori-channel \\'):
        "fixed release approval transport, explicitly provenance-checked; verifies the exact HEAD and "
        "destination surface before any chat, notice, or ops override write",
    ("automation/memory_curator/deploy.sh",
     'if ! "$repo_root/automation/release_approval_remote.sh" authorize-dori-agent-chat \\'):
        "fixed release approval transport, included explicitly in this deployer's provenance check; "
        "the existing surface gate verifies the same HEAD and bot before any node config write",
    **{(f"{package}/deploy.sh", _NODE_ENV_LINE): _NODE_ENV for package in _NODE_ENV_DEPLOYERS},
    **{(f"automation/hermes_compat/{name}", _NODE_ENV_LINE): _NODE_ENV
       for name in ("deploy.sh", "deploy-owner-dm.sh", "deploy-public-message-policy.sh")},
    ("automation/deploy-skill.sh", _NODE_ENV_LINE.replace("$repo_root", "$REPO_ROOT")): _NODE_ENV,
    ("automation/deploy-skill.sh", 'sudo -n -u "$acct" -H bash -c "$script"'):
        "local-host branch of run_as: runs the deployer's own command string as the target account",
    ("automation/deploy-skill.sh", 'eval "exec ${EXECUTION_LOCK_READ_FD}<&-" 2>/dev/null || true'): _LOCK_FD,
    ("automation/deploy-skill.sh", 'eval "exec ${EXECUTION_LOCK_WRITE_FD}>&-" 2>/dev/null || true'): _LOCK_FD,
    ("automation/deploy-skill.sh", "trap cleanup_deploy_temps EXIT"):
        "removes this run's temporary files through a function defined in the same file",
    ("automation/deploy_cron.sh", 'exec 9>"$HOME/.hermes/cron/.autophagy-converge.lock" || exit 6'):
        "opens the converge lock descriptor inside the remote function body",
    ("automation/deploy_tree_remote.sh", 'mkdir -p -- "$(dirname -- "$1")" && exec 9>>"$1" && flock -w "$2" 9'):
        "opens the tree-swap lock descriptor inside the remote function body",
    ("automation/deploy_tree_remote.sh",
     "find . -type d \\( -name __pycache__ -o -name .venv -o -name .ruff_cache -o -name .pytest_cache \\) -prune \\"):
        "`.` is find's start directory, not the source builtin",
    ("automation/deploy_tree.sh",
     'local table="$_DEPLOY_TREE_DIR/../configs/runtime-package-manifest.txt" account source runtime rest'):
        "`source` is a local variable naming a manifest column",
    ("automation/deploy_tree.sh", "while IFS='|' read -r account source runtime rest; do"):
        "`source` is a variable read from the runtime package manifest",
    (_GUARD, 'untracked="$(git -C "$repo_root" -c core.quotepath=false ls-files --others --exclude-standard -- .)" || {'):
        "`.` is the pathspec of git ls-files, not the source builtin",
    (_GUARD, 'restore="$(builtin shopt -p extdebug)" || true'): "captures the caller's extdebug setting",
    (_GUARD, "if ! shopt -s extdebug || ! builtin shopt -q extdebug; then"):
        "confirms extdebug really is on; `builtin` bypasses a caller's shopt function",
    (_GUARD, 'line="$(builtin declare -F "$name")"'): "reads a function's source file under extdebug",
    (_GUARD, 'done <<<"$(builtin declare -F)"'): "lists the functions defined in this shell",
    (_GUARD, 'eval "builtin $restore"'): "restores the extdebug setting captured by `builtin shopt -p` above",
    **{(_GUARD, line): _NEWLINE for line in (
        'listed=$\'\\n\'"$(printf \'%s\\n\' "${targets[@]}")"$\'\\n\'',
        '[[ "$listed" == *$\'\\n\'"$relative"$\'\\n\'* ]] && continue', 'listed+="$relative"$\'\\n\'')},
    **{("automation/deploy_tree.sh", line): _NEWLINE for line in (
        'staging_id="${out%%$\'\\n\'*}" out="${out#*$\'\\n\'}"',
        'prelude+=$\'\\n\'"staging_id=${staging_id#id=}"',
        '[[ "$out" == previous=* && "$out" != *$\'\\n\'* ]] || {')},
    **{("automation/deploy_tree.sh", line): _REMOTE_CALL for line in (
        '| run_agent "$prelude"$\'\\n_deploy_tree_remote_prepare\')"',
        'out="$(printf \'%s\\n\' "$expected" | run_agent "$prelude"$\'\\n_deploy_tree_remote_switch\')"',
        'out="$(run_agent "$prelude"$\'\\n_deploy_tree_remote_discard\' </dev/null)"',
        'out="$(run_agent "$prelude"$\'\\n_deploy_tree_remote_verify\' </dev/null)"',
        'run_agent "$prelude"$\'\\n\'"_deploy_tree_remote_rollback $(printf \'%q\' "$previous")" </dev/null')},
    ("automation/deploy_tree.sh", 'run_agent "$prelude"$\'\\nrm -rf -- "$staging"\' </dev/null \\'): _REMOTE_CLEANUP,
}


def resolve(script: Path, root: Path, raw: str) -> str | None:
    """Repository path a source argument names through a supported ROOT form; None for anything else."""
    text = script.read_text(encoding="utf-8")
    roots = {name: f"{script.parent}{tail or ''}" for name, tail in _ROOT_VARIABLE.findall(text)}
    prefixes = [(f"${{{name}}}", base) for name, base in roots.items()] + [(f"${name}", base) for name, base in roots.items()]
    prefixes += [(prefix, str(script.parent)) for prefix in _SCRIPT_DIRECTORY]
    word = plain(raw)
    for prefix, base in prefixes:
        if word.startswith(f"{prefix}/"):
            path = Path(os.path.normpath(base + word[len(prefix):]))
            if "$" in str(path) or "`" in str(path) or not path.is_relative_to(root) or not path.is_file():
                return None
            return path.relative_to(root).as_posix()
    return None


def listed_helpers(root: Path) -> set[str]:
    block = re.search(r"^DEPLOY_PROVENANCE_SHARED_HELPERS=\(([^)]*)\)", (root / _GUARD).read_text(encoding="utf-8"),
                      re.MULTILINE)
    assert block
    return set(block.group(1).split())


def _source_statement(command: Command) -> bool:
    return command.word.text in SOURCE_WORDS and len(command.args) == 1 and not command.args[0].text.startswith("-")


def _dispatch(text: str, base: int, *, nested: bool) -> list[tuple[int, str]]:
    result = scan(text, base)
    classified = set() if nested else {c.word.pos for c in result.commands if _source_statement(c)}
    out = [(c.word.pos, f"indirect:{c.word.text}") for c in result.commands if "$" in c.word.text or "`" in c.word.text]
    for word in result.words:
        token = plain(word.text)
        if word.pos in classified:
            continue
        if quotes := special_quotes(word.text):
            out += [(word.pos, f"quoting:{kind}") for kind in sorted(quotes)]
        elif _ENV_WORD.match(token):
            out.append((word.pos, f"bash-env:{token}"))
        elif token in _DISPATCH or token.rsplit("/", 1)[-1] in _SHELLS:
            out.append((word.pos, f"dispatch:{token}"))
        elif token in _CWD and not (nested or word.in_subshell):
            out.append((word.pos, f"dispatch:{token}"))
    for pos, body in result.substitutions:
        out += _dispatch(body, pos, nested=True)
    for pos, body in result.heredocs:
        out += [(pos + match.end() - 1, "heredoc-dispatch") for match in _HEREDOC.finditer(body)]
    return out


def findings(root: Path, script: Path, listed: set[str]) -> list[tuple[str, str]]:
    """(stripped line text, problem) for everything the closed grammar does not accept, before the allowlist."""
    text = script.read_text(encoding="utf-8")
    found = _dispatch(text, 0, nested=False)
    commands = scan(text).commands
    check = next((c.index for c in commands if plain(c.word.text) == _CALL), None)
    for command in filter(_source_statement, commands):
        raw = command.args[0].text
        relative = resolve(script, root, raw)
        if relative is None:
            found.append((command.word.pos, f"{'unresolved' if '$' in raw else 'literal'}-target:{raw}"))
        elif relative in listed:
            continue
        elif command.in_function:
            found.append((command.word.pos, f"deferred:{relative}"))
        elif check is not None and command.index > check:
            found.append((command.word.pos, f"late:{relative}"))
        elif not _FUNCTION.search((root / relative).read_text(encoding="utf-8")):
            found.append((command.word.pos, f"invisible:{relative}"))
    lines = text.splitlines()
    return [(lines[text.count("\n", 0, pos)].strip(), problem) for pos, problem in sorted(found)]


def problems(root: Path, script: Path, listed: set[str]) -> list[str]:
    relative = script.relative_to(root).as_posix()
    return [problem for line, problem in findings(root, script, listed) if (relative, line) not in _ALLOWED]


def _deployers() -> list[Path]:
    listed = subprocess.run(("git", "-C", str(_REPO), "ls-files", "*.sh"), check=True, capture_output=True,
                            text=True, timeout=60).stdout.split()
    return [_REPO / name for name in listed if name != _GUARD
            and any(plain(c.word.text) == _CALL for c in scan((_REPO / name).read_text(encoding="utf-8")).commands)]


def test_the_guard_binds_every_shared_helper_and_their_sources() -> None:
    listed = listed_helpers(_REPO)

    assert listed == _HELPERS
    assert {helper: problems(_REPO, _REPO / helper, listed) for helper in sorted(listed)} == dict.fromkeys(
        sorted(listed), [])


def test_every_real_check_calling_script_passes_the_closed_grammar() -> None:
    listed = listed_helpers(_REPO)
    deployers = _deployers()
    scripts = [*deployers, *(_REPO / helper for helper in sorted(listed))]
    used = {(path.relative_to(_REPO).as_posix(), line) for path in scripts for line, _ in findings(_REPO, path, listed)}

    assert len(deployers) >= 31
    assert _REPO / "automation/deploy-skill.sh" in deployers
    assert {str(p.relative_to(_REPO)): problems(_REPO, p, listed) for p in deployers} == {
        str(p.relative_to(_REPO)): [] for p in deployers}
    assert set(_ALLOWED) - used == set()
