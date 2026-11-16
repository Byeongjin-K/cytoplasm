"""도리 herdr 백엔드 설치 — `dori.py` 가 `--backend herdr`(기본)일 때 부른다.

`dori/herdr.toml` 에 고정한 herdr·ttyd 를 받아 sha256 을 확인하고 계정의 `~/.local` 에 둔다. 포크의 유닛으로
herdr 서버(`dori-herdr.service`)와 웹(`herdr-web@<포트>`)을 켜고, 도리 상시 루프(디스코드 수신 등)는 윈도우의
「Dori serve」 작업과 같은 `dori serve` 를 사용자 유닛 `dori-serve.service` 로 켠다(루프를 유닛 하나씩 따로 돌리는 계정은
그대로 둔다). 새로 복사한 config 이면 리드를 herdr pane 에 띄워 `leadPane` 에 적는다. root 는 쓰지 않는다.

네이티브 윈도우(WSL 없음)에서는 systemd 대신 작업 스케줄러다: herdr zip 을 풀어 계정 PATH 에 넣고, 로그온 때 herdr
서버를 띄우는 작업과 포크의 `dori serve --install-task`(도리 상시 루프) 작업을 등록해 바로 켠다. 웹(ttyd·systemd
유닛)과 herdr-guard(bash)는 윈도우에 없다. 이미 있는 파일은 덮지 않는다 — 다른 herdr·ttyd·유닛·웹 설정이
있으면 그대로 두고 그렇다고 말한다. 다른 프로그램(옛 aoe 웹)이 웹 포트를 쓰면 웹만 켜지 않고 넘어간다.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import secrets
import socket
import subprocess
import shutil
import sys
import tempfile
import time
import tomllib
import urllib.request
import uuid
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

TOOLS_RELPATH: Final = Path("dori") / "herdr.toml"
_FORK_SETUPS: Final = Path("skills") / "dori-mode" / "setups"
_SERVER_UNIT: Final = "dori-herdr.service"
SERVER_TASK: Final = "Dori herdr server"
SERVE_TASK: Final = "Dori serve"
SERVE_UNIT: Final = "dori-serve.service"
# an account that runs the loops as one unit each (setups/discord-aoe) already has the Discord listener: a second one
# from dori serve would answer every owner message twice
LOOP_UNITS: Final = ("dori-inbound.service", "dori-lanes.service", "dori-session-watch.service")
SERVE_UNIT_TEXT: Final = """\
[Unit]
Description=This Dori's always-on loops (dori serve: watch, freshness, dead-panes, guard, state-git, Discord inbound, session watch), from the cytoplasm installer
After=network-online.target dori-herdr.service

[Service]
# no EnvironmentFile: every dori command reads ~/.dori/dori.env itself
Environment=PATH=%h/.bun/bin:%h/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=%h/.bun/bin/dori serve
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
"""
OMO_INSTALL: Final = "curl -fsSL https://get.omo.dev/install.sh | bash (윈도우: irm https://get.omo.dev/install.ps1 | iex)"
_ZIP_MARK: Final = ".zip-sha256"
_EXAMPLE_CWD: Final = "~/code/my-project"
_READY_TRIES: Final = 50
_AGENT_TRIES: Final = 60

Capture = Callable[[tuple[str, ...]], tuple[int, str]]
Fetch = Callable[[str, Path], None]
Which = Callable[[str], str | None]


class HerdrInstallError(RuntimeError):
    """herdr 설치를 멈출 이유 — 메시지 첫 낱말이 판정 표식이다."""


@dataclass(frozen=True, slots=True)
class Asset:
    url: str
    sha256: str


@dataclass(frozen=True, slots=True)
class Tools:
    herdr_version: str
    herdr: Mapping[str, Asset]
    ttyd_version: str
    ttyd: Mapping[str, Asset]
    web_port: int
    ttyd_offset: int
    state_offset: int


def load_tools(root: Path) -> Tools:
    data = tomllib.loads((root / TOOLS_RELPATH).read_text(encoding="utf-8"))
    def assets(table: Mapping[str, Mapping[str, str]]) -> dict[str, Asset]:
        return {key: Asset(v["url"], v["sha256"]) for key, v in table.items()}
    web = data["web"]
    return Tools(data["herdr"]["version"], assets(data["herdr"]["assets"]), data["ttyd"]["version"],
                 assets(data["ttyd"]["assets"]), int(web["port"]), int(web["ttyd_offset"]), int(web["state_offset"]))


def platform_key(system: str | None = None, machine: str | None = None) -> str:
    os_name = {"Linux": "linux", "Darwin": "macos", "Windows": "windows"}.get(system or platform.system(), (system or platform.system()).lower())
    arch = {"amd64": "x86_64", "arm64": "aarch64"}.get((machine or platform.machine()).lower(), (machine or platform.machine()).lower())
    return f"{os_name}-{arch}"


@dataclass(frozen=True, slots=True)
class Layout:
    home: Path
    src: Path
    web_port: int
    windows: bool = sys.platform == "win32"

    @property
    def herdr(self) -> Path: return self.home / ".local" / "lib" / "herdr" / ("herdr.exe" if self.windows else "herdr")
    @property
    def dori_cli(self) -> Path: return self.src / "skills" / "dori-mode" / "scripts" / "src" / "cli.ts"
    @property
    def herdr_link(self) -> Path: return self.home / ".local" / "bin" / "herdr"
    @property
    def guard(self) -> Path: return self.src / _FORK_SETUPS / "discord-aoe" / "herdr-guard"
    @property
    def ttyd(self) -> Path: return self.home / ".local" / "bin" / "ttyd"
    @property
    def herdr_config(self) -> Path: return self.home / ".config" / "herdr" / "config.toml"
    @property
    def units(self) -> Path: return self.home / ".config" / "systemd" / "user"
    @property
    def server_unit(self) -> Path: return self.src / _FORK_SETUPS / "discord-aoe" / "systemd" / _SERVER_UNIT
    @property
    def web_units(self) -> tuple[Path, ...]: return tuple(sorted((self.src / _FORK_SETUPS / "herdr-web").glob("herdr-web*@.service")))
    @property
    def web_env(self) -> Path: return self.home / ".config" / "herdr" / f"web-{self.web_port}.env"
    @property
    def aoe_serve_env(self) -> Path: return self.home / ".config" / "agent-of-empires" / "serve.env"


def plan(layout: Layout, tools: Tools, key: str) -> list[tuple[str, str]]:
    herdr = tools.herdr.get(key)
    ttyd = tools.ttyd.get(key)
    if layout.windows:
        return [
            ("herdr-bin", f"{layout.herdr} herdr {tools.herdr_version} ({key}{', zip sha256 ' + herdr.sha256[:12] if herdr else ' — 받을 바이너리 없음, 건너뜀'}), 그 폴더를 계정 PATH 에"),
            ("herdr-server", f"작업 스케줄러 '{SERVER_TASK}'(로그온, 관리자 권한 없음) — 안 돌고 있으면 바로 켠다"),
            ("serve-task", f"작업 스케줄러 '{SERVE_TASK}' = 포크 dori serve --install-task(도리 상시 루프) — 바로 켠다, --update 면 새 코드로 다시 켠다"),
            ("herdr-web", "윈도우에는 없다(ttyd·systemd 유닛) — herdr 터미널 화면을 쓴다"),
            ("herdr-lead", "새로 복사한 config 이면 리드를 herdr pane 에 띄우고 leadPane 에 적는다"),
        ]
    return [
        ("herdr-bin", f"{layout.herdr} herdr {tools.herdr_version} ({key}{', sha256 ' + herdr.sha256[:12] if herdr else ' — 받을 바이너리 없음, 건너뜀'}), {layout.herdr_link} -> 포크 herdr-guard"),
        ("herdr-ttyd", f"{layout.ttyd} ttyd {tools.ttyd_version} ({key}{', sha256 ' + ttyd.sha256[:12] if ttyd else ' — 받을 바이너리 없음, 웹 건너뜀'})"),
        ("herdr-server", f"{layout.units / _SERVER_UNIT} 를 켠다(systemctl --user, 이미 있으면 그대로)"),
        ("herdr-web", f"herdr-web@{layout.web_port} (ttyd {layout.web_port + tools.ttyd_offset}, 상태 {layout.web_port + tools.state_offset}) — {layout.web_env}(0600, 없을 때만) · 포트를 다른 프로그램이 쓰면 켜지 않음"),
        ("serve-unit", f"{layout.units / SERVE_UNIT} = dori serve(도리 상시 루프, 디스코드 수신) — 새로 만들면 켠다, 켜져 있으면 --update 때 새 코드로 다시 켜고 꺼 둔 것은 그대로 둔다 · 루프별 유닛({LOOP_UNITS[0]} 등)이 있으면 그대로 둔다"),
        ("herdr-lead", "새로 복사한 config 이면 리드를 herdr pane 에 띄우고 leadPane 에 적는다"),
    ]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def default_fetch(url: str, dest: Path) -> None:
    with urllib.request.urlopen(url, timeout=120) as response, dest.open("wb") as out:  # noqa: S310 - fixed https URLs from dori/herdr.toml
        while block := response.read(1 << 20):
            _ = out.write(block)


def default_capture(argv: tuple[str, ...]) -> tuple[int, str]:
    # a shell inside a herdr pane carries HERDR_SOCKET_PATH and friends, which would aim herdr at that pane's server
    env = {k: v for k, v in os.environ.items() if not k.startswith("HERDR_")}
    done = subprocess.run(argv, check=False, capture_output=True, text=True, env=env)
    return done.returncode, done.stdout + done.stderr


def install_zip(dest: Path, asset: Asset, fetch: Fetch) -> str:
    """Windows herdr ships as a zip (herdr.exe plus its ConPTY runtime); the zip's sha256 is the pin, kept beside it."""
    folder, mark = dest.parent, dest.parent / _ZIP_MARK
    if dest.exists():
        return "same" if mark.is_file() and mark.read_text(encoding="utf-8").strip() == asset.sha256 else "kept"
    folder.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{folder.name}-", dir=folder.parent))
    try:
        archive = staging / "asset.zip"
        fetch(asset.url, archive)
        got = _sha256(archive)
        if got != asset.sha256:
            raise HerdrInstallError(f"DORI-HERDR-SHA-MISMATCH: {asset.url} sha256 {got}, want {asset.sha256} — nothing installed")
        unpacked = staging / "unpacked"
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(unpacked)
        if not (unpacked / dest.name).is_file():
            raise HerdrInstallError(f"DORI-HERDR-ZIP-INVALID: {asset.url} has no {dest.name} at its top — nothing installed")
        _ = (unpacked / _ZIP_MARK).write_text(asset.sha256 + "\n", encoding="utf-8")
        if folder.exists():
            for entry in unpacked.iterdir():
                os.replace(entry, folder / entry.name)
        else:
            os.replace(unpacked, folder)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return "installed"


def install_binary(dest: Path, asset: Asset, fetch: Fetch) -> str:
    if asset.url.endswith(".zip"):
        return install_zip(dest, asset, fetch)
    if dest.exists():
        return "same" if _sha256(dest) == asset.sha256 else "kept"
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    try:
        fetch(asset.url, part)
        got = _sha256(part)
        if got != asset.sha256:
            raise HerdrInstallError(f"DORI-HERDR-SHA-MISMATCH: {asset.url} sha256 {got}, want {asset.sha256} — nothing installed")
        part.chmod(0o755)
        os.replace(part, dest)
    finally:
        part.unlink(missing_ok=True)
    return "installed"


def _copy_once(source: Path, dest: Path) -> str:
    content = source.read_bytes()
    if dest.exists():
        return "same" if dest.read_bytes() == content else "differs"
    dest.parent.mkdir(parents=True, exist_ok=True)
    _ = dest.write_bytes(content)
    return "new"


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _web_env(layout: Layout, tools: Tools, origin: str) -> str:
    lines = [
        f"HERDR_TTYD_PORT={layout.web_port + tools.ttyd_offset}",
        f"HERDR_WEB_ORIGIN={origin}",
        f"HERDR_STATE_PORT={layout.web_port + tools.state_offset}",
        f"HERDR_STATE_SECRET={secrets.token_hex(32)}",
    ]
    if not layout.aoe_serve_env.exists():
        lines.append(f"HERDR_WEB_PASSPHRASE={secrets.token_hex(16)}")
    return "\n".join(lines) + "\n"


def _env_ports(layout: Layout) -> list[int]:
    # the ports this account's web units will bind, read back from its env file (it may predate this installer)
    values = dict(line.split("=", 1) for line in layout.web_env.read_text(encoding="utf-8").splitlines() if "=" in line)
    return [layout.web_port, *(int(values[k]) for k in ("HERDR_TTYD_PORT", "HERDR_STATE_PORT") if values.get(k, "").isdigit())]


def write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    # LF on every OS: the fork reads dori.env line by line and a Windows CRLF line never matches (DORI_DISCORD_TOKEN is not set)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        _ = handle.write(text)


def _systemctl(capture: Capture, *args: str) -> tuple[int, str]:
    return capture(("systemctl", "--user", *args))


def open_lead(layout: Layout, config: Path, lead_dir: Path, label: str, capture: Capture, which: Which,
              sleep: Callable[[float], None]) -> None:
    data = json.loads(config.read_text(encoding="utf-8"))
    current = str(data.get("leadPane", ""))
    if data.get("backend") != "herdr" or (current and not current.startswith("<")):
        print(f"DORI-LEAD-KEEP {config} backend={data.get('backend')} leadPane={current or '-'} — 리드는 그대로 둔다")
        return
    herdr = str(layout.herdr)
    lead_dir.mkdir(parents=True, exist_ok=True)
    code, out = capture((herdr, "workspace", "create", "--label", label, "--cwd", str(lead_dir), "--no-focus"))
    if code != 0:
        print(f"DORI-LEAD-FAIL herdr workspace create: {out.strip()[:200]}")
        return
    pane = json.loads(out)["result"]["root_pane"]["pane_id"]
    for _ in range(_READY_TRIES):
        if capture((herdr, "pane", "read", pane, "--source", "visible"))[1].strip():
            break
        sleep(0.2)
    data["leadPane"] = pane
    if data.get("defaultCwd") == _EXAMPLE_CWD:
        data["defaultCwd"] = str(lead_dir)
    _ = config.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    agent = str((data.get("agentCommand") or ["omo"])[0])
    if which(agent) is None:
        install = f" ({OMO_INSTALL})" if agent == "omo" else ""
        print(f"DORI-LEAD-PANE {pane} — {agent} 가 PATH 에 없어 리드 에이전트는 켜지 않았다. 깐 뒤{install}: herdr pane run {pane} '{agent} --session-id {uuid.uuid4()}'")
        return
    session = str(uuid.uuid4())
    code, out = capture((herdr, "pane", "run", pane, f"{agent} --session-id {session}"))
    if code != 0:
        print(f"DORI-LEAD-FAIL herdr pane run {pane}: {out.strip()[:200]}")
        return
    screen = ""
    for _ in range(_AGENT_TRIES):
        screen = capture((herdr, "pane", "read", pane, "--source", "visible"))[1]
        if _omo_input_box(screen):
            print(f"DORI-LEAD-STARTED pane={pane} session={session} cwd={lead_dir}")
            return
        sleep(1.0)
    tail = " | ".join(line.strip() for line in screen.strip().splitlines()[-3:])
    print(f"DORI-LEAD-NOT-READY pane={pane}: {agent} 입력창이 {_AGENT_TRIES}초 안에 뜨지 않았다(화면 끝: {tail[:200]}) — "
          f"고친 뒤 herdr pane run {pane} '{agent} --session-id {session}'")


def _omo_input_box(screen: str) -> bool:
    # omo's own input box: a ❯ line with the box's ─ border right above it (a shell prompt drawn as ❯ has no box);
    # the same rule the fork's reviveHerdr waits on
    lines = screen.split("\n")
    return any(line.lstrip().startswith("❯") and any(up.lstrip().startswith("─") for up in lines[max(0, i - 2):i])
               for i, line in enumerate(lines))


def ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def powershell(script: str) -> tuple[str, ...]:
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return ("powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded)


def add_user_path(folder: Path) -> str:
    """Appends a folder to the account's own PATH (HKCU\\Environment), as the Windows installers of bun and herdr do."""
    import ctypes
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
        try:
            current, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current, kind = "", winreg.REG_EXPAND_SZ
        parts = [p for p in str(current).split(";") if p]
        if any(os.path.normcase(os.path.expandvars(p)).rstrip("\\") == os.path.normcase(str(folder)) for p in parts):
            return "same"
        winreg.SetValueEx(key, "Path", 0, kind, ";".join([*parts, str(folder)]))
    # tell running Explorer (and the tasks it starts) that the environment changed; WM_SETTINGCHANGE, SMTO_ABORTIFHUNG
    _ = ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, None)
    return "added"


def _server_task_script(herdr: Path) -> str:
    # hidden: a console window left open at logon would end the server when closed
    argument = f"-NoProfile -NonInteractive -WindowStyle Hidden -Command \"& '{str(herdr).replace(chr(39), chr(39) * 2)}' server\""
    return "; ".join((
        "$ErrorActionPreference = 'Stop'",
        f"$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument {ps_quote(argument)}",
        "$trigger = New-ScheduledTaskTrigger -AtLogOn -User ([Security.Principal.WindowsIdentity]::GetCurrent().Name)",
        "$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries",
        "$settings.ExecutionTimeLimit = 'PT0S'",
        f"Register-ScheduledTask -TaskName {ps_quote(SERVER_TASK)} -Action $action -Trigger $trigger -Settings $settings -RunLevel Limited -Force | Out-Null",
    ))


def _serve_processes(cli: Path) -> str:
    # the serve supervisor and its loops are bun processes running this fork's cli.ts; Stop-ScheduledTask only ends the
    # task's powershell host and leaves them running (windows-latest, 2026-11-07), so they are matched by command line
    return (f"Get-CimInstance Win32_Process -Filter \"Name='bun.exe'\" | Where-Object {{ $_.CommandLine -and "
            f"$_.CommandLine.Contains({ps_quote(str(cli))}) -and $_.CommandLine -match ' serve( |$)' }}")


def windows_supervise(layout: Layout, capture: Capture, which: Which, sleep: Callable[[float], None], *, restart: bool) -> int:
    """The Windows stand-in for the systemd user units: two logon tasks, both running when this returns."""
    herdr = str(layout.herdr)
    code, out = capture(powershell(_server_task_script(layout.herdr)))
    if code != 0:
        print(f"DORI-HERDR-TASK-FAIL {SERVER_TASK}: {out.strip()[:200]}")
        return 6
    if capture((herdr, "pane", "list"))[0] != 0:
        _ = capture(powershell(f"Start-ScheduledTask -TaskName {ps_quote(SERVER_TASK)}"))
        for _ in range(_READY_TRIES):
            if capture((herdr, "pane", "list"))[0] == 0:
                break
            sleep(0.2)
        else:
            print(f"DORI-HERDR-SERVER-FAIL: '{SERVER_TASK}' 를 켰지만 herdr 가 답하지 않는다")
            return 6
    print(f"DORI-HERDR-SERVER '{SERVER_TASK}' running")

    bun = which("bun")
    if bun is None:
        print(f"DORI-SERVE-TASK-FAIL: bun 이 PATH 에 없다 — '{SERVE_TASK}' 를 등록하지 못했다")
        return 6
    code, out = capture((bun, str(layout.dori_cli), "serve", "--install-task"))
    if code != 0 or "SERVE_TASK_INSTALLED" not in out:
        print(f"DORI-SERVE-TASK-FAIL: {out.strip()[:200]}")
        return 6
    running = capture(powershell(f"@({_serve_processes(layout.dori_cli)}).Count"))[1].strip()
    restarted = running not in ("", "0") and restart
    if restarted:
        # the task's host first (a task still marked Running ignores Start-ScheduledTask), then the bun tree it leaves
        _ = capture(powershell(f"Stop-ScheduledTask -TaskName {ps_quote(SERVE_TASK)}; "
                               f"{_serve_processes(layout.dori_cli)} | ForEach-Object {{ taskkill.exe /PID $_.ProcessId /T /F | Out-Null }}"))
        running = "0"
    if running in ("", "0"):
        code, out = capture(powershell(f"Start-ScheduledTask -TaskName {ps_quote(SERVE_TASK)}"))
        if code != 0:
            print(f"DORI-SERVE-TASK-FAIL Start-ScheduledTask: {out.strip()[:200]}")
            return 6
    print(f"DORI-SERVE-TASK '{SERVE_TASK}' {'restarted' if restarted else 'running'} — 로그온마다 작업 스케줄러가 다시 켭니다")
    return 0


def serve_unit(layout: Layout, capture: Capture, *, restart: bool) -> int:
    """The Linux twin of the Windows 'Dori serve' task: dori serve in one user unit, running when this returns."""
    if loops := [name for name in LOOP_UNITS if (layout.units / name).exists()]:
        print(f"DORI-SERVE-UNIT-SKIP {' '.join(loops)} 가 루프를 이미 돌린다 — {SERVE_UNIT} 는 만들지 않는다")
        return 0
    dest = layout.units / SERVE_UNIT
    if dest.exists():
        state = "same" if dest.read_text(encoding="utf-8") == SERVE_UNIT_TEXT else "kept"
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        _ = dest.write_text(SERVE_UNIT_TEXT, encoding="utf-8")
        state = "new"
    _ = _systemctl(capture, "daemon-reload")
    if state == "new":
        action, verb = ("enable", "--now", SERVE_UNIT), "active"
    elif _systemctl(capture, "is-active", SERVE_UNIT)[0] != 0:
        # a unit this account already had and stopped stays stopped: the owner paused the loops, an update must not resume them
        print(f"DORI-SERVE-UNIT {dest} {state} stopped — 꺼져 있어 그대로 둔다(켜려면 systemctl --user enable --now {SERVE_UNIT})")
        return 0
    elif restart:
        action, verb = ("restart", SERVE_UNIT), "restarted"
    else:
        print(f"DORI-SERVE-UNIT {dest} {state} active")
        return 0
    code, out = _systemctl(capture, *action)
    if code != 0:
        print(f"DORI-SERVE-UNIT-FAIL {SERVE_UNIT}: {out.strip()[:200]}")
        return 6
    print(f"DORI-SERVE-UNIT {dest} {state} {verb} — 디스코드 수신은 ~/.dori/dori.env 토큰을 읽는다")
    return 0


def install(layout: Layout, tools: Tools, key: str, *, origin: str, config: Path, lead_dir: Path, lead_label: str,
            capture: Capture, fetch: Fetch, which: Which, sleep: Callable[[float], None] = time.sleep,
            restart: bool = False, path_adder: Callable[[Path], str] = add_user_path) -> int:
    herdr_asset = tools.herdr.get(key)
    if herdr_asset is None:
        print(f"DORI-HERDR-UNSUPPORTED {key}: herdr {tools.herdr_version} 바이너리가 없다 — herdr 단계를 건너뛴다 (https://herdr.dev)")
        return 0
    if layout.windows:
        try:
            print(f"DORI-HERDR-BIN {layout.herdr} {install_binary(layout.herdr, herdr_asset, fetch)}")
        except (HerdrInstallError, OSError, zipfile.BadZipFile) as error:
            print(error if isinstance(error, HerdrInstallError) else f"DORI-HERDR-FETCH-FAIL: {error}")
            return 5
        print(f"DORI-HERDR-PATH {layout.herdr.parent} {path_adder(layout.herdr.parent)}")
        os.environ["PATH"] = f"{os.environ.get('PATH', '')}{os.pathsep}{layout.herdr.parent}"
        if not layout.herdr_config.exists():
            layout.herdr_config.parent.mkdir(parents=True, exist_ok=True)
            _ = layout.herdr_config.write_text("onboarding = false\n", encoding="utf-8")
        code = windows_supervise(layout, capture, which, sleep, restart=restart)
        if code != 0:
            return code
        open_lead(layout, config, lead_dir, lead_label, capture, which, sleep)
        return 0
    try:
        print(f"DORI-HERDR-BIN {layout.herdr} {install_binary(layout.herdr, herdr_asset, fetch)}")
        if layout.herdr_link.exists() and not layout.herdr_link.is_symlink():
            print(f"DORI-HERDR-KEEP {layout.herdr_link} 은 링크가 아닌 herdr 다 — 그대로 둔다(옮기면 가드가 붙는다: {layout.herdr})")
        else:
            layout.herdr_link.parent.mkdir(parents=True, exist_ok=True)
            layout.herdr_link.unlink(missing_ok=True)
            layout.herdr_link.symlink_to(layout.guard)
        ttyd_asset = tools.ttyd.get(key)
        ttyd = install_binary(layout.ttyd, ttyd_asset, fetch) if ttyd_asset else "unsupported"
        print(f"DORI-HERDR-TTYD {layout.ttyd} {ttyd}")
    except (HerdrInstallError, OSError) as error:
        print(error if isinstance(error, HerdrInstallError) else f"DORI-HERDR-FETCH-FAIL: {error}")
        return 5
    if not layout.herdr_config.exists():
        layout.herdr_config.parent.mkdir(parents=True, exist_ok=True)
        _ = layout.herdr_config.write_text("onboarding = false\n", encoding="utf-8")

    if _systemctl(capture, "show-environment")[0] != 0:
        print("DORI-HERDR-NO-SYSTEMD: systemctl --user 를 쓸 수 없어 서버·웹 유닛과 리드를 건너뛴다 — `herdr` 를 열면 서버가 뜬다")
        return 0
    for source in (layout.server_unit, *layout.web_units):
        state = _copy_once(source, layout.units / source.name)
        print(f"DORI-HERDR-UNIT {layout.units / source.name} {state}")
    web_env = "kept" if layout.web_env.exists() else "new"
    if web_env == "new":
        write_private(layout.web_env, _web_env(layout, tools, origin))
    print(f"DORI-HERDR-WEB-ENV {layout.web_env} {web_env}")
    _ = _systemctl(capture, "daemon-reload")
    code, out = _systemctl(capture, "enable", "--now", _SERVER_UNIT)
    if code != 0:
        print(f"DORI-HERDR-SERVER-FAIL: {out.strip()[:200]}")
        return 6
    for _ in range(_READY_TRIES):
        if capture((str(layout.herdr), "pane", "list"))[0] == 0:
            break
        sleep(0.2)
    else:
        print(f"DORI-HERDR-SERVER-FAIL: {_SERVER_UNIT} 가 켜졌지만 herdr 가 답하지 않는다")
        return 6
    print(f"DORI-HERDR-SERVER {_SERVER_UNIT} active")

    web = f"herdr-web@{layout.web_port}.service"
    if ttyd == "unsupported":
        print(f"DORI-HERDR-WEB-SKIP {key}: ttyd 바이너리가 없다")
    elif _systemctl(capture, "is-active", web)[0] == 0:
        print(f"DORI-HERDR-WEB {web} 이미 켜짐")
    elif busy := [p for p in _env_ports(layout) if not port_free(p)]:
        print(f"DORI-HERDR-WEB-PORT-BUSY {' '.join(map(str, busy))}: 다른 프로그램(옛 aoe 웹, 다른 도리 계정의 웹 등)이 쓴다 — "
              f"그것을 멈추거나 --web-port 로 다른 포트를 골라 {web} 를 켠다 (docs/guide/herdr-전환.md)")
    else:
        code, out = _systemctl(capture, "enable", "--now", web)
        print(f"DORI-HERDR-WEB {web} {'active' if code == 0 else 'FAIL ' + out.strip()[:200]} — {origin}")
    code = serve_unit(layout, capture, restart=restart)
    if code != 0:
        return code
    open_lead(layout, config, lead_dir, lead_label, capture, which, sleep)
    return 0
