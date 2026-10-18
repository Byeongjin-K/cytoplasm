"""설치 마법사의 도리 선택지 — 신뢰키·root 없이 도리 설치기로 가고, Hermes 경로는 그대로다.

기존 마법사 시험(test_install_wizard.py)은 Hermes 프로필의 흐름을 고정한다. 도리는 그 흐름과
다른 설치기(`automation.install.dori`)로 가므로 따로 둔다. 마법사는 argv 만 조립하고, 명령은
주입된 콘솔이 기록만 한다.
"""
from __future__ import annotations

from pathlib import Path

from automation.install.wizard import main

_DORI = ("python3", "-m", "automation.install.dori")


class _Fake:
    def __init__(self, lines: list[str], *, tty: bool = True, rc: int = 0) -> None:
        self.lines = list(lines)
        self.tty = tty
        self.rc = rc
        self.commands: list[tuple[str, ...]] = []
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
        return self.rc, "DORI-PLAN-OK\n"

    def write(self, text: str) -> None:
        self.printed.append(text)


def test_the_dori_profile_needs_no_trust_key_and_only_plans_on_dry_run_only() -> None:
    fake = _Fake([], tty=False)

    code = main(("--profile", "dori", "--dori-role", "okdori", "--owner-call", "형", "--dry-run-only"), io=fake)

    assert code == 0
    assert fake.commands == [(*_DORI, "--role", "okdori", "--owner-call", "형", "--dry-run")]
    assert not any("WIZARD-TRUST-KEY" in text for text in fake.printed)


def test_dori_from_the_menu_plans_then_installs_after_yes() -> None:
    fake = _Fake(["", "yes"])

    assert main((), io=fake) == 0

    assert fake.commands == [(*_DORI, "--dry-run"), _DORI]
    assert any("도리 설치 완료" in text for text in fake.printed)


def test_anything_but_yes_installs_nothing() -> None:
    fake = _Fake(["dori", "n"])

    assert main((), io=fake) == 3
    assert fake.commands == [(*_DORI, "--dry-run")]


def test_a_refused_plan_stops_before_the_question() -> None:
    fake = _Fake(["1"], rc=2)

    assert main((), io=fake) == 2
    assert fake.commands == [(*_DORI, "--dry-run")]
    assert fake.lines == []


def test_without_a_terminal_the_dori_install_still_needs_an_explicit_yes() -> None:
    fake = _Fake([], tty=False)

    assert main(("--profile", "dori"), io=fake) == 2
    assert fake.commands == []


def test_legacy_component_cannot_be_silently_ignored_by_default_wizard() -> None:
    # Given
    console = _Fake([], tty=False)
    # When
    result = main(("--with-component", "doctor-watch", "--dry-run-only"), io=console)
    # Then
    assert result == 2
    assert console.commands == []
