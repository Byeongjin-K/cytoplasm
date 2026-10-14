"""배포 쓰기 예외 원장의 마감(RCB todo 28, GAP-10·GAP-11).

todo 11 의 원장(`configs/deploy-write-exemptions.txt`)은 이관 중 행(`migrating:`)을 허용했다 — 나중 todo 가
그 쓰기를 공용 헬퍼로 옮기며 지운다는 표시였다. todo 28 이 마지막 여섯 행을 옮겼으므로 원장에는 영구
예외만 남고, 이 파일이 그 상태를 기계 필드로 고정한다:

- 이관 중 행의 거부는 공용 검사(`test_deploy_write_inventory.check` 의 R6)에 있다. 여기의 시험은 그
  검사를 b95e3d65 에도 있던 인터페이스로만 부른다 — 이 파일만 옛 체크아웃에 얹으면 옛 검사가 같은 행을
  받아들이는 것이 단언에서 드러난다.
- 승인된 예외는 (배포기 경로, 종류)별 행 수로 고정한다. 사유 문장은 사람이 읽는 설명이라 대조하지 않고
  비어 있지 않음(R5)과 이관 표시 없음(R6)만 본다 — 문구를 다듬어도 깨지지 않고, 새 예외는 이 표를 함께
  바꿔야 들어온다.
- 계획의 추출 감사 명령은 인벤토리 스캔보다 넓다(동결된 `deploy-skill.sh` 와 공용 헬퍼까지 읽는다). 그
  명령의 모든 줄은 원장의 raw 행이나 `#audit|` 항목으로 설명돼야 하고, 맞는 줄이 없는 항목은 죽은 항목이다.
"""
from __future__ import annotations

import shutil
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from tests.unit.test_deploy_write_inventory import LEDGER, _DEPLOYER, _tree, check, parse_ledger

_REPO: Final = Path(__file__).resolve().parents[2]
_AUDIT_COMMAND: Final = "grep -nE 'tar -x' $(git ls-files | grep -E '(^|/)deploy[^/]*\\.sh$')"
_AUDIT_PREFIX: Final = "#audit|"
_AUDIT_KINDS: Final = ("frozen", "helper", "comment")
_LIVE_TAR: Final = "run_agent 'mkdir -p \"$HOME/.hermes/x\"; tar -xzf - -C \"$HOME/.hermes/x\"'\n"
_LIVE_TARGET: Final = 'tar -xzf - -C "$HOME/.hermes/x"'
_APPROVED: Final = {
    ("automation/hermes_compat/deploy-owner-dm.sh", "raw"): 5,
    ("automation/hermes_compat/deploy-public-message-policy.sh", "raw"): 5,
    ("automation/hermes_compat/deploy.sh", "raw"): 1,
    ("automation/interop/deploy.sh", "raw"): 3,
    ("automation/notes_organize/deploy.sh", "raw"): 1,
    ("automation/rag_stack/deploy.sh", "raw"): 2,
    ("automation/regression_bank/deploy_lab_node.sh", "raw"): 8,
    ("automation/research_trends/deploy.sh", "raw"): 1,
    ("skills/budget/deploy.sh", "raw"): 1,
    ("skills/mail/deploy.sh", "dest"): 2,
    ("skills/mail/deploy.sh", "raw"): 5,
}


@dataclass(frozen=True, slots=True)
class Hit:
    path: str
    line: int
    text: str


def identity_problems(ledger_text: str) -> list[str]:
    rows, _ = parse_ledger(ledger_text)
    found = Counter((row.deployer, row.kind) for row in rows)
    return [f"{key}: {found[key]} row(s), approved {_APPROVED.get(key, 0)}"
            for key in sorted(set(found) | set(_APPROVED)) if found[key] != _APPROVED.get(key, 0)]


def extraction_hits(root: Path) -> list[Hit]:
    result = subprocess.run(("bash", "-c", _AUDIT_COMMAND), cwd=root, capture_output=True, text=True,
                            check=False, timeout=60)
    assert result.returncode in (0, 1), result.stderr
    hits: list[Hit] = []
    for line in result.stdout.splitlines():
        path, number, text = line.split(":", 2)
        hits.append(Hit(path, int(number), text))
    return hits


def audit_problems(root: Path, ledger_text: str) -> list[str]:
    rows, _ = parse_ledger(ledger_text)
    raw = [row for row in rows if row.kind == "raw"]
    uncovered: dict[str, list[Hit]] = {}
    for hit in extraction_hits(root):
        if not any(row.deployer == hit.path and row.target in hit.text for row in raw):
            uncovered.setdefault(hit.path, []).append(hit)
    problems: list[str] = []
    seen: set[str] = set()
    for line in ledger_text.splitlines():
        if not line.startswith(_AUDIT_PREFIX):
            continue
        fields = line.removeprefix(_AUDIT_PREFIX).split("|", 3)
        if len(fields) != 4 or fields[1] not in _AUDIT_KINDS or not fields[2].isdigit() or not fields[3].strip():
            problems.append(f"malformed audit entry: {line}")
            continue
        path, kind, count = fields[0], fields[1], int(fields[2])
        hits = uncovered.pop(path, [])
        if path in seen:
            problems.append(f"duplicate audit entry: {path}")
        seen.add(path)
        if len(hits) != count or count == 0:
            problems.append(f"audit entry {path} declares {count} hit(s), the command finds {len(hits)}")
        if kind == "comment" and not all(hit.text.lstrip().startswith("#") for hit in hits):
            problems.append(f"audit entry {path} is kind comment but a hit is not a comment line")
    problems.extend(f"unrecorded extraction {hit.path}:{hit.line}" for hits in uncovered.values() for hit in hits)
    return problems


def _ledger() -> str:
    return (_REPO / LEDGER).read_text(encoding="utf-8")


def _deployer_copy(tmp_path: Path) -> Path:
    listed = subprocess.run(("git", "ls-files"), cwd=_REPO, capture_output=True, text=True, check=True,
                            timeout=30).stdout.splitlines()
    for relative in (entry for entry in listed if Path(entry).name.startswith("deploy") and entry.endswith(".sh")):
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        _ = shutil.copyfile(_REPO / relative, tmp_path / relative)
    for args in (("init", "-q"), ("add", "-A")):
        _ = subprocess.run(("git", "-C", str(tmp_path), *args), check=True, capture_output=True, timeout=30)
    return tmp_path


def test_only_permanent_exemptions_remain() -> None:
    _, problems = parse_ledger(_ledger())

    assert problems == ()
    assert identity_problems(_ledger()) == []


def test_a_migrating_exemption_is_rejected(tmp_path: Path) -> None:
    """The row covers a LIVE flagged site, so only the sentinel can reject it (base b95e3d65 accepts it)."""
    root = _tree(tmp_path, {"deploy.sh": _LIVE_TAR})
    assert check(root, f"{_DEPLOYER}|raw|{_LIVE_TARGET}|stays outside the helpers\n") == ()

    problems = check(root, f"{_DEPLOYER}|raw|{_LIVE_TARGET}|migrating: todo 99\n")

    assert {problem.rule for problem in problems} == {"R6"}


def test_a_migrating_row_in_a_copy_of_the_real_ledger_is_rejected() -> None:
    lines = _ledger().splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith("skills/budget/deploy.sh|raw|"))
    lines[index] = lines[index].rsplit("|", 1)[0] + "|migrating: todo 99"

    assert {(p.rule, p.deployer) for p in check(_REPO, "\n".join(lines) + "\n")} == {("R6", "skills/budget/deploy.sh")}


def test_an_unapproved_exception_identity_is_rejected() -> None:
    ledger = _ledger() + "skills/wiki/deploy.sh|raw|rm -f|a new exception nobody approved\n"

    assert identity_problems(ledger) == ["('skills/wiki/deploy.sh', 'raw'): 1 row(s), approved 0"]


def test_a_reason_only_rewording_passes() -> None:
    reworded = [line if not line or line.startswith("#") else line.rsplit("|", 1)[0] + "|reworded explanation"
                for line in _ledger().splitlines()]
    ledger = "\n".join(reworded) + "\n"

    assert ledger != _ledger()
    assert identity_problems(ledger) == []
    assert parse_ledger(ledger)[1] == ()


def test_every_extraction_hit_is_accounted_for_and_no_audit_entry_is_dead() -> None:
    assert extraction_hits(_REPO)
    assert audit_problems(_REPO, _ledger()) == []


def test_an_unrecorded_extraction_in_a_deployer_copy_is_rejected(tmp_path: Path) -> None:
    root = _deployer_copy(tmp_path)
    assert audit_problems(root, _ledger()) == []
    extra = root / "automation/pkg/deploy-extra.sh"
    extra.parent.mkdir(parents=True)
    _ = extra.write_text("#!/usr/bin/env bash\n" + _LIVE_TAR, encoding="utf-8")
    _ = subprocess.run(("git", "-C", str(root), "add", "-A"), check=True, capture_output=True, timeout=30)

    assert audit_problems(root, _ledger()) == ["unrecorded extraction automation/pkg/deploy-extra.sh:2"]


def test_an_audit_entry_without_a_live_hit_is_dead() -> None:
    ledger = _ledger() + "#audit|automation/nothing/deploy.sh|helper|1|no such file\n"

    assert audit_problems(_REPO, ledger) == [
        "audit entry automation/nothing/deploy.sh declares 1 hit(s), the command finds 0"
    ]
