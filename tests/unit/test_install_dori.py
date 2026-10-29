"""도리 프로필 설치기 — 고정 포크와 기본 도리 구성을 깔되, 이미 있는 것은 덮지 않는다.

다른 설치자가 cytoplasm 을 받아 "도리"를 고르면 리드와 비서가 깔리고, 소유자의 기존 도리
폴더는 재설치로 바뀌지 않아야 한다. 포크는 로컬 git 저장소가 대신하고, bun 은 기록만 하는
가짜 runner 가 대신한다(네트워크·실제 bun link 없음).
"""
from __future__ import annotations

import hashlib
import subprocess
from functools import partial
from pathlib import Path

import pytest

from automation import public_gate
from automation.dori_pin import DoriPin
from automation.install import dori
from automation.install.entrypoint import main as public_install

_GIT_ENV = ("-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false")
_COMMON_HEAD = "Square avatar, centered, circle-crop safe"


def _fork(tmp_path: Path) -> DoriPin:
    origin = tmp_path / "fork"
    scripts = origin / "skills" / "dori-mode" / "scripts"
    references = origin / "skills" / "dori-mode" / "references"
    scripts.mkdir(parents=True)
    references.mkdir(parents=True)
    (scripts / "package.json").write_text("{}\n", encoding="utf-8")
    (references / "config.aoe.example.json").write_text('{"backend": "aoe"}\n', encoding="utf-8")
    (references / "config.example.json").write_text('{"backend": "herdr"}\n', encoding="utf-8")
    _ = subprocess.run(("git", "init", "--quiet", "-b", "main", str(origin)), check=True)
    _ = subprocess.run(("git", "-C", str(origin), "add", "."), check=True)
    _ = subprocess.run(("git", *_GIT_ENV, "-C", str(origin), "commit", "--quiet", "-m", "fork"), check=True)
    sha = subprocess.run(
        ("git", "-C", str(origin), "rev-parse", "HEAD"), check=True, capture_output=True, text=True
    ).stdout.strip()
    return DoriPin(str(origin), sha)


class _Runner:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], Path]] = []

    def __call__(self, argv: tuple[str, ...], cwd: Path) -> int:
        self.calls.append((argv, cwd))
        return 0


def _run(home: Path, *extra: str, runner: _Runner | None = None) -> int:
    return dori.main(
        ("--src", str(home / ".dori" / "src"), "--skills", str(home / ".agents" / "skills"),
         "--workroot", str(home / "Documents"), *extra),
        runner=runner or _Runner(), which=lambda tool: f"/usr/bin/{tool}",
    )


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    monkeypatch.setenv("HOME", str(path))
    return path


def _tree(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def test_the_dry_run_plans_lead_and_secretary_and_writes_nothing(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(home, "--dry-run") == 0

    out = capsys.readouterr().out
    assert "DORI-PIN repo=" in out and "DORI-PLAN-OK" in out
    assert "역할: lead(짱돌), yoodori(유돌)" in out
    assert "okdori" not in out and "moodori" not in out
    assert _tree(home) == []


def test_optional_roles_are_added_only_when_named(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(home, "--dry-run", "--role", "okdori", "--role", "moodori", "--role", "gongdori") == 0
    assert "lead(짱돌), yoodori(유돌), okdori(옥돌), moodori(무돌), gongdori(공돌)" in capsys.readouterr().out

    assert _run(home, "--dry-run", "--role", "nope") == 2
    assert "DORI-UNKNOWN-ROLE: nope" in capsys.readouterr().out


def test_every_role_renders_completely_and_carries_no_personal_value(home: Path) -> None:
    options = dori.Options(home / ".dori" / "src", home / "s", home / "Documents", home, "형", "aoe", (), True)
    roles = dori.load_roles()
    files = dori.role_files(roles, options)
    repo = Path(dori.REPO_ROOT)
    denylist = public_gate.resolve_denylist(repo, None)

    assert {r.key for r in roles} == {"lead", "yoodori", "okdori", "moodori", "gongdori"}
    assert {p.name for p in (repo / "dori" / "roles").iterdir() if p.is_dir()} == {r.key for r in roles}
    for entry in files:
        assert "{{" not in entry.content, entry.path
        assert public_gate.scan_text(str(entry.path), entry.content, denylist) == [], entry.path
        if entry.path.name == "AGENTS.md":
            assert "형" in entry.content and entry.role.name in entry.content


def test_the_five_avatars_share_one_family_header() -> None:
    heads = set()
    for key in ("lead", "yoodori", "okdori", "moodori", "gongdori"):
        text = (Path(dori.REPO_ROOT) / "dori" / "roles" / key / "avatar.md").read_text(encoding="utf-8")
        prompt = text.split("```text\n", 1)[1].split("\n```", 1)[0]
        assert prompt.startswith(_COMMON_HEAD)
        heads.add(prompt.split("This brother:", 1)[0].rstrip())
    assert len(heads) == 1


def test_apply_installs_the_pinned_fork_and_the_default_roles(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pin = _fork(tmp_path)
    monkeypatch.setattr(dori, "load_pin", lambda: pin)
    runner = _Runner()

    assert _run(home, "--owner-call", "형", runner=runner) == 0

    src = home / ".dori" / "src"
    head = subprocess.run(("git", "-C", str(src), "rev-parse", "HEAD"), capture_output=True, text=True).stdout.strip()
    assert head == pin.commit
    assert (home / ".agents" / "skills" / "dori-mode").resolve() == (src / "skills" / "dori-mode").resolve()
    assert [argv for argv, _ in runner.calls] == [("bun", "install", "--silent"), ("bun", "link", "--silent")]
    assert (home / ".dori" / "config.json").read_text(encoding="utf-8") == '{"backend": "aoe"}\n'
    assert (home / "Documents" / "zzangdori" / "AGENTS.md").is_file()
    assert (home / "Documents" / "yoodori" / "avatar.md").is_file()
    assert not (home / "Documents" / "okdori").exists()
    assert "DORI-INSTALLED" in capsys.readouterr().out


def test_an_existing_agents_file_keeps_its_bytes_and_shows_the_difference(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(dori, "load_pin", lambda: _fork(tmp_path))
    mine = home / "Documents" / "zzangdori" / "AGENTS.md"
    mine.parent.mkdir(parents=True)
    _ = mine.write_text("# 내 리드 지침\n- 이 줄은 내가 썼다\n", encoding="utf-8")
    before = hashlib.sha256(mine.read_bytes()).hexdigest()

    assert _run(home) == 0

    out = capsys.readouterr().out
    assert hashlib.sha256(mine.read_bytes()).hexdigest() == before
    assert f"DORI-ROLE-KEEP {mine}" in out
    assert "-- 이 줄은 내가 썼다" in out
    assert "kept=1" in out


def test_a_fork_checkout_on_another_commit_blocks_everything(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pin = _fork(tmp_path)
    monkeypatch.setattr(dori, "load_pin", lambda: DoriPin(pin.repo, "1" * 40))
    src = home / ".dori" / "src"
    _ = subprocess.run(("git", "clone", "--quiet", pin.repo, str(src)), check=True)
    runner = _Runner()

    assert _run(home, runner=runner) == 2

    assert "DORI-PIN-SRC-DIFFERS" in capsys.readouterr().out
    assert runner.calls == []
    assert not (home / "Documents").exists()


def test_a_wrong_pin_stops_before_anything_is_installed(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pin = _fork(tmp_path)
    monkeypatch.setattr(dori, "load_pin", lambda: DoriPin(pin.repo, "0" * 40))
    runner = _Runner()

    assert _run(home, runner=runner) == 3

    out = capsys.readouterr().out
    assert sum(1 for line in out.splitlines() if line.startswith("DORI-PIN-FETCH-FAIL")) == 1
    assert not (home / ".dori").exists()
    assert runner.calls == []
    assert not (home / "Documents").exists()


def test_default_install_preserves_existing_node_and_dori_state(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the stopped node has paused jobs; Dori is already configured locally.
    pin = _fork(tmp_path)
    monkeypatch.setattr(dori, "load_pin", lambda: pin)
    runner = _Runner()
    monkeypatch.setattr(
        dori, "main", partial(dori.main, runner=runner, which=lambda tool: f"/usr/bin/{tool}"),
    )
    existing = {
        ".hermes/cron/jobs.json": '{"jobs": [{"id": "old-job", "state": "paused"}]}\n',
        ".hermes/gateway-stopped-by-release": "stopped",
        ".hermes/config.yaml": "model:\n  provider: preserved\n",
        ".dori/config.json": '{"backend": "herdr", "local": true}\n',
        "Documents/yoodori/AGENTS.md": "# Locally customized secretary\n",
    }
    for name, content in existing.items():
        path = home / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    # When: apply, not just dry-run, through the public default entrypoint.
    result = public_install(())
    # Then
    assert result == 0
    assert {name: (home / name).read_text(encoding="utf-8") for name in existing} == existing
    assert (home / "Documents/zzangdori/AGENTS.md").is_file()
    assert all(command[0] == "bun" for command, _cwd in runner.calls)
