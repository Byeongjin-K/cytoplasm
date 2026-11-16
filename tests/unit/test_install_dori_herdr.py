"""도리 설치기의 herdr 단계 — 새 설치는 herdr 도리를 받고, 옛 aoe 설치의 재실행은 아무것도 빼앗기지 않는다.

`test_install_dori.py` 는 포크·역할 설치를 보고, 여기서는 herdr 쪽만 본다: 고정 바이너리의 sha 확인, 유닛·웹 설정을
한 번만 쓰기, 옛 aoe 웹이 쓰는 포트를 빼앗지 않기, 새 config 에만 리드 pane 을 적기. systemctl·herdr 는 기록만 하는
가짜 capture 가, 내려받기는 가짜 fetch 가 대신한다(네트워크·실제 유닛 없음).
"""
from __future__ import annotations

import hashlib
import json
import stat
import subprocess
from pathlib import Path

import pytest

from automation.dori_pin import DoriPin
from automation.install import dori, dori_herdr

_GIT_ENV = ("-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false")
_KEY = "linux-x86_64"
_HERDR = b"herdr-0.9.5-bytes"
_TTYD = b"ttyd-1.7.7-bytes"
_CREATED = {"result": {"root_pane": {"pane_id": "w1:p1", "tab_id": "w1:t1"}}}


def _asset(name: str, body: bytes) -> dori_herdr.Asset:
    return dori_herdr.Asset(f"https://example.invalid/{name}", hashlib.sha256(body).hexdigest())


_TOOLS = dori_herdr.Tools("0.9.5", {_KEY: _asset("herdr", _HERDR)}, "1.7.7", {_KEY: _asset("ttyd", _TTYD)}, 8181, 1100, 1200)


def _fork(tmp_path: Path) -> DoriPin:
    origin = tmp_path / "fork"
    skill = origin / "skills" / "dori-mode"
    files = {
        "scripts/package.json": "{}\n",
        "references/config.aoe.example.json": '{"backend": "aoe", "leadPane": "<tmux>"}\n',
        "references/config.example.json": json.dumps({
            "backend": "herdr", "leadPane": "<your herdr pane id, from: herdr pane current>",
            "defaultCwd": "~/code/my-project", "agentCommand": ["omo", "--model", "{model}", "{prompt}"]}, indent=2) + "\n",
        "setups/discord-aoe/herdr-guard": "#!/bin/sh\n",
        "setups/discord-aoe/systemd/dori-herdr.service": "[Service]\nExecStart=herdr server\n",
        "setups/discord-aoe/dori.env.example": "# copy me\nDORI_DISCORD_TOKEN=replace-with-token\nDORI_LEAD_AOE_ID=0a1b\nDORI_LEAD_TMUX=aoe_Dori\n",
        "setups/herdr-web/herdr-web@.service": "[Service]\nExecStart=proxy\n",
        "setups/herdr-web/herdr-web-terminal@.service": "[Service]\nExecStart=ttyd\n",
    }
    for name, text in files.items():
        (skill / name).parent.mkdir(parents=True, exist_ok=True)
        _ = (skill / name).write_text(text, encoding="utf-8")
    _ = subprocess.run(("git", "init", "--quiet", "-b", "main", str(origin)), check=True)
    _ = subprocess.run(("git", "-C", str(origin), "add", "."), check=True)
    _ = subprocess.run(("git", *_GIT_ENV, "-C", str(origin), "commit", "--quiet", "-m", "fork"), check=True)
    sha = subprocess.run(("git", "-C", str(origin), "rev-parse", "HEAD"), check=True, capture_output=True, text=True).stdout.strip()
    return DoriPin(str(origin), sha)


class _World:
    """systemctl --user and herdr as one account sees them: records every call, answers like the real tools."""

    def __init__(self, *, active: set[str] | None = None, agent_screen: str = "────\n❯ \n────\n") -> None:
        self.calls: list[tuple[str, ...]] = []
        self.active = active or set()
        self.fetched: list[str] = []
        self.agent_screen = agent_screen
        self.agent_ran = False

    def capture(self, argv: tuple[str, ...]) -> tuple[int, str]:
        self.calls.append(argv)
        if argv[:2] == ("systemctl", "--user"):
            if argv[2] == "is-active":
                return (0, "active\n") if argv[3] in self.active else (3, "inactive\n")
            if argv[2] == "enable":
                self.active.add(argv[4])
            return 0, ""
        if argv[1:3] == ("workspace", "create"):
            return 0, json.dumps(_CREATED)
        if argv[1:3] == ("pane", "run"):
            self.agent_ran = True
        if argv[1:3] == ("pane", "read"):
            return 0, self.agent_screen if self.agent_ran else "$ \n"
        return 0, ""

    def fetch(self, url: str, dest: Path) -> None:
        self.fetched.append(url)
        _ = dest.write_bytes(_HERDR if url.endswith("/herdr") else _TTYD)

    def ran(self, *prefix: str) -> list[tuple[str, ...]]:
        return [c for c in self.calls if c[: len(prefix)] == prefix or c[1 : 1 + len(prefix)] == prefix]


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    monkeypatch.setenv("HOME", str(path))
    pin = _fork(tmp_path)
    monkeypatch.setattr(dori, "load_pin", lambda: pin)
    monkeypatch.setattr(dori_herdr, "load_tools", lambda _root: _TOOLS)
    monkeypatch.setattr(dori_herdr, "platform_key", lambda: _KEY)
    monkeypatch.setattr(dori_herdr, "port_free", lambda _port: True)
    return path


def _install(home: Path, world: _World, *extra: str, which: dict[str, str] | None = None) -> int:
    found = which if which is not None else {"git": "/usr/bin/git", "bun": "/usr/bin/bun", "omo": "/usr/bin/omo"}
    return dori.main(
        ("--src", str(home / ".dori" / "src"), "--skills", str(home / ".agents" / "skills"),
         "--workroot", str(home / "Documents"), *extra),
        runner=lambda _argv, _cwd: 0, which=found.get, capture=world.capture, fetch=world.fetch, sleep=lambda _s: None,
    )


def test_a_fresh_install_gets_herdr_ttyd_the_server_the_web_and_the_lead_in_a_herdr_pane(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    world = _World()

    assert _install(home, world, "--owner-call", "형") == 0

    out = capsys.readouterr().out
    herdr = home / ".local" / "lib" / "herdr" / "herdr"
    assert herdr.read_bytes() == _HERDR and stat.S_IMODE(herdr.stat().st_mode) == 0o755
    assert (home / ".local" / "bin" / "herdr").resolve() == (home / ".dori/src/skills/dori-mode/setups/discord-aoe/herdr-guard").resolve()
    assert (home / ".local" / "bin" / "ttyd").read_bytes() == _TTYD
    assert (home / ".config" / "herdr" / "config.toml").read_text(encoding="utf-8") == "onboarding = false\n"
    units = home / ".config" / "systemd" / "user"
    assert sorted(p.name for p in units.iterdir()) == [
        "dori-herdr.service", "dori-serve.service", "herdr-web-terminal@.service", "herdr-web@.service"]
    assert (units / "dori-serve.service").read_text(encoding="utf-8") == dori_herdr.SERVE_UNIT_TEXT
    dori_env = home / ".dori" / "dori.env"
    assert stat.S_IMODE(dori_env.stat().st_mode) == 0o600
    assert dori_env.read_text(encoding="utf-8") == (
        "# copy me\nDORI_DISCORD_TOKEN=replace-with-token\n"
        "# (herdr: not used) DORI_LEAD_AOE_ID=0a1b\n# (herdr: not used) DORI_LEAD_TMUX=aoe_Dori\n")
    env = home / ".config" / "herdr" / "web-8181.env"
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    keys = dict(line.split("=", 1) for line in env.read_text(encoding="utf-8").splitlines())
    assert keys["HERDR_WEB_ORIGIN"] == "http://127.0.0.1:8181" and keys["HERDR_TTYD_PORT"] == "9281"
    assert len(keys["HERDR_WEB_PASSPHRASE"]) == 32 and len(keys["HERDR_STATE_SECRET"]) == 64
    assert world.active == {"dori-herdr.service", "herdr-web@8181.service", "dori-serve.service"}
    config = json.loads((home / ".dori" / "config.json").read_text(encoding="utf-8"))
    lead_dir = home / "Documents" / "zzangdori"
    assert config["backend"] == "herdr" and config["leadPane"] == "w1:p1" and config["defaultCwd"] == str(lead_dir)
    assert world.ran("workspace", "create") == [(str(herdr), "workspace", "create", "--label", "짱돌", "--cwd", str(lead_dir), "--no-focus")]
    [started] = world.ran("pane", "run")
    assert started[3] == "w1:p1" and started[4].startswith("omo --session-id ")
    assert "DORI-LEAD-STARTED pane=w1:p1" in out and "backend=herdr" in out
    assert keys["HERDR_WEB_PASSPHRASE"] not in out


def test_a_lead_agent_that_does_not_come_up_is_reported_not_started(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    world = _World(agent_screen="$ omo --session-id x\nSyntaxError: Unexpected token 'with'\n$ \n")

    assert _install(home, world) == 0

    out = capsys.readouterr().out
    assert "DORI-LEAD-NOT-READY pane=w1:p1" in out and "SyntaxError" in out
    assert "DORI-LEAD-STARTED" not in out


def test_an_aoe_install_rerun_gets_herdr_ready_but_keeps_its_config_web_port_and_passphrase(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = home / ".dori" / "config.json"
    config.parent.mkdir(parents=True)
    _ = config.write_text('{"backend": "aoe", "leadPane": "aoe_Dori_12345678"}\n', encoding="utf-8")
    serve = home / ".config" / "agent-of-empires" / "serve.env"
    serve.parent.mkdir(parents=True)
    _ = serve.write_text("AOE_SERVE_PASSPHRASE=old\n", encoding="utf-8")
    monkeypatch.setattr(dori_herdr, "port_free", lambda port: port != 8181)
    world = _World()

    assert _install(home, world, "--web-origin", "https://node.example:443") == 0

    out = capsys.readouterr().out
    assert config.read_text(encoding="utf-8") == '{"backend": "aoe", "leadPane": "aoe_Dori_12345678"}\n'
    env = (home / ".config" / "herdr" / "web-8181.env").read_text(encoding="utf-8")
    assert "HERDR_WEB_PASSPHRASE" not in env and "HERDR_WEB_ORIGIN=https://node.example:443\n" in env
    assert "DORI-HERDR-WEB-PORT-BUSY 8181" in out
    assert world.active == {"dori-herdr.service", "dori-serve.service"}
    assert world.ran("workspace", "create") == [] and world.ran("pane", "run") == []
    assert "DORI-LEAD-KEEP" in out


def test_an_aoe_config_still_at_its_placeholder_lead_gets_no_herdr_lead(home: Path) -> None:
    config = home / ".dori" / "config.json"
    config.parent.mkdir(parents=True)
    _ = config.write_text('{"backend": "aoe", "leadPane": "<tmux>"}\n', encoding="utf-8")
    world = _World()

    assert _install(home, world) == 0

    assert config.read_text(encoding="utf-8") == '{"backend": "aoe", "leadPane": "<tmux>"}\n'
    assert world.ran("workspace", "create") == []


def test_a_second_account_on_another_web_port_gets_its_own_ttyd_and_state_ports(home: Path) -> None:
    world = _World()

    assert _install(home, world, "--web-port", "8282") == 0

    keys = dict(line.split("=", 1) for line in (home / ".config" / "herdr" / "web-8282.env").read_text(encoding="utf-8").splitlines())
    assert (keys["HERDR_TTYD_PORT"], keys["HERDR_STATE_PORT"]) == ("9382", "9482")
    assert "herdr-web@8282.service" in world.active


def test_a_ttyd_port_held_by_another_account_keeps_the_web_off(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(dori_herdr, "port_free", lambda port: port != 9281)
    world = _World()

    assert _install(home, world) == 0

    assert "DORI-HERDR-WEB-PORT-BUSY 9281" in capsys.readouterr().out
    assert world.active == {"dori-herdr.service", "dori-serve.service"}


def test_an_account_running_the_loops_as_one_unit_each_gets_no_second_listener(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    units = home / ".config" / "systemd" / "user"
    units.mkdir(parents=True)
    _ = (units / "dori-inbound.service").write_text("[Service]\nExecStart=dori inbound discord\n", encoding="utf-8")
    world = _World()

    assert _install(home, world) == 0

    assert not (units / "dori-serve.service").exists()
    assert "dori-serve.service" not in world.active
    assert "DORI-SERVE-UNIT-SKIP dori-inbound.service" in capsys.readouterr().out


def test_an_update_restarts_a_running_serve_unit_on_the_new_code(home: Path) -> None:
    world = _World()
    assert _install(home, world) == 0
    again = _World(active=set(world.active))

    assert _install(home, again, "--update") == 0

    assert again.ran("systemctl", "--user", "restart") == [("systemctl", "--user", "restart", "dori-serve.service")]


def test_an_update_leaves_a_serve_unit_the_owner_stopped_stopped(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    world = _World()
    assert _install(home, world) == 0
    stopped = _World(active=set(world.active) - {"dori-serve.service"})
    _ = capsys.readouterr()

    assert _install(home, stopped, "--update") == 0

    assert "dori-serve.service" not in stopped.active
    assert [c for c in stopped.calls if c[:2] == ("systemctl", "--user") and "dori-serve.service" in c and c[2] != "is-active"] == []
    assert "dori-serve.service same stopped" in capsys.readouterr().out


def test_a_lead_left_without_omo_names_the_omo_installer_and_a_ready_session_id(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    world = _World()

    assert _install(home, world, which={"git": "/usr/bin/git", "bun": "/usr/bin/bun"}) == 0

    [line] = [out for out in capsys.readouterr().out.splitlines() if out.startswith("DORI-LEAD-PANE")]
    assert dori_herdr.OMO_INSTALL in line and "<" not in line.split("herdr pane run", 1)[1]


def test_a_second_run_writes_nothing_new_and_starts_no_second_lead(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    world = _World()
    assert _install(home, world) == 0
    env = home / ".config" / "herdr" / "web-8181.env"
    before = {p: p.read_bytes() for p in (env, home / ".dori" / "config.json", home / ".config" / "herdr" / "config.toml")}
    _ = capsys.readouterr()
    again = _World(active=set(world.active))

    assert _install(home, again) == 0

    out = capsys.readouterr().out
    assert {p: p.read_bytes() for p in before} == before
    assert again.fetched == []
    assert again.ran("workspace", "create") == [] and again.ran("pane", "run") == []
    assert "DORI-HERDR-BIN" in out and " same" in out and "이미 켜짐" in out


def test_a_download_whose_sha256_differs_installs_nothing_and_stops(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    world = _World()
    monkeypatch.setattr(world, "fetch", lambda _url, dest: dest.write_bytes(b"tampered"))

    assert _install(home, world) == 5

    out = capsys.readouterr().out
    assert "DORI-HERDR-SHA-MISMATCH" in out
    assert not (home / ".local" / "lib" / "herdr").exists() or list((home / ".local" / "lib" / "herdr").iterdir()) == []
    assert world.calls == []
    assert "DORI-INSTALLED" not in out


def test_an_existing_other_herdr_is_kept(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    mine = home / ".local" / "lib" / "herdr" / "herdr"
    mine.parent.mkdir(parents=True)
    _ = mine.write_bytes(b"my own herdr build")

    assert _install(home, _World()) == 0

    assert mine.read_bytes() == b"my own herdr build"
    assert f"DORI-HERDR-BIN {mine} kept" in capsys.readouterr().out


def test_backend_aoe_installs_the_aoe_config_and_touches_no_herdr(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    world = _World()

    assert _install(home, world, "--backend", "aoe") == 0

    assert json.loads((home / ".dori" / "config.json").read_text(encoding="utf-8"))["backend"] == "aoe"
    assert world.calls == [] and world.fetched == []
    assert not (home / ".local").exists() and not (home / ".config").exists()
    assert "backend=aoe" in capsys.readouterr().out


def test_without_a_user_manager_the_binaries_land_and_units_and_lead_are_skipped(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    world = _World()
    real = world.capture
    monkeypatch.setattr(world, "capture", lambda argv: (1, "") if argv[2:3] == ("show-environment",) else real(argv))

    assert _install(home, world) == 0

    out = capsys.readouterr().out
    assert "DORI-HERDR-NO-SYSTEMD" in out
    assert (home / ".local" / "bin" / "ttyd").is_file()
    assert not (home / ".config" / "systemd").exists()
    assert world.ran("workspace", "create") == []


def test_the_pinned_tools_cover_linux_and_carry_real_digests() -> None:
    tools = dori_herdr.load_tools(Path(dori.REPO_ROOT))

    assert {"linux-x86_64", "linux-aarch64"} <= set(tools.herdr) and {"linux-x86_64", "linux-aarch64"} <= set(tools.ttyd)
    for asset in (*tools.herdr.values(), *tools.ttyd.values()):
        assert asset.url.startswith("https://github.com/") and len(asset.sha256) == 64
        assert f"/{tools.herdr_version}/" in asset.url.replace("/v", "/") or f"/{tools.ttyd_version}/" in asset.url
    assert dori_herdr.platform_key("Linux", "aarch64") == "linux-aarch64"
    assert dori_herdr.platform_key("Darwin", "arm64") == "macos-aarch64"
