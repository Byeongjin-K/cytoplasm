"""토큰 수준 닫힌 세계가 dispatch 형태를 하나도 조용히 통과시키지 않는다(RCB todo 58 재작업 3·4, 검증 B58-R2·R3).

검증자가 bash 로 실제 실행해 헬퍼를 source 하는 것을 확인한 형태들 — `builtin`/`command`(옵션·`--` 포함)·`eval`·
치환·heredoc·따옴표/역슬래시/변수 명령어·`exec`/`trap`/`alias`·셸 실행(경로 표기 포함)·`BASH_ENV`/`ENV`·함수 안의
`cd` — 과 상대 리터럴 대상, 다른 파일의 허용 줄 재사용이 모두 문제로 보고된다. 서브셸과 명령 치환 안의 `cd` 는
허용되고, 지원하는 루트 형태는 검사 전이면 통과하고 검사 뒤면 `late` 다.
새 파일인 이유: `test_deploy_provenance_source_order.py` 는 정책·허용 목록·실제 트리 대조를 담아 순수 LOC 여유가 적다.
"""
from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest

from tests.unit.test_deploy_provenance_source_order import listed_helpers, problems

_REPO: Final = Path(__file__).resolve().parents[2]
_HEAD: Final = 'repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"\n'
_CHECK: Final = 'deploy_provenance_check "$repo_root" "$repo_root/payload" || exit 4\n'
_GUARD: Final = "automation/deploy_provenance.sh"
_H: Final = '"$repo_root/automation/h.sh"'


def _tree(root: Path, body: str, extra: tuple[tuple[str, str], ...] = ()) -> Path:
    files = ((_GUARD, (_REPO / _GUARD).read_text(encoding="utf-8")), ("automation/h.sh", "h_remote() { :; }\n"),
             ("automation/data.sh", "VALUE=1\n"), *extra)
    for relative, text in files:
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        _ = (root / relative).write_text(text, encoding="utf-8")
    script = root / "automation/deploy_x.sh"
    _ = script.write_text(_HEAD + body, encoding="utf-8")
    return script


def _reported(found: list[str], kind: str) -> bool:
    return any(problem == kind or problem.startswith(f"{kind}:") for problem in found)


_SUPPORTED: Final = (
    f"source {_H}", f". {_H}", "source $repo_root/automation/h.sh", 'source "${repo_root}/automation/h.sh"',
    'source "$(dirname "$0")/h.sh"', 'source "$(dirname "${BASH_SOURCE[0]}")/h.sh"', 'source "${BASH_SOURCE%/*}/h.sh"',
    f"true && . {_H}", f"if true; then source {_H}; fi",
)


@pytest.mark.parametrize("form", _SUPPORTED)
def test_supported_root_forms_resolve_and_are_ordered(tmp_path: Path, form: str) -> None:
    early = _tree(tmp_path / "early", f"{form}\n{_CHECK}")
    late = _tree(tmp_path / "late", f"{_CHECK}{form}\n")

    assert problems(tmp_path / "early", early, listed_helpers(_REPO)) == []
    assert problems(tmp_path / "late", late, listed_helpers(_REPO)) == ["late:automation/h.sh"]


@pytest.mark.parametrize("form", ['(cd "$repo_root" && true)', 'here="$(cd "$repo_root" && pwd)"'])
def test_cd_inside_a_subshell_or_substitution_is_accepted(tmp_path: Path, form: str) -> None:
    script = _tree(tmp_path, f"{form}\n{_CHECK}")

    assert problems(tmp_path, script, listed_helpers(_REPO)) == []


_REJECTED: Final = (
    (". ./automation/h.sh", "literal-target"), ("source automation/h.sh", "literal-target"),
    ("source -- automation/h.sh", "dispatch:source"), ('source "$other/h.sh"', "unresolved-target"),
    ("builtin source automation/h.sh", "dispatch:builtin"), ("command . automation/h.sh", "dispatch:command"),
    ("builtin eval 'source automation/h.sh'", "dispatch:builtin"), ("command eval 'source automation/h.sh'", "dispatch:command"),
    (f"builtin -- source {_H}", "dispatch:builtin"), (f"command -p . {_H}", "dispatch:command"),
    ('eval "source automation/h.sh"', "dispatch:eval"),
    ("/bin/bash -c 'source \"$1\"' _ automation/h.sh", "dispatch:/bin/bash"),
    ("printf x | xargs -I{} bash -c 'source \"$1\"' _ {}", "dispatch:bash"),
    ("$( . automation/h.sh )", "dispatch:."), ("result=$( . automation/h.sh )", "dispatch:."),
    ('echo "$( . automation/h.sh )"', "dispatch:."), ("echo `source automation/h.sh`", "dispatch:source"),
    ('x="${y:-$(. automation/h.sh)}"', "dispatch:."), ("cat <<EOF\n$( . automation/h.sh )\nEOF", "dispatch:."),
    ("cat <<'EOF'\nsource automation/h.sh\nEOF", "heredoc-dispatch"),
    ("\\source automation/h.sh", "dispatch:source"), ('"source" automation/h.sh', "dispatch:source"),
    ("$'\\163ource' automation/h.sh", "indirect"), ('cmd=source; "$cmd" automation/h.sh', "indirect"),
    ("trap 'source automation/h.sh' EXIT", "dispatch:trap"), ("alias s='source automation/h.sh'", "dispatch:alias"),
    ("exec bash -c 'source automation/h.sh'", "dispatch:exec"),
    ("BASH_ENV=automation/h.sh bash run.sh", "bash-env"), ("export BASH_ENV=automation/h.sh", "bash-env"),
    ("ENV=automation/h.sh sh run.sh", "bash-env"),
    (f"restore='source {_H}'; eval \"builtin $restore\"", "dispatch:eval"),
    ('enter() { cd "$RIG"; }\nenter', "dispatch:cd"), ('pushd "$RIG"', "dispatch:pushd"),
    (f"load() {{ source {_H}; }}\nload", "deferred"), ('source "$repo_root/automation/data.sh"', "invisible"),
    *((f"{carrier}{shell} -c 'source \"$1\"' _ automation/h.sh", kind)
      for carrier in ("", "env ", "nohup ", "timeout 5 ", "printf x | xargs ", "sudo -n ")
      for shell, kind in (("$'bash'", "quoting:ansi-c"), ("$'\\142ash'", "quoting:ansi-c"), ('$"bash"', "quoting:locale"))),
)


@pytest.mark.parametrize(("form", "kind"), _REJECTED)
def test_every_dispatch_form_is_reported(tmp_path: Path, form: str, kind: str) -> None:
    placed = f"{form}\n{_CHECK}" if kind in {"deferred", "invisible", "dispatch:cd", "dispatch:pushd"} else f"{_CHECK}{form}\n"
    script = _tree(tmp_path, placed)

    assert _reported(problems(tmp_path, script, listed_helpers(_REPO)), kind)


def _effect_request(root: Path, extra: str) -> Path:
    deployer = "automation/effect_request/deploy.sh"
    for relative, text in ((deployer, (_REPO / deployer).read_text(encoding="utf-8") + extra),
                           ("automation/gate_extra.sh", "extra_remote() { :; }\n"),
                           *((helper, (_REPO / helper).read_text(encoding="utf-8")) for helper in listed_helpers(_REPO))):
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        _ = (root / relative).write_text(text, encoding="utf-8")
    return root / deployer


def test_the_unmodified_real_effect_request_is_clean(tmp_path: Path) -> None:
    assert problems(tmp_path, _effect_request(tmp_path, ""), listed_helpers(_REPO)) == []


@pytest.mark.parametrize(("line", "kind"), [
    ("builtin source automation/gate_extra.sh", "dispatch:builtin"),
    ('eval "source automation/gate_extra.sh"', "dispatch:eval"),
    ("builtin eval 'source \"$repo_root/automation/gate_extra.sh\"'", "dispatch:builtin"),
    ('builtin -- source "$repo_root/automation/gate_extra.sh"', "dispatch:builtin"),
    ('restore=\'source "$repo_root/automation/gate_extra.sh"\'; eval "builtin $restore"', "dispatch:eval"),
    *((f"env {shell} -c 'source \"$1\"' _ \"$repo_root/automation/gate_extra.sh\"", kind)
      for shell, kind in (("$'bash'", "quoting:ansi-c"), ("$'\\142ash'", "quoting:ansi-c"), ('$"bash"', "quoting:locale"))),
])
def test_the_verifier_real_effect_request_counterexamples_fail(tmp_path: Path, line: str, kind: str) -> None:
    script = _effect_request(tmp_path, f'{line}\nrun_agent "$(declare -f extra_remote); extra_remote"\n')

    assert _reported(problems(tmp_path, script, listed_helpers(_REPO)), kind)


@pytest.mark.parametrize("driver", [
    f'source "$repo_root/{_GUARD}"\ncd "$repo_root/automation"\nsource closure_probe_helper.sh\ncd "$RIG"\n{_CHECK}',
    f'source "$repo_root/{_GUARD}"\nenter() {{ cd "$RIG"; }}\npayload_remote() {{ :; }}\nenter\n{_CHECK}',
])
def test_the_verifier_caller_identity_drivers_fail(tmp_path: Path, driver: str) -> None:
    script = _tree(tmp_path, driver, (("closure_probe_helper.sh", "unrelated_root_function() { :; }\n"),))

    assert _reported(problems(tmp_path, script, listed_helpers(_REPO)), "dispatch:cd")
