"""배포기가 계정 홈에 쓰는 길의 적합성 검사(RCB todo 11, GAP-10).

새 파일인 이유: `test_watcher_manifest_declarations.py` 는 배포기 본문에서 `.hermes/scripts`·
`.hermes/plugins` **모양의 문자열**만 찾아 선언과 대조한다 — 그 모양이 아닌 목적지(런타임 트리,
`.hermes/hermes-compat`, 메일 staging)와 날것 원격 쓰기(`tar -x`·`cat >`·`rm` …)는 아무도 보지
않았다. 예외 맵 선례(`test_watcher_deploy_coverage.py`)는 FS3 고정 파일이라 거기 더할 수 없다.

검사 대상은 `automation/*/deploy*.sh` 와 `skills/*/deploy*.sh` 전부다(`deploy.sh` 가 아닌 이름 포함).
`automation/deploy-skill.sh` 는 범위 밖이다 — 수정 동결 파일이고 그것이 쓰는
`~/.hermes/interop_runtime`·`~/.hermes/skill-gate` 는 스킬 배포기의 작업 영역이지 선언 대상
배포물이 아니다(todo 30 문서). 그래서 예외 행이 아니라 glob 이 그 파일을 고르지 않는다.

규칙(행 단위 어휘 검사이며 bash 해석기가 아니다 — DEC-24):
  R1 `deploy.sh` 옆에는 `deploy-manifest.txt` 가 있다. 없으면 `no-manifest` 예외가 있어야 한다.
  R2 줄 연속(`\\` 개행)·여러 줄 따옴표·heredoc 본문을 한 논리 줄로 잇고 주석을 뺀 뒤, 원격 실행
     표식과 쓰기 관용구가 함께 있는 줄(또는 `scp`·`rsync` 원격 복사)은 날것 원격 쓰기다.
     허용되는 길은 공용 헬퍼(`push_file`·`deploy_tree_swap`·`converge_cron`·`converge_config`)뿐이고, 나머지는
     `raw` 예외(대상 = 그 줄에서 정확히 한 줄만 고르는 부분 문자열)가 있어야 한다.
  R3 헬퍼 호출의 목적지(리터럴, 또는 같은 파일의 리터럴 대입 하나로 풀리는 변수)는 어느 선언
     파일에든 있어야 한다(목적지 문자열로 대조). 아니면 `dest` 예외. 풀 수 없는 변수는 예외로도
     덮을 수 없는 실패다.
  R4 어떤 위반에도 맞지 않는 예외 행은 실패다(죽은 예외). raw 행이 두 줄 이상에 맞아도 실패다.
  R5 사유가 빈 예외 행은 실패다.
  R6 사유에 `migrating:` 이 든 행은 실패다 — RCB todo 28 이 이관 중 행을 마감했으므로 원장에는 영구 예외만 남는다.
원장 형식: `<배포기 경로>|<raw|dest|no-manifest>|<대상>|<사유>` (`configs/deploy-write-exemptions.txt`).
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest

from automation.deploy_declarations import all_declarations

_REPO: Final = Path(__file__).resolve().parents[2]
LEDGER: Final = "configs/deploy-write-exemptions.txt"
KINDS: Final = ("raw", "dest", "no-manifest")

_REMOTE: Final = re.compile(
    r"(?<![\w-])(?:run_agent|run_account|run_ops|run_peer)(?![\w-])|(?<![\w-])ssh\s"
)
_REMOTE_COPY: Final = re.compile(r"(?<![\w-])(?:scp|rsync)\s")
_WRITE: Final = re.compile(
    r"(?<![\w-])tar\s[^|;&]*?(?<=\s)(?:-[A-Za-z]*x[A-Za-z]*|--extract|--get)(?![\w-])"
    r"|(?<![\w-])tar\s+x\w*"
    r"|(?<![\w-])cat\s*>"
    r"|(?<![\w./-])(?:cp|install|mv|rm|crontab)\s"
    r"|(?<![\w-])hermes\s+cron\s+(?:create|edit|remove)(?![\w-])"
)
_HELPER: Final = re.compile(r"(?<![\w-])(push_file|deploy_tree_swap|converge_cron|converge_config)\s+(.+)")
_HEREDOC: Final = re.compile(r"(?<!<)<<(?!<)(-?)\s*(['\"]?)([A-Za-z_]\w*)\2")
_VARIABLE: Final = re.compile(r"\$(?:\{(\w+)\}|(\w+))")
_OPERATORS: Final = frozenset({"||", "&&", ";", "|", "&"})


@dataclass(frozen=True, slots=True)
class Line:
    number: int
    text: str


@dataclass(frozen=True, slots=True)
class Write:
    deployer: str
    kind: str
    target: str
    line: int
    resolved: bool = True


@dataclass(frozen=True, slots=True)
class Exemption:
    deployer: str
    kind: str
    target: str
    reason: str
    number: int


@dataclass(frozen=True, slots=True)
class Problem:
    rule: str
    deployer: str
    target: str
    line: int


def _heredoc_body(source: str, start: int, text: str) -> tuple[str, int, int]:
    match = _HEREDOC.search(text)
    if match is None:
        return "", start, 0
    dash, delimiter = match.group(1), match.group(3)
    body: list[str] = []
    index, consumed = start, 0
    while index < len(source):
        end = source.find("\n", index)
        end = len(source) if end < 0 else end
        physical = source[index:end]
        index, consumed = end + 1, consumed + 1
        if (physical.lstrip("\t") if dash else physical) == delimiter:
            break
        body.append(physical)
    return " ; ".join(body), index, consumed


def logical_lines(source: str) -> tuple[Line, ...]:
    """Join continuations, multi-line quotes and heredoc bodies; drop comments."""
    lines: list[Line] = []
    buf: list[str] = []
    state, index, number, start = "", 0, 1, 1
    while index < len(source):
        char = source[index]
        if state == "'":
            state = "" if char == "'" else state
        elif char == "\\" and index + 1 < len(source):
            if source[index + 1] == "\n":
                number, index = number + 1, index + 2
                continue
            buf.append(source[index : index + 2])
            index += 2
            continue
        elif state == '"':
            state = "" if char == '"' else state
        elif char in "'\"":
            state = char
        elif char == "#" and (not buf or buf[-1] in " \t;"):
            end = source.find("\n", index)
            index = len(source) if end < 0 else end
            continue
        elif char == "\n":
            text = "".join(buf).strip()
            body, index, consumed = _heredoc_body(source, index + 1, text)
            if text:
                lines.append(Line(start, f"{text} ; {body}" if body else text))
            buf, number = [], number + 1 + consumed
            start = number
            continue
        if char == "\n":
            number += 1
        buf.append(" " if char == "\n" else char)
        index += 1
    if "".join(buf).strip():
        lines.append(Line(start, "".join(buf).strip()))
    return tuple(lines)


def deployers(root: Path) -> tuple[Path, ...]:
    return tuple(sorted(root.glob("automation/*/deploy*.sh")) + sorted(root.glob("skills/*/deploy*.sh")))


def _helper_destination(name: str, rest: str) -> str | None:
    try:
        tokens = shlex.split(rest)
    except ValueError:
        return None
    args: list[str] = []
    for token in tokens:
        if token in _OPERATORS:
            break
        args.append(token)
    if name == "converge_cron":
        return args[0] if args else None
    if name in ("push_file", "converge_config"):
        return args[1] if len(args) >= 2 else None
    position = 0
    while position < len(args) and args[position].startswith("--"):
        position += 1 if args[position] == "--link" else 2
    return args[position + 1] if len(args) > position + 1 else None


def _resolve(token: str, text: str) -> str | None:
    """Substitute `$name` from exactly one literal assignment in the same file."""
    def value_of(name: str) -> str | None:
        found = re.findall(rf"(?:^|[\s;]){name}=('[^']*'|\"[^\"]*\"|[^\s;]*)", text, re.M)
        values = {raw[1:-1] if raw[:1] in "'\"" else raw for raw in found}
        if len(values) != 1:
            return None
        value = values.pop()
        return None if any(char in value for char in "$`'\"") else value

    resolved = token
    for match in _VARIABLE.finditer(token):
        value = value_of(match.group(1) or match.group(2))
        if value is None:
            return None
        resolved = resolved.replace(match.group(0), value)
    return None if "$" in resolved or "`" in resolved else resolved


def classify(root: Path) -> tuple[tuple[Path, ...], tuple[Write, ...]]:
    # 은퇴(`policy=retired`) 선언은 "더 이상 쓰지 않는다"는 뜻이라 배포기의 쓰기를 정당화하지 못한다.
    declared = {row.destination for row in all_declarations(root) if row.policy != "retired"}
    found: list[Write] = []
    scripts = deployers(root)
    for script in scripts:
        relative = script.relative_to(root).as_posix()
        if script.name == "deploy.sh" and not (script.parent / "deploy-manifest.txt").is_file():
            manifest = (script.parent / "deploy-manifest.txt").relative_to(root).as_posix()
            found.append(Write(relative, "no-manifest", manifest, 0))
        lines = logical_lines(script.read_text(encoding="utf-8"))
        joined = "\n".join(line.text for line in lines)
        for line in lines:
            if _REMOTE_COPY.search(line.text) or (
                _REMOTE.search(line.text) and _WRITE.search(line.text)
            ):
                found.append(Write(relative, "raw", line.text, line.number))
            helper = _HELPER.search(line.text)
            if helper is None:
                continue
            token = _helper_destination(helper.group(1), helper.group(2))
            destination = None if token is None else _resolve(token, joined)
            if destination is None:
                found.append(Write(relative, "dest", token or helper.group(0), line.number, False))
            elif destination not in declared:
                found.append(Write(relative, "dest", destination, line.number))
    return scripts, tuple(found)


def parse_ledger(text: str) -> tuple[tuple[Exemption, ...], tuple[Problem, ...]]:
    rows: list[Exemption] = []
    problems: list[Problem] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split("|", 3)
        if len(fields) != 4 or fields[1] not in KINDS or not fields[0] or not fields[2]:
            problems.append(Problem("ledger", fields[0], line, number))
            continue
        row = Exemption(fields[0], fields[1], fields[2], fields[3].strip(), number)
        if not row.reason:
            problems.append(Problem("R5", row.deployer, row.target, number))
        if "migrating:" in row.reason:
            problems.append(Problem("R6", row.deployer, row.target, number))
        rows.append(row)
    return tuple(rows), tuple(problems)


def _covers(row: Exemption, write: Write) -> bool:
    if (row.deployer, row.kind) != (write.deployer, write.kind) or not write.resolved:
        return False
    return row.target in write.target if row.kind == "raw" else row.target == write.target


def check(root: Path, ledger_text: str) -> tuple[Problem, ...]:
    scripts, writes = classify(root)
    rows, problems = parse_ledger(ledger_text)
    found = list(problems)
    if not scripts:
        found.append(Problem("scan", "", "no deployer matched automation/*/deploy*.sh or skills/*/deploy*.sh", 0))
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        key = (row.deployer, row.kind, row.target)
        if key in seen:
            found.append(Problem("ledger-duplicate", row.deployer, row.target, row.number))
        seen.add(key)
        hits = [write for write in writes if _covers(row, write)]
        if not hits:
            found.append(Problem("R4", row.deployer, row.target, row.number))
        elif row.kind == "raw" and len(hits) > 1:
            found.append(Problem("R4-ambiguous", row.deployer, row.target, row.number))
    rule = {"no-manifest": "R1", "raw": "R2", "dest": "R3"}
    for write in writes:
        if not write.resolved:
            found.append(Problem("R3-unresolved", write.deployer, write.target, write.line))
        elif not any(_covers(row, write) for row in rows):
            found.append(Problem(rule[write.kind], write.deployer, write.target, write.line))
    return tuple(found)


def _facts(problems: tuple[Problem, ...]) -> set[tuple[str, str]]:
    return {(problem.rule, problem.deployer) for problem in problems}


def _tree(tmp_path: Path, scripts: dict[str, str], manifest: bool = True) -> Path:
    package = tmp_path / "automation" / "pkg"
    package.mkdir(parents=True)
    if manifest:
        (package / "deploy-manifest.txt").write_text(
            "agent|automation/pkg/w.py|.hermes/scripts/w.py|required\n"
            "agent|automation/pkg|.hermes/pkg_runtime|v2:tree;files=w.py\n",
            encoding="utf-8",
        )
    for name, body in scripts.items():
        (package / name).write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    return tmp_path


_DEPLOYER: Final = "automation/pkg/deploy.sh"


def test_every_deployer_write_is_declared_or_exempt() -> None:
    scripts, _ = classify(_REPO)
    assert len(scripts) == len(list(_REPO.glob("automation/*/deploy*.sh"))) + len(
        list(_REPO.glob("skills/*/deploy*.sh"))
    )
    assert scripts
    assert _REPO / "automation/deploy-skill.sh" not in scripts
    ledger = _REPO / LEDGER
    problems = check(_REPO, ledger.read_text(encoding="utf-8") if ledger.is_file() else "")
    assert not problems, "\n".join(
        f"{p.rule} {p.deployer}:{p.line} {p.target[:160]}" for p in problems
    )
    assert ledger.is_file()


def test_undeclared_tree_destination_is_rejected(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"deploy.sh": (
        "deploy_tree_swap --lock .hermes/x.lock \"$repo_root/automation/pkg\" '.hermes/pkg_runtime'\n"
        "deploy_tree_swap --link \"$repo_root/automation/pkg\" '.hermes/undeclared_runtime' w.py\n"
    )})
    problems = check(root, "")
    assert {(p.rule, p.target) for p in problems} == {("R3", ".hermes/undeclared_runtime")}


def test_raw_tar_extract_without_exemption_is_rejected(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"deploy.sh": (
        "tar -C x --exclude=y -czf - w.py \\\n"
        "  | run_agent 'mkdir -p \"$HOME/.hermes/x\"; tar -xzf - -C \"$HOME/.hermes/x\"'\n"
    )})
    assert _facts(check(root, "")) == {("R2", _DEPLOYER)}
    ledger = f"{_DEPLOYER}|raw|tar -xzf - -C \"$HOME/.hermes/x\"|test reason\n"
    assert check(root, ledger) == ()


def test_remote_cp_without_exemption_is_rejected(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"deploy.sh": "ssh \"$host\" \"cp a \\\"\\$HOME/b\\\"\"\n"})
    problems = check(root, "")
    assert _facts(problems) == {("R2", _DEPLOYER)}
    assert [p.line for p in problems] == [2]


def test_unresolvable_destination_variable_is_rejected(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"deploy.sh": (
        "dest_dir=\"$(compute)\"\n"
        "push_file \"$repo_root/automation/pkg/w.py\" \"$dest_dir/w.py\"\n"
    )})
    assert _facts(check(root, "")) == {("R3-unresolved", _DEPLOYER)}
    ledger = f"{_DEPLOYER}|dest|$dest_dir/w.py|an exemption cannot cover an unresolvable path\n"
    assert {p.rule for p in check(root, ledger)} == {"R3-unresolved", "R4"}


def test_literal_assignment_resolves_the_destination(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"deploy.sh": (
        "ship() {\n  local dir='.hermes/scripts'\n"
        "  push_file \"$repo_root/automation/pkg/w.py\" \"$dir/w.py\"\n"
        "  push_file \"$repo_root/automation/pkg/w.py\" \"${dir}/other.py\"\n}\n"
    )})
    assert {(p.rule, p.target) for p in check(root, "")} == {("R3", ".hermes/scripts/other.py")}


def test_dead_exemption_is_rejected(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"deploy.sh": "push_file \"$s\" '.hermes/scripts/w.py'\n"})
    ledger = f"{_DEPLOYER}|raw|rm -rf \"$HOME/.hermes/gone\"|the write it covered is gone\n"
    assert {(p.rule, p.line) for p in check(root, ledger)} == {("R4", 1)}


def test_ambiguous_raw_exemption_is_rejected(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"deploy.sh": "run_agent 'rm -f \"$HOME/a\"'\nrun_agent 'rm -f \"$HOME/b\"'\n"})
    ledger = f"{_DEPLOYER}|raw|rm -f|too broad\n"
    assert {p.rule for p in check(root, ledger)} == {"R4-ambiguous"}


def test_empty_reason_is_rejected(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"deploy.sh": "run_agent 'rm -f \"$HOME/a\"'\n"})
    assert {p.rule for p in check(root, f"{_DEPLOYER}|raw|rm -f|  \n")} == {"R5"}
    assert {p.rule for p in check(root, f"{_DEPLOYER}|bogus|rm -f|x\n")} >= {"ledger"}


def test_deployer_without_manifest_is_rejected(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"deploy.sh": "echo nothing\n"}, manifest=False)
    assert _facts(check(root, "")) == {("R1", _DEPLOYER)}
    ledger = f"{_DEPLOYER}|no-manifest|automation/pkg/deploy-manifest.txt|test reason\n"
    assert check(root, ledger) == ()


def test_non_default_deployer_names_are_scanned(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"deploy-extra.sh": "run_peer 'cat > \"$HOME/.hermes/x\"' < f\n"}, manifest=False)
    assert _facts(check(root, "")) == {("R2", "automation/pkg/deploy-extra.sh")}


def test_a_scan_that_reads_no_deployer_fails(tmp_path: Path) -> None:
    assert {p.rule for p in check(tmp_path, "")} == {"scan"}


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("# run_agent 'rm -rf \"$HOME/x\"'\n", set()),
        ("echo ok  # ssh h 'rm -rf x'\n", set()),
        ("run_agent \\\n  'rm -rf \"$HOME/x\"'\n", {("R2", _DEPLOYER)}),
        ("run_agent 'set -e\nrm -rf \"$HOME/x\"\n'\n", {("R2", _DEPLOYER)}),
        ("run_agent <<'EOF'\nmkdir -p a\ncat > \"$HOME/x\"\nEOF\necho done\n", {("R2", _DEPLOYER)}),
        ("printf '%s' '# not a comment' && ssh h 'mv a b'\n", {("R2", _DEPLOYER)}),
    ],
)
def test_lexer_keeps_hidden_writes_and_drops_comments(
    tmp_path: Path, body: str, expected: set[tuple[str, str]]
) -> None:
    assert _facts(check(_tree(tmp_path, {"deploy.sh": body}), "")) == expected

