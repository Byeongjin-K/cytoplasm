"""Hermes product retirement uses fixture homes; no node or real CLI is touched."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from automation import hermes_product as product
from automation import hermes_product_live as live
from automation import release_approval
from automation.ledger import migration, store
from tests.unit import test_release_authorize as auth
from tests.unit.test_healthcheck_wrapper_release import _fake_plan, _plan_args, _spec

HEAD = "a" * 40
KEPT = (".local/bin/hermes", ".hermes/hermes-agent/cli.py", ".hermes/config.yaml",
        ".hermes/auth.json", ".hermes/USER.md", ".hermes/scripts/our_runtime.py", ".hermes/SOUL.md")


def _home(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    for name in (*product.TARGETS, ".hermes/kanban.db", *KEPT):
        path = home / name
        if name in (".hermes/sessions", ".hermes/memories", ".hermes/skills"):
            path /= "content"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
    runtime = tmp_path / "runtime"
    declaration = runtime / "automation/example/deploy-manifest.txt"
    declaration.parent.mkdir(parents=True)
    declaration.write_text("agent|automation/example/run.py|.hermes/scripts/run.py|required\n")
    return home, runtime


def _remove(home: Path, runtime: Path, head: str = HEAD, *, verified: bool = False) -> Path:
    return product.remove(home, head, runtime=runtime, proc_reader=lambda: (),
                          verify_migration=lambda: verified)


@pytest.mark.parametrize("value", [b"", b"retired\n", b"active", b" retired", b"\xff"])
def test_switch_rejects_nonliteral_content(tmp_path: Path, value: bytes) -> None:
    flag = tmp_path / ".hermes/release-flags/hermes-product"
    flag.parent.mkdir(parents=True)
    flag.write_bytes(value)
    with pytest.raises(ValueError, match="HERMES-PRODUCT-REFUSED"):
        product.release_switch(tmp_path)


def test_switch_absent_and_exact_retired(tmp_path: Path) -> None:
    assert product.release_switch(tmp_path) == "active"
    flag = tmp_path / ".hermes/release-flags/hermes-product"
    flag.parent.mkdir(parents=True)
    flag.write_bytes(b"retired")
    assert product.release_switch(tmp_path) == "retired"
    assert product.surface() == ("hermes-product:retired", hashlib.sha256(b"retired").hexdigest())


@pytest.mark.parametrize("version", [6, 7, 8])
def test_card_contains_shipped_copy_only_for_product_surface(version: int) -> None:
    base = replace(_spec(), render_version=version)
    selected = replace(base, surface_digests=(*base.surface_digests, product.surface()))
    assert all(line not in base.render() for line in product.CARD_LINES)
    assert all(selected.render().count(line) == 1 for line in product.CARD_LINES)
    assert "hermes-product:retired" not in selected.render()
    assert selected.action_hash() != base.action_hash()
    assert len(selected.render()) <= 1900


@pytest.mark.parametrize("selected", [False, True])
def test_plan_binds_only_explicit_product_switch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], selected: bool,
) -> None:
    _fake_plan(monkeypatch)
    args = _plan_args()
    argv = ["plan", "--repo", args.repo, "--base", args.base, "--head", args.head,
            "--version", args.version]
    if selected:
        argv += ["--hermes-product", "retired"]
    assert release_approval.main(argv) == 0
    payload = json.loads(capsys.readouterr().out)
    assert (list(product.surface()) in payload["surface_digests"]) == selected


@pytest.mark.parametrize("reaction,covered,head,code", [
    ("approve", True, HEAD, 0), ("pending", True, HEAD, 1),
    ("both", True, HEAD, 8), ("approve", False, HEAD, 4),
    ("approve", True, "f" * 40, 4),
])
def test_authorization_binds_head_surface_and_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    reaction: str, covered: bool, head: str, code: int,
) -> None:
    base = auth._spec()
    spec = replace(base, surface_digests=(*base.surface_digests,
                                        *([product.surface()] if covered else [])))
    monkeypatch.setattr(auth, "_spec", lambda: spec)
    owner = [{"id": auth._OWNER, "bot": False}]
    reactions = {auth._APPROVE: owner if reaction in ("approve", "both") else [],
                 auth._CANCEL: owner if reaction == "both" else []}
    auth._env(tmp_path, monkeypatch, reactions=reactions, api_log=[], render_version=6)
    assert release_approval.main(["authorize-hermes-product", "--head", head]) == code


def test_only_listed_targets_removed_with_verified_private_backup(tmp_path: Path) -> None:
    home, runtime = _home(tmp_path)
    originals = {name: (home / name).read_bytes() for name in KEPT}
    soul = (home / ".hermes/SOUL.md").read_bytes()
    kanban = (home / ".hermes/kanban.db").read_bytes()
    assert ".hermes/SOUL.md" not in product.TARGETS
    folder = _remove(home, runtime)
    assert all(not (home / name).exists() for name in product.TARGETS)
    assert all((home / name).read_bytes() == data for name, data in originals.items())
    assert (home / ".hermes").is_dir()
    assert (home / ".hermes/kanban.db").read_bytes() == kanban
    assert (home / product.MARKER).read_text() == HEAD
    assert (home / ".hermes/SOUL.md").read_bytes() == soul  # a declared live file stays
    assert {p.name for p in folder.iterdir()} == {"kanban.db", "sha256.json"}
    assert json.loads((folder / "sha256.json").read_text()) == {
        "kanban.db": hashlib.sha256(kanban).hexdigest(),
    }
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in folder.iterdir())


def test_bad_backup_hash_refuses_before_any_deletion(tmp_path: Path) -> None:
    home, runtime = _home(tmp_path)
    folder = home / ".hermes/retired/hermes-product" / HEAD
    product.backup(home, folder)
    (folder / "kanban.db").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="백업 해시"):
        _remove(home, runtime, verified=True)
    assert (home / ".hermes/state.db").exists()
    assert (home / ".hermes/SOUL.md").exists()
    assert not (home / product.MARKER).exists()


def test_source_change_after_backup_refuses_before_deletion(tmp_path: Path) -> None:
    home, runtime = _home(tmp_path)
    product.backup(home, home / ".hermes/retired/hermes-product" / HEAD)
    (home / ".hermes/kanban.db").write_bytes(b"new source")
    with pytest.raises(ValueError, match="백업 뒤 원본"):
        _remove(home, runtime, verified=True)
    assert (home / ".hermes/state.db").exists()


def test_backup_is_read_back_before_first_unlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, runtime = _home(tmp_path)
    unlink = Path.unlink

    def checked_unlink(path: Path, *args, **kwargs) -> None:
        folder = home / ".hermes/retired/hermes-product" / HEAD
        index = json.loads((folder / "sha256.json").read_text())
        assert all(hashlib.sha256((folder / name).read_bytes()).hexdigest() == digest
                   for name, digest in index.items())
        assert not (home / product.MARKER).exists()
        unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", checked_unlink)
    _remove(home, runtime)


@pytest.mark.parametrize("kind", ["unit", "process", "declaration"])
def test_live_path_refuses_before_backup_or_deletion(
    tmp_path: Path, kind: str, capsys: pytest.CaptureFixture[str],
) -> None:
    home, runtime = _home(tmp_path)
    opened: tuple[Path, ...] = ()
    if kind == "unit":
        unit = home / ".config/systemd/user/x.service.d/override.conf"
        unit.parent.mkdir(parents=True)
        unit.write_text("Environment=PRIVATE=not-for-output\nExecStart=read %h/.hermes/state.db\n")
    if kind == "process":
        opened = (home / ".hermes/sessions/content",)
    if kind == "declaration":
        (runtime / "automation/example/deploy-manifest.txt").write_text(
            "agent|automation/example/run.py|.hermes/skills/bundle/SKILL.md|required\n")
    with pytest.raises(ValueError, match="제품 경로 사용 중"):
        product.remove(home, HEAD, runtime=runtime, proc_reader=lambda: opened,
                       verify_migration=lambda: False)
    assert all((home / name).exists() for name in product.TARGETS)
    assert not (home / ".hermes/retired").exists()
    assert f"reason={kind}" in capsys.readouterr().out
    assert not (home / product.MARKER).exists()


@pytest.mark.parametrize("link", ["cwd", "fd/3"])
def test_proc_reader_detects_real_open_and_cwd_links(tmp_path: Path, link: str) -> None:
    proc = tmp_path / "proc"
    entry = proc / "123"
    (entry / "fd").mkdir(parents=True)
    target = tmp_path / "home/.hermes/sessions"
    (entry / link).symlink_to(target)
    assert live.process_paths(proc) == (target,)


def test_unreadable_proc_refuses_without_deletion(tmp_path: Path) -> None:
    home, runtime = _home(tmp_path)

    def unreadable() -> tuple[Path, ...]:
        raise PermissionError("private process content")

    with pytest.raises(ValueError, match="사용 경로 판독 불가"):
        product.remove(home, HEAD, runtime=runtime, proc_reader=unreadable,
                       verify_migration=lambda: False)
    assert (home / ".hermes/state.db").exists()


def test_unit_directory_symlink_does_not_hide_a_live_target(tmp_path: Path) -> None:
    home, runtime = _home(tmp_path)
    external = tmp_path / "dropins"
    external.mkdir()
    (external / "override.conf").write_text("ExecStart=read %h/.hermes/state.db\n")
    units = home / ".config/systemd/user"
    units.mkdir(parents=True)
    (units / "x.service.d").symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match="제품 경로 사용 중"):
        _remove(home, runtime)
    assert (home / ".hermes/state.db").exists()


def test_no_migration_record_keeps_kanban_using_real_verifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, runtime = _home(tmp_path)
    monkeypatch.setattr(store, "default_path", lambda: home / ".local/state/autophagy/ledger.db")
    product.remove(home, HEAD, runtime=runtime, proc_reader=lambda: ())
    assert (home / ".hermes/kanban.db").exists()
    assert not (home / ".hermes/state.db").exists()


def test_retry_removes_reappearing_nonbackup_target_without_new_archive(tmp_path: Path) -> None:
    home, runtime = _home(tmp_path)
    folder = _remove(home, runtime)
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    assert _remove(home, runtime, "b" * 40) == folder
    (home / ".hermes/state.db").write_bytes(b"late")
    assert _remove(home, runtime, "c" * 40) == folder
    assert not (home / ".hermes/state.db").exists()
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before


def test_reappearing_target_is_not_exempt_from_live_checks(tmp_path: Path) -> None:
    home, runtime = _home(tmp_path)
    _remove(home, runtime)
    target = home / ".hermes/state.db"
    target.write_bytes(b"late")
    with pytest.raises(ValueError, match="제품 경로 사용 중"):
        product.remove(home, HEAD, runtime=runtime, proc_reader=lambda: (target,),
                       verify_migration=lambda: False)
    assert target.read_bytes() == b"late"


def _migrated(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    kanban = home / ".hermes/kanban.db"
    kanban.unlink()
    with sqlite3.connect(kanban) as db:
        db.execute("CREATE TABLE tasks(id, project_id, status, idempotency_key)")
        db.execute("INSERT INTO tasks VALUES ('t_a',NULL,'done','key')")
    boards = [{"project_id": None, "slug": "default", "db_path": str(kanban)}]
    cards = [{"task": {"id": "t_a", "project_id": None, "status": "done"},
              "events": [], "comments": []}]
    ledger = store.Store(home / ".local/state/autophagy/ledger.db")
    migration.import_snapshot(ledger, boards, cards, migration.source_keys(boards))
    monkeypatch.setattr(store, "default_path", lambda: ledger.path)
    monkeypatch.setattr(migration, "export_hermes", lambda: (boards, cards))


def test_kanban_removed_only_after_count_and_source_verified_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, runtime = _home(tmp_path)
    _migrated(home, monkeypatch)
    assert product.migration_verified()
    folder = product.remove(home, HEAD, runtime=runtime, proc_reader=lambda: ())
    assert not (home / ".hermes/kanban.db").exists()
    assert (folder / "kanban.db").read_bytes().startswith(b"SQLite format 3")


def test_source_changed_since_migration_keeps_kanban(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, runtime = _home(tmp_path)
    _migrated(home, monkeypatch)
    with sqlite3.connect(home / ".hermes/kanban.db") as db:
        db.execute("UPDATE tasks SET status='ready'")
    assert not product.migration_verified()
    product.remove(home, HEAD, runtime=runtime, proc_reader=lambda: ())
    assert (home / ".hermes/kanban.db").exists()


def test_deploy_refuses_unapproved_product_before_removal(tmp_path: Path) -> None:
    from tests.unit.test_hermes_gateways_stop import _host

    repo, env = _host(tmp_path, None, auth_rc=4)
    flag = Path(env["HOME"]) / ".hermes/release-flags/hermes-product"
    flag.parent.mkdir(parents=True)
    flag.write_bytes(b"retired")
    result = subprocess.run(("bash", str(repo / "automation/deploy_all.sh"), "--apply"),
                            env=env, text=True, capture_output=True, check=False)
    assert result.returncode == 4
    calls = (tmp_path / "calls").read_text()
    assert "authorize-hermes-product --head " + HEAD in calls
    assert "automation.hermes_product --remove" not in calls


@pytest.mark.parametrize("selected", [True, False])
def test_deploy_runs_approved_product_once_after_every_other_step(
    tmp_path: Path, selected: bool,
) -> None:
    from tests.unit.test_hermes_gateways_stop import _host

    repo, env = _host(tmp_path, "stopped")
    flags = Path(env["HOME"]) / ".hermes/release-flags"
    (flags / "peer-retired").write_bytes(b"retired")
    if selected:
        (flags / "hermes-product").write_bytes(b"retired")
    env.update(NODE_AGENT_ACCOUNT="agent", NODE_PEER_ACCOUNT="peer", NODE_OPS_ACCOUNT="ops",
               NODE_PRIVATE_ROOT=str(tmp_path / "private"))
    ssh = tmp_path / "bin/ssh"
    script = ssh.read_text()
    script = script.replace('*) exit 97;;', (
        '*"automation.p5_remove"*|*"automation.hermes_product --remove"*|*"printf retired >"*) exit 0;;\n'
        '*"getent passwd"*) exit 1;;\n'
        '*) exit 97;;'
    ))
    ssh.write_text(script)
    ssh.chmod(0o755)
    result = subprocess.run(("bash", str(repo / "automation/deploy_all.sh"), "--apply"),
                            env=env, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = (tmp_path / "calls").read_text().splitlines()
    products = [i for i, line in enumerate(calls) if "automation.hermes_product --remove" in line]
    assert len(products) == (1 if selected else 0)
    for index in products:
        assert "authorize-hermes-product --head " + HEAD in calls[index - 1]
        assert "sudo -n -u agent -H env PYTHONPATH=" in calls[index]
        assert any("automation.p5_remove --role peer" in line for line in calls[:index])
    if selected:
        peer_calls = [i for i, line in enumerate(calls) if "automation.p5_remove --role peer" in line]
        assert products[0] > max(peer_calls)  # only after the final peer recheck


def test_cli_switch_does_not_read_processes_or_remove(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        (os.sys.executable, "-B", "-m", "automation.hermes_product", "--release-switch"),
        cwd=repo, env={**os.environ, "HOME": str(tmp_path)}, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0
    assert result.stdout == "active\n"
    assert not (tmp_path / ".hermes").exists()


def test_cli_invalid_switch_reports_one_safe_line(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    flag = tmp_path / ".hermes/release-flags/hermes-product"
    flag.parent.mkdir(parents=True)
    flag.write_bytes(b"private-invalid-switch")
    result = subprocess.run(
        (os.sys.executable, "-B", "-m", "automation.hermes_product", "--release-switch"),
        cwd=repo, env={**os.environ, "HOME": str(tmp_path)}, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 4
    assert len(result.stderr.splitlines()) == 1
    assert "HERMES-PRODUCT-REFUSED" in result.stderr
    assert "private-invalid-switch" not in result.stderr


def _proc_entry(proc: Path, pid: str, argv: bytes, *, readable: bool, target: Path) -> None:
    entry = proc / pid
    (entry / "fd").mkdir(parents=True)
    (entry / "cmdline").write_bytes(argv)
    (entry / "cwd").symlink_to(target)
    if not readable:
        (entry / "fd").chmod(0)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-0 directory")
@pytest.mark.parametrize("argv", [
    b"/usr/lib/systemd/systemd\0--user\0", b"(sd-pam)\0", b"/usr/bin/gpg-agent\0--supervised\0",
    b"sshd: agent@notty\0", b"ssh\0-i\0key\0",
])
def test_non_dumpable_system_helper_is_skipped_but_readable_users_still_count(
    tmp_path: Path, argv: bytes,
) -> None:
    proc, target = tmp_path / "proc", tmp_path / "home/.hermes/sessions"
    _proc_entry(proc, "10", argv, readable=False, target=target)
    _proc_entry(proc, "11", b"python3\0hermes\0", readable=True, target=target)
    try:
        assert live.process_paths(proc) == (target,)
    finally:
        (proc / "10/fd").chmod(0o700)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-0 directory")
def test_unreadable_process_that_is_not_a_known_helper_still_refuses(tmp_path: Path) -> None:
    proc = tmp_path / "proc"
    _proc_entry(proc, "12", b"python3\0hermes\0", readable=False, target=tmp_path / "x")
    try:
        with pytest.raises(PermissionError):
            live.process_paths(proc)
    finally:
        (proc / "12/fd").chmod(0o700)


@pytest.mark.parametrize(("wal", "removed"), [(b"", True), (b"frame", False)])
def test_kanban_sidecars_from_the_migration_read_go_with_it_only_when_the_wal_is_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wal: bytes, removed: bool,
) -> None:
    home, runtime = _home(tmp_path)
    _migrated(home, monkeypatch)
    kanban = home / ".hermes/kanban.db"
    Path(f"{kanban}-wal").write_bytes(wal)
    Path(f"{kanban}-shm").write_bytes(b"index")
    product.remove(home, HEAD, runtime=runtime, proc_reader=lambda: ())
    for path in (kanban, Path(f"{kanban}-wal"), Path(f"{kanban}-shm")):
        assert path.exists() is not removed
