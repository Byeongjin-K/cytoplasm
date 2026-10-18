"""빨간 CI 위에서, 또는 체크가 다 나오기도 전에 머지되는가 — 머지 게이트.

2026-08-25 실측으로 두 번 뚫렸다. PR #267 은 빨간 CI 위에서 머지됐고, PR #269 는 체크가
큐잉되기도 전에(`no checks reported`) 머지됐다 — 둘 다 에이전트가 눌렀다. 브랜치 보호로 막을
수도 없다: private + Free 라 403 이고, 켜더라도 에이전트 토큰이 admin 이라 기본 설정은 그를
통과시키며, 관리자까지 묶으면 `automation/land.sh` 의 main 직접 착지가 서버에서 거부된다.

2026-08-26 첫 실사용이 더 미묘한 변종을 드러냈다: 그 순간 올라와 있던 체크 **1건**만 green
이었고 CI 두 잡은 아직 큐잉 전이었는데 게이트가 통과시켰다. 0건만 막아서는 부족하다 —
워크플로가 선언한 잡이 **전부** 보고해야 한다.

그래서 판정을 머지 **명령 자체**에 둔다. 태그는 더 이상 여기서 자르지 않는다(VA-3) —
머지는 축적이고, 서명 태그는 릴리스 승인을 받은 automation/release.sh 가 자른다. 남겨 둔
release-tag 스텁은 tripwire 다: 그 호출이 되살아나면 calls 로그에 "tag" 로 드러난다.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Final

import yaml

_REPO: Final = Path(__file__).resolve().parents[2]
_WRAPPER: Final = _REPO / "automation" / "merge-pr.sh"
_WORKFLOW: Final = _REPO / ".github" / "workflows" / "ci.yml"
#: gh pr diff 이음새가 돌려주는 PR diff — 보상 스캔이 받은 stdin 과 바이트로 대조한다.
_DIFF: Final = "diff --git a/README.md b/README.md\n+a safe change\n"
#: 2026-09-30 PR #557 에서 20분 넘게 in_progress 로 멈췄던 외부 앱 체크.
_STUCK_APP_CHECK: Final = "GitGuardian Security Checks"


def _workflow_jobs() -> tuple[str, ...]:
    """게이트가 기다려야 할 이름은 워크플로에서 파생된다 — 하드코딩하면 잡이 늘 때 조용히 샌다."""
    return tuple(yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))["jobs"])


def _check(name: str, status: str = "COMPLETED", conclusion: str | None = "SUCCESS") -> dict[str, object]:
    return {"name": name, "status": status, "conclusion": conclusion}


def _all_green() -> list[dict[str, object]]:
    return [_check(job) for job in _workflow_jobs()]


def _view(
    checks: list[dict[str, object]],
    *,
    state: str = "OPEN",
    base: str = "main",
    mergeable: str = "MERGEABLE",
) -> dict[str, object]:
    return {
        "state": state,
        "baseRefName": base,
        "headRefOid": "a" * 40,
        "mergeable": mergeable,
        "statusCheckRollup": checks,
    }


def _stubs(
    tmp_path: Path, views: list[dict[str, object]], *, merge_rc: int = 0, diff: str | None = _DIFF
) -> tuple[Path, Path, Path]:
    """gh 와 release-tag 를 주입 이음새로 세운다 — 네트워크 없이 판정 전체를 구동한다."""
    log = tmp_path / "calls.log"
    for index, view in enumerate(views, start=1):
        (tmp_path / f"view-{index}.json").write_text(json.dumps(view), encoding="utf-8")
    if diff is not None:
        (tmp_path / "pr.diff").write_text(diff, encoding="utf-8")
    diff_branch = 'cat "$dir/pr.diff"; exit 0' if diff is not None else "exit 1"
    gh = tmp_path / "gh"
    gh.write_text(
        "#!/usr/bin/env bash\n"
        f'dir="{tmp_path}"\n'
        'if [[ "$1 $2" == "pr view" ]]; then\n'
        '  n=$(( $(cat "$dir/calls" 2>/dev/null || echo 0) + 1 ))\n'
        '  printf %s "$n" > "$dir/calls"\n'
        f'  last={len(views)}\n'
        '  (( n > last )) && n=$last\n'
        '  cat "$dir/view-$n.json"; exit 0\n'
        'fi\n'
        'if [[ "$1 $2" == "pr merge" ]]; then\n'
        f'  printf "merge\\n" >> "{log}"; exit {merge_rc}\n'
        'fi\n'
        'if [[ "$1 $2" == "pr diff" ]]; then\n'
        f'  {diff_branch}\n'
        'fi\n'
        'exit 0\n',
        encoding="utf-8",
    )
    gh.chmod(0o755)
    tag = tmp_path / "release-tag-stub"
    tag.write_text(f'#!/usr/bin/env bash\nprintf "tag\\n" >> "{log}"\nexit 0\n', encoding="utf-8")
    tag.chmod(0o755)
    return gh, tag, log


def _run(tmp_path: Path, gh: Path, tag: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.pop("MERGE_PR_ALLOW_UNCHECKED", None)
    env.pop("MERGE_PR_GH", None)
    env.pop("MERGE_PR_GITLEAKS", None)
    env.pop("MERGE_PR_EXTERNAL_GRACE_SECONDS", None)
    env.update(
        PATH=f"{gh.parent}:{env['PATH']}",
        MERGE_PR_RELEASE_TAG=str(tag),
        MERGE_PR_POLL_SECONDS="0",
        MERGE_PR_DEADLINE_SECONDS="2",
    )
    env.update(extra)
    return subprocess.run(
        ("bash", str(_WRAPPER), "269"), cwd=_REPO, env=env, capture_output=True, text=True, check=False
    )


def _calls(log: Path) -> list[str]:
    return log.read_text(encoding="utf-8").split() if log.exists() else []


def _view_call_count(tmp_path: Path) -> int:
    calls = tmp_path / "calls"
    return int(calls.read_text(encoding="utf-8")) if calls.exists() else 0


def test_refuses_to_merge_when_a_check_failed(tmp_path: Path) -> None:
    failing = [_check(_workflow_jobs()[0], conclusion="FAILURE"), *_all_green()[1:]]
    gh, tag, log = _stubs(tmp_path, [_view(failing)])

    result = _run(tmp_path, gh, tag)

    assert result.returncode != 0
    assert _workflow_jobs()[0] in result.stdout + result.stderr
    assert _calls(log) == []


def test_refuses_when_no_check_ever_appears(tmp_path: Path) -> None:
    """PR #269 의 구멍 — 체크 0건은 통과가 아니다."""
    gh, tag, log = _stubs(tmp_path, [_view([])])

    result = _run(tmp_path, gh, tag)

    assert result.returncode != 0
    assert _calls(log) == []


def test_refuses_while_a_workflow_job_has_not_reported_yet(tmp_path: Path) -> None:
    """2026-08-26 실측 — 무관한 체크 1건만 green 인 순간을 통과시켰다."""
    gh, tag, log = _stubs(tmp_path, [_view([_check("GitGuardian Security Checks")])])

    result = _run(tmp_path, gh, tag)

    assert result.returncode != 0
    assert _calls(log) == []
    assert any(job in result.stdout + result.stderr for job in _workflow_jobs())


def test_waits_for_an_in_progress_check_and_then_merges(tmp_path: Path) -> None:
    pending = [_check(_workflow_jobs()[0], status="IN_PROGRESS", conclusion=None), *_all_green()[1:]]
    gh, tag, log = _stubs(tmp_path, [_view(pending), _view(_all_green())])

    result = _run(tmp_path, gh, tag)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _calls(log) == ["merge"]


def test_unknown_mergeability_is_not_reported_as_a_conflict(tmp_path: Path) -> None:
    """GitHub may transiently return UNKNOWN immediately after a push."""
    gh, tag, log = _stubs(tmp_path, [_view(_all_green(), mergeable="UNKNOWN")])

    result = _run(
        tmp_path,
        gh,
        tag,
        MERGE_PR_DEADLINE_SECONDS="0",
        MERGE_PR_MERGEABILITY_RETRIES="1",
        MERGE_PR_MERGEABILITY_POLL_SECONDS="0",
    )

    output = result.stdout + result.stderr
    assert result.returncode == 4
    assert "MERGEABILITY-UNKNOWN" in output
    assert "Merge origin/main into the branch" not in output
    assert _view_call_count(tmp_path) == 2
    assert _calls(log) == []


def test_repolls_unknown_mergeability_until_the_pull_request_is_mergeable(tmp_path: Path) -> None:
    unknown = _view(_all_green(), mergeable="UNKNOWN")
    gh, tag, log = _stubs(tmp_path, [unknown, _view(_all_green())])

    result = _run(
        tmp_path,
        gh,
        tag,
        MERGE_PR_MERGEABILITY_RETRIES="1",
        MERGE_PR_MERGEABILITY_POLL_SECONDS="0",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert _view_call_count(tmp_path) == 2
    assert _calls(log) == ["merge"]


def test_refuses_a_pull_request_that_cannot_be_merged_cleanly(tmp_path: Path) -> None:
    """2026-08-26 실측 — 충돌은 gh 가 실패한 뒤가 아니라 판정 단계에서 걸러야 한다."""
    gh, tag, log = _stubs(tmp_path, [_view(_all_green(), mergeable="CONFLICTING")])

    result = _run(tmp_path, gh, tag)

    assert result.returncode != 0
    assert "main" in result.stdout + result.stderr
    assert _calls(log) == []


def test_a_merge_cuts_no_release_tag(tmp_path: Path) -> None:
    """VA-3: 머지는 축적이다 — 성공한 머지 뒤에도 태그 호출이 없어야 한다."""
    gh, tag, log = _stubs(tmp_path, [_view(_all_green())])

    result = _run(tmp_path, gh, tag)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _calls(log) == ["merge"]
    assert "release.sh" in result.stdout + result.stderr


def test_a_failed_merge_runs_nothing_else(tmp_path: Path) -> None:
    gh, tag, log = _stubs(tmp_path, [_view(_all_green())], merge_rc=1)

    result = _run(tmp_path, gh, tag)

    assert result.returncode != 0
    assert _calls(log) == ["merge"]


def test_refuses_a_pull_request_that_is_not_open(tmp_path: Path) -> None:
    gh, tag, log = _stubs(tmp_path, [_view(_all_green(), state="MERGED")])

    result = _run(tmp_path, gh, tag)

    assert result.returncode != 0
    assert _calls(log) == []


def test_refuses_a_pull_request_that_does_not_target_main(tmp_path: Path) -> None:
    gh, tag, log = _stubs(tmp_path, [_view(_all_green(), base="develop")])

    result = _run(tmp_path, gh, tag)

    assert result.returncode != 0
    assert _calls(log) == []


def test_the_escape_hatch_merges_unchecked_but_announces_itself(tmp_path: Path) -> None:
    gh, tag, log = _stubs(tmp_path, [_view([])])

    result = _run(tmp_path, gh, tag, MERGE_PR_ALLOW_UNCHECKED="1")

    assert result.returncode == 0, result.stdout + result.stderr
    assert _calls(log) == ["merge"]
    assert "MERGE_PR_ALLOW_UNCHECKED" in result.stderr


def test_the_escape_hatch_does_not_cover_a_failing_check(tmp_path: Path) -> None:
    failing = [_check(_workflow_jobs()[0], conclusion="FAILURE"), *_all_green()[1:]]
    gh, tag, log = _stubs(tmp_path, [_view(failing)])

    result = _run(tmp_path, gh, tag, MERGE_PR_ALLOW_UNCHECKED="1")

    assert result.returncode != 0
    assert _calls(log) == []


def test_no_source_file_merges_a_pull_request_outside_the_wrapper() -> None:
    """주석으로 적으면 grep 은 통과하고 동작은 없다 — 코드가 진실이어야 한다."""
    listing = subprocess.run(
        ("git", "-C", str(_REPO), "ls-files", "automation", "skills", "tests"),
        capture_output=True, text=True, check=True,
    ).stdout.split()
    offenders = []
    for relative in listing:
        if relative in {"automation/merge-pr.sh", "tests/unit/test_merge_pr_gate.py"}:
            continue
        if not relative.endswith((".sh", ".py")):
            continue
        if "gh pr merge" in (_REPO / relative).read_text(encoding="utf-8", errors="replace"):
            offenders.append(relative)

    assert not offenders, (
        "these files merge a pull request without the check gate; route them through "
        f"automation/merge-pr.sh: {offenders}"
    )


def test_the_wrapper_ships_executable() -> None:
    assert os.access(_WRAPPER, os.X_OK)


def _external_stuck() -> list[dict[str, object]]:
    """워크플로 잡은 전부 green 이고 외부 앱 체크 하나만 끝나지 않은 모양."""
    return [*_all_green(), _check(_STUCK_APP_CHECK, status="IN_PROGRESS", conclusion=None)]


def _gitleaks(tmp_path: Path, rc: int) -> Path:
    """받은 stdin 을 남기고 정해진 코드로 끝나는 gitleaks 이음새 — 0 깨끗함, 3 발견."""
    stub = tmp_path / "gitleaks-stub"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'cat > "{tmp_path}/scanned.diff"\n'
        f'printf "scan-%s\\n" "$1" >> "{tmp_path}/calls.log"\n'
        f"exit {rc}\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return stub


def test_a_stuck_external_check_merges_once_the_compensating_scan_is_clean(tmp_path: Path) -> None:
    # Given: every workflow job is green and only an app check stays in progress.
    gh, tag, log = _stubs(tmp_path, [_view(_external_stuck())])
    scanner = _gitleaks(tmp_path, 0)

    # When: its grace has already run out.
    result = _run(
        tmp_path, gh, tag, MERGE_PR_EXTERNAL_GRACE_SECONDS="0", MERGE_PR_GITLEAKS=str(scanner)
    )

    # Then: the pull request's diff is scanned first, then merged, and the bypass is announced.
    assert result.returncode == 0, result.stdout + result.stderr
    assert _calls(log) == ["scan-stdin", "merge"]
    assert (tmp_path / "scanned.diff").read_text(encoding="utf-8") == _DIFF
    assert "EXTERNAL-UNSETTLED" in result.stderr
    assert _STUCK_APP_CHECK in result.stderr


def test_a_stuck_external_check_is_refused_when_the_diff_holds_a_secret(tmp_path: Path) -> None:
    gh, tag, log = _stubs(tmp_path, [_view(_external_stuck())])

    result = _run(
        tmp_path, gh, tag,
        MERGE_PR_EXTERNAL_GRACE_SECONDS="0", MERGE_PR_GITLEAKS=str(_gitleaks(tmp_path, 3)),
    )

    assert result.returncode != 0
    assert _calls(log) == ["scan-stdin"]


def test_a_stuck_external_check_is_refused_without_a_scanner(tmp_path: Path) -> None:
    gh, tag, log = _stubs(tmp_path, [_view(_external_stuck())])

    result = _run(
        tmp_path, gh, tag,
        MERGE_PR_EXTERNAL_GRACE_SECONDS="0", MERGE_PR_GITLEAKS=str(tmp_path / "no-such-gitleaks"),
    )

    assert result.returncode != 0
    assert _calls(log) == []


def test_a_stuck_external_check_is_refused_when_the_diff_cannot_be_read(tmp_path: Path) -> None:
    gh, tag, log = _stubs(tmp_path, [_view(_external_stuck())], diff=None)

    result = _run(
        tmp_path, gh, tag,
        MERGE_PR_EXTERNAL_GRACE_SECONDS="0", MERGE_PR_GITLEAKS=str(_gitleaks(tmp_path, 0)),
    )

    assert result.returncode != 0
    assert _calls(log) == []


def test_an_external_check_is_not_bypassed_before_its_grace_runs_out(tmp_path: Path) -> None:
    # Given: a grace longer than the gate's deadline (2 s in _run).
    gh, tag, log = _stubs(tmp_path, [_view(_external_stuck())])

    result = _run(
        tmp_path, gh, tag,
        MERGE_PR_EXTERNAL_GRACE_SECONDS="3600", MERGE_PR_GITLEAKS=str(_gitleaks(tmp_path, 0)),
    )

    # Then: nothing is scanned or merged; it times out like any unsettled check.
    assert result.returncode != 0
    assert _calls(log) == []
    assert _STUCK_APP_CHECK in result.stderr


def test_a_pending_workflow_job_is_never_compensated_by_the_scan(tmp_path: Path) -> None:
    pending = [
        _check(_workflow_jobs()[0], status="IN_PROGRESS", conclusion=None),
        *_all_green()[1:],
        _check(_STUCK_APP_CHECK, status="IN_PROGRESS", conclusion=None),
    ]
    gh, tag, log = _stubs(tmp_path, [_view(pending)])

    result = _run(
        tmp_path, gh, tag,
        MERGE_PR_EXTERNAL_GRACE_SECONDS="0", MERGE_PR_GITLEAKS=str(_gitleaks(tmp_path, 0)),
    )

    assert result.returncode != 0
    assert _calls(log) == []


def test_a_failing_external_check_is_refused_even_when_the_scan_would_be_clean(
    tmp_path: Path,
) -> None:
    failing = [*_all_green(), _check(_STUCK_APP_CHECK, conclusion="FAILURE")]
    gh, tag, log = _stubs(tmp_path, [_view(failing)])

    result = _run(
        tmp_path, gh, tag,
        MERGE_PR_EXTERNAL_GRACE_SECONDS="0", MERGE_PR_GITLEAKS=str(_gitleaks(tmp_path, 0)),
    )

    assert result.returncode != 0
    assert _calls(log) == []


def _helper_stub(tmp_path: Path, changed: str, body: str) -> tuple[Path, Path, Path]:
    """Wrap the fake gh: chosen changed files for pr diff --name-only, chosen PR body."""
    gh, tag, log = _stubs(tmp_path, [_view(_all_green())])
    inner = tmp_path / "gh-inner"
    gh.rename(inner)
    (tmp_path / "changed.txt").write_text(changed, encoding="utf-8")
    (tmp_path / "body.txt").write_text(body, encoding="utf-8")
    gh.write_text(
        "#!/usr/bin/env bash\n"
        f'dir="{tmp_path}"\n'
        'if [[ "$1 $2" == "pr diff" && " $* " == *" --name-only "* ]]; then cat "$dir/changed.txt"; exit 0; fi\n'
        'if [[ "$1 $2" == "pr view" && " $* " == *" body "* ]]; then printf "body\\n" >> "$dir/calls.log"; cat "$dir/body.txt"; exit 0; fi\n'
        f'exec "{inner}" "$@"\n',
        encoding="utf-8",
    )
    gh.chmod(0o755)
    return gh, tag, log


def test_a_root_helper_change_without_the_owner_step_is_refused(tmp_path: Path) -> None:
    # Given: a PR that changes a file the node keeps as a root-owned copy, with no marker.
    gh, tag, log = _helper_stub(tmp_path, "automation/node_config.py\nREADME.md\n", "plain body")
    # When/Then: it does not merge and names the file and its provisioner.
    result = _run(tmp_path, gh, tag)
    assert result.returncode != 0
    assert "automation/node_config.py (provision-deploy-converge.sh)" in result.stderr
    assert "merge" not in _calls(log)


def test_a_root_helper_change_that_names_the_owner_step_merges(tmp_path: Path) -> None:
    gh, tag, log = _helper_stub(tmp_path, "automation/node_config.py\n",
                                "ROOT-HELPER-BLOCK: owner reruns provision-deploy-converge.sh")
    result = _run(tmp_path, gh, tag)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "merge" in _calls(log)


def test_an_unrelated_change_merges_without_reading_the_body(tmp_path: Path) -> None:
    gh, tag, log = _helper_stub(tmp_path, "README.md\n", "plain body")
    result = _run(tmp_path, gh, tag)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "body" not in _calls(log)
