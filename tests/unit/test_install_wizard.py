"""The install wizard asks, explains and confirms; the installer decides.

`quickstart.sh` already proved the order that keeps a first install safe — plan without
root, show it, get an explicit yes, only then escalate. The wizard keeps that order and
adds what the shell wrapper could not: it asks the few questions a third party would
otherwise answer by editing TOML, writes the config for them, and names the profile so the
healthcheck declaration is fixed at install time. It contains no install logic — every
proof below is either pure or drives `main` through injected I/O, so nothing here can
escalate privilege or touch the host.
"""
from __future__ import annotations

import base64
from dataclasses import replace
from pathlib import Path
from typing import Final

import pytest

from automation.install.installer import main as installer_main
from automation.node_config import load_node_config

_REPO: Final = Path(__file__).resolve().parents[2]


def _public_key() -> str:
    algorithm = b"ssh-ed25519"
    material = len(algorithm).to_bytes(4, "big") + algorithm
    material += (32).to_bytes(4, "big") + bytes(range(32))
    return f"ssh-ed25519 {base64.b64encode(material).decode()} wizard-test"


def _answers(tmp_path: Path, **overrides: object):
    from automation.install.wizard_screens import Answers

    key = tmp_path / "trust.pub"
    _ = key.write_text(f"{_public_key()}\n", encoding="utf-8")
    base = Answers(
        profile="rag",
        origin_url="https://github.com/orientpine/cytoplasm.git",
        node_name="lab-node-1",
        operator_account="mingeuk",
        config_path=tmp_path / "node.toml",
        trust_key=key,
        expected_fingerprint=None,
        components=(),
    )
    return replace(base, **overrides)


class _Fake:
    """Injected I/O: scripted answers in, every command the wizard would run recorded."""

    def __init__(
        self, lines: list[str], *, tty: bool = True, dry_run_output: str = "",
        install_rc: int = 0, doctor_outputs: list[str] | None = None,
    ) -> None:
        self.lines = list(lines)
        self.tty = tty
        self.commands: list[tuple[str, ...]] = []
        self.dry_run_output = dry_run_output
        self.install_rc = install_rc
        self.doctor_outputs = list(doctor_outputs or [])
        self.printed: list[str] = []
        self.environ: dict[str, str] = {}
        self.euid = 1000
        self.log_dir: Path | None = None

    def read_line(self, prompt: str) -> str:
        self.printed.append(prompt)
        if not self.lines:
            raise EOFError
        return self.lines.pop(0)

    def run(self, argv: tuple[str, ...]) -> tuple[int, str]:
        self.commands.append(argv)
        if "--dry-run" in argv:
            return 0, self.dry_run_output
        if argv[-1] == "automation.doctor":
            return 0, self.doctor_outputs.pop(0) if self.doctor_outputs else "--- DOCTOR agent: 통과 9 · 확인 0 · 고장 0\n"
        return self.install_rc, "[PASS] healthcheck: healthcheck.sh ALL_HEALTHY\n--- INSTALLED: 3건 중 실패 0 / 경고 0\n"

    def write(self, text: str) -> None:
        self.printed.append(text)


def _real_dry_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> str:
    key = tmp_path / "trust.pub"
    _ = key.write_text(f"{_public_key()}\n", encoding="utf-8")
    assert installer_main(("--update-trust-key", str(key), "--dry-run", "--profile", "rag")) == 0
    return capsys.readouterr().out


# --- pure seams -----------------------------------------------------------------------


def test_the_wizard_composes_the_same_installer_argv_the_docs_show(tmp_path: Path) -> None:
    from automation.install.wizard_screens import installer_argv

    answers = _answers(tmp_path, expected_fingerprint="SHA256:abc")
    dry = installer_argv(answers, dry_run=True, as_root=False)
    apply = installer_argv(answers, dry_run=False, as_root=False)

    assert dry[:3] == ("python3", "-m", "automation.install")
    assert "--dry-run" in dry and "sudo" not in dry
    assert ("--profile", "rag") == tuple(dry[dry.index("--profile"):dry.index("--profile") + 2])
    assert ("--expect-update-trust-fingerprint", "SHA256:abc") == tuple(
        apply[apply.index("--expect-update-trust-fingerprint"):][:2]
    )
    assert apply[0] == "sudo" and "--dry-run" not in apply


def test_a_fingerprint_the_operator_did_not_give_is_never_invented(tmp_path: Path) -> None:
    from automation.install.wizard_screens import installer_argv

    argv = installer_argv(_answers(tmp_path), dry_run=True, as_root=True)

    assert "--expect-update-trust-fingerprint" not in argv
    assert argv[0] != "sudo"


def test_the_rendered_node_toml_is_one_the_installer_loads(tmp_path: Path) -> None:
    from automation.install.wizard_screens import render_node_toml

    answers = _answers(tmp_path)
    path = answers.config_path
    _ = path.write_text(render_node_toml(answers), encoding="utf-8")

    config = load_node_config(path)
    assert config.origin_url == answers.origin_url
    assert config.primary_node_name == "lab-node-1" and config.rag_node_name == "lab-node-1"
    assert config.operator_account == "mingeuk"
    assert config.require_signed_updates is True
    assert config.peer_attest_mode == "signed"
    assert config.deploy_ssh_host == ""


def test_the_plan_summary_is_counted_from_the_real_dry_run_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from automation.install.wizard_screens import summarize_plan

    output = _real_dry_run(tmp_path, capsys)
    numbered = [line for line in output.splitlines() if line[:2].isdigit() and line[2:4] == ". "]

    summary = summarize_plan(output)

    assert f"{len(numbered)}" in summary
    assert "healthcheck.env" in summary
    assert "timer" in summary and "check" in summary


def test_the_verdict_summary_names_the_next_action() -> None:
    from automation.install.wizard_screens import summarize_verdict

    done = summarize_verdict(
        "[PASS] trust-key.fingerprint: 공지 지문과 일치\n"
        "[PASS] healthcheck: healthcheck.sh ALL_HEALTHY\n--- INSTALLED: 60건 중 실패 0 / 경고 0\n",
        returncode=0,
    )
    stopped = summarize_verdict(
        "26. check hermes-gateway\n[FAIL] hermes-gateway: Hermes is an external prerequisite for agent\n"
        "--- NOT-INSTALLED: 1건 중 실패 1 / 경고 0\n",
        returncode=1,
    )

    assert "설치 완료" in done and "다음" in done
    assert "hermes-gateway" in stopped and "다시 실행" in stopped


# --- main through injected I/O ---------------------------------------------------------


def test_without_a_terminal_and_without_yes_the_wizard_refuses_by_name(tmp_path: Path) -> None:
    from automation.install.wizard import main

    fake = _Fake([], tty=False)
    answers = _answers(tmp_path)
    code = main(
        (
            "--profile", answers.profile, "--origin-url", answers.origin_url,
            "--node-name", answers.node_name, "--operator", answers.operator_account,
            "--config", str(answers.config_path), "--update-trust-key", str(answers.trust_key),
        ),
        io=fake,
    )

    assert code == 2
    assert any("WIZARD-NO-TTY" in text for text in fake.printed)
    assert fake.commands == []


def test_anything_but_yes_ends_the_run_before_any_sudo(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from automation.install.wizard import main

    dry_run_output = _real_dry_run(tmp_path, capsys)
    fake = _Fake(["n"], dry_run_output=dry_run_output)
    answers = _answers(tmp_path)

    code = main(
        (
            "--profile", answers.profile, "--origin-url", answers.origin_url,
            "--node-name", answers.node_name, "--operator", answers.operator_account,
            "--config", str(answers.config_path), "--update-trust-key", str(answers.trust_key),
        ),
        io=fake,
    )

    assert code == 3
    assert len(fake.commands) == 1 and "--dry-run" in fake.commands[0]
    assert not any(argv[0] == "sudo" for argv in fake.commands)
    assert answers.config_path.is_file()


def test_yes_runs_the_apply_once_with_the_same_arguments(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from automation.install.wizard import main

    fake = _Fake(["yes"], dry_run_output=_real_dry_run(tmp_path, capsys))
    answers = _answers(tmp_path)

    code = main(
        (
            "--profile", answers.profile, "--origin-url", answers.origin_url,
            "--node-name", answers.node_name, "--operator", answers.operator_account,
            "--config", str(answers.config_path), "--update-trust-key", str(answers.trust_key),
        ),
        io=fake,
    )

    assert code == 0
    dry, apply, doctor = fake.commands
    assert "--dry-run" in dry and "--dry-run" not in apply
    assert [arg for arg in dry if arg != "--dry-run"] == [arg for arg in apply if arg != "sudo"]
    assert doctor == ("python3", "-m", "automation.doctor")


def test_the_wizard_asks_for_what_the_flags_did_not_give(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from automation.install.wizard import main

    answers = _answers(tmp_path)
    fake = _Fake(
        ["3", answers.origin_url, answers.node_name, answers.operator_account, "", "n"],
        dry_run_output=_real_dry_run(tmp_path, capsys),
    )

    code = main(
        ("--config", str(answers.config_path), "--update-trust-key", str(answers.trust_key)),
        io=fake,
    )

    assert code == 3
    assert fake.lines == []
    assert ("--profile", "rag") == tuple(fake.commands[0][fake.commands[0].index("--profile"):][:2])
    assert load_node_config(answers.config_path).origin_url == answers.origin_url


def test_a_mistyped_profile_at_the_prompt_is_asked_again_not_crashed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from automation.install.wizard import main

    answers = _answers(tmp_path)
    fake = _Fake(["rga", "report-hub", "", "", "", "", "n"], dry_run_output=_real_dry_run(tmp_path, capsys))

    code = main(
        ("--config", str(answers.config_path), "--update-trust-key", str(answers.trust_key)),
        io=fake,
    )

    assert code == 3
    assert any("rga" in text for text in fake.printed)
    assert ("--profile", "report-hub") == tuple(
        fake.commands[0][fake.commands[0].index("--profile"):][:2]
    )


def test_an_unknown_profile_flag_is_refused_listing_the_known_ones(tmp_path: Path) -> None:
    from automation.install.wizard import main

    fake = _Fake([])
    code = main(("--profile", "rga", "--update-trust-key", str(tmp_path / "k.pub")), io=fake)

    assert code == 2
    joined = "".join(fake.printed)
    assert "rga" in joined and "report-hub" in joined and "full" in joined
    assert fake.commands == []


def test_an_unreadable_trust_key_is_named_before_anything_runs(tmp_path: Path) -> None:
    from automation.install.wizard import main

    fake = _Fake([])
    missing = tmp_path / "absent.pub"
    code = main(("--profile", "core", "--update-trust-key", str(missing), "--yes"), io=fake)

    assert code == 2
    assert any(str(missing) in text for text in fake.printed)
    assert fake.commands == []


# --- 연결·승인 안내(⑦) ------------------------------------------------------------------

_BROKEN: Final = "[FAIL] codex-oauth · 주 모델 로그인 — Codex 구독 로그인이 없다\n--- DOCTOR agent: 통과 8 · 확인 0 · 고장 1\n"
_SETTLED: Final = "[WARN] institutional-mail · 기관메일 — 미구성\n--- DOCTOR agent: 통과 8 · 확인 1 · 고장 0\n"


def _install(tmp_path: Path, capsys: pytest.CaptureFixture[str], fake: _Fake, *extra: str) -> int:
    from automation.install.wizard import main

    answers = _answers(tmp_path)
    fake.dry_run_output = _real_dry_run(tmp_path, capsys)
    return main(
        (
            "--profile", answers.profile, "--origin-url", answers.origin_url,
            "--node-name", answers.node_name, "--operator", answers.operator_account,
            "--config", str(answers.config_path), "--update-trust-key", str(answers.trust_key), *extra,
        ),
        io=fake,
    )


def _doctor_runs(fake: _Fake) -> int:
    return sum(1 for argv in fake.commands if argv == ("python3", "-m", "automation.doctor"))


def test_the_approvals_a_person_must_give_are_listed_before_the_install_starts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from automation.doctor.capabilities import CAPABILITIES

    fake = _Fake(["n"])
    assert _install(tmp_path, capsys, fake) == 3

    before_install = "".join(fake.printed)
    for capability in CAPABILITIES:
        if capability.approve_at:
            assert capability.approve_at in before_install
    assert _doctor_runs(fake) == 0


def test_after_install_the_wizard_rechecks_on_enter_until_nothing_is_broken(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = _Fake(["yes", ""], doctor_outputs=[_BROKEN, _SETTLED])

    assert _install(tmp_path, capsys, fake) == 0
    assert _doctor_runs(fake) == 2
    assert fake.lines == []
    assert any("필수 연결이 모두 통과" in text for text in fake.printed)


def test_q_leaves_the_connection_loop_without_changing_the_install_result(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = _Fake(["yes", "q"], doctor_outputs=[_BROKEN, _BROKEN])

    assert _install(tmp_path, capsys, fake) == 0
    assert _doctor_runs(fake) == 1


def test_an_unreached_account_keeps_the_loop_open(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    unreached = "[WARN] doctor-run · agent 계정 진단 — 실행하지 못했다: sudo: a password is required\n"
    fake = _Fake(["yes", "q"], doctor_outputs=[unreached])

    assert _install(tmp_path, capsys, fake) == 0
    assert _doctor_runs(fake) == 1
    assert any("다시 점검" in text for text in fake.printed)


def test_non_interactive_runs_doctor_once_and_never_waits(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = _Fake([], doctor_outputs=[_BROKEN])

    assert _install(tmp_path, capsys, fake, "--yes") == 0
    assert _doctor_runs(fake) == 1


def test_a_failed_install_never_runs_doctor(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    failed = _Fake(["yes"], install_rc=1)

    assert _install(tmp_path, capsys, failed) == 1
    assert _doctor_runs(failed) == 0


def test_a_dry_run_only_never_runs_doctor(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    planned = _Fake([])

    assert _install(tmp_path, capsys, planned, "--dry-run-only") == 0
    assert _doctor_runs(planned) == 0
