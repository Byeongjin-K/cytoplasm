"""인터롭 게이트웨이 묶음 배포기(`automation/interop/deploy.sh`, RCB todo 22)를 통째로 돌린다.

가짜 `ssh`·`sudo` 가 계정마다 임시 HOME 에서 원격 스크립트를 실행하고, 가짜 `systemctl`·
`hermes` 와 원격 명령이 한 기록 파일에 순서대로 남는다. 여기서 증명하는 불변식:
  D1 순서 — 규칙 파일 확인·교체 → 두 계정 roster 관문 → 두 계정 사전 import 시험 → 백업 →
     계정마다 가드·고정 스크립트 → 드롭인 → daemon-reload → plugin.yaml → shim → enable →
     드라이버·작업 영역. 어느 계정의 묶음도 두 계정의 관문·시험이 끝나기 전에 놓이지 않는다.
  D2 실패 복귀 — roster 결손은 rc 0 HELD(규칙 파일만 수렴), 규칙 파일·사전 시험 실패는 rc 5 이고
     묶음·작업 영역·백업은 그대로다. 전송 중단은 rc 5 이고 이전 파일과 백업이 남는다.
  D3 재시동 — 기본은 하지 않는다. `--restart` 만 agent→peer 를 재시동하고 세대 확인을 기다린다.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Final

import pytest

_REPO: Final = Path(__file__).resolve().parents[2]
_DEPLOY: Final = _REPO / "automation/interop/deploy.sh"
_ACCOUNTS: Final = ("fixture-agent", "fixture-peer")
_BUNDLE: Final = {
    ".hermes/interop/production_guard.sh": "automation/interop/production_guard.sh",
    ".hermes/interop/pin_import_root.sh": "automation/interop/pin_import_root.sh",
    ".config/systemd/user/hermes-gateway.service.d/20-interop-guard.conf": "automation/interop/gateway-dropin.conf",
    ".hermes/plugins/interop-protocol/plugin.yaml": "automation/interop/hermes_plugin/plugin.yaml",
    ".hermes/plugins/interop-protocol/__init__.py": "automation/interop/plugin_shim/__init__.py",
}
_LATE: Final = {
    ".hermes/interop/gate_driver.py": "automation/interop/gate_driver.py",
    ".hermes/interop_runtime/automation/__init__.py": "automation/__init__.py",
    ".hermes/interop_runtime/automation/node_config.py": "automation/node_config.py",
    ".hermes/interop_runtime/automation/node.example.toml": "configs/node.example.toml",
}
_RULES: Final = ".hermes/interop/external-effect-tools.yaml"


def _exe(path: Path, body: str, interpreter: str = sys.executable) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(f"#!{interpreter}\n{body}", encoding="utf-8")
    path.chmod(0o755)


_LOG_HELPER: Final = (
    "import json, os, sys\n"
    "def log(entry):\n"
    "    entry['account'] = os.environ.get('FAKE_ACCOUNT', '-')\n"
    "    with open(os.environ['FAKE_LOG'], 'a', encoding='utf-8') as handle:\n"
    "        handle.write(json.dumps(entry) + '\\n')\n"
)


def _tree_bytes(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        str(p.relative_to(root)): (b"LINK:" + os.readlink(p).encode()) if p.is_symlink() else p.read_bytes()
        for p in sorted(root.rglob("*"))
        if (p.is_file() or p.is_symlink()) and "__pycache__" not in p.parts
    }


class Node:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.log = tmp_path / "calls.jsonl"
        self.homes = tmp_path / "homes"
        bin_dir = tmp_path / "bin"
        _exe(bin_dir / "ssh", "import os, sys\nassert sys.argv[1] == 'fixture-host', sys.argv\n"
             "os.execvp('bash', ['bash', '-c', sys.argv[2]])\n")
        _exe(bin_dir / "sudo", _LOG_HELPER + (
            "import subprocess\n"
            "args = sys.argv[1:]\n"
            "assert args[:2] == ['-n', '-u'] and args[3:6] == ['-H', 'bash', '-lc'], args\n"
            "account, script = args[2], args[6]\n"
            "os.environ['FAKE_ACCOUNT'] = account\n"
            "log({'tool': 'ssh', 'script': script})\n"
            "fail = os.environ.get('FAKE_SSH_FAIL')\n"
            "if fail and fail in script:\n"
            "    sys.exit(255)\n"
            "home = os.path.join(os.environ['FAKE_HOMES'], account)\n"
            "env = dict(os.environ, HOME=home)\n"
            "sys.exit(subprocess.run(['bash', '-c', script], env=env, cwd=home).returncode)\n"
        ))
        _exe(bin_dir / "systemctl", _LOG_HELPER + (
            "import hashlib\nfrom pathlib import Path\n"
            "log({'tool': 'systemctl', 'args': sys.argv[1:]})\n"
            "if 'restart' in sys.argv and os.environ.get('FAKE_RESTART_RECORDS') == '1':\n"
            "    home = Path(os.environ['HOME'])\n"
            "    pid = int(os.environ['FAKE_LIVE_PID'])\n"
            "    shim = home / '.hermes/plugins/interop-protocol/__init__.py'\n"
            "    (home / '.hermes/gateway.pid').write_text(json.dumps({'pid': pid}))\n"
            "    (home / '.hermes/gateway-generation.json').write_text(json.dumps({'pid': pid,\n"
            "        'import_root': os.environ['FAKE_GEN'], 'plugins': {'interop-protocol':\n"
            "        hashlib.sha256(shim.read_bytes()).hexdigest()}}))\n"
        ))
        for account in _ACCOUNTS:
            self._seed(self.homes / account)
        (tmp_path / "current").symlink_to(_REPO)
        toml = (_REPO / "configs/node.example.toml").read_text(encoding="utf-8")
        for old, new in (('deploy_ssh_host = "example-primary-node"', 'deploy_ssh_host = "fixture-host"'),
                         ('agent_account = "agent"', 'agent_account = "fixture-agent"'),
                         ('peer_account = "peer"', 'peer_account = "fixture-peer"')):
            toml = toml.replace(old, new)
        _ = (tmp_path / "node.toml").write_text(toml, encoding="utf-8")
        hidden = ("AUTOPHAGY_RUNTIME_ROOT", "PYTHONPATH", "DEPLOY_SSH_HOST", "NODE_DEPLOY_SSH_HOST")
        self.env = {k: v for k, v in os.environ.items() if k not in hidden}
        self.env.update(
            PATH=f"{bin_dir}:{os.environ['PATH']}", FAKE_LOG=str(self.log), FAKE_HOMES=str(self.homes),
            HOME=str(tmp_path / "operator"), HEALTHCHECK_NODE_CONFIG_PATH=str(tmp_path / "node.toml"),
            DEPLOY_ALLOW_UNPUSHED="1", PIN_RELEASE_POINTER=str(tmp_path / "current"),
            PIN_MIRROR_ROOT=str(tmp_path / "absent-mirror"), FAKE_LIVE_PID=str(os.getpid()),
            FAKE_GEN=os.path.realpath(_REPO), INTEROP_RESTART_POLL="0",
        )

    def _seed(self, home: Path) -> None:
        runtime = home / ".hermes/interop_runtime/automation"
        runtime.mkdir(parents=True)
        _ = shutil.copyfile(_REPO / "automation/__init__.py", runtime / "__init__.py")
        _ = shutil.copytree(_REPO / "automation/interop", runtime / "interop",
                            ignore=shutil.ignore_patterns("__pycache__"))
        _ = (runtime / "interop/stale_module.py").write_text("OLD = 1\n", encoding="utf-8")
        _ = (runtime / "skill_gate_helper.py").write_text("FOREIGN = 1\n", encoding="utf-8")
        for relative in (*_BUNDLE, _RULES):
            target = home / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            _ = target.write_text(f"old {relative}\n", encoding="utf-8")
        _ = shutil.copyfile(_REPO / "configs/roster.example.yaml", home / ".hermes/roster.yaml")
        self.gateway_python(home, ok=True)
        _exe(home / ".local/bin/hermes", _LOG_HELPER + (
            "log({'tool': 'hermes', 'args': sys.argv[1:], 'stdin': os.readlink('/proc/self/fd/0')})\n"))

    def gateway_python(self, home: Path, *, ok: bool) -> None:
        body = f'exec "{sys.executable}" "$@"\n' if ok else "exit 1\n"
        _exe(home / ".hermes/hermes-agent/venv/bin/python", body, "/usr/bin/env bash")

    def run(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", str(_DEPLOY), *args], env={**self.env, **env}, cwd=self.tmp,
                              capture_output=True, text=True, check=False, timeout=240)

    def calls(self) -> list[dict[str, object]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def snapshot(self) -> dict[str, dict[str, bytes]]:
        return {account: _tree_bytes(self.homes / account) for account in _ACCOUNTS}


@pytest.fixture
def node(tmp_path: Path) -> Node:
    return Node(tmp_path)


def _first(calls: list[dict[str, object]], account: str, match: Callable[[dict[str, object]], bool]) -> int:
    for index, call in enumerate(calls):
        if call["account"] == account and match(call):
            return index
    raise AssertionError(f"no matching call for {account}")


def _ssh(marker: str) -> Callable[[dict[str, object]], bool]:
    return lambda call: call["tool"] == "ssh" and marker in str(call["script"])


def _push(destination: str) -> Callable[[dict[str, object]], bool]:
    return _ssh(f'dest="$HOME"/{destination}\n')


def _bundle_only(snapshot: dict[str, bytes]) -> dict[str, bytes]:
    return {k: v for k, v in snapshot.items() if not k.startswith(_RULES) and not k.endswith(f"/{_RULES}")}


def _tracked_interop() -> dict[str, bytes]:
    listed = subprocess.run(["git", "ls-files", "-z", "automation/interop"], cwd=_REPO,
                            capture_output=True, check=True).stdout.decode().split("\0")
    return {
        name.removeprefix("automation/interop/"): (_REPO / name).read_bytes()
        for name in listed if name.endswith(".py")
    }


def test_with_valid_rosters_the_bundle_lands_on_both_accounts_without_a_restart(node: Node) -> None:
    result = node.run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "INTEROP-DEPLOYED: gateway restart pending (agent+peer)" in result.stdout
    for account in _ACCOUNTS:
        home = node.homes / account
        for destination, source in {**_BUNDLE, **_LATE, _RULES: "configs/external-effect-tools.yaml"}.items():
            assert (home / destination).read_bytes() == (_REPO / source).read_bytes(), (account, destination)
        for executable in ("production_guard.sh", "pin_import_root.sh", "gate_driver.py"):
            assert (home / ".hermes/interop" / executable).stat().st_mode & 0o777 == 0o700
        assert _tree_bytes(home / ".hermes/interop_runtime/automation/interop") == _tracked_interop()
        assert (home / ".hermes/interop_runtime/automation/skill_gate_helper.py").read_text() == "FOREIGN = 1\n"
        olds = list((home / ".hermes/interop_runtime/automation").glob("interop.old.*"))
        assert len(olds) == 1 and (olds[0] / "stale_module.py").is_file()
        backups = list((home / ".hermes/interop/rollback").iterdir())
        assert len(backups) == 1
        assert {k: v.decode() for k, v in _tree_bytes(backups[0]).items()} == {
            relative: f"old {relative}\n" for relative in (*_BUNDLE, _RULES) if "pin_import_root" not in relative
        }
        assert not list(home.rglob("*.deploy-tmp.*"))
    calls = node.calls()
    tools = [(c["tool"], c.get("args")) for c in calls if c["tool"] != "ssh"]
    assert all("restart" not in (args or []) for _, args in tools)
    enables = [c for c in calls if c["tool"] == "hermes"]
    assert [(c["account"], c["args"], c["stdin"]) for c in enables] == [
        (a, ["plugins", "enable", "interop-protocol"], "/dev/null") for a in _ACCOUNTS
    ]
    reloads = [c["account"] for c in calls if c["tool"] == "systemctl" and "daemon-reload" in c["args"]]
    assert reloads == list(_ACCOUNTS)
    first_bundle = min(_first(calls, a, _push(".hermes/interop/production_guard.sh")) for a in _ACCOUNTS)
    for account in _ACCOUNTS:
        assert _first(calls, account, _push(_RULES)) < first_bundle
        assert _first(calls, account, _ssh("group_roster validate")) < first_bundle
        assert _first(calls, account, _ssh("gateway-preflight")) < first_bundle
        assert _first(calls, account, _ssh("interop/rollback")) < first_bundle
        order = [
            _first(calls, account, _push(".hermes/interop/production_guard.sh")),
            _first(calls, account, _push(".hermes/interop/pin_import_root.sh")),
            _first(calls, account, _push(".config/systemd/user/hermes-gateway.service.d/20-interop-guard.conf")),
            _first(calls, account, lambda c: c["tool"] == "systemctl" and "daemon-reload" in c["args"]),
            _first(calls, account, _push(".hermes/plugins/interop-protocol/plugin.yaml")),
            _first(calls, account, _push(".hermes/plugins/interop-protocol/__init__.py")),
            _first(calls, account, lambda c: c["tool"] == "hermes"),
            _first(calls, account, _push(".hermes/interop/gate_driver.py")),
        ]
        assert order == sorted(order), (account, order)


# 계정마다 "배포기가 놓은 파일"을 선언과 맞댄다. 놓였다는 것은 배포 전후의 (inode, mtime, ctime, 크기)가
# 달라졌다는 뜻이다 — mtime 을 되돌려 둔 제자리 재기록도 ctime 이 남기므로 잡히고, 같은 바이트를 다시
# 쓴 파일도 잡힌다. 선언이 아닌 것으로 인정하는 자리는 헬퍼가 실제로 만드는 아래 셋뿐이고, 그 밖의
# 경로(`__pycache__`, 이름만 닮은 임시·백업, 선언된 트리 안의 여분 파일)는 전부 "선언 없음"이다:
#   1 `<선언된 파일 목적지>.deploy-tmp.<pid>`            (deploy_push.sh 의 임시 파일)
#   2 `<선언된 트리>.old.<스탬프>-<pid>/…`                (deploy_tree.sh 가 보관하는 직전 트리)
#   3 `.hermes/interop/rollback/<스탬프>-<pid>/<선언된 파일 목적지>` (deploy.sh 단계 (e) 의 백업 사본)
_ROLLBACK_ROOT: Final = ".hermes/interop/rollback/"
_BACKUP_STAMP: Final = re.compile(r"[0-9]{8}T[0-9]{6}Z-[0-9]+")


def _stat_tree(home: Path) -> dict[str, tuple[int, int, int, int]]:
    found: dict[str, tuple[int, int, int, int]] = {}
    for path in sorted(home.rglob("*")):
        if path.is_file() or path.is_symlink():
            info = path.lstat()
            found[path.relative_to(home).as_posix()] = (
                info.st_ino, info.st_mtime_ns, info.st_ctime_ns, info.st_size,
            )
    return found


def _is_deployer_artefact(relative: str, files: set[str], trees: set[str]) -> bool:
    if any(re.fullmatch(re.escape(destination) + r"\.deploy-tmp\.[0-9]+", relative) for destination in files):
        return True
    if any(re.match(re.escape(tree) + r"\.old\.[^/]+/", relative) for tree in trees):
        return True
    if relative.startswith(_ROLLBACK_ROOT):
        stamp, _, backed_up = relative.removeprefix(_ROLLBACK_ROOT).partition("/")
        return bool(_BACKUP_STAMP.fullmatch(stamp)) and backed_up in files
    return False


def _placed_by_the_deployer(
    node: Node, account: str, before: dict[str, tuple[int, int, int, int]], files: set[str], trees: set[str]
) -> set[str]:
    after = _stat_tree(node.homes / account)
    return {
        relative for relative, stat in after.items()
        if before.get(relative) != stat and not _is_deployer_artefact(relative, files, trees)
    }


def test_each_account_receives_exactly_the_files_its_own_declaration_names(node: Node) -> None:
    from automation.deploy_declarations import all_declarations

    owner = "automation/interop"
    rows = [d for d in all_declarations(_REPO) if d.owner == owner and not d.legacy and d.policy != "retired"]
    stat_before = {account: _stat_tree(node.homes / account) for account in _ACCOUNTS}
    result = node.run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "INTEROP-DEPLOYED" in result.stdout
    for account in _ACCOUNTS:
        mine = [d for d in rows if f"fixture-{d.account}" == account]
        assert mine, f"{account}: 인터롭 v2 선언이 없다"
        files = {d.destination for d in mine if d.kind == "file"}
        tree_rows = [d for d in mine if d.kind == "tree"]
        expected = set(files)
        for tree in tree_rows:
            listed = subprocess.run(["git", "ls-files", "-z", tree.source], cwd=_REPO,
                                    capture_output=True, check=True).stdout.decode().split("\0")
            expected |= {
                f"{tree.destination}/{name.removeprefix(tree.source + '/')}"
                for name in listed if name and (tree.attr("profile") != "python" or name.endswith(".py"))
            }
        placed = _placed_by_the_deployer(
            node, account, stat_before[account], files, {tree.destination for tree in tree_rows}
        )
        assert placed == expected, (
            account,
            "놓였는데 선언 없음: " + ", ".join(sorted(placed - expected)),
            "선언됐는데 안 놓임: " + ", ".join(sorted(expected - placed)),
        )


def test_without_a_roster_only_the_rule_file_is_converged(node: Node) -> None:
    for account in _ACCOUNTS:
        (node.homes / account / ".hermes/roster.yaml").unlink()
    before = node.snapshot()
    result = node.run()
    assert result.returncode == 0, result.stderr
    assert "INTEROP-DEPLOY-HELD: roster missing or invalid on fixture-agent" in result.stdout
    after = node.snapshot()
    for account in _ACCOUNTS:
        assert _bundle_only(after[account]) == _bundle_only(before[account])
        assert (node.homes / account / _RULES).read_bytes() == (_REPO / "configs/external-effect-tools.yaml").read_bytes()
    assert not [c for c in node.calls() if c["tool"] in ("hermes", "systemctl")]


def test_a_roster_on_one_account_holds_both(node: Node) -> None:
    _ = (node.homes / "fixture-peer/.hermes/roster.yaml").write_text("schema: 1\n", encoding="utf-8")
    before = node.snapshot()
    result = node.run()
    assert result.returncode == 0, result.stderr
    assert "INTEROP-DEPLOY-HELD: roster missing or invalid on fixture-peer" in result.stdout
    after = node.snapshot()
    for account in _ACCOUNTS:
        assert _bundle_only(after[account]) == _bundle_only(before[account])
    assert not [c for c in node.calls() if c["tool"] in ("hermes", "systemctl")]


def _assert_bundle_untouched(node: Node, before: dict[str, dict[str, bytes]]) -> None:
    after = node.snapshot()
    for account in _ACCOUNTS:
        assert _bundle_only(after[account]) == _bundle_only(before[account]), account
        kept = _tree_bytes(node.homes / account / ".hermes/interop/rollback")
        assert all(relative.endswith(f"/{_RULES}") for relative in kept), (account, sorted(kept))
    assert not [c for c in node.calls() if c["tool"] in ("hermes", "systemctl")]


def test_failed_preflight_changes_nothing(node: Node) -> None:
    for account in _ACCOUNTS:
        rules = node.homes / account / _RULES
        _ = rules.write_bytes((_REPO / "configs/external-effect-tools.yaml").read_bytes())
    _exe(node.homes / "fixture-peer/.hermes/hermes-agent/venv/bin/python",
         f'case "$*" in *gateway-preflight*) exit 1 ;; esac\nexec "{sys.executable}" "$@"\n', "/usr/bin/env bash")
    before = node.snapshot()
    result = node.run()
    assert result.returncode == 5, result.stdout + result.stderr
    assert "INTEROP-DEPLOY-BLOCK: preflight import failed on fixture-peer" in result.stderr
    assert node.snapshot() == before
    _assert_bundle_untouched(node, before)


def test_a_shadowing_automation_package_fails_the_preflight(node: Node) -> None:
    home = node.homes / "fixture-agent"
    shadow = home / "shadow/automation"
    shadow.mkdir(parents=True)
    _ = (shadow / "__init__.py").write_text("", encoding="utf-8")
    boot = home / ".hermes/hermes-compat/hermes_compat_boot.py"
    boot.parent.mkdir(parents=True)
    _ = boot.write_text(f"import sys\nsys.path.insert(0, {str(shadow.parent)!r})\nimport automation\n", encoding="utf-8")
    before = node.snapshot()
    result = node.run()
    assert result.returncode == 5
    assert "INTEROP-DEPLOY-BLOCK: preflight import failed on fixture-agent" in result.stderr
    _assert_bundle_untouched(node, before)


def test_an_unparsable_rule_file_is_never_installed(node: Node) -> None:
    gate = node.homes / "fixture-peer/.hermes/interop_runtime/automation/interop/external_effect_gate.py"
    _ = gate.write_text("def load_denylist(path):\n    raise ValueError('old format only')\n", encoding="utf-8")
    before = node.snapshot()
    result = node.run()
    assert result.returncode == 5, result.stdout + result.stderr
    assert "INTEROP-DEPLOY-BLOCK" in result.stderr
    for account in _ACCOUNTS:
        assert (node.homes / account / _RULES).read_text() == f"old {_RULES}\n"
    _assert_bundle_untouched(node, before)


def test_a_gate_that_reads_fewer_rules_blocks_the_rule_file(node: Node) -> None:
    gate = node.homes / "fixture-agent/.hermes/interop_runtime/automation/interop/external_effect_gate.py"
    _ = gate.write_text("def load_denylist(path):\n    return (1,)\n", encoding="utf-8")
    result = node.run()
    assert result.returncode == 5
    assert all((node.homes / a / _RULES).read_text() == f"old {_RULES}\n" for a in _ACCOUNTS)


def test_restart_flag_restarts_both_and_waits_for_the_generation(node: Node) -> None:
    result = node.run("--restart", FAKE_RESTART_RECORDS="1", INTEROP_RESTART_WAIT="10")
    assert result.returncode == 0, result.stdout + result.stderr
    restarts = [c["account"] for c in node.calls() if c["tool"] == "systemctl" and "restart" in c["args"]]
    assert restarts == list(_ACCOUNTS)
    checks = [c for c in node.calls() if c["tool"] == "ssh" and "gateway_generation --check" in str(c["script"])]
    assert {c["account"] for c in checks} == set(_ACCOUNTS)
    last_restart = max(i for i, c in enumerate(node.calls()) if c["tool"] == "systemctl" and "restart" in c["args"])
    assert all(node.calls().index(c) > last_restart for c in checks)
    for account in _ACCOUNTS:
        record = json.loads((node.homes / account / ".hermes/gateway-generation.json").read_text())
        shim = node.homes / account / ".hermes/plugins/interop-protocol/__init__.py"
        assert record["plugins"]["interop-protocol"] == hashlib.sha256(shim.read_bytes()).hexdigest()


def test_a_restart_that_never_reaches_the_generation_fails_with_the_backup_path(node: Node) -> None:
    result = node.run("--restart", INTEROP_RESTART_WAIT="0")
    assert result.returncode == 5
    assert ".hermes/interop/rollback/" in result.stderr
    restarts = [c["account"] for c in node.calls() if c["tool"] == "systemctl" and "restart" in c["args"]]
    assert restarts == list(_ACCOUNTS)


def test_an_interrupted_transfer_keeps_the_previous_shim_and_leaves_no_temp(node: Node) -> None:
    result = node.run(FAKE_SSH_FAIL='dest="$HOME"/.hermes/plugins/interop-protocol/__init__.py\n')
    assert result.returncode == 5, result.stdout + result.stderr
    assert "INTEROP-DEPLOY-BLOCK" in result.stderr and ".hermes/interop/rollback/" in result.stderr
    home = node.homes / "fixture-agent"
    assert (home / ".hermes/plugins/interop-protocol/__init__.py").read_text().startswith("old ")
    assert len(list((home / ".hermes/interop/rollback").iterdir())) == 1
    assert not list(node.homes.rglob("*.deploy-tmp.*"))
    peer = node.homes / "fixture-peer"
    assert (peer / ".config/systemd/user/hermes-gateway.service.d/20-interop-guard.conf").read_text().startswith("old ")
    assert not [c for c in node.calls() if c["tool"] == "hermes"]


def test_an_unknown_argument_is_refused_before_any_ssh(node: Node) -> None:
    result = node.run("--restar")
    assert result.returncode == 2
    assert node.calls() == []
