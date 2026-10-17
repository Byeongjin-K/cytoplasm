"""도리 포크 고정 커밋 — 설치기는 이 SHA 하나만 받고, 받은 것이 그 SHA 인지 다시 본다.

포크의 install.sh 는 main 을 그대로 pull 한다. 서명된 cytoplasm 릴리스가 무엇이 깔릴지를
정하려면 고정값이 틀렸을 때, 그 커밋을 받을 수 없을 때, 목적지가 이미 있을 때 모두 아무것도
남기지 않고 멈춰야 한다. 원격은 로컬 git 저장소로 대신한다(네트워크 없음).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from automation import dori_pin
from automation.dori_pin import DoriPin, PinError, fetch_at, load_pin, parse_pin

_GIT_ENV = ("-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false")


def _origin(tmp_path: Path) -> tuple[Path, str]:
    origin = tmp_path / "fork"
    origin.mkdir()
    _ = subprocess.run(("git", "init", "--quiet", "-b", "main", str(origin)), check=True)
    (origin / "README.md").write_text("fork\n", encoding="utf-8")
    _ = subprocess.run(("git", "-C", str(origin), "add", "."), check=True)
    _ = subprocess.run(("git", *_GIT_ENV, "-C", str(origin), "commit", "--quiet", "-m", "c1"), check=True)
    sha = subprocess.run(
        ("git", "-C", str(origin), "rev-parse", "HEAD"), check=True, capture_output=True, text=True
    ).stdout.strip()
    return origin, sha


def test_the_shipped_pin_names_a_full_commit_of_the_fork() -> None:
    pin = load_pin()

    assert pin.repo.startswith("https://github.com/") and pin.repo.endswith(".git")
    assert len(pin.commit) == 40 and all(ch in "0123456789abcdef" for ch in pin.commit)


@pytest.mark.parametrize(
    ("text", "marker"),
    [
        ('schema = 1\nrepo = "https://x/y.git"\ncommit = "abc123"\n', "commit must be a full"),
        ('schema = 2\nrepo = "https://x/y.git"\ncommit = "' + "a" * 40 + '"\n', "schema"),
        ('schema = 1\nrepo = "git@x:y.git"\ncommit = "' + "a" * 40 + '"\n', "https"),
        ("not toml [", "not TOML"),
    ],
)
def test_a_malformed_pin_is_refused_by_name(text: str, marker: str) -> None:
    with pytest.raises(PinError, match="DORI-PIN-INVALID") as caught:
        _ = parse_pin(text)
    assert marker in str(caught.value)


def test_fetch_puts_exactly_the_pinned_commit_at_the_destination(tmp_path: Path) -> None:
    origin, sha = _origin(tmp_path)
    dest = tmp_path / "home" / ".dori" / "src"

    fetch_at(DoriPin(str(origin), sha), dest)

    assert dori_pin.checkout_head(dest) == sha
    assert (dest / "README.md").read_text(encoding="utf-8") == "fork\n"


def test_an_unknown_commit_leaves_nothing_behind(tmp_path: Path) -> None:
    origin, _ = _origin(tmp_path)
    dest = tmp_path / "home" / ".dori" / "src"

    with pytest.raises(PinError, match="DORI-PIN-FETCH-FAIL"):
        fetch_at(DoriPin(str(origin), "0" * 40), dest)

    assert not dest.exists()
    assert not (tmp_path / "home").exists()


def test_an_existing_destination_is_never_touched(tmp_path: Path) -> None:
    origin, sha = _origin(tmp_path)
    dest = tmp_path / "src"
    dest.mkdir()
    (dest / "mine.txt").write_text("keep\n", encoding="utf-8")

    with pytest.raises(PinError, match="DORI-PIN-DEST-EXISTS"):
        fetch_at(DoriPin(str(origin), sha), dest)

    assert (dest / "mine.txt").read_text(encoding="utf-8") == "keep\n"


def test_verify_reports_a_checkout_on_another_commit(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    origin, _ = _origin(tmp_path)

    assert dori_pin.main(("verify", "--checkout", str(origin))) == 1
    assert "DORI-PIN-MISMATCH" in capsys.readouterr().out
