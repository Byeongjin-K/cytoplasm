"""헬스체크 허용 목록(강제명령 래퍼) 갱신을 릴리스 ✅ 하나에 묶는다 — 소유자 지시 2026-10-14.

카드 한 줄 · 승인 해시 결합 · ✅ 없이는 설치하지 않음 · 설치 뒤 지문 불일치는 실패로 영수증에 남음 ·
지문이 트리 위치가 아니라 내용만 말함(승인 전 스테이징 트리 = 수렴 뒤 /srv 트리).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Final

import pytest

from automation import deploy_all, deploy_receipt, release_approval, release_notes, release_plan
from automation.deploy_all import parse_observations
from automation.deploy_all_kinds import ObservationError
from automation.release_spec import ReleaseSpec

_REPO: Final = Path(__file__).resolve().parents[2]
_HELPER: Final = _REPO / "automation" / "healthcheck_wrapper_release.sh"
_GENERATOR: Final = _REPO / "automation" / "healthcheck_probe_wrapper.sh"
_EXAMPLE_CONFIG: Final = _REPO / "configs" / "node.example.toml"
_PRIMARY: Final = "example-primary-node"
_RAG: Final = "example-rag-node"
_D1: Final = "1" * 64
_D2: Final = "2" * 64
_LINE: Final = "헬스체크 허용 목록도 갱신"


# --- 카드 ---------------------------------------------------------------------------


def _spec(*extra: tuple[str, str]) -> ReleaseSpec:
    return ReleaseSpec(
        version="v1.2.3",
        head_sha="a" * 40,
        release_nonce="b" * 32,
        surface_digests=(("skill:meeting", "d" * 64), ("repo", "c" * 64), *extra),
        patch_notes="- meeting skill",
        detail_message_ids=("123456789012345678",),
        detail_channel_id="123456789012345679",
        detail_guild_id="123456789012345680",
    )


def test_a_wrapper_surface_adds_one_card_line_and_is_bound() -> None:
    plain = _spec()
    bound = _spec(("healthcheck-wrapper:node-a", _D1), ("healthcheck-wrapper:node-b", _D2))

    card = bound.render()

    assert f"{_LINE}: node-a, node-b" in card
    assert card.count(_LINE) == 1
    assert "healthcheck-wrapper" not in card.split(_LINE)[0], "묶음 요약에 표면 이름이 새지 않는다"
    assert bound.action_hash() != plain.action_hash(), "갱신은 승인 해시에 묶여야 한다"


def test_no_wrapper_surface_means_no_card_line() -> None:
    card = _spec().render()

    assert _LINE not in card
    assert "healthcheck-wrapper" not in card


# --- plan CLI ------------------------------------------------------------------------


def _fake_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = release_plan.ReleasePlan(
        version="v1.2.3",
        base="e" * 40,
        head="a" * 40,
        tree_digest="f" * 64,
        changed_paths=(),
        surface_digests=(("repo", "c" * 64),),
        commit_titles=(),
        major_signals=(),
        commits=(),
    )
    monkeypatch.setattr(release_plan, "build_plan", lambda *_a, **_k: plan)
    monkeypatch.setattr(release_notes, "major_note", lambda _plan: "")
    monkeypatch.setattr(release_notes, "detail_body", lambda _plan: "- x")


def _plan_args(*wrappers: str) -> argparse.Namespace:
    return argparse.Namespace(
        repo=".", base="e" * 40, head="a" * 40, version="v1.2.3", bump="patch",
        healthcheck_wrapper=list(wrappers) or None,
    )


def test_the_plan_binds_each_wrapper_as_a_surface(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_plan(monkeypatch)

    rc = release_approval.cmd_plan(_plan_args(f"node-a={_D1}"))

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert ["healthcheck-wrapper:node-a", _D1] in payload["surface_digests"]


@pytest.mark.parametrize("bad", ["node-a", f"Bad Host={_D1}", "node-a=xyz"])
def test_a_malformed_wrapper_argument_blocks_the_plan(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    _fake_plan(monkeypatch)

    assert release_approval.cmd_plan(_plan_args(bad)) == 4


# --- plan / refresh 셸 ----------------------------------------------------------------

_FAKE_SSH: Final = r"""#!/usr/bin/env bash
[[ "$1" == -n ]] && shift
host="$1"; cmd="$2"
printf '%s\t%s\n' "$host" "$cmd" >> "$CALLS"
node_of() { [[ "$cmd" =~ $1\ \'([^\']+)\' ]] && printf '%s' "${BASH_REMATCH[1]}"; }
case "$cmd" in
  *"mktemp -d /tmp/autophagy-wrapper-plan."*) echo /tmp/autophagy-wrapper-plan.abc123 ;;
  *"mktemp -d /tmp/autophagy-wrapper-install."*) echo /tmp/autophagy-wrapper-install.abc123 ;;
  *"for n in"*) for n in example-primary-node example-rag-node; do printf '%s %s\n' "$n" "$(cat "$STATE/release.$n")"; done ;;
  *"wrapper-inputs: //p"*) cat "$STATE/installed.$host" 2>/dev/null; true ;;
  *"--inputs-digest"*) cat "$STATE/expected.$(node_of --inputs-digest)" ;;
  *"--install"*) n="$(node_of --install)"; cp "$STATE/writes.$n" "$STATE/installed.$host" ;;
  *"--print"*) n="$(node_of --print)"
     printf '#!/usr/bin/env bash\n# wrapper-node: %s\n# wrapper-inputs: %s\nexit 126\n' "$n" "$(cat "$STATE/writes.$n")" ;;
  *"rm -rf"*) ;;
  *) echo "unexpected: $cmd" >&2; exit 97 ;;
esac
"""


def _harness(tmp_path: Path, *, auth_rc: int = 0) -> tuple[dict[str, str], Path]:
    state = tmp_path / "state"
    state.mkdir()
    fake_ssh = tmp_path / "ssh.sh"
    _ = fake_ssh.write_text(_FAKE_SSH, encoding="utf-8")
    fake_scp = tmp_path / "scp.sh"
    _ = fake_scp.write_text('printf "scp %s\\n" "$*" >> "$CALLS"\n', encoding="utf-8")
    fake_auth = tmp_path / "auth.sh"
    _ = fake_auth.write_text(
        f'printf "auth %s\\n" "$*" >> "$CALLS"; exit {auth_rc}\n', encoding="utf-8"
    )
    home = tmp_path / "home"
    home.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "TMPDIR": str(tmp_path),
        "CALLS": str(tmp_path / "calls.log"),
        "STATE": str(state),
        "HEALTHCHECK_NODE_CONFIG_PATH": str(_EXAMPLE_CONFIG),
        "DEPLOY_SSH_HOST": _PRIMARY,
        "WRAPPER_RELEASE_SSH": f"bash {fake_ssh}",
        "WRAPPER_RELEASE_SCP": f"bash {fake_scp}",
        "WRAPPER_RELEASE_AUTHORIZE_CMD": f"bash {fake_auth}",
        "WRAPPER_RELEASE_LOCAL_NODE": _RAG,
    }
    return env, state


def _write(state: Path, name: str, value: str) -> None:
    _ = (state / name).write_text(value + "\n", encoding="utf-8")


def _local_wrapper(env: dict[str, str]) -> Path:
    return Path(env["HOME"]) / ".local" / "libexec" / "autophagy-healthcheck-probe"


def _install_local(env: dict[str, str], digest: str) -> None:
    path = _local_wrapper(env)
    path.parent.mkdir(parents=True)
    _ = path.write_text(f"# wrapper-node: {_RAG}\n# wrapper-inputs: {digest}\n", encoding="utf-8")


def _helper(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("bash", str(_HELPER), *args), env=env, capture_output=True, text=True, check=False,
        timeout=60,
    )


def _head() -> str:
    return subprocess.run(
        ("git", "rev-parse", "HEAD"), cwd=_REPO, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_plan_lists_only_hosts_whose_installed_fingerprint_differs(tmp_path: Path) -> None:
    env, state = _harness(tmp_path)
    _write(state, f"release.{_PRIMARY}", _D1)
    _write(state, f"release.{_RAG}", _D2)
    _write(state, f"installed.{_PRIMARY}", _D1)
    _install_local(env, "9" * 64)

    result = _helper(env, "plan", _head())

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [f"{_RAG}={_D2}"]
    calls = (tmp_path / "calls.log").read_text(encoding="utf-8")
    assert "rm -rf -- '/tmp/autophagy-wrapper-plan.abc123'" in calls, "스테이징은 지운다"


def test_plan_with_every_host_current_lists_nothing(tmp_path: Path) -> None:
    env, state = _harness(tmp_path)
    _write(state, f"release.{_PRIMARY}", _D1)
    _write(state, f"release.{_RAG}", _D2)
    _write(state, f"installed.{_PRIMARY}", _D1)
    _install_local(env, _D2)

    result = _helper(env, "plan", _head())

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def _refresh_state(state: Path, env: dict[str, str], *, writes: str = _D1) -> None:
    _write(state, f"expected.{_PRIMARY}", _D1)
    _write(state, f"installed.{_PRIMARY}", "8" * 64)
    _write(state, f"writes.{_PRIMARY}", writes)
    _write(state, f"expected.{_RAG}", _D2)
    _write(state, f"writes.{_RAG}", _D2)
    _install_local(env, _D2)


def test_without_the_owner_approval_nothing_is_installed(tmp_path: Path) -> None:
    env, state = _harness(tmp_path, auth_rc=1)
    _refresh_state(state, env)

    result = _helper(env, "refresh", "a" * 40)

    assert f"WRAPPER-REFRESH|{_PRIMARY}|skipped|approval-unverified" in result.stdout
    calls = (tmp_path / "calls.log").read_text(encoding="utf-8")
    assert "--install" not in calls
    assert (state / f"installed.{_PRIMARY}").read_text(encoding="utf-8").strip() == "8" * 64


def test_an_approved_refresh_installs_and_reads_the_fingerprint_back(tmp_path: Path) -> None:
    env, state = _harness(tmp_path)
    _refresh_state(state, env)

    result = _helper(env, "refresh", "a" * 40)

    assert f"WRAPPER-REFRESH|{_PRIMARY}|installed|{_D1[:12]}" in result.stdout, result.stderr
    assert f"WRAPPER-REFRESH|{_RAG}|current|{_D2[:12]}" in result.stdout
    calls = (tmp_path / "calls.log").read_text(encoding="utf-8")
    assert f"auth authorize-wrapper --node {_PRIMARY} --hash {_D1} --head {'a' * 40}" in calls
    assert f"--node {_RAG}" not in calls, "이미 맞는 노드는 승인을 묻지도 않는다"


def test_a_fingerprint_mismatch_after_install_is_a_failure(tmp_path: Path) -> None:
    env, state = _harness(tmp_path)
    _refresh_state(state, env, writes="7" * 64)

    result = _helper(env, "refresh", "a" * 40)

    assert (
        f"WRAPPER-REFRESH|{_PRIMARY}|failed|mismatch:want={_D1[:12]},got={'7' * 12}"
        in result.stdout
    )


def test_a_host_without_a_release_tree_gets_the_primary_generated_wrapper(
    tmp_path: Path,
) -> None:
    env, state = _harness(tmp_path)
    _refresh_state(state, env)
    _write(state, f"installed.{_PRIMARY}", _D1)
    _local_wrapper(env).unlink()

    result = _helper(env, "refresh", "a" * 40)

    assert f"WRAPPER-REFRESH|{_RAG}|installed|{_D2[:12]}" in result.stdout, result.stderr
    installed = _local_wrapper(env).read_text(encoding="utf-8")
    assert f"# wrapper-inputs: {_D2}" in installed
    assert oct(_local_wrapper(env).stat().st_mode & 0o777) == "0o755"


# --- 영수증 ---------------------------------------------------------------------------


def _clean_plan() -> deploy_all.Plan:
    return parse_observations(
        [
            "OBS|release|abc123",
            "OBS|mounts|judged",
            "OBS|home|agent|.hermes/scripts/d.py|automation/pkg/d.py|required|aaa|aaa",
            "OBS|artifacts|0",
            "OBS|end",
        ]
    )


def test_a_failed_refresh_reaches_the_owner_through_the_receipt() -> None:
    document = json.loads(
        deploy_receipt.render_receipt(
            _clean_plan(),
            verified_at="2026-10-14T00:00:00+00:00",
            wrapper_refresh=(f"{_PRIMARY}|failed|mismatch:want={_D1[:12]},got={'7' * 12}",),
        )
    )

    assert document["wrapper_refresh"] == [
        {"node": _PRIMARY, "status": "failed", "detail": f"mismatch:want={_D1[:12]},got={'7' * 12}"}
    ]
    lines = deploy_receipt.pending_lines(document)
    assert any("헬스체크 허용 목록 갱신 실패" in line and _PRIMARY in line for line in lines)


def test_a_successful_refresh_leaves_no_owner_action() -> None:
    document = json.loads(
        deploy_receipt.render_receipt(
            _clean_plan(),
            verified_at="2026-10-14T00:00:00+00:00",
            wrapper_refresh=(f"{_PRIMARY}|installed|{_D1[:12]}",),
        )
    )

    assert deploy_receipt.pending_lines(document) == ()


def test_a_malformed_refresh_result_is_not_signed() -> None:
    with pytest.raises(ObservationError):
        _ = deploy_receipt.render_receipt(
            _clean_plan(), verified_at="2026-10-14T00:00:00+00:00", wrapper_refresh=("x|y",)
        )


# --- 생성기 지문의 위치 독립성 ---------------------------------------------------------


def test_the_inputs_digest_does_not_depend_on_where_the_tree_sits(tmp_path: Path) -> None:
    """승인 전 임시 디렉터리에 푼 트리와 수렴 뒤 /srv 트리가 같은 지문을 내야 카드가 맞는다."""
    copy = tmp_path / "staged"
    for part in ("automation", "configs"):
        _ = shutil.copytree(_REPO / part, copy / part, symlinks=True)
    env = {**os.environ, "HEALTHCHECK_SSH_USER": "", "HEALTHCHECK_SSH_IDENTITY": ""}

    here, staged = (
        subprocess.run(
            ("bash", str(root / "automation" / "healthcheck_probe_wrapper.sh"), "--inputs-digest"),
            capture_output=True, text=True, check=False, env=env, timeout=300,
        ).stdout.strip()
        for root in (_REPO, copy)
    )

    assert len(here) == 64
    assert here == staged
